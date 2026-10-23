"""Премиум-эмодзи везде: в премиум-режиме обычных эмодзи в сообщениях нет.

Иконки из таблицы `ICONS` подставляются при сборке текста (`{@имя}`) и на
кнопках. Но обычные эмодзи ещё вписаны прямо в строки: админ-панель, бот
поддержки, алерты нод, уведомления о продажах, рассылки, подписи кнопок,
которые правит владелец. Править каждое место вручную — значит забывать новые.
Поэтому здесь один общий слой на уровне запросов к Telegram: если включён
премиум-режим, любой известный обычный эмодзи в исходящем тексте заменяется
на премиум из того же набора (таблица `PREMIUM_BY_CHAR`), а эмодзи в начале
текста кнопки уходит в иконку кнопки — в тексте кнопки премиум-эмодзи нельзя.

Если Telegram не принимает такую разметку (например, у владельца бота нет
Premium), запрос повторяется без замены: сообщение важнее оформления.
"""

import logging
import re
from typing import Any

from aiogram.client.default import Default
from aiogram.client.session.middlewares.base import (
    BaseRequestMiddleware,
    NextRequestMiddlewareType,
)
from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import TelegramMethod
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app.icons import PREMIUM_BY_CHAR

log = logging.getLogger("bot.premium")

_enabled = False


def set_enabled(value: bool) -> None:
    global _enabled
    _enabled = value


def enabled() -> bool:
    return _enabled


# Ключи таблицы — без селектора вариации (U+FE0F); в тексте он может быть.
_VS16 = "️"
_EMOJI = re.compile(
    "(" + "|".join(re.escape(c) for c in sorted(PREMIUM_BY_CHAR, key=len, reverse=True)) + ")"
    + _VS16 + "?"
)
_TAG = re.compile(r"(<[^>]*>)")
_TAG_NAME = re.compile(r"</?\s*([A-Za-z0-9-]+)")
# Внутри этих тегов Telegram не разрешает вложенные сущности.
_NO_NESTING = frozenset({"tg-emoji", "code", "pre"})


def _tag(char: str) -> str:
    emoji_id, shown = PREMIUM_BY_CHAR[char]
    return f'<tg-emoji emoji-id="{emoji_id}">{shown}</tg-emoji>'


def convert_html(text: str) -> str:
    """Обычные эмодзи в HTML-тексте → премиум. Теги, их атрибуты, уже
    готовые <tg-emoji> и код не трогаем."""
    if not text:
        return text
    out: list[str] = []
    depth = 0
    for part in _TAG.split(text):
        if part.startswith("<") and part.endswith(">"):
            name = _TAG_NAME.match(part)
            if name and name.group(1).lower() in _NO_NESTING:
                depth = max(depth + (-1 if part.startswith("</") else 1), 0)
            out.append(part)
        elif depth:
            out.append(part)
        else:
            out.append(_EMOJI.sub(lambda m: _tag(m.group(1)), part))
    return "".join(out)


def convert_button(button: InlineKeyboardButton) -> InlineKeyboardButton:
    """Эмодзи в начале текста кнопки → её иконка."""
    if button.icon_custom_emoji_id or not button.text:
        return button
    text = button.text.lstrip()
    match = _EMOJI.match(text)
    if match is None:
        return button
    rest = text[match.end():].strip()
    if not rest:
        return button  # кнопка из одного эмодзи: без текста Telegram её не примет
    emoji_id, _ = PREMIUM_BY_CHAR[match.group(1)]
    return button.model_copy(update={"text": rest, "icon_custom_emoji_id": emoji_id})


def convert_markup(markup: Any) -> Any:
    if not isinstance(markup, InlineKeyboardMarkup):
        return markup
    return markup.model_copy(
        update={"inline_keyboard": [[convert_button(b) for b in row] for row in markup.inline_keyboard]}
    )


def _is_html(method: TelegramMethod) -> bool:
    mode = getattr(method, "parse_mode", None)
    # Default(...) — значение по умолчанию бота, а у наших ботов это HTML.
    # None, переданный явно, — «без разметки»: там теги — просто текст.
    return isinstance(mode, Default) or str(mode).upper() == "HTML"


def convert_method(method: TelegramMethod) -> TelegramMethod:
    updates: dict[str, Any] = {}
    if _is_html(method):
        for field in ("text", "caption"):
            value = getattr(method, field, None)
            if isinstance(value, str):
                converted = convert_html(value)
                if converted != value:
                    updates[field] = converted
    markup = getattr(method, "reply_markup", None)
    if isinstance(markup, InlineKeyboardMarkup):
        converted_markup = convert_markup(markup)
        if converted_markup != markup:
            updates["reply_markup"] = converted_markup
    return method.model_copy(update=updates) if updates else method


class PremiumEmojiMiddleware(BaseRequestMiddleware):
    async def __call__(self, make_request: NextRequestMiddlewareType, bot, method):
        if not _enabled:
            return await make_request(bot, method)
        converted = convert_method(method)
        if converted is method:
            return await make_request(bot, method)
        try:
            return await make_request(bot, converted)
        except TelegramBadRequest as exc:
            detail = str(exc).lower()
            if any(word in detail for word in ("entit", "emoji", "icon", "can't parse")):
                log.warning("Telegram не принял премиум-эмодзи (%s) — отправляю обычные", exc)
                return await make_request(bot, method)
            raise
