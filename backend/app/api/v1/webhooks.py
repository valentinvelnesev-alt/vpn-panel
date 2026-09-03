"""Колбэки платёжных провайдеров.

Без аутентификации — провайдер не умеет посылать наш JWT. Вместо доверия
телу колбэка (лёгкая цель для подделки, если угадать формат) мы
перезапрашиваем статус транзакции у провайдера собственными учётными
данными и верим только этому ответу. Подделать такой ответ может только
тот, у кого есть секрет мерчанта из настроек панели.

Дополнительно: лимит запросов с одного IP (иначе любой мог бы заставить
бэкенд долбить провайдера) и, если в настройках задан секрет подписи
RollyPay, проверка HMAC-подписи колбэка.
"""

import hashlib
import hmac
import logging
import time
from collections import defaultdict, deque
from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, Request, status
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

# ── Лимит запросов ────────────────────────────────────────────────────
RATE_LIMIT = 60  # запросов
RATE_WINDOW = 60.0  # секунд
_hits: dict[str, deque[float]] = defaultdict(deque)


def _client_ip(request: Request) -> str:
    # За Caddy реальный адрес приходит в X-Forwarded-For.
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def check_rate_limit(request: Request) -> None:
    now = time.monotonic()
    ip = _client_ip(request)
    window = _hits[ip]
    while window and now - window[0] > RATE_WINDOW:
        window.popleft()
    if len(window) >= RATE_LIMIT:
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Too many requests")
    window.append(now)
    # Не даём словарю расти бесконечно от случайных сканеров.
    if len(_hits) > 10_000:
        for key in [k for k, v in _hits.items() if not v or now - v[-1] > RATE_WINDOW]:
            _hits.pop(key, None)


def _reset_rate_limit() -> None:  # для тестов
    _hits.clear()


# ── Подпись RollyPay ──────────────────────────────────────────────────
MAX_SIGNATURE_AGE = 300  # секунд — защита от повтора старого колбэка


def verify_rollypay_signature(
    secret: str, raw_body: bytes, timestamp: str, signature: str
) -> bool:
    """HMAC-SHA256 от `timestamp + "." + body` на секрете подписи, hex.
    Заголовки колбэка: X-Signature и X-Timestamp."""
    if not secret or not signature or not timestamp:
        return False
    try:
        if abs(time.time() - int(timestamp)) > MAX_SIGNATURE_AGE:
            return False
    except (TypeError, ValueError):
        return False
    mac = hmac.new(secret.encode(), digestmod=hashlib.sha256)
    mac.update(timestamp.encode())
    mac.update(b".")
    mac.update(raw_body)
    return hmac.compare_digest(mac.hexdigest(), signature.strip().lower())


async def _mark_paid(db: DbSession, payment: Payment) -> None:
    if payment.status == PaymentStatus.PAID:
        return  # уже обработан — провайдеры повторяют колбэки
    payment.status = PaymentStatus.PAID
    payment.paid_at = datetime.now(UTC)
    # Коммит ДО публикации: бот получает событие мгновенно и читает платёж
    # своей сессией — без коммита он видел бы ещё «pending» и выходил, а
    # доступ выдавался бы только догоняющим воркером спустя минуту.
    await db.commit()
    await bus.publish(bus.EVENT_PAYMENT_COMPLETED, payment_id=payment.id)


async def _read_json(request: Request) -> dict:
    try:
        body = await request.json()
    except ValueError:
        return {}
    return body if isinstance(body, dict) else {}


@router.post("/platega")
async def platega_webhook(request: Request, db: DbSession) -> dict:
    check_rate_limit(request)
    body = await _read_json(request)

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

    payment.raw_payload = body

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
    check_rate_limit(request)
    raw_body = await request.body()

    signing_secret = await cfg.get(db, cfg.ROLLYPAY_SIGNING_SECRET)
    if signing_secret:
        ok = verify_rollypay_signature(
            signing_secret,
            raw_body,
            request.headers.get("x-timestamp", ""),
            request.headers.get("x-signature", ""),
        )
        if not ok:
            log.warning("RollyPay webhook: неверная подпись, отклонён")
            raise HTTPException(status.HTTP_403_FORBIDDEN, "invalid signature")

    body = await _read_json(request)
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

    payment.raw_payload = body

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
    check_rate_limit(request)
    body = await _read_json(request)
    payload = body.get("payload", {}) if isinstance(body.get("payload"), dict) else {}
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

    payment.raw_payload = body

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
