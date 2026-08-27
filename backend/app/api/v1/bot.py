from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import delete, select

from app.api.deps import CurrentAdmin, DbSession
from app.services import telegram
from app.services.telegram import TelegramError
from shared import bus
from shared.crypto import decrypt, encrypt, mask
from shared.db.models import AuditLog, BotConfig, BotState, EmojiMode, Plan, PlanCategory

router = APIRouter(prefix="/bot", tags=["bot"])


async def _config(db: DbSession) -> BotConfig:
    row = await db.get(BotConfig, 1)
    if row is None:
        row = BotConfig(id=1)
        db.add(row)
        await db.flush()
    return row


def _audit(db, admin, action: str, request: Request, **details) -> None:
    db.add(
        AuditLog(
            admin_id=admin.id,
            action=action,
            details=details or None,
            ip=request.client.host if request.client else None,
            created_at=datetime.now(UTC),
        )
    )


# ── Состояние ─────────────────────────────────────────────────────────
class BotStatusOut(BaseModel):
    configured: bool
    enabled: bool
    state: str
    state_message: str | None
    token_masked: str | None
    bot_username: str | None
    bot_name: str | None
    started_at: datetime | None

    emoji_mode: str
    premium_available: bool
    premium_emoji: dict[str, str]

    welcome_text: str | None
    support_url: str | None
    channel_url: str | None
    channel_id: str | None
    require_channel_sub: bool

    trial_enabled: bool
    trial_days: int
    trial_squad_uuids: list[str]
    trial_hwid_limit: int

    node_alerts_enabled: bool
    node_alerts_chat_id: int | None

    purchase_notify_chat_id: int | None
    admin_telegram_ids: list[int]


def _status(row: BotConfig) -> BotStatusOut:
    token = decrypt(row.token_encrypted) if row.token_encrypted else None
    return BotStatusOut(
        configured=bool(token),
        enabled=row.enabled,
        state=str(row.state),
        state_message=row.state_message,
        token_masked=mask(token) if token else None,
        bot_username=row.bot_username,
        bot_name=row.bot_name,
        started_at=row.started_at,
        emoji_mode=str(row.emoji_mode),
        premium_available=row.premium_available,
        premium_emoji=row.premium_emoji or {},
        welcome_text=row.welcome_text,
        support_url=row.support_url,
        channel_url=row.channel_url,
        channel_id=row.channel_id,
        require_channel_sub=row.require_channel_sub,
        trial_enabled=row.trial_enabled,
        trial_days=row.trial_days,
        trial_squad_uuids=list(row.trial_squad_uuids or []),
        trial_hwid_limit=row.trial_hwid_limit,
        node_alerts_enabled=row.node_alerts_enabled,
        node_alerts_chat_id=row.node_alerts_chat_id,
        purchase_notify_chat_id=row.purchase_notify_chat_id,
        admin_telegram_ids=list(row.admin_telegram_ids or []),
    )


@router.get("", response_model=BotStatusOut)
async def get_status(admin: CurrentAdmin, db: DbSession) -> BotStatusOut:
    return _status(await _config(db))


# ── Токен ─────────────────────────────────────────────────────────────
class TokenIn(BaseModel):
    token: str = Field(min_length=10, max_length=256)


class TokenCheckOut(BaseModel):
    ok: bool
    message: str
    bot_username: str | None = None
    bot_name: str | None = None


@router.post("/token/check", response_model=TokenCheckOut)
async def check_token(data: TokenIn, admin: CurrentAdmin) -> TokenCheckOut:
    """Показывает, какому боту принадлежит токен, до его сохранения."""
    try:
        identity = await telegram.get_me(data.token.strip())
    except TelegramError as exc:
        return TokenCheckOut(ok=False, message=str(exc))
    return TokenCheckOut(
        ok=True,
        message=f"Токен принадлежит боту @{identity.username}",
        bot_username=identity.username,
        bot_name=identity.name,
    )


@router.put("/token", response_model=BotStatusOut)
async def set_token(
    data: TokenIn, admin: CurrentAdmin, db: DbSession, request: Request
) -> BotStatusOut:
    """Сохраняет токен и переключает бота на него.

    База пользователей не трогается: привязка идёт по telegram_id, поэтому
    после смены бота клиенты остаются теми же.
    """
    token = data.token.strip()
    try:
        identity = await telegram.get_me(token)
    except TelegramError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc

    row = await _config(db)
    changed = row.bot_id is not None and row.bot_id != identity.id

    row.token_encrypted = encrypt(token)
    row.bot_id = identity.id
    row.bot_username = identity.username
    row.bot_name = identity.name
    if changed:
        # Другой бот — прежняя проверка Premium к нему не относится.
        row.premium_available = False
        row.premium_checked_at = None
        if row.emoji_mode == EmojiMode.PREMIUM:
            row.emoji_mode = EmojiMode.PLAIN

    _audit(db, admin, "bot.token", request, bot=identity.username, changed=changed)
    await db.flush()

    if row.enabled:
        await db.commit()  # видно другим сессиям ДО pub/sub-уведомления бота
        await bus.publish(bus.CMD_START)
    return _status(row)


