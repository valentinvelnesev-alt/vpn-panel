"""Клиенты Telegram-бота: карточка, баланс и персональные тарифы.

Раздел «Пользователи» панели показывает аккаунты Remnawave; здесь — сами
люди из бота: их кошелёк, ключи, покупки. Отсюда админ вручную начисляет
или списывает баланс (с причиной — она видна в истории) и заводит клиенту
персональный тариф со своей ценой, сроком и лимитом устройств.
"""

import html
from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, Query, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import delete as sa_delete
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentAdmin, DbSession
from app.api.v1.bot import PlanIn, PlanOut, _plan_out
from app.services import telegram
from shared.crypto import decrypt
from shared.db.models import (
    AuditLog,
    BotConfig,
    BotSubscription,
    BotUser,
    Plan,
    Purchase,
    Wallet,
    WalletTransaction,
    WalletTxType,
)

router = APIRouter(prefix="/bot/clients", tags=["clients"])


class ClientRow(BaseModel):
    id: int
    telegram_id: int
    username: str | None
    first_name: str | None
    balance_rub: float
    subscriptions: int
    expire_at: datetime | None
    created_at: datetime
    last_seen_at: datetime | None
    has_stopped_bot: bool


class ClientPage(BaseModel):
    items: list[ClientRow]
    total: int


class SubscriptionOut(BaseModel):
    id: int
    username: str
    expire_at: datetime | None
    subscription_url: str | None


class TransactionOut(BaseModel):
    id: int
    amount_rub: float
    type: str
    description: str | None
    created_at: datetime


class PurchaseOut(BaseModel):
    id: int
    days: int
    amount_rub: float
    source: str
    created_at: datetime


class ClientDetail(ClientRow):
    pending_discount_percent: int
    subscriptions_list: list[SubscriptionOut]
    transactions: list[TransactionOut]
    purchases: list[PurchaseOut]
    personal_plans: list[PlanOut]


def _audit(db, admin, action: str, request: Request, target: str, **details) -> None:
    db.add(
        AuditLog(
            admin_id=admin.id,
            action=action,
            target=target,
            details=details or None,
            ip=request.client.host if request.client else None,
            created_at=datetime.now(UTC),
        )
    )


async def _get_user(db: AsyncSession, client_id: int) -> BotUser:
    user = await db.get(BotUser, client_id)
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Клиент не найден")
    return user


async def _wallet(db: AsyncSession, user: BotUser, *, lock: bool = False) -> Wallet:
    query = select(Wallet).where(Wallet.user_id == user.id)
    if lock:
        query = query.with_for_update()
    wallet = await db.scalar(query)
    if wallet is None:
        wallet = Wallet(user_id=user.id, balance_kopeks=0)
        db.add(wallet)
        await db.flush()
    return wallet


@router.get("", response_model=ClientPage)
async def list_clients(
    admin: CurrentAdmin,
    db: DbSession,
    search: str = Query("", max_length=64),
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
) -> ClientPage:
    subs_count = (
        select(BotSubscription.user_id, func.count(BotSubscription.id).label("n"))
        .group_by(BotSubscription.user_id)
        .subquery()
    )
    query = (
        select(BotUser, Wallet.balance_kopeks, subs_count.c.n)
        .outerjoin(Wallet, Wallet.user_id == BotUser.id)
        .outerjoin(subs_count, subs_count.c.user_id == BotUser.id)
    )
    term = search.strip().lstrip("@")
    if term:
        conditions = [
            BotUser.username.ilike(f"%{term}%"),
            BotUser.first_name.ilike(f"%{term}%"),
        ]
        if term.isdigit():
            conditions.append(BotUser.telegram_id == int(term))
        query = query.where(or_(*conditions))

    total = await db.scalar(select(func.count()).select_from(query.subquery())) or 0
    rows = await db.execute(
        query.order_by(BotUser.created_at.desc()).offset(offset).limit(limit)
    )
    return ClientPage(
        items=[_row(user, balance or 0, n or 0) for user, balance, n in rows],
        total=total,
    )


def _row(user: BotUser, balance_kopeks: int, subscriptions: int) -> ClientRow:
    return ClientRow(
        id=user.id,
        telegram_id=user.telegram_id,
        username=user.username,
        first_name=user.first_name,
        balance_rub=balance_kopeks / 100,
        subscriptions=subscriptions,
        expire_at=user.expire_at,
        created_at=user.created_at,
        last_seen_at=user.last_seen_at,
        has_stopped_bot=user.has_stopped_bot,
    )


