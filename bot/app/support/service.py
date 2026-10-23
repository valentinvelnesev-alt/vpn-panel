"""Логика поддержки без привязки к polling: обращения, темы, пересылка.

Одно обращение — одна тема (топик) группы операторов. Клиент пишет боту —
сообщение копируется в его тему; оператор отвечает в теме — ответ копируется
клиенту. /close в теме закрывает обращение, следующее сообщение клиента
заведёт новое. Всё через copy_message: так одинаково работают текст, фото,
файлы, голосовые и стикеры.
"""

import html
import logging
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import UTC, datetime

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from sqlalchemy import select

from shared.db.models import BotUser, SupportTicket, Wallet
from shared.db.session import session

log = logging.getLogger("bot.support")

DEFAULT_WELCOME = (
    "Здравствуйте! Напишите ваш вопрос одним или несколькими сообщениями — "
    "оператор ответит прямо здесь."
)
TOPIC_NAME_LIMIT = 128

# Что можно переслать. Служебные сообщения группы (создана тема, кто-то
# вошёл) пересылать нельзя — Telegram отвечает ошибкой.
RELAYED_TYPES = frozenset(
    {
        "text", "photo", "video", "document", "audio", "voice",
        "video_note", "sticker", "animation", "location", "contact",
    }
)

# Не больше стольких сообщений от одного клиента за окно — защита от флуда.
FLOOD_LIMIT = 20
FLOOD_WINDOW = 60.0
_recent: dict[int, deque[float]] = defaultdict(deque)


@dataclass
class SupportContext:
    chat_id: int
    bot_id: int
    bot_username: str | None = None
    admin_ids: set[int] = field(default_factory=set)
    welcome: str | None = None


def flooding(user_id: int, now: float | None = None) -> bool:
    now = time.monotonic() if now is None else now
    window = _recent[user_id]
    while window and now - window[0] > FLOOD_WINDOW:
        window.popleft()
    if len(window) >= FLOOD_LIMIT:
        return True
    window.append(now)
    if len(_recent) > 20_000:
        for key in [k for k, v in _recent.items() if not v or now - v[-1] > FLOOD_WINDOW]:
            _recent.pop(key, None)
    return False


def topic_name(user_id: int, username: str | None, full_name: str) -> str:
    who = f"@{username}" if username else (full_name or "без имени")
    return f"{who} | ID {user_id}"[:TOPIC_NAME_LIMIT]


async def _client_card(user_id: int, username: str | None, full_name: str) -> str:
    """Шапка темы: кто пишет и что у него с подпиской — оператору не нужно
    искать клиента в панели."""
    who = html.escape(f"@{username}" if username else (full_name or "—"))
    lines = [
        "<b>Новое обращение</b>",
        f"Клиент: {who}",
        f"ID: <code>{user_id}</code> · <a href=\"tg://user?id={user_id}\">профиль</a>",
    ]
    async with session() as db:
        user = await db.scalar(select(BotUser).where(BotUser.telegram_id == user_id))
        if user is not None:
            expire = user.expire_at
            if expire is not None and expire.tzinfo is None:
                expire = expire.replace(tzinfo=UTC)
            if expire is None:
                lines.append("Подписка: нет")
            elif expire > datetime.now(UTC):
                lines.append(f"Подписка: до {expire:%d.%m.%Y}")
            else:
                lines.append(f"Подписка: истекла {expire:%d.%m.%Y}")
            wallet = await db.scalar(select(Wallet).where(Wallet.user_id == user.id))
            if wallet is not None and wallet.balance_kopeks:
                lines.append(f"Баланс: {wallet.balance_kopeks / 100:.2f} ₽")
            if user.is_blocked:
                lines.append("⛔ Заблокирован в боте продаж")
        else:
            lines.append("В боте продаж не найден")
    lines += ["", "Отвечайте в этой теме — ответ уйдёт клиенту.", "Закрыть обращение: /close"]
    return "\n".join(lines)


async def open_ticket_for(user_id: int) -> SupportTicket | None:
    async with session() as db:
        return await db.scalar(
            select(SupportTicket)
            .where(SupportTicket.user_telegram_id == user_id, SupportTicket.status == "open")
            .order_by(SupportTicket.id.desc())
            .limit(1)
        )


async def ticket_by_thread(chat_id: int, thread_id: int) -> SupportTicket | None:
    async with session() as db:
        return await db.scalar(
            select(SupportTicket).where(
                SupportTicket.chat_id == chat_id,
                SupportTicket.thread_id == thread_id,
                SupportTicket.status == "open",
            )
        )