# ── Запуск и остановка ────────────────────────────────────────────────
@router.post("/start", response_model=BotStatusOut)
async def start_bot(
    admin: CurrentAdmin, db: DbSession, request: Request
) -> BotStatusOut:
    row = await _config(db)
    if not row.token_encrypted:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "Сначала укажите токен бота из @BotFather"
        )
    row.enabled = True
    _audit(db, admin, "bot.start", request)
    await db.flush()
    await db.commit()  # видно другим сессиям ДО pub/sub-уведомления бота
    await bus.publish(bus.CMD_START)
    return _status(row)


@router.post("/stop", response_model=BotStatusOut)
async def stop_bot(admin: CurrentAdmin, db: DbSession, request: Request) -> BotStatusOut:
    row = await _config(db)
    row.enabled = False
    row.state = BotState.STOPPED
    _audit(db, admin, "bot.stop", request)
    await db.flush()
    await db.commit()  # видно другим сессиям ДО pub/sub-уведомления бота
    await bus.publish(bus.CMD_STOP)
    return _status(row)


# ── Настройки ─────────────────────────────────────────────────────────
class BotSettingsIn(BaseModel):
    welcome_text: str | None = Field(default=None, max_length=4000)
    support_url: str | None = Field(default=None, max_length=255)
    channel_url: str | None = Field(default=None, max_length=255)
    channel_id: str | None = Field(default=None, max_length=64)
    require_channel_sub: bool = False

    trial_enabled: bool = True
    trial_days: int = Field(default=3, ge=1, le=365)
    trial_squad_uuids: list[str] = Field(default_factory=list)
    trial_hwid_limit: int = Field(default=3, ge=1, le=100)

    # Уведомление о каждой продаже в чат/группу (бот должен состоять в ней).
    # Пусто/0 — уведомления отключены.
    purchase_notify_chat_id: int | None = None

    # Telegram ID, которым в самом боте открывается команда /admin.
    admin_telegram_ids: list[int] = Field(default_factory=list)


@router.put("/settings", response_model=BotStatusOut)
async def save_settings(
    data: BotSettingsIn, admin: CurrentAdmin, db: DbSession, request: Request
) -> BotStatusOut:
    row = await _config(db)
    for field, value in data.model_dump().items():
        setattr(row, field, value)
    _audit(db, admin, "bot.settings", request)
    await db.flush()
    await db.commit()  # видно другим сессиям ДО pub/sub-уведомления бота
    await bus.publish(bus.CMD_RELOAD)
    return _status(row)


# ── Режим эмодзи ──────────────────────────────────────────────────────
class EmojiModeIn(BaseModel):
    mode: EmojiMode
    premium_emoji: dict[str, str] = Field(default_factory=dict)
    # Куда слать проверочное сообщение — обычно свой Telegram ID.
    test_chat_id: int | None = None


class EmojiModeOut(BaseModel):
    ok: bool
    message: str
    status: BotStatusOut


