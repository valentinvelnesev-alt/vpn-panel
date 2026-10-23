"""Премиум-режим: обычных эмодзи в исходящих сообщениях не остаётся."""

import re
from datetime import UTC, datetime

import pytest
from aiogram import Bot
from aiogram.client.session.base import BaseSession
from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import SendMessage
from aiogram.types import (
    Chat,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from app import premium
from app.icons import ICONS, PREMIUM_BY_CHAR
from app.premium import PremiumEmojiMiddleware, convert_button, convert_html


@pytest.fixture(autouse=True)
def _reset():
    premium.set_enabled(False)
    yield
    premium.set_enabled(False)


def emoji_tag(char: str) -> str:
    emoji_id, shown = PREMIUM_BY_CHAR[char]
    return f'<tg-emoji emoji-id="{emoji_id}">{shown}</tg-emoji>'


# ── Текст ─────────────────────────────────────────────────────────────
def test_known_plain_emoji_become_premium() -> None:
    assert convert_html("📊 Статистика") == f"{emoji_tag('📊')} Статистика"
    # С селектором вариации и без — одно и то же.
    assert convert_html("⚠️ Нода") == f"{emoji_tag('⚠')} Нода"
    assert convert_html("⚠ Нода") == f"{emoji_tag('⚠')} Нода"


def test_existing_markup_and_code_are_left_alone() -> None:
    ready = f"{emoji_tag('✅')} уже премиум"
    assert convert_html(ready) == ready
    assert convert_html("<code>📊</code>") == "<code>📊</code>"
    assert convert_html("<pre>✅ x</pre> ✅") == "<pre>✅ x</pre> " + emoji_tag("✅")
    # Атрибуты тегов не трогаем.
    link = '<a href="https://x.ru/✅">ссылка</a>'
    assert convert_html(link) == link


def test_unknown_emoji_and_plain_text_unchanged() -> None:
    assert convert_html("Цена 100 ₽ — привет") == "Цена 100 ₽ — привет"
    assert convert_html("🦄 единорог") == "🦄 единорог"
    assert convert_html("") == ""


def test_no_plain_emoji_left_for_any_known_character() -> None:
    sample = " ".join(PREMIUM_BY_CHAR)
    result = convert_html(sample)
    leftover = re.sub(r"<tg-emoji[^>]*>.*?</tg-emoji>", "", result)
    assert leftover.strip() == ""


# ── Кнопки ────────────────────────────────────────────────────────────
def test_leading_emoji_of_button_moves_to_icon() -> None:
    button = convert_button(InlineKeyboardButton(text="🔥 Скидки", callback_data="x"))
    assert button.text == "Скидки"
    assert button.icon_custom_emoji_id == PREMIUM_BY_CHAR["🔥"][0]


def test_button_with_own_icon_or_only_emoji_is_unchanged() -> None:
    own = InlineKeyboardButton(text="🔥 Скидки", callback_data="x", icon_custom_emoji_id="1")
    assert convert_button(own) == own
    only = InlineKeyboardButton(text="🔥", callback_data="x")
    assert convert_button(only) == only  # без текста Telegram кнопку не примет


# ── Запросы к Telegram ────────────────────────────────────────────────
class Fake(BaseSession):
    def __init__(self, *, reject_premium: bool = False) -> None:
        super().__init__()
        self.sent: list[SendMessage] = []
        self.reject_premium = reject_premium
        self.middleware(PremiumEmojiMiddleware())

    async def close(self) -> None:
        pass

    async def stream_content(self, *args, **kwargs):  # pragma: no cover
        yield b""

    async def make_request(self, bot, method, timeout=None):
        if self.reject_premium and "tg-emoji" in (method.text or ""):
            raise TelegramBadRequest(method=method, message="Bad Request: can't parse entities")
        self.sent.append(method)
        return Message(message_id=1, date=datetime.now(UTC), chat=Chat(id=1, type="private"))


async def send(session: Fake, text: str, markup=None) -> None:
    bot = Bot("123456:TESTTOKENTESTTOKENTESTTOKENTESTTOK", session=session)
    await bot.send_message(1, text, reply_markup=markup)


async def test_middleware_converts_text_and_buttons_in_premium_mode() -> None:
    premium.set_enabled(True)
    fake = Fake()
    markup = InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="📢 Рассылка", callback_data="b")]]
    )
    await send(fake, "🛠 Админ-панель", markup)

    sent = fake.sent[0]
    assert sent.text == f"{emoji_tag('🛠')} Админ-панель"
    button = sent.reply_markup.inline_keyboard[0][0]
    assert button.text == "Рассылка" and button.icon_custom_emoji_id == PREMIUM_BY_CHAR["📢"][0]


async def test_middleware_does_nothing_in_plain_mode() -> None:
    fake = Fake()
    await send(fake, "🛠 Админ-панель")
    assert fake.sent[0].text == "🛠 Админ-панель"


async def test_falls_back_to_plain_text_if_telegram_refuses_premium() -> None:
    premium.set_enabled(True)
    fake = Fake(reject_premium=True)
    await send(fake, "📊 Статистика")
    # Сообщение дошло — обычным текстом, а не пропало.
    assert fake.sent[0].text == "📊 Статистика"


async def test_non_html_messages_are_not_touched() -> None:
    premium.set_enabled(True)
    fake = Fake()
    bot = Bot("123456:TESTTOKENTESTTOKENTESTTOKENTESTTOK", session=fake)
    await bot.send_message(1, "📊 <b>x", parse_mode=None)
    assert fake.sent[0].text == "📊 <b>x"


# ── Набор ─────────────────────────────────────────────────────────────
def test_every_icon_has_an_id_and_the_packs_own_character() -> None:
    for name, icon in ICONS.items():
        assert icon.emoji_id and icon.emoji_id.isdigit(), name
        assert icon.premium_char, name


def test_no_old_placeholder_squares_left() -> None:
    # В прошлом наборе половина иконок стояла с общей заглушкой «▫️» — это
    # была подмена по смыслу, а не подходящая иконка.
    assert not [k for k, i in ICONS.items() if i.premium_char == "▫️"]


def test_icons_are_thematically_distinct_where_it_matters() -> None:
    ids = {name: ICONS[name].emoji_id for name in ("check", "cross", "back", "gift", "balance", "support", "channel", "referral", "profile", "history")}
    assert len(set(ids.values())) == len(ids)
