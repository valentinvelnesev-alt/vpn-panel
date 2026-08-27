"""Создание внешнего платежа (Platega / CryptoBot) до его подтверждения.

Сама оплата подтверждается вебхуком на бэкенде (см. backend webhooks.py) —
здесь только заводим запись Payment и получаем у провайдера ссылку/счёт,
на которую отправить пользователя.
"""

from uuid import uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Config
from shared.db.models import BotUser, Payment, PaymentProvider, PaymentPurpose
from shared.payments.cryptobot import CryptoBotClient
from shared.payments.platega import PlategaClient
from shared.payments.rollypay import RollyPayClient


class PaymentFlowError(Exception):
    """Показывается пользователю как есть — без внутренних деталей."""


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
    ключа — покупка нового ключа его не передаёт."""
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

    amount_rub = amount_kopeks / 100

    if provider is PaymentProvider.PLATEGA:
        if not (config.platega_enabled and config.platega_merchant_id and config.platega_secret):
            raise PaymentFlowError("Оплата картой сейчас недоступна")
        client = PlategaClient(config.platega_merchant_id, config.platega_secret)
        result = await client.create_transaction(
            amount_rub=amount_rub,
            description=description,
            order_id=str(payment.id),
            return_url="https://t.me",
        )
        payment.external_id = str(result["id"])
        pay_url = result.get("redirectUrl") or result.get("url") or ""

    elif provider is PaymentProvider.ROLLYPAY:
        if not (config.rollypay_enabled and config.rollypay_api_key):
            raise PaymentFlowError("Оплата через РоллиПей сейчас недоступна")
        client = RollyPayClient(config.rollypay_api_key)
        result = await client.create_payment(
            amount_rub=amount_rub,
            description=description,
            order_id=str(payment.id),
        )
        payment.external_id = str(result.get("payment_id") or result.get("order_id"))
        pay_url = result.get("pay_url") or ""

    elif provider is PaymentProvider.CRYPTOBOT:
        if not (config.cryptobot_enabled and config.cryptobot_token):
            raise PaymentFlowError("Оплата криптовалютой сейчас недоступна")
        from shared.payments.currency import rub_to_crypto

        client = CryptoBotClient(config.cryptobot_token)
        asset = "USDT"
        amount_asset = await rub_to_crypto(amount_rub, asset)
        invoice = await client.create_invoice(
            amount=amount_asset,
            asset=asset,
            description=description,
            payload=str(payment.id),
        )
        payment.external_id = str(invoice["invoice_id"])
        pay_url = invoice.get("pay_url") or invoice.get("bot_invoice_url") or ""

    else:
        raise PaymentFlowError(f"Провайдер {provider} не поддерживает внешние счета")

    return payment, pay_url
