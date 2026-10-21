import re
from typing import Literal
from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from app.api.deps import CurrentAdmin, DbSession
from shared import bus
from shared.db.models import (
    AdEarning,
    AdLink,
    AuditLog,
    BotConfig,
    BotUser,
    Wallet,
    WalletTransaction,
    WalletTxType,
)

router = APIRouter(prefix="/bot/ad-links", tags=["ad-links"])

_PREFIX = re.compile(r"^[a-z0-9_-]{2,40}$")
_USERNAME = re.compile(r"^[A-Za-z0-9_]{3,32}$")


class AdLinkIn(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    prefix: str = Field(min_length=2, max_length=40)
    percent: int = Field(default=50, ge=1, le=100)
    # Telegram ID (цифры) или @username владельца ссылки.
    owner: str = Field(min_length=1, max_length=64)
    is_active: bool = True
    # balance — доля сразу на баланс в боте; manual — только учёт, платит админ.
    payout_mode: Literal["balance", "manual"] = "balance"


class AdLinkOut(BaseModel):
    id: int
    name: str
    prefix: str
    percent: int
    owner: str
    owner_found: bool
    is_active: bool
    url: str | None
    users: int
    buyers: int
    turnover_rub: float
    earned_rub: float
    pending_rub: float
    payout_mode: str
    paid_out_rub: float
    owed_rub: float


class AdLinksOut(BaseModel):
    enabled: bool
    links: list[AdLinkOut]


class AdLinksSettingsIn(BaseModel):
    enabled: bool


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


def _parse_prefix(raw: str) -> str:
    prefix = raw.strip().lower()
    if not _PREFIX.match(prefix):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "Префикс — 2–40 символов: латинские буквы, цифры, «_» и «-»",
        )
    if prefix.startswith("ref"):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "Префикс не может начинаться с «ref» — это обычные реферальные ссылки",
        )
    return prefix


def _parse_owner(raw: str) -> tuple[int | None, str | None]:
    value = raw.strip().removeprefix("@")
    if value.isdigit():
        return int(value), None
    if not _USERNAME.match(value):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "Владелец — Telegram ID (цифры) или @username",
        )
    return None, value


async def _resolve_owner(db: DbSession, link: AdLink) -> None:
    """Username → Telegram ID, если человек уже пользовался ботом."""
    if link.owner_telegram_id is None and link.owner_username:
        tg = await db.scalar(
            select(BotUser.telegram_id).where(
                func.lower(BotUser.username) == link.owner_username.lower()
            )
        )
        if tg is not None:
            link.owner_telegram_id = tg


async def _out(db: DbSession, link: AdLink, bot_username: str | None) -> AdLinkOut:
    users = await db.scalar(
        select(func.count()).select_from(BotUser).where(BotUser.ad_link_id == link.id)
    )
    buyers, turnover, earned = (
        await db.execute(
            select(
                func.count(func.distinct(AdEarning.buyer_user_id)),
                func.coalesce(func.sum(AdEarning.amount_kopeks), 0),
                func.coalesce(func.sum(AdEarning.commission_kopeks), 0),
            ).where(AdEarning.ad_link_id == link.id)
        )
    ).one()
    pending = await db.scalar(
        select(func.coalesce(func.sum(AdEarning.commission_kopeks), 0)).where(
            AdEarning.ad_link_id == link.id, AdEarning.paid.is_(False)
        )
    )
    return AdLinkOut(
        id=link.id,
        name=link.name,
        prefix=link.prefix,
        percent=link.percent,
        owner=(
            str(link.owner_telegram_id)
            if link.owner_telegram_id is not None
            else f"@{link.owner_username}"
        ),
        owner_found=link.owner_telegram_id is not None,
        is_active=link.is_active,
        url=f"https://t.me/{bot_username}?start={link.prefix}" if bot_username else None,
        users=users or 0,
        buyers=buyers or 0,
        turnover_rub=turnover / 100,
        earned_rub=earned / 100,
        pending_rub=(pending or 0) / 100,
        payout_mode=link.payout_mode,
        paid_out_rub=link.paid_out_kopeks / 100,
        owed_rub=max(earned - link.paid_out_kopeks, 0) / 100,
    )


async def _listing(db: DbSession) -> AdLinksOut:
    config = await _config(db)
    links = list(await db.scalars(select(AdLink).order_by(AdLink.id.desc())))
    return AdLinksOut(
        enabled=config.ad_links_enabled,
        links=[await _out(db, link, config.bot_username) for link in links],
    )


