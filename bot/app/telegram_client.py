"""Создание Bot с учётом SOCKS5-прокси из настроек.

Бот живёт в нескольких местах (супервизор, рассылки, уведомления), и все они
должны ходить в Telegram одним путём. Прокси приходит из БД вместе с
конфигом; супервизор запоминает его здесь при каждом (пере)запуске.
"""

from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.enums import ParseMode

_proxy: str | None = None


def set_proxy(url: str | None) -> None:
    global _proxy
    _proxy = url or None


def make_bot(token: str, proxy: str | None = None) -> Bot:
    url = proxy or _proxy
    return Bot(
        token=token,
        session=AiohttpSession(proxy=url) if url else None,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
