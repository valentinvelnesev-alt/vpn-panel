"""Напоминания об окончании подписки.

Окна 3 дня / 1 день / истекла. Отметка в bot_expiry_notifications не даёт
слать одно и то же дважды, а привязка отметки к дате окончания означает,
что после продления напоминания начнутся заново.
"""

import asyncio
import logging
from datetime import UTC, datetime, timedelta

from aiogram import Bot
from aiogram.exceptions import TelegramForbiddenError, TelegramRetryAfter
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app import texts
from app.config import Config
from shared.db.models import BotUser, ExpiryNotification
from shared.db.session import session

log = logging.getLogger("bot.expiry")

CHECK_INTERVAL = 3600  # раз в час: точнее не нужно, а нагрузка ниже

# окно → (сколько осталось «не больше», подпись)
WINDOWS = {
    "3d": timedelta(days=3),
    "1d": timedelta(days=1),
    "expired": timedelta(0),
}


def _window_for(expire_at: datetime, now: datetime) -> str | None:
    left = expire_at - now
    if left <= timedelta(0):
        return "expired"
    if left <= WINDOWS["1d"]:
        return "1d"
    if left <= WINDOWS["3d"]:
        return "3d"
    return None


async def _notify(bot: Bot, config: Config, user: BotUser, window: str) -> bool:
    expire_at = user.expire_at
    if window == "expired":
        text = texts.render(texts.EXPIRED, config.emoji_mode, config.premium_emoji)
    else:
        left = "1 день" if window == "1d" else "3 дня"
        text = texts.render(
            texts.EXPIRY_WARNING,
            config.emoji_mode,
            config.premium_emoji,
            left=left,
            until=expire_at.strftime("%d.%m.%Y"),
        )

    try:
        await bot.send_message(user.telegram_id, text)
    except TelegramForbiddenError:
        # Бот заблокирован — помечаем и больше не пробуем.
        user.has_stopped_bot = True
        return False
    except TelegramRetryAfter as exc:
        await asyncio.sleep(exc.retry_after)
        return False
    except Exception as exc:  # noqa: BLE001
        log.warning("Не удалось уведомить %s: %s", user.telegram_id, exc)
        return False
    return True


async def run_once(bot: Bot, config: Config) -> int:
    """Один проход. Возвращает число отправленных напоминаний."""
    now = datetime.now(UTC)
    horizon = now + WINDOWS["3d"]
    sent = 0

    async with session() as db:
        candidates = await db.scalars(
            select(BotUser).where(
                BotUser.expire_at.is_not(None),
                BotUser.expire_at <= horizon,
                BotUser.has_stopped_bot.is_(False),
                BotUser.is_blocked.is_(False),
            )
        )

        for user in candidates:
            expire_at = user.expire_at
            if expire_at.tzinfo is None:
                expire_at = expire_at.replace(tzinfo=UTC)

            window = _window_for(expire_at, now)
            if window is None:
                continue

            # Уникальный индекс — надёжнее проверки «а слали ли уже»:
            # два процесса не отправят дубль.
            marker = ExpiryNotification(
                user_id=user.id,
                window=window,
                expire_at=expire_at,
                sent_at=now,
            )
            db.add(marker)
            try:
                await db.flush()
            except IntegrityError:
                await db.rollback()
                continue

            if await _notify(bot, config, user, window):
                sent += 1
            else:
                await db.delete(marker)

            await asyncio.sleep(0.05)  # мягкий темп, чтобы не ловить лимиты

    if sent:
        log.info("Отправлено напоминаний: %s", sent)
    return sent


async def worker(bot: Bot, config: Config) -> None:
    while True:
        try:
            await run_once(bot, config)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            log.exception("Сбой в воркере напоминаний")
        await asyncio.sleep(CHECK_INTERVAL)
