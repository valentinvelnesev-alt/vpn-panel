"""Поддержка клиентов: ссылка на @username или отдельный бот с темами в группе.

Два режима:
- link — кнопка «Поддержка» в боте продаж ведёт на @username или ссылку;
- bot — клиент пишет отдельному боту поддержки, его сообщения попадают в
  темы (топики) группы операторов, ответы операторов уходят клиенту.

Сам бот поддержки работает в контейнере бота (bot/app/support). Здесь —
настройки, проверка, что всё подключено правильно, и подсказки.
"""

from typing import Literal

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, select

from app.api.deps import CurrentAdmin, DbSession
from app.api.v1.bot import _audit, _config
from app.services import telegram
from app.services.links import normalize_support_link
from app.services.telegram import TelegramError
from shared import bus
from shared.crypto import decrypt, encrypt, mask
from shared.db.models import SupportTicket

router = APIRouter(prefix="/bot/support", tags=["support"])


class SeenChat(BaseModel):
    id: int
    title: str | None


class SupportOut(BaseModel):
    mode: str
    # Ссылка для режима link (то, что увидит клиент по кнопке «Поддержка»).
    link: str | None
    token_masked: str | None
    bot_username: str | None
    chat_id: int | None
    admin_ids: list[int]
    welcome_text: str | None
    state: str
    state_message: str | None
    seen_chat: SeenChat | None
    open_tickets: int
    total_tickets: int


async def _out(db, row) -> SupportOut:
    token = decrypt(row.support_token_encrypted) if row.support_token_encrypted else None
    open_tickets = await db.scalar(
        select(func.count()).select_from(SupportTicket).where(SupportTicket.status == "open")
    )
    total = await db.scalar(select(func.count()).select_from(SupportTicket))
    return SupportOut(
        mode=row.support_mode or "link",
        link=row.support_url,
        token_masked=mask(token) if token else None,
        bot_username=row.support_bot_username,
        chat_id=row.support_chat_id,
        admin_ids=list(row.support_admin_ids or []),
        welcome_text=row.support_welcome,
        state=row.support_state or "stopped",
        state_message=row.support_state_message,
        seen_chat=(
            SeenChat(id=row.support_seen_chat_id, title=row.support_seen_chat_title)
            if row.support_seen_chat_id
            else None
        ),
        open_tickets=open_tickets or 0,
        total_tickets=total or 0,
    )


@router.get("", response_model=SupportOut)
async def get_support(admin: CurrentAdmin, db: DbSession) -> SupportOut:
    return await _out(db, await _config(db))


class SupportIn(BaseModel):
    mode: Literal["link", "bot"]
    link: str | None = Field(default=None, max_length=512)
    # -100… — группа операторов. Пусто — не задана.
    chat_id: int | None = None
    admin_ids: list[int] = Field(default_factory=list, max_length=30)
    welcome_text: str | None = Field(default=None, max_length=2000)

    @field_validator("link")
    @classmethod
    def _link(cls, value: str | None) -> str | None:
        return normalize_support_link(value)

    @field_validator("welcome_text")
    @classmethod
    def _blank(cls, value: str | None) -> str | None:
        return (value or "").strip() or None


@router.put("", response_model=SupportOut)
async def save_support(
    data: SupportIn, admin: CurrentAdmin, db: DbSession, request: Request
) -> SupportOut:
    row = await _config(db)
    if data.mode == "bot":
        if not row.support_token_encrypted:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                "Сначала подключите бота поддержки (шаг 2): вставьте его токен из @BotFather",
            )
        if not data.chat_id:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                "Укажите ID группы операторов (шаг 4)",
            )
        if data.chat_id > 0:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                "ID группы начинается с «-100» — вы указали ID человека. "
                "Напишите /id в самой группе, бот ответит правильным числом",
            )
    row.support_mode = data.mode
    row.support_url = data.link
    row.support_chat_id = data.chat_id
    row.support_admin_ids = sorted(set(data.admin_ids))
    row.support_welcome = data.welcome_text
    _audit(db, admin, "bot.support", request, mode=data.mode)
    await db.flush()
    await db.commit()  # видно боту ДО pub/sub-уведомления
    await bus.publish(bus.CMD_RELOAD)
    return await _out(db, row)


class SupportTokenIn(BaseModel):
    token: str = Field(min_length=10, max_length=256)


@router.put("/token", response_model=SupportOut)
async def set_support_token(
    data: SupportTokenIn, admin: CurrentAdmin, db: DbSession, request: Request
) -> SupportOut:
    token = data.token.strip()
    try:
        identity = await telegram.get_me(token)
    except TelegramError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc

    row = await _config(db)
    # Один токен нельзя слушать двумя процессами: Telegram отдаёт обновления
    # только одному, и оба бота начнут ломаться.
    if row.bot_id == identity.id:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "Это токен основного бота продаж. Для поддержки создайте отдельного бота "
            "в @BotFather и вставьте его токен",
        )
    row.support_token_encrypted = encrypt(token)
    row.support_bot_username = identity.username
    row.support_bot_id = identity.id
    row.support_state = "stopped"
    row.support_state_message = None
    _audit(db, admin, "bot.support.token", request, bot=identity.username)
    await db.flush()
    await db.commit()
    await bus.publish(bus.CMD_RELOAD)
    return await _out(db, row)


