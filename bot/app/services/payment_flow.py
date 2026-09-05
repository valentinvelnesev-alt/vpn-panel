"""Создание внешнего платежа (Platega / RollyPay / CryptoBot) до его подтверждения.

Сама оплата подтверждается вебхуком на бэкенде (см. backend webhooks.py) или
опросом провайдера (payment_check.py) — здесь только заводим запись Payment
и получаем у провайдера ссылку/счёт, на которую отправить пользователя.
"""

import logging
from uuid import uuid4

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Config
from shared.db.models import BotUser, Payment, PaymentProvider, PaymentPurpose
from shared.payments.cryptobot import CryptoBotClient, CryptoBotError
from shared.payments.currency import CurrencyError, rub_to_crypto
from shared.payments.platega import PlategaClient, PlategaError
from shared.payments.rollypay import RollyPayClient, RollyPayError

log = logging.getLogger("bot.payment_flow")


class PaymentFlowError(Exception):
    """Показывается пользователю как есть — без внутренних деталей."""


def _ensure_configured(config: Config, provider: PaymentProvider) -> None:
    """Проверяем реквизиты ДО создания строки Payment — иначе в БД
    оставался бы платёж-сирота с external_id «pending-…», который воркер
    опроса потом бесконечно дёргал бы у провайдера."""
    if provider is PaymentProvider.PLATEGA and not config.platega_ready:
        raise PaymentFlowError("Оплата картой сейчас недоступна")
    if provider is PaymentProvider.ROLLYPAY and not config.rollypay_ready:
        raise PaymentFlowError("Оплата через СБП сейчас недоступна")
    if provider is PaymentProvider.CRYPTOBOT and not config.cryptobot_ready:
        raise PaymentFlowError("Оплата криптовалютой сейчас недоступна")
    if provider not in (
        PaymentProvider.PLATEGA,
        PaymentProvider.ROLLYPAY,
        PaymentProvider.CRYPTOBOT,
    ):
        raise PaymentFlowError(f"Провайдер {provider} не поддерживает внешние счета")


async def create_external_payment(
    db: AsyncSession,
    config: Config,
    user: BotUser,
    *,
    purpose: PaymentPurpose,
    amount_kopeks: int,
    provider: PaymentProvider,
    plan_id: int | None = None,
    subscription_id: int | None = None,
    description: str,
) -> tuple[Payment, str]:
    """Возвращает (запись платежа, ссылка на оплату).

    subscription_id задан только для продления конкретного существующего
    ключа — покупка нового ключа его не передаёт.

    Всё идёт внутри SAVEPOINT: если провайдер не ответил, откатывается
    только строка Payment, а не изменения, сделанные в сессии до этого."""
    _ensure_configured(config, provider)

    try:
        async with db.begin_nested():
            payment = Payment(
                user_id=user.id,
                provider=provider,
                # Временное значение — обязательный NOT NULL UNIQUE, обновится ниже
                # на настоящий id провайдера сразу после его получения.
                external_id=f"pending-{uuid4()}",
                amount_kopeks=amount_kopeks,
                purpose=purpose,
                plan_id=plan_id,
                subscription_id=subscription_id,
            )
            db.add(payment)
            await db.flush()  # нужен payment.id для order_id

            pay_url = await _create_at_provider(
                config, provider, payment, amount_kopeks / 100, description
            )
            if not pay_url:
                raise PaymentFlowError("Провайдер не вернул ссылку на оплату")
    except (PlategaError, RollyPayError, CryptoBotError, CurrencyError, httpx.HTTPError) as exc:
        # Пользователю показывается общая фраза, но в лог пишем ответ
        # провайдера целиком — без этого причина отказа (например,
        # невалидное тело запроса) не видна вообще нигде.
        log.error("Провайдер %s не принял платёж: %s", provider, exc)
        raise PaymentFlowError(
            "Платёжный сервис не отвечает, попробуйте позже или другой способ"
        ) from exc

    return payment, pay_url


async def _create_at_provider(
    config: Config,
    provider: PaymentProvider,
    payment: Payment,
    amount_rub: float,
    description: str,
) -> str:
    if provider is PaymentProvider.PLATEGA:
        client = PlategaClient(config.platega_merchant_id, config.platega_secret)
        result = await client.create_transaction(
            amount_rub=amount_rub,
            description=description,
            order_id=str(payment.id),
            return_url="https://t.me",
        )
        external = PlategaClient.transaction_id(result)
        if not external:
            raise PlategaError(f"в ответе нет id транзакции: {result}")
        payment.external_id = external
        return PlategaClient.pay_url(result)

    if provider is PaymentProvider.ROLLYPAY:
        client = RollyPayClient(config.rollypay_api_key)
        result = await client.create_payment(
            amount_rub=amount_rub,
            description=description,
            order_id=str(payment.id),
        )
        external = result.get("payment_id") or result.get("order_id")
        if not external:
            raise RollyPayError("в ответе нет payment_id")
        payment.external_id = str(external)
        return result.get("pay_url") or ""

    client = CryptoBotClient(config.cryptobot_token)
    asset = "USDT"
    amount_asset = await rub_to_crypto(amount_rub, asset)
    invoice = await client.create_invoice(
        amount=amount_asset,
        asset=asset,
        description=description,
        payload=str(payment.id),
    )
    if not invoice.get("invoice_id"):
        raise CryptoBotError("в ответе нет invoice_id")
    payment.external_id = str(invoice["invoice_id"])
    return invoice.get("pay_url") or invoice.get("bot_invoice_url") or ""
