"""Отправка сообщения без привязки к работающему поллингу.

Подтверждение оплаты может прийти, когда админ на минуту остановил бота в
панели — деньги всё равно нужно зачислить и написать клиенту. Для этого
достаточно токена, самого polling'а поднимать не нужно.
"""

import logging

from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramAPIError

log = logging.getLogger("bot.notify")


async def send(token: str, chat_id: int, text: str, **kwargs: object) -> bool:
    bot = Bot(token=token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    try:
        await bot.send_message(chat_id, text, **kwargs)
        return True
    except TelegramAPIError as exc:
        log.warning("Не удалось отправить сообщение %s: %s", chat_id, exc)
        return False
    finally:
        await bot.session.close()