async def _create_ticket(
    bot: Bot, ctx: SupportContext, user_id: int, username: str | None, full_name: str
) -> SupportTicket:
    topic = await bot.create_forum_topic(
        chat_id=ctx.chat_id, name=topic_name(user_id, username, full_name)
    )
    ticket = SupportTicket(
        user_telegram_id=user_id,
        chat_id=ctx.chat_id,
        thread_id=topic.message_thread_id,
        username=username,
        status="open",
        created_at=datetime.now(UTC),
    )
    async with session() as db:
        db.add(ticket)
    card = await _client_card(user_id, username, full_name)
    await bot.send_message(
        chat_id=ctx.chat_id,
        message_thread_id=ticket.thread_id,
        text=card,
        disable_web_page_preview=True,
    )
    return ticket


async def _mark_closed(ticket_id: int) -> None:
    async with session() as db:
        row = await db.get(SupportTicket, ticket_id)
        if row is not None and row.status == "open":
            row.status = "closed"
            row.closed_at = datetime.now(UTC)


def _thread_gone(exc: TelegramBadRequest) -> bool:
    text = str(exc).lower()
    return "thread not found" in text or "topic" in text and "not found" in text


async def relay_from_client(
    bot: Bot,
    ctx: SupportContext,
    *,
    user_id: int,
    username: str | None,
    full_name: str,
    message_id: int,
) -> str:
    """Клиент → тема. Возвращает: ok / new (создано обращение) / blocked / flood / error."""
    async with session() as db:
        banned = await db.scalar(select(BotUser.is_blocked).where(BotUser.telegram_id == user_id))
    if banned:
        return "blocked"
    if flooding(user_id):
        return "flood"

    ticket = await open_ticket_for(user_id)
    created = False
    for _attempt in range(2):
        try:
            if ticket is None:
                ticket = await _create_ticket(bot, ctx, user_id, username, full_name)
                created = True
            await bot.copy_message(
                chat_id=ctx.chat_id,
                from_chat_id=user_id,
                message_id=message_id,
                message_thread_id=ticket.thread_id,
            )
            return "new" if created else "ok"
        except TelegramBadRequest as exc:
            if ticket is not None and _thread_gone(exc):
                # Оператор удалил тему руками — обращение считаем закрытым и
                # заводим новое, а не теряем сообщение клиента.
                await _mark_closed(ticket.id)
                ticket = None
                continue
            log.exception("Не удалось передать сообщение клиента %s: %s", user_id, exc)
            return "error"
        except Exception as exc:  # noqa: BLE001
            log.exception("Не удалось передать сообщение клиента %s: %s", user_id, exc)
            return "error"
    return "error"


async def is_operator(bot: Bot, ctx: SupportContext, user_id: int) -> bool:
    if user_id in ctx.admin_ids:
        return True
    try:
        member = await bot.get_chat_member(ctx.chat_id, user_id)
    except Exception:  # noqa: BLE001
        return False
    return member.status in {"administrator", "creator"}


async def close_ticket(bot: Bot, ctx: SupportContext, ticket: SupportTicket) -> None:
    await _mark_closed(ticket.id)
    try:
        await bot.close_forum_topic(chat_id=ctx.chat_id, message_thread_id=ticket.thread_id)
    except Exception:  # noqa: BLE001
        pass  # тема уже закрыта или удалена — обращение всё равно закрыто
    try:
        await bot.send_message(
            ticket.user_telegram_id,
            "Обращение закрыто. Если появятся вопросы — просто напишите снова.",
        )
    except (TelegramForbiddenError, TelegramBadRequest):
        pass  # клиент заблокировал бота поддержки


async def relay_from_operator(
    bot: Bot,
    ctx: SupportContext,
    *,
    thread_id: int,
    message_id: int,
    text: str,
    operator_id: int | None,
    anonymous: bool = False,
) -> str:
    """Оператор → клиент. Возвращает: ok / closed / ignored / forbidden / error."""
    ticket = await ticket_by_thread(ctx.chat_id, thread_id)
    if ticket is None:
        return "ignored"  # общая тема, закрытое обращение или чужая тема
    if not anonymous and (operator_id is None or not await is_operator(bot, ctx, operator_id)):
        return "forbidden"

    command = (text or "").split(maxsplit=1)[0].lower() if text else ""
    if command == "/close" or command.startswith("/close@"):
        await close_ticket(bot, ctx, ticket)
        await bot.send_message(
            chat_id=ctx.chat_id,
            message_thread_id=thread_id,
            text="Обращение закрыто, клиент уведомлён.",
        )
        return "closed"

    try:
        await bot.copy_message(
            chat_id=ticket.user_telegram_id, from_chat_id=ctx.chat_id, message_id=message_id
        )
    except TelegramForbiddenError:
        await bot.send_message(
            chat_id=ctx.chat_id,
            message_thread_id=thread_id,
            text="⚠️ Клиент заблокировал бота поддержки — сообщение не доставлено.",
        )
        return "error"
    except Exception as exc:  # noqa: BLE001
        log.exception("Не удалось отправить ответ клиенту %s: %s", ticket.user_telegram_id, exc)
        await bot.send_message(
            chat_id=ctx.chat_id,
            message_thread_id=thread_id,
            text="⚠️ Не удалось отправить сообщение клиенту.",
        )
        return "error"
    return "ok"
