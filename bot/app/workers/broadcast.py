"""Отправка рассылок, созданных в панели.

Панель только заводит запись Broadcast и сегмент — сама отправка идёт
здесь, у живого Bot-инстанса. Прогресс (sent_count/failed_count) коммитится
после каждого сообщения отдельной короткой транзакцией, чтобы панель,
опрашивающая БД через WebSocket, видела его в реальном времени, а не только
после завершения всей рассылки.
"""

import asyncio
import logging
from datetime import UTC, datetime

from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramForbiddenError, TelegramRetryAfter
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy import select

from shared.db.models import Broadcast, BroadcastSegment, BroadcastStatus, BotUser
from shared.db.session import SessionLocal, session
from shared.segments import recipients_query

log = logging.getLogger("bot.workers.broadcast")

CHECK_INTERVAL = 10
SEND_DELAY = 0.05  # ~20 сообщений в секунду — с запасом от лимитов Telegram


def _keyboard(buttons: list[dict]):
    if not buttons:
        return None
    builder = InlineKeyboardBuilder()
    for button in buttons:
        text = str(button.get("text") or "").strip()
        url = str(button.get("url") or "").strip()
        if text and url:
            builder.button(text=text, url=url)
    builder.adjust(1)
    return builder.as_markup() if buttons else None


async def _claim_due_broadcast() -> int | None:
    """Берёт в работу одну наступившую рассылку, помечая её как sending."""
    now = datetime.now(UTC)
    async with session() as db:
        row = await db.scalar(
            select(Broadcast)
            .where(
                Broadcast.status == BroadcastStatus.SCHEDULED,
                Broadcast.scheduled_at <= now,
            )
            .order_by(Broadcast.scheduled_at)
            .limit(1)
        )
        if row is None:
            return None
        row.status = BroadcastStatus.SENDING
        row.started_at = now
        return row.id


async def _send(broadcast_id: int, token: str) -> None:
    async with SessionLocal() as db:
        broadcast = await db.get(Broadcast, broadcast_id)
        if broadcast is None:
            return
        recipients = list(
            await db.scalars(recipients_query(BroadcastSegment(broadcast.segment)))
        )
        broadcast.total_recipients = len(recipients)
        await db.commit()

        keyboard = _keyboard(broadcast.buttons)
        text = broadcast.text
        photo_url = broadcast.photo_url

    bot = Bot(token=token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    try:
        for user in recipients:
            try:
                if photo_url:
                    await bot.send_photo(
                        user.telegram_id,
                        photo_url,
                        caption=text,
                        reply_markup=keyboard,
                    )
                else:
                    await bot.send_message(user.telegram_id, text, reply_markup=keyboard)
                ok = True
            except TelegramForbiddenError:
                ok = False
                async with SessionLocal() as db:
                    blocked_user = await db.get(BotUser, user.id)
                    if blocked_user is not None:
                        blocked_user.has_stopped_bot = True
                        await db.commit()
            except TelegramRetryAfter as exc:
                await asyncio.sleep(exc.retry_after)
                ok = False
            except Exception as exc:  # noqa: BLE001
                log.warning("Не удалось отправить %s: %s", user.telegram_id, exc)
                ok = False

            async with SessionLocal() as db:
                broadcast = await db.get(Broadcast, broadcast_id)
                if broadcast is None:
                    return
                if ok:
                    broadcast.sent_count += 1
                else:
                    broadcast.failed_count += 1
                await db.commit()

            await asyncio.sleep(SEND_DELAY)
    finally:
        await bot.session.close()

    async with SessionLocal() as db:
        broadcast = await db.get(Broadcast, broadcast_id)
        if broadcast is not None:
            broadcast.status = BroadcastStatus.COMPLETED
            broadcast.finished_at = datetime.now(UTC)
            await db.commit()

    log.info("Рассылка %s завершена", broadcast_id)


async def run_once(token: str | None) -> bool:
    """Один проход: если есть наступившая рассылка — отправляет её. Возвращает,
    была ли обработана хоть одна рассылка (для мгновенного вызова из шины)."""
    if not token:
        return False
    broadcast_id = await _claim_due_broadcast()
    if broadcast_id is None:
        return False
    await _send(broadcast_id, token)
    return True


async def worker(token: str) -> None:
    while True:
        try:
            # Догоняем все наступившие рассылки за проход, не только одну.
            while await run_once(token):
                pass
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            log.exception("Сбой в воркере рассылок")
        await asyncio.sleep(CHECK_INTERVAL)