@router.delete("/token", response_model=SupportOut)
async def delete_support_token(
    admin: CurrentAdmin, db: DbSession, request: Request
) -> SupportOut:
    row = await _config(db)
    row.support_token_encrypted = None
    row.support_bot_username = None
    row.support_bot_id = None
    row.support_mode = "link"  # без бота поддержки через бота работать нечему
    row.support_state = "stopped"
    row.support_state_message = None
    _audit(db, admin, "bot.support.token.delete", request)
    await db.flush()
    await db.commit()
    await bus.publish(bus.CMD_RELOAD)
    return await _out(db, row)


class CheckItem(BaseModel):
    key: str
    ok: bool
    title: str
    hint: str | None = None


class CheckOut(BaseModel):
    ready: bool
    items: list[CheckItem]


@router.post("/check", response_model=CheckOut)
async def check_support(admin: CurrentAdmin, db: DbSession) -> CheckOut:
    """Проверяет по порядку всё, что нужно для работы, и говорит, что именно
    не так — чтобы человеку не пришлось гадать, почему «ничего не пишет»."""
    row = await _config(db)
    items: list[CheckItem] = []

    token = decrypt(row.support_token_encrypted) if row.support_token_encrypted else None
    items.append(
        CheckItem(
            key="token",
            ok=bool(token),
            title=f"Бот поддержки подключён{f' (@{row.support_bot_username})' if token else ''}",
            hint=None if token else "Создайте бота в @BotFather и вставьте токен (шаг 2).",
        )
    )

    chat_id = row.support_chat_id
    chat: dict | None = None
    chat_hint = None
    if token and chat_id:
        try:
            chat = await telegram.get_chat(token, chat_id)
        except TelegramError as exc:
            chat_hint = telegram.explain_chat_error(str(exc))
    elif not chat_id:
        chat_hint = "Укажите ID группы операторов (шаг 4)."
    elif not token:
        chat_hint = "Сначала подключите бота."
    chat_title = f"Группа найдена: «{chat.get('title')}»" if chat else "Группа найдена"
    items.append(CheckItem(key="chat", ok=chat is not None, title=chat_title, hint=chat_hint))

    forum_ok = bool(chat and chat.get("is_forum"))
    items.append(
        CheckItem(
            key="topics",
            ok=forum_ok,
            title="В группе включены темы",
            hint=None
            if forum_ok
            else "Откройте группу → её название → Изменить → включите «Темы» (Topics). "
            "Группа станет супергруппой — её ID изменится, обновите его на шаге 4.",
        )
    )

    admin_ok = False
    admin_hint = "Сделайте бота администратором группы (шаг 3)."
    if token and chat and row.support_bot_id:
        try:
            member = await telegram.get_chat_member(token, chat_id, row.support_bot_id)
        except TelegramError as exc:
            admin_hint = telegram.explain_chat_error(str(exc))
        else:
            if member.get("status") == "administrator":
                admin_ok = bool(member.get("can_manage_topics"))
                if not admin_ok:
                    admin_hint = "У бота нет права «Управление темами» — включите его в правах администратора."
            elif member.get("status") == "creator":
                admin_ok = True
            else:
                admin_hint = "Бот в группе, но не администратор. Сделайте его администратором (шаг 3)."
    items.append(
        CheckItem(
            key="admin",
            ok=admin_ok,
            title="Бот — администратор с правом «Управление темами»",
            hint=None if admin_ok else admin_hint,
        )
    )

    ready = all(item.ok for item in items)
    return CheckOut(ready=ready, items=items)


@router.post("/test", response_model=CheckItem)
async def test_support(admin: CurrentAdmin, db: DbSession) -> CheckItem:
    """Сообщение от бота поддержки в группу — видно, что он действительно пишет."""
    row = await _config(db)
    if not row.support_token_encrypted or not row.support_chat_id:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "Сначала подключите бота и укажите ID группы"
        )
    try:
        await telegram.send_plain(
            decrypt(row.support_token_encrypted),
            row.support_chat_id,
            "✅ Поддержка подключена. Сообщения клиентов будут появляться здесь "
            "отдельными темами; отвечайте прямо в теме.",
        )
    except TelegramError as exc:
        return CheckItem(key="test", ok=False, title="Не отправилось", hint=telegram.explain_chat_error(str(exc)))
    return CheckItem(key="test", ok=True, title="Проверочное сообщение отправлено в группу")
