"""Отправка рассылок, созданных в панели.

Панель только заводит запись Broadcast и сегмент — сама отправка идёт
здесь, у живого Bot-инстанса. Прогресс (sent_count/failed_count) коммитится
после каждого сообщения отдельной короткой транзакцией, чтобы панель,
опрашивающая БД через WebSocket, видела его в реальном времени, а не только
после завершения всей рассылки.

Возобновление: получатели идут по возрастанию id, после каждого сообщения
сохраняются `cursor_user_id` и `heartbeat_at`. Если контейнер упал, рассылка
остаётся в `sending` без свежего heartbeat — воркер подхватывает её и
продолжает с курсора, не отправляя повторно тем, кто уже получил.
"""

import asyncio
import logging
from datetime import UTC, datetime, timedelta

from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramForbiddenError, TelegramRetryAfter
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy import func, select

from shared.db.models import Broadcast, BroadcastSegment, BroadcastStatus, BotUser
from shared.db.session import SessionLocal, session
from shared.segments import recipients_query

log = logging.getLogger("bot.workers.broadcast")

CHECK_INTERVAL = 10
SEND_DELAY = 0.05  # ~20 сообщений в секунду — с запасом от лимитов Telegram
# Рассылка в `sending` без heartbeat дольше этого считается брошенной.
STALE_AFTER = timedelta(minutes=2)


def _keyboard(buttons: list[dict]):
    if not buttons:
        return None
    builder = InlineKeyboardBuilder()
    added = 0
    for button in buttons:
        text = str(button.get("text") or "").strip()
        url = str(button.get("url") or "").strip()
        if text and url:
            builder.button(text=text, url=url)
            added += 1
    if not added:
        return None
    builder.adjust(1)
    return builder.as_markup()


async def _claim_due_broadcast() -> int | None:
    """Берёт в работу одну наступившую рассылку (или брошенную — для
    возобновления), помечая её как sending."""
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
            row = await db.scalar(
                select(Broadcast)
                .where(
                    Broadcast.status == BroadcastStatus.SENDING,
                    Broadcast.heartbeat_at.is_not(None),
                    Broadcast.heartbeat_at < now - STALE_AFTER,
                )
                .order_by(Broadcast.started_at)
                .limit(1)
            )
            if row is not None:
                log.warning("Возобновляю брошенную рассылку %s с курсора %s", row.id, row.cursor_user_id)
        if row is None:
            return None
        row.status = BroadcastStatus.SENDING
        if row.started_at is None:
            row.started_at = now
        row.heartbeat_at = now
        return row.id


async def _send(broadcast_id: int, token: str) -> None:
    async with SessionLocal() as db:
        broadcast = await db.get(Broadcast, broadcast_id)
        if broadcast is None:
            return
        base = recipients_query(BroadcastSegment(broadcast.segment))
        if not broadcast.total_recipients:
            broadcast.total_recipients = (
                await db.scalar(select(func.count()).select_from(base.subquery()))
            ) or 0
        cursor = broadcast.cursor_user_id
        query = base.order_by(BotUser.id)
        if cursor is not None:
            query = query.where(BotUser.id > cursor)
        recipients = list(await db.scalars(query))
        await db.commit()

        keyboard = _keyboard(broadcast.buttons)
        text = broadcast.text
        photo_url = broadcast.photo_url

    bot = Bot(token=token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    try:
        for user in recipients:
            ok = False
            for _attempt in range(3):
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
                    break
                except TelegramForbiddenError:
                    async with SessionLocal() as db:
                        blocked_user = await db.get(BotUser, user.id)
                        if blocked_user is not None:
                            blocked_user.has_stopped_bot = True
                            await db.commit()
                    break
                except TelegramRetryAfter as exc:
                    # Лимит Telegram — это не отказ адресата: ждём и
                    # повторяем, а не записываем в «не доставлено».
                    await asyncio.sleep(exc.retry_after + 0.5)
                    continue
                except Exception as exc:  # noqa: BLE001
                    log.warning("Не удалось отправить %s: %s", user.telegram_id, exc)
                    break

            async with SessionLocal() as db:
                broadcast = await db.get(Broadcast, broadcast_id)
                if broadcast is None or broadcast.status == BroadcastStatus.CANCELLED:
                    return
                if ok:
                    broadcast.sent_count += 1
                else:
                    broadcast.failed_count += 1
                broadcast.cursor_user_id = user.id
                broadcast.heartbeat_at = datetime.now(UTC)
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