@router.get("", response_model=AdLinksOut)
async def list_links(admin: CurrentAdmin, db: DbSession) -> AdLinksOut:
    return await _listing(db)


@router.put("/settings", response_model=AdLinksOut)
async def set_enabled(
    data: AdLinksSettingsIn, admin: CurrentAdmin, db: DbSession, request: Request
) -> AdLinksOut:
    config = await _config(db)
    config.ad_links_enabled = data.enabled
    _audit(db, admin, "ad_links.toggle", request, enabled=data.enabled)
    await db.flush()
    await db.commit()  # видно другим сессиям ДО pub/sub-уведомления бота
    await bus.publish(bus.CMD_RELOAD)
    return await _listing(db)


async def _save(db: DbSession, link: AdLink, data: AdLinkIn) -> None:
    prefix = _parse_prefix(data.prefix)
    taken = await db.scalar(
        select(AdLink.id).where(AdLink.prefix == prefix, AdLink.id != (link.id or 0))
    )
    if taken is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "Такой префикс уже занят")
    link.name = data.name.strip()
    link.prefix = prefix
    link.percent = data.percent
    link.owner_telegram_id, link.owner_username = _parse_owner(data.owner)
    link.is_active = data.is_active
    link.payout_mode = data.payout_mode
    await _resolve_owner(db, link)


@router.post("", response_model=AdLinksOut, status_code=status.HTTP_201_CREATED)
async def create_link(
    data: AdLinkIn, admin: CurrentAdmin, db: DbSession, request: Request
) -> AdLinksOut:
    link = AdLink(created_at=datetime.now(UTC))
    await _save(db, link, data)
    db.add(link)
    _audit(db, admin, "ad_links.create", request, prefix=link.prefix)
    await db.flush()
    await db.commit()
    return await _listing(db)


@router.put("/{link_id}", response_model=AdLinksOut)
async def update_link(
    link_id: int, data: AdLinkIn, admin: CurrentAdmin, db: DbSession, request: Request
) -> AdLinksOut:
    link = await db.get(AdLink, link_id)
    if link is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Ссылка не найдена")
    await _save(db, link, data)
    _audit(db, admin, "ad_links.update", request, prefix=link.prefix)
    await db.flush()
    await db.commit()
    return await _listing(db)


@router.post("/{link_id}/payout", response_model=AdLinksOut)
async def payout(
    link_id: int, admin: CurrentAdmin, db: DbSession, request: Request
) -> AdLinksOut:
    """«Выплатил»: обнуляет сумму к выплате. В режиме «на баланс» ту же
    сумму (но не больше остатка) списывает с баланса владельца в боте —
    иначе он получил бы деньги дважды."""
    link = await db.get(AdLink, link_id)
    if link is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Ссылка не найдена")
    earned = await db.scalar(
        select(func.coalesce(func.sum(AdEarning.commission_kopeks), 0)).where(
            AdEarning.ad_link_id == link.id
        )
    )
    owed = max((earned or 0) - link.paid_out_kopeks, 0)
    if owed <= 0:
        raise HTTPException(status.HTTP_409_CONFLICT, "К выплате ничего нет")

    debited = 0
    if link.payout_mode == "balance" and link.owner_telegram_id is not None:
        wallet = await db.scalar(
            select(Wallet)
            .join(BotUser, BotUser.id == Wallet.user_id)
            .where(BotUser.telegram_id == link.owner_telegram_id)
            .with_for_update()
        )
        if wallet is not None:
            debited = min(wallet.balance_kopeks, owed)
            if debited > 0:
                wallet.balance_kopeks -= debited
                db.add(
                    WalletTransaction(
                        wallet_id=wallet.id,
                        amount_kopeks=-debited,
                        type=WalletTxType.ADMIN_ADJUST,
                        description=f"Выплата по рекламной ссылке «{link.name}»",
                    )
                )

    link.paid_out_kopeks += owed
    _audit(
        db, admin, "ad_links.payout", request,
        prefix=link.prefix, amount_kopeks=owed, debited_kopeks=debited,
    )
    await db.flush()
    await db.commit()
    return await _listing(db)


@router.delete("/{link_id}", response_model=AdLinksOut)
async def delete_link(
    link_id: int, admin: CurrentAdmin, db: DbSession, request: Request
) -> AdLinksOut:
    link = await db.get(AdLink, link_id)
    if link is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Ссылка не найдена")
    _audit(db, admin, "ad_links.delete", request, prefix=link.prefix)
    await db.delete(link)
    await db.flush()
    await db.commit()
    return await _listing(db)
