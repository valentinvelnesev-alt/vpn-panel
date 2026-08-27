"""Колбэки платёжных провайдеров.

Без аутентификации — провайдер не умеет посылать наш JWT. Вместо доверия
телу колбэка (лёгкая цель для подделки, если угадать формат) мы
перезапрашиваем статус транзакции у провайдера собственными учётными
данными и верим только этому ответу. Подделать такой ответ может только
тот, у кого есть секрет мерчанта из настроек панели.
"""

import logging
from datetime import UTC, datetime

from fastapi import APIRouter, Request
from sqlalchemy import select

from app.api.deps import DbSession
from app.services import settings_service as cfg
from shared import bus
from shared.db.models import Payment, PaymentProvider, PaymentStatus
from shared.payments.cryptobot import CryptoBotClient, CryptoBotError
from shared.payments.platega import PlategaClient, PlategaError
from shared.payments.rollypay import RollyPayClient, RollyPayError

log = logging.getLogger("webhooks")

router = APIRouter(prefix="/webhooks", tags=["webhooks"])


async def _mark_paid(db: DbSession, payment: Payment) -> None:
    if payment.status == PaymentStatus.PAID:
        return  # уже обработан — провайдеры повторяют колбэки
    payment.status = PaymentStatus.PAID
    payment.paid_at = datetime.now(UTC)
    await db.flush()
    await bus.publish(bus.EVENT_PAYMENT_COMPLETED, payment_id=payment.id)


@router.post("/platega")
async def platega_webhook(request: Request, db: DbSession) -> dict:
    body = await request.json()

    external_id = str(
        body.get("id") or body.get("transactionId") or body.get("orderId") or ""
    )
    if not external_id:
        return {"ok": False, "reason": "no id in payload"}

    payment = await db.scalar(
        select(Payment).where(
            Payment.provider == PaymentProvider.PLATEGA,
            Payment.external_id == external_id,
        )
    )
    if payment is None:
        log.warning("Platega webhook: платёж %s не найден", external_id)
        return {"ok": False, "reason": "payment not found"}

    payment.raw_payload = body if isinstance(body, dict) else None

    values = await cfg.get_many(db, cfg.PLATEGA_MERCHANT_ID, cfg.PLATEGA_SECRET)
    if not values[cfg.PLATEGA_MERCHANT_ID] or not values[cfg.PLATEGA_SECRET]:
        log.error("Platega webhook пришёл, но провайдер не настроен")
        return {"ok": False, "reason": "provider not configured"}

    client = PlategaClient(values[cfg.PLATEGA_MERCHANT_ID], values[cfg.PLATEGA_SECRET])
    try:
        transaction = await client.get_transaction(external_id)
    except PlategaError as exc:
        log.error("Не удалось подтвердить транзакцию Platega %s: %s", external_id, exc)
        return {"ok": False, "reason": "verification failed"}

    if not PlategaClient.is_paid(transaction):
        return {"ok": True, "applied": False}

    await _mark_paid(db, payment)
    return {"ok": True, "applied": True}


@router.post("/rollypay")
async def rollypay_webhook(request: Request, db: DbSession) -> dict:
    body = await request.json()

    external_id = str(body.get("payment_id") or body.get("order_id") or "")
    if not external_id:
        return {"ok": False, "reason": "no payment_id in payload"}

    payment = await db.scalar(
        select(Payment).where(
            Payment.provider == PaymentProvider.ROLLYPAY,
            Payment.external_id == external_id,
        )
    )
    if payment is None:
        log.warning("RollyPay webhook: платёж %s не найден", external_id)
        return {"ok": False, "reason": "payment not found"}

    payment.raw_payload = body if isinstance(body, dict) else None

    api_key = await cfg.get(db, cfg.ROLLYPAY_API_KEY)
    if not api_key:
        log.error("RollyPay webhook пришёл, но провайдер не настроен")
        return {"ok": False, "reason": "provider not configured"}

    client = RollyPayClient(api_key)
    try:
        remote = await client.get_payment(external_id)
    except RollyPayError as exc:
        log.error("Не удалось подтвердить платёж RollyPay %s: %s", external_id, exc)
        return {"ok": False, "reason": "verification failed"}

    if not RollyPayClient.is_paid(remote):
        return {"ok": True, "applied": False}

    await _mark_paid(db, payment)
    return {"ok": True, "applied": True}


@router.post("/cryptobot")
async def cryptobot_webhook(request: Request, db: DbSession) -> dict:
    body = await request.json()
    payload = body.get("payload", {}) if isinstance(body, dict) else {}
    invoice_id = str(payload.get("invoice_id") or "")
    if not invoice_id:
        return {"ok": False, "reason": "no invoice_id in payload"}

    payment = await db.scalar(
        select(Payment).where(
            Payment.provider == PaymentProvider.CRYPTOBOT,
            Payment.external_id == invoice_id,
        )
    )
    if payment is None:
        log.warning("CryptoBot webhook: платёж %s не найден", invoice_id)
        return {"ok": False, "reason": "payment not found"}

    payment.raw_payload = body if isinstance(body, dict) else None

    token = await cfg.get(db, cfg.CRYPTOBOT_TOKEN)
    if not token:
        log.error("CryptoBot webhook пришёл, но провайдер не настроен")
        return {"ok": False, "reason": "provider not configured"}

    client = CryptoBotClient(token)
    try:
        invoice = await client.get_invoice(invoice_id)
    except CryptoBotError as exc:
        log.error("Не удалось подтвердить инвойс CryptoBot %s: %s", invoice_id, exc)
        return {"ok": False, "reason": "verification failed"}

    if invoice is None or not CryptoBotClient.is_paid(invoice):
        return {"ok": True, "applied": False}

    await _mark_paid(db, payment)
    return {"ok": True, "applied": True}
