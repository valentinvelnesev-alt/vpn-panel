"""Безопасное редактирование сообщений Telegram.

`edit_text` падает, если текст и клавиатура не изменились («message is not
modified»), если сообщение слишком старое или у него нет текста (фото,
инвойс). Без обёртки каждый такой случай — трейсбек в логе и вечный
спиннер на кнопке у пользователя.
"""

import logging

from aiogram.exceptions import TelegramBadRequest
from aiogram.types import InlineKeyboardMarkup, Message

log = logging.getLogger("bot.ui")


async def safe_edit(
    message: Message,
    text: str,
    reply_markup: InlineKeyboardMarkup | None = None,
    **kwargs: object,
) -> None:
    try:
        if message.text is None and message.caption is not None:
            await message.edit_caption(caption=text, reply_markup=reply_markup, **kwargs)
        else:
            await message.edit_text(text, reply_markup=reply_markup, **kwargs)
    except TelegramBadRequest as exc:
        detail = str(exc).lower()
        if "message is not modified" in detail:
            return
        if (
            "message can't be edited" in detail
            or "there is no text in the message" in detail
            or "message to edit not found" in detail
        ):
            await message.answer(text, reply_markup=reply_markup, **kwargs)
            return
        raise
