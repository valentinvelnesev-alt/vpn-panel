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


async def send_test_to_chat(token: str, chat: str) -> int:
    """Отправляет проверочное сообщение в чат продаж, возвращает его id.

    `chat` — числовой id или @имя публичной группы/канала; имя переводим в
    id через getChat, потому что в настройке хранится число.
    """
    target: int | str = chat
    if chat.lstrip("-").isdigit():
        target = int(chat)
    elif not chat.startswith("@"):
        target = f"@{chat}"
    info = await _call(token, "getChat", chat_id=target)
    chat_id = int(info["id"])
    await _call(
        token,
        "sendMessage",
        chat_id=chat_id,
        text="✅ Проверка: сюда будут приходить уведомления о продажах.",
    )
    return chat_id


def explain_chat_error(description: str) -> str:
    """Переводит ответ Telegram в понятную подсказку админу."""
    text = description.lower()
    if "chat not found" in text:
        return (
            "Чат не найден. Добавьте бота в группу и укажите её id целиком, "
            "вместе с «-100» в начале (например -1001234567890). Id можно "
            "узнать, переслав сообщение из группы боту @getidsbot."
        )
    if "upgraded to a supergroup" in text:
        return (
            "Группа стала супергруппой и сменила id. Узнайте новый id "
            "(начинается с -100) и сохраните его."
        )
    if "not enough rights" in text or "have no rights" in text:
        return "У бота нет прав писать в этот чат — разрешите ему отправку сообщений."
    if "bot was kicked" in text or "bot is not a member" in text:
        return "Бот не состоит в этом чате — добавьте его в группу."
    if "bots can't send messages to bots" in text:
        return "Это id бота, а не группы."
    return f"Telegram ответил: {description}"


async def send_broadcast_test(
    token: str,
    chat_id: int,
    text: str,
    *,
    photo_path: str | None = None,
    photo_url: str | None = None,
    buttons: list[dict] | None = None,
) -> None:
    """Пробная отправка рассылки — ровно так, как её получат клиенты: фото с
    подписью и кнопками одним сообщением, HTML-разметка Telegram."""
    markup = None
    rows = [
        [{"text": b["text"], "url": b["url"]}]
        for b in buttons or []
        if b.get("text") and b.get("url")
    ]
    if rows:
        markup = {"inline_keyboard": rows}

    if photo_path is None and photo_url is None:
        payload = {"chat_id": chat_id, "text": text, "parse_mode": "HTML"}
        if markup:
            payload["reply_markup"] = markup
        await _call(token, "sendMessage", **payload)
        return

    if photo_path is None:
        payload = {"chat_id": chat_id, "photo": photo_url, "caption": text, "parse_mode": "HTML"}
        if markup:
            payload["reply_markup"] = markup
        await _call(token, "sendPhoto", **payload)
        return

    import json

    form = {"chat_id": str(chat_id), "caption": text, "parse_mode": "HTML"}
    if markup:
        form["reply_markup"] = json.dumps(markup)
    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            with open(photo_path, "rb") as f:
                response = await client.post(
                    f"{API}/bot{token}/sendPhoto", data=form, files={"photo": f}
                )
        except httpx.HTTPError as exc:
            raise TelegramError(f"Не удалось связаться с Telegram: {exc}") from exc
    body = response.json()
    if not body.get("ok"):
        raise TelegramError(body.get("description") or "Telegram отклонил запрос")


async def send_message(token: str, chat_id: int, text: str) -> None:
    await _call(token, "sendMessage", chat_id=chat_id, text=text, parse_mode="HTML")