@router.put("/emoji", response_model=EmojiModeOut)
async def set_emoji_mode(
    data: EmojiModeIn, admin: CurrentAdmin, db: DbSession, request: Request
) -> EmojiModeOut:
    """Переключает набор эмодзи.

    Премиум-режим включается только после успешной проверки: панель шлёт
    тестовое сообщение с кастомным эмодзи. Если у аккаунта, создавшего
    бота, нет Telegram Premium, Telegram откажет — и мы честно об этом
    скажем, оставив обычный режим.
    """
    row = await _config(db)

    if data.mode is EmojiMode.PLAIN:
        row.emoji_mode = EmojiMode.PLAIN
        row.premium_emoji = data.premium_emoji
        _audit(db, admin, "bot.emoji", request, mode="plain")
        await db.flush()
        await db.commit()  # видно другим сессиям ДО pub/sub-уведомления бота
        await bus.publish(bus.CMD_RELOAD)
        return EmojiModeOut(
            ok=True, message="Включены обычные эмодзи", status=_status(row)
        )

    if not row.token_encrypted:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "Сначала укажите токен бота"
        )
    if not data.premium_emoji:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "Укажите хотя бы один id премиум-эмодзи — универсальных не бывает",
        )
    if not data.test_chat_id:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "Укажите ваш Telegram ID — туда придёт проверочное сообщение",
        )

    token = decrypt(row.token_encrypted)
    sample = next(iter(data.premium_emoji.values()))
    try:
        await telegram.check_premium_emoji(token, data.test_chat_id, sample)
    except TelegramError as exc:
        row.premium_available = False
        row.premium_checked_at = datetime.now(UTC)
        row.emoji_mode = EmojiMode.PLAIN
        await db.flush()
        return EmojiModeOut(
            ok=False,
            message=(
                f"Премиум-эмодзи включить не удалось: {exc}. "
                "Проверьте, что у аккаунта, на котором бот создан в @BotFather, "
                "активен Telegram Premium, и что вы начали диалог с ботом."
            ),
            status=_status(row),
        )

    row.emoji_mode = EmojiMode.PREMIUM
    row.premium_emoji = data.premium_emoji
    row.premium_available = True
    row.premium_checked_at = datetime.now(UTC)
    _audit(db, admin, "bot.emoji", request, mode="premium")
    await db.flush()
    await db.commit()  # видно другим сессиям ДО pub/sub-уведомления бота
    await bus.publish(bus.CMD_RELOAD)
    return EmojiModeOut(
        ok=True, message="Премиум-эмодзи включены", status=_status(row)
    )


# ── Тарифы ────────────────────────────────────────────────────────────
class PlanIn(BaseModel):
    title: str = Field(min_length=1, max_length=64)
    days: int = Field(ge=1, le=3650)
    price_rub: float = Field(ge=0, le=1_000_000)
    squad_uuids: list[str] = Field(default_factory=list)
    hwid_limit: int = Field(default=3, ge=1, le=100)
    traffic_limit_bytes: int = Field(default=0, ge=0)
    category_id: int | None = None
    is_active: bool = True
    sort_order: int = 0


class PlanOut(PlanIn):
    id: int


def _plan_out(plan: Plan) -> PlanOut:
    return PlanOut(
        id=plan.id,
        title=plan.title,
        days=plan.days,
        price_rub=plan.price_kopeks / 100,
        squad_uuids=list(plan.squad_uuids or []),
        hwid_limit=plan.hwid_limit,
        traffic_limit_bytes=plan.traffic_limit_bytes,
        category_id=plan.category_id,
        is_active=plan.is_active,
        sort_order=plan.sort_order,
    )


# ── Категории тарифов ────────────────────────────────────────────────
class PlanCategoryIn(BaseModel):
    title: str = Field(min_length=1, max_length=64)
    sort_order: int = 0


class PlanCategoryOut(PlanCategoryIn):
    id: int


@router.get("/plan-categories", response_model=list[PlanCategoryOut])
async def list_plan_categories(
    admin: CurrentAdmin, db: DbSession
) -> list[PlanCategoryOut]:
    rows = await db.scalars(
        select(PlanCategory).order_by(PlanCategory.sort_order, PlanCategory.title)
    )
    return [PlanCategoryOut(id=r.id, title=r.title, sort_order=r.sort_order) for r in rows]