async def _detail(db: AsyncSession, user: BotUser) -> ClientDetail:
    wallet = await db.scalar(select(Wallet).where(Wallet.user_id == user.id))
    subs = list(
        await db.scalars(
            select(BotSubscription)
            .where(BotSubscription.user_id == user.id)
            .order_by(BotSubscription.created_at.desc())
        )
    )
    transactions = []
    if wallet is not None:
        transactions = list(
            await db.scalars(
                select(WalletTransaction)
                .where(WalletTransaction.wallet_id == wallet.id)
                .order_by(WalletTransaction.created_at.desc(), WalletTransaction.id.desc())
                .limit(100)
            )
        )
    purchases = list(
        await db.scalars(
            select(Purchase)
            .where(Purchase.user_id == user.id)
            .order_by(Purchase.created_at.desc())
            .limit(50)
        )
    )
    plans = list(
        await db.scalars(
            select(Plan).where(Plan.owner_user_id == user.id).order_by(Plan.sort_order, Plan.days)
        )
    )
    base = _row(user, wallet.balance_kopeks if wallet else 0, len(subs))
    return ClientDetail(
        **base.model_dump(),
        pending_discount_percent=user.discount_percent or 0,
        subscriptions_list=[
            SubscriptionOut(
                id=s.id,
                username=s.username,
                expire_at=s.expire_at,
                subscription_url=s.subscription_url,
            )
            for s in subs
        ],
        transactions=[
            TransactionOut(
                id=t.id,
                amount_rub=t.amount_kopeks / 100,
                type=str(t.type),
                description=t.description,
                created_at=t.created_at,
            )
            for t in transactions
        ],
        purchases=[
            PurchaseOut(
                id=p.id,
                days=p.days,
                amount_rub=p.amount_kopeks / 100,
                source=p.source,
                created_at=p.created_at,
            )
            for p in purchases
        ],
        personal_plans=[_plan_out(p) for p in plans],
    )


@router.get("/{client_id}", response_model=ClientDetail)
async def get_client(client_id: int, admin: CurrentAdmin, db: DbSession) -> ClientDetail:
    return await _detail(db, await _get_user(db, client_id))


# ── Баланс ────────────────────────────────────────────────────────────
class BalanceIn(BaseModel):
    # Положительное — начислить, отрицательное — списать.
    amount_rub: float = Field(ge=-1_000_000, le=1_000_000)
    reason: str = Field(min_length=1, max_length=200)
    notify: bool = True


@router.post("/{client_id}/balance", response_model=ClientDetail)
async def adjust_balance(
    client_id: int,
    data: BalanceIn,
    admin: CurrentAdmin,
    db: DbSession,
    request: Request,
) -> ClientDetail:
    amount = round(data.amount_rub * 100)
    if amount == 0:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Укажите сумму")

    user = await _get_user(db, client_id)
    wallet = await _wallet(db, user, lock=True)
    if wallet.balance_kopeks + amount < 0:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            f"Нельзя списать больше, чем на балансе ({wallet.balance_kopeks / 100:.2f} ₽)",
        )
    wallet.balance_kopeks += amount
    reason = data.reason.strip()
    db.add(
        WalletTransaction(
            wallet_id=wallet.id,
            amount_kopeks=amount,
            type=WalletTxType.ADMIN_ADJUST,
            description=reason,
        )
    )
    _audit(
        db, admin, "client.balance", request, str(user.telegram_id),
        amount_rub=amount / 100, reason=reason,
    )
    await db.flush()

    if data.notify:
        await _notify(
            db,
            user,
            (
                f"💰 Баланс {'пополнен' if amount > 0 else 'уменьшен'} на "
                f"<b>{abs(amount) / 100:.2f} ₽</b>\n"
                f"Причина: {html.escape(reason)}\n"
                f"Текущий баланс: <b>{wallet.balance_kopeks / 100:.2f} ₽</b>"
            ),
        )
    return await _detail(db, user)


