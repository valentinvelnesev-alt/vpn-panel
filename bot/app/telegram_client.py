"""Создание Bot с учётом SOCKS5-прокси из настроек.

Бот живёт в нескольких местах (супервизор, рассылки, уведомления), и все они
должны ходить в Telegram одним путём. Прокси приходит из БД вместе с
конфигом; супервизор запоминает его здесь при каждом (пере)запуске.
"""

from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.enums import ParseMode

from app.premium import PremiumEmojiMiddleware

_proxy: str | None = None


def set_proxy(url: str | None) -> None:
    global _proxy
    _proxy = url or None


def make_bot(token: str, proxy: str | None = None) -> Bot:
    url = proxy or _proxy
    session = AiohttpSession(proxy=url) if url else AiohttpSession()
    # Во всех ботах (продаж, поддержки, рассылки, уведомления) один общий слой,
    # который в премиум-режиме подменяет обычные эмодзи на премиум.
    session.middleware(PremiumEmojiMiddleware())
    return Bot(
        token=token,
        session=session,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
