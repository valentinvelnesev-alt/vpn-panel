"""Активная проверка статуса внешнего платежа у провайдера.

Вебхук — основной путь подтверждения оплаты, но иногда не доходит: сервер
панели недоступен снаружи (IP-режим без домена, файрвол, провайдер не смог
достучаться) — тогда Payment годами висит в PENDING, хотя деньги пришли.
Здесь — тот же самый запрос статуса, которым бэкенд проверяет вебхук: не
доверяем ничему, кроме прямого ответа API по нашим кредам (см. комментарии
в shared/payments/*.py).

Используется и кнопкой «Проверить оплату», и фоновым воркером polling.
"""

import logging
from datetime import UTC, datetime

from app import config as config_module
from app.services import payment_processor
from shared.db.models import Payment, PaymentProvider, PaymentStatus
from shared.db.session import session
from shared.payments.cryptobot import CryptoBotClient
from shared.payments.platega import PlategaClient
from shared.payments.rollypay import RollyPayClient

log = logging.getLogger("bot.payment_check")


async def check_and_apply(payment_id: int) -> bool:
    """Возвращает True, если платёж подтверждён (и применён, если ещё не был)."""
    already_applied = False
    async with session() as db:
        payment = await db.get(Payment, payment_id)
        if payment is None:
            return False

        if payment.status == PaymentStatus.PAID:
            already_applied = payment.applied_at is not None
        else:
            if payment.status != PaymentStatus.PENDING:
                return False
            config = await config_module.load(db)
            paid = await _is_paid_at_provider(config, payment)
            if not paid:
                return False
            payment.status = PaymentStatus.PAID
            payment.paid_at = datetime.now(UTC)
            await db.commit()

    if not already_applied:
        await payment_processor.handle(payment_id)
    return True


async def _is_paid_at_provider(config, payment: Payment) -> bool:
    try:
        if payment.provider == PaymentProvider.PLATEGA:
            if not (config.platega_enabled and config.platega_merchant_id and config.platega_secret):
                return False
            client = PlategaClient(config.platega_merchant_id, config.platega_secret)
            data = await client.get_transaction(payment.external_id)
            return PlategaClient.is_paid(data)

        if payment.provider == PaymentProvider.ROLLYPAY:
            if not (config.rollypay_enabled and config.rollypay_api_key):
                return False
            client = RollyPayClient(config.rollypay_api_key)
            data = await client.get_payment(payment.external_id)
            return RollyPayClient.is_paid(data)

        if payment.provider == PaymentProvider.CRYPTOBOT:
            if not (config.cryptobot_enabled and config.cryptobot_token):
                return False
            client = CryptoBotClient(config.cryptobot_token)
            data = await client.get_invoice(payment.external_id)
            return bool(data) and CryptoBotClient.is_paid(data)
    except Exception:  # noqa: BLE001
        log.exception("Не удалось проверить статус платежа %s у провайдера", payment.id)
        return False
    return False