async def _notify(db: AsyncSession, user: BotUser, text: str) -> None:
    """Пишет клиенту от имени бота. Не получилось (бот выключен, клиент его
    заблокировал) — операция всё равно проходит, это лишь уведомление."""
    row = await db.get(BotConfig, 1)
    if row is None or not row.token_encrypted:
        return
    try:
        await telegram.send_message(decrypt(row.token_encrypted), user.telegram_id, text)
    except telegram.TelegramError:
        pass


# ── Персональные тарифы ───────────────────────────────────────────────
async def _own_plan(db: AsyncSession, user: BotUser, plan_id: int) -> Plan:
    plan = await db.get(Plan, plan_id)
    if plan is None or plan.owner_user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Тариф не найден")
    return plan


def _apply(plan: Plan, data: PlanIn) -> None:
    plan.title = data.title
    plan.days = data.days
    plan.price_kopeks = round(data.price_rub * 100)
    plan.squad_uuids = data.squad_uuids
    plan.hwid_limit = data.hwid_limit
    plan.traffic_limit_bytes = data.traffic_limit_bytes
    # Категории — вкладки общего меню; персональный тариф показывается
    # отдельно, сверху списка, поэтому категория ему не нужна.
    plan.category_id = None
    plan.is_active = data.is_active
    plan.sort_order = data.sort_order


@router.post(
    "/{client_id}/plans", response_model=PlanOut, status_code=status.HTTP_201_CREATED
)
async def create_personal_plan(
    client_id: int, data: PlanIn, admin: CurrentAdmin, db: DbSession, request: Request
) -> PlanOut:
    user = await _get_user(db, client_id)
    plan = Plan(owner_user_id=user.id, title=data.title, days=data.days, price_kopeks=0)
    _apply(plan, data)
    db.add(plan)
    _audit(db, admin, "client.plan.create", request, str(user.telegram_id), title=data.title)
    await db.flush()
    return _plan_out(plan)


@router.put("/{client_id}/plans/{plan_id}", response_model=PlanOut)
async def update_personal_plan(
    client_id: int,
    plan_id: int,
    data: PlanIn,
    admin: CurrentAdmin,
    db: DbSession,
    request: Request,
) -> PlanOut:
    user = await _get_user(db, client_id)
    plan = await _own_plan(db, user, plan_id)
    _apply(plan, data)
    _audit(db, admin, "client.plan.update", request, str(user.telegram_id), plan_id=plan_id)
    await db.flush()
    return _plan_out(plan)


@router.delete("/{client_id}/plans/{plan_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_personal_plan(
    client_id: int, plan_id: int, admin: CurrentAdmin, db: DbSession, request: Request
) -> None:
    user = await _get_user(db, client_id)
    plan = await _own_plan(db, user, plan_id)
    await db.delete(plan)
    _audit(db, admin, "client.plan.delete", request, str(user.telegram_id), plan_id=plan_id)


# ── Удаление клиента ──────────────────────────────────────────────────
@router.delete("/{client_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_client(
    client_id: int,
    admin: CurrentAdmin,
    db: DbSession,
    request: Request,
    with_keys: bool = Query(True, description="Удалить и его ключи в Remnawave"),
) -> None:
    """Удаляет клиента бота вместе с балансом, историей и персональными
    тарифами. with_keys — заодно удалить его аккаунты в Remnawave, иначе
    VPN у него продолжит работать до конца срока."""
    user = await _get_user(db, client_id)
    keys = list(
        await db.scalars(select(BotSubscription).where(BotSubscription.user_id == user.id))
    )
    failed = []
    if with_keys and keys:
        from app.services.remnawave_provider import get_client
        from shared.remnawave import RemnawaveError

        remnawave = await get_client(db)
        for key in keys:
            try:
                await remnawave.delete_user(key.remote_ref)
            except RemnawaveError as exc:
                if exc.status_code != 404:
                    failed.append(f"{key.username}: {exc}")
    if failed:
        # Клиента не удаляем: иначе потеряли бы связь с ключами, которые
        # остались в Remnawave и продолжают работать.
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY,
            "Не удалось удалить ключи в Remnawave: " + "; ".join(failed),
        )
    _audit(
        db, admin, "client.delete", request, str(user.telegram_id),
        keys=len(keys), with_keys=with_keys,
    )
    # Одним DELETE: подписки, кошелёк, платежи и прочее удалит каскад в
    # самой БД. ORM-каскад в async-сессии лениво грузил бы связи и падал.
    await db.execute(sa_delete(BotUser).where(BotUser.id == user.id))