@router.post(
    "/plan-categories",
    response_model=PlanCategoryOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_plan_category(
    data: PlanCategoryIn, admin: CurrentAdmin, db: DbSession, request: Request
) -> PlanCategoryOut:
    row = PlanCategory(title=data.title, sort_order=data.sort_order)
    db.add(row)
    _audit(db, admin, "bot.plan_category.create", request, title=data.title)
    await db.flush()
    await db.commit()  # видно другим сессиям ДО pub/sub-уведомления бота
    await bus.publish(bus.CMD_RELOAD)
    return PlanCategoryOut(id=row.id, title=row.title, sort_order=row.sort_order)


@router.put("/plan-categories/{category_id}", response_model=PlanCategoryOut)
async def update_plan_category(
    category_id: int,
    data: PlanCategoryIn,
    admin: CurrentAdmin,
    db: DbSession,
    request: Request,
) -> PlanCategoryOut:
    row = await db.get(PlanCategory, category_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Категория не найдена")
    row.title = data.title
    row.sort_order = data.sort_order
    _audit(db, admin, "bot.plan_category.update", request, category_id=category_id)
    await db.flush()
    await db.commit()  # видно другим сессиям ДО pub/sub-уведомления бота
    await bus.publish(bus.CMD_RELOAD)
    return PlanCategoryOut(id=row.id, title=row.title, sort_order=row.sort_order)


@router.delete("/plan-categories/{category_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_plan_category(
    category_id: int, admin: CurrentAdmin, db: DbSession, request: Request
) -> None:
    """Удаляет категорию. Тарифы в ней не удаляются — просто становятся без
    категории (ON DELETE SET NULL), чтобы случайное удаление вкладки не
    стёрло тарифы, которыми уже кто-то пользуется."""
    result = await db.execute(delete(PlanCategory).where(PlanCategory.id == category_id))
    if result.rowcount == 0:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Категория не найдена")
    _audit(db, admin, "bot.plan_category.delete", request, category_id=category_id)
    await db.commit()  # видно другим сессиям ДО pub/sub-уведомления бота
    await bus.publish(bus.CMD_RELOAD)


@router.get("/plans", response_model=list[PlanOut])
async def list_plans(admin: CurrentAdmin, db: DbSession) -> list[PlanOut]:
    plans = await db.scalars(select(Plan).order_by(Plan.sort_order, Plan.days))
    return [_plan_out(p) for p in plans]


@router.post("/plans", response_model=PlanOut, status_code=status.HTTP_201_CREATED)
async def create_plan(
    data: PlanIn, admin: CurrentAdmin, db: DbSession, request: Request
) -> PlanOut:
    plan = Plan(
        title=data.title,
        days=data.days,
        # Цены в копейках: float в деньгах рано или поздно теряет копейку.
        price_kopeks=round(data.price_rub * 100),
        squad_uuids=data.squad_uuids,
        hwid_limit=data.hwid_limit,
        traffic_limit_bytes=data.traffic_limit_bytes,
        category_id=data.category_id,
        is_active=data.is_active,
        sort_order=data.sort_order,
    )
    db.add(plan)
    _audit(db, admin, "bot.plan.create", request, title=data.title)
    await db.flush()
    await db.commit()  # видно другим сессиям ДО pub/sub-уведомления бота
    await bus.publish(bus.CMD_RELOAD)
    return _plan_out(plan)


@router.put("/plans/{plan_id}", response_model=PlanOut)
async def update_plan(
    plan_id: int,
    data: PlanIn,
    admin: CurrentAdmin,
    db: DbSession,
    request: Request,
) -> PlanOut:
    plan = await db.get(Plan, plan_id)
    if plan is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Тариф не найден")

    plan.title = data.title
    plan.days = data.days
    plan.price_kopeks = round(data.price_rub * 100)
    plan.squad_uuids = data.squad_uuids
    plan.hwid_limit = data.hwid_limit
    plan.traffic_limit_bytes = data.traffic_limit_bytes
    plan.category_id = data.category_id
    plan.is_active = data.is_active
    plan.sort_order = data.sort_order

    _audit(db, admin, "bot.plan.update", request, plan_id=plan_id)
    await db.flush()
    await db.commit()  # видно другим сессиям ДО pub/sub-уведомления бота
    await bus.publish(bus.CMD_RELOAD)
    return _plan_out(plan)


@router.delete("/plans/{plan_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_plan(
    plan_id: int, admin: CurrentAdmin, db: DbSession, request: Request
) -> None:
    result = await db.execute(delete(Plan).where(Plan.id == plan_id))
    if result.rowcount == 0:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Тариф не найден")
    _audit(db, admin, "bot.plan.delete", request, plan_id=plan_id)
    await db.commit()  # видно другим сессиям ДО pub/sub-уведомления бота
    await bus.publish(bus.CMD_RELOAD)


# ── Сквады для выпадающих списков ─────────────────────────────────────
@router.get("/squads")
async def list_squads(admin: CurrentAdmin, db: DbSession) -> list[dict]:
    """Сквады Remnawave — чтобы тариф выбирался списком, а не вводом UUID."""
    from app.services.remnawave_provider import get_client

    client = await get_client(db)
    squads = await client.get_internal_squads()
    return [
        {"uuid": s.get("uuid"), "name": s.get("name")}
        for s in squads
        if s.get("uuid")
    ]


# ── Алерты о падении нод (Pro) ─────────────────────────────────────────
class NodeAlertsIn(BaseModel):
    enabled: bool
    chat_id: int | None = None


@router.put("/node-alerts", response_model=BotStatusOut)
async def save_node_alerts(
    data: NodeAlertsIn, admin: CurrentAdmin, db: DbSession, request: Request
) -> BotStatusOut:
    row = await _config(db)
    row.node_alerts_enabled = data.enabled
    row.node_alerts_chat_id = data.chat_id
    _audit(db, admin, "bot.node_alerts", request)
    await db.flush()
    await db.commit()  # видно другим сессиям ДО pub/sub-уведомления бота
    await bus.publish(bus.CMD_RELOAD)
    return _status(row)
