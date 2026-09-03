"""Активный опрос провайдеров по зависшим внешним платежам.

Вебхук на бэкенде — основной путь подтверждения, но если сервер панели
недоступен снаружи (IP-режим, файрвол, сбой провайдера при колбэке),
Payment остаётся в PENDING навсегда — пользователь заплатил, а подписка
не выдана. Здесь каждые CHECK_INTERVAL секунд запрашиваем статус у самого
провайдера для всех недавних незавершённых платежей — так же, как это
делает кнопка «Проверить оплату» (см. app/services/payment_check.py).
"""

import asyncio
import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app.services import payment_check
from shared.db.models import Payment, PaymentProvider, PaymentStatus
from shared.db.session import session

log = logging.getLogger("bot.workers.payment_polling")

CHECK_INTERVAL = 30
# Не спрашиваем провайдера бесконечно долго — счета живут не более пары часов.
MAX_AGE = timedelta(hours=6)

_EXTERNAL_PROVIDERS = (
    PaymentProvider.PLATEGA,
    PaymentProvider.ROLLYPAY,
    PaymentProvider.CRYPTOBOT,
)


async def run_once() -> int:
    cutoff = datetime.now(UTC) - MAX_AGE
    async with session() as db:
        ids = list(
            await db.scalars(
                select(Payment.id).where(
                    Payment.status == PaymentStatus.PENDING,
                    Payment.provider.in_(_EXTERNAL_PROVIDERS),
                    Payment.created_at >= cutoff,
                )
            )
        )
    applied = 0
    for payment_id in ids:
        try:
            if await payment_check.check_and_apply(payment_id):
                applied += 1
        except Exception:  # noqa: BLE001
            log.exception("Сбой опроса платежа %s у провайдера", payment_id)
    return applied


async def worker() -> None:
    while True:
        try:
            applied = await run_once()
            if applied:
                log.info("Подтверждено опросом провайдеров: %s", applied)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            log.exception("Сбой в воркере опроса платежей")
        await asyncio.sleep(CHECK_INTERVAL)
