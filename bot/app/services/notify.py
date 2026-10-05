"""Отправка сообщения без привязки к работающему поллингу.

Подтверждение оплаты может прийти, когда админ на минуту остановил бота в
панели — деньги всё равно нужно зачислить и написать клиенту. Для этого
достаточно токена, самого polling'а поднимать не нужно.
"""

import logging

from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramAPIError, TelegramMigrateToChat

log = logging.getLogger("bot.notify")


async def send(token: str | None, chat_id: int, text: str, **kwargs: object) -> bool:
    if not token:
        return False
    bot = Bot(token=token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    try:
        await bot.send_message(chat_id, text, **kwargs)
        return True
    except TelegramAPIError as exc:
        log.warning("Не удалось отправить сообщение %s: %s", chat_id, exc)
        return False
    finally:
        await bot.session.close()


async def send_sales(token: str, chat_id: int, text: str) -> bool:
    """Уведомление о продаже в чат/группу из панели.

    Отдельно от `send`, потому что у групп свои грабли: обычная группа при
    включении некоторых настроек превращается в супергруппу и получает
    новый id (-100…) — Telegram отвечает «group chat was upgraded» с новым
    id. Тогда переотправляем туда и сохраняем новый id в настройках, чтобы
    уведомления не пропадали молча до следующей правки в панели.
    """
    bot = Bot(token=token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    try:
        try:
            await bot.send_message(chat_id, text, disable_web_page_preview=True)
            return True
        except TelegramMigrateToChat as exc:
            new_chat_id = exc.migrate_to_chat_id
            log.info("Чат продаж %s стал супергруппой %s", chat_id, new_chat_id)
            await bot.send_message(new_chat_id, text, disable_web_page_preview=True)
            await _save_sales_chat(new_chat_id)
            return True
    except TelegramAPIError as exc:
        log.warning(
            "Уведомление о продаже в чат %s не отправлено: %s. Проверьте, что бот "
            "добавлен в группу и может в ней писать (кнопка «Проверить» в панели).",
            chat_id,
            exc,
        )
        return False
    finally:
        await bot.session.close()


async def _save_sales_chat(chat_id: int) -> None:
    from shared.db.models import BotConfig
    from shared.db.session import session

    try:
        async with session() as db:
            row = await db.get(BotConfig, 1)
            if row is not None:
                row.purchase_notify_chat_id = chat_id
    except Exception:  # noqa: BLE001
        log.exception("Не удалось сохранить новый id чата продаж")
