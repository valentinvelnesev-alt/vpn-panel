"""Подстраховка для оплат: догоняет то, что потерялось в pub/sub.

Redis pub/sub — «выстрелил и забыл»: если в момент публикации бот не был
подписан (перезапускался, панель его останавливала), сообщение о платеже
пропадает без следа. Здесь — редкий опрос БД: раз в минуту находим оплаты
со статусом paid, которые ещё не применены, и дожимаем их. Обычным путём
через шину платежи применяются мгновенно; этот воркер — на случай сбоя.
"""

import asyncio
import logging

from sqlalchemy import select

from app.services import payment_processor
from shared.db.models import Payment, PaymentStatus
from shared.db.session import session

log = logging.getLogger("bot.workers.payments")

CHECK_INTERVAL = 60


async def run_once() -> int:
    async with session() as db:
        ids = list(
            await db.scalars(
                select(Payment.id).where(
                    Payment.status == PaymentStatus.PAID,
                    Payment.applied_at.is_(None),
                )
            )
        )
    for payment_id in ids:
        await payment_processor.handle(payment_id)
    return len(ids)


async def worker() -> None:
    while True:
        try:
            applied = await run_once()
            if applied:
                log.info("Дообработано отложенных оплат: %s", applied)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            log.exception("Сбой в воркере догонки оплат")
        await asyncio.sleep(CHECK_INTERVAL)
