"""Прямые вызовы Telegram Bot API из панели.

Панель обращается к Telegram только для проверок: валиден ли токен и можно
ли включить премиум-эмодзи. Всё остальное делает контейнер бота.
"""

import logging
from dataclasses import dataclass

import httpx

log = logging.getLogger("telegram")

API = "https://api.telegram.org"
TIMEOUT = 10.0


class TelegramError(Exception):
    """Ошибка Telegram, пригодная для показа пользователю панели."""


@dataclass(frozen=True, slots=True)
class BotIdentity:
    id: int
    username: str
    name: str


async def _call(token: str, method: str, **payload) -> dict:
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        try:
            response = await client.post(f"{API}/bot{token}/{method}", json=payload)
        except httpx.HTTPError as exc:
            raise TelegramError(f"Не удалось связаться с Telegram: {exc}") from exc

    body = response.json()
    if not body.get("ok"):
        raise TelegramError(body.get("description") or "Telegram отклонил запрос")
    return body["result"]


async def get_me(token: str) -> BotIdentity:
    """Проверяет токен и возвращает, какому боту он принадлежит."""
    if not token or ":" not in token:
        raise TelegramError("Токен не похож на настоящий — скопируйте его из @BotFather")
    result = await _call(token, "getMe")
    return BotIdentity(
        id=result["id"],
        username=result.get("username", ""),
        name=result.get("first_name", ""),
    )


async def check_premium_emoji(token: str, chat_id: int, emoji_id: str) -> None:
    """Пробует отправить сообщение с премиум-эмодзи и сразу удаляет его.

    Telegram разрешает боту кастомные эмодзи только если у аккаунта, на
    котором бот создан, активен Telegram Premium. Единственный честный
    способ узнать это — попробовать: отдельного метода в API нет.

    Молча возвращается при успехе, иначе бросает TelegramError с
    объяснением от Telegram.
    """
    text = f'<tg-emoji emoji-id="{emoji_id}">✅</tg-emoji> Проверка премиум-эмодзи'
    result = await _call(
        token,
        "sendMessage",
        chat_id=chat_id,
        text=text,
        parse_mode="HTML",
        disable_notification=True,
    )
    message_id = result.get("message_id")
    if message_id:
        try:
            await _call(token, "deleteMessage", chat_id=chat_id, message_id=message_id)
        except TelegramError:
            # Проверка удалась — то, что тестовое сообщение осталось
            # висеть, не повод считать её неуспешной.
            log.info("Тестовое сообщение не удалось удалить")
