"""Рекламные ссылки: привязка пришедшего пользователя и доля владельцу.

Ссылка t.me/bot?start=<prefix> закрепляет нового пользователя за ссылкой
(первое касание, как у рефералки). С каждой оплаты тарифа такого
пользователя владельцу ссылки идёт процент на баланс. Владелец задан
Telegram ID или @username; доля того, кого бот ещё не видел, копится и
зачисляется, как только он появится.
"""

import logging
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Config
from app.services import wallet
from shared.db.models import AdEarning, AdLink, BotUser, WalletTxType

log = logging.getLogger("bot.adlinks")


async def attach(db: AsyncSession, config: Config, user: BotUser, payload: str) -> bool:
    """Закрепляет нового пользователя за рекламной ссылкой по /start-параметру."""
    if not config.ad_links_enabled or user.ad_link_id is not None:
        return False
    if user.trial_used or user.expire_at is not None or user.referred_by_id is not None:
        return False  # уже не «новый» пользователь

    link = await db.scalar(
        select(AdLink).where(AdLink.prefix == payload.strip().lower(), AdLink.is_active)
    )
    if link is None:
        return False
    user.ad_link_id = link.id
    log.info("Пользователь %s пришёл по рекламной ссылке %s", user.telegram_id, link.prefix)
    return True


async def _owner(db: AsyncSession, link: AdLink) -> BotUser | None:
    owner = None
    if link.owner_telegram_id is not None:
        owner = await db.scalar(
            select(BotUser).where(BotUser.telegram_id == link.owner_telegram_id)
        )
    elif link.owner_username:
        owner = await db.scalar(
            select(BotUser).where(func.lower(BotUser.username) == link.owner_username.lower())
        )
        if owner is not None:
            link.owner_telegram_id = owner.telegram_id
    return owner


async def _settle(db: AsyncSession, link: AdLink, owner: BotUser) -> int:
    """Зачисляет на баланс владельца накопленные, но не выплаченные доли."""
    pending = list(
        await db.scalars(
            select(AdEarning).where(AdEarning.ad_link_id == link.id, AdEarning.paid.is_(False))
        )
    )
    total = sum(e.commission_kopeks for e in pending)
    if total > 0:
        await wallet.credit(
            db,
            owner,
            total,
            WalletTxType.REFERRAL_REWARD,
            description=f"Рекламная ссылка «{link.name}»",
        )
    for earning in pending:
        earning.paid = True
    return total


async def credit_commission(
    db: AsyncSession, config: Config, user: BotUser, amount_kopeks: int
) -> list[tuple[BotUser, int]]:
    """Доля владельцу ссылки с оплаты тарифа. Возвращает [(владелец, копейки)]
    для уведомления — пусто, если зачислять некому или ещё не время."""
    if not config.ad_links_enabled or user.ad_link_id is None or amount_kopeks <= 0:
        return []
    link = await db.get(AdLink, user.ad_link_id)
    if link is None or not link.is_active or link.percent <= 0:
        return []
    share = amount_kopeks * link.percent // 100
    if share <= 0:
        return []

    db.add(
        AdEarning(
            ad_link_id=link.id,
            buyer_user_id=user.id,
            amount_kopeks=amount_kopeks,
            commission_kopeks=share,
            paid=False,
            created_at=datetime.now(UTC),
        )
    )
    await db.flush()
    owner = await _owner(db, link)
    if owner is None:
        return []
    await _settle(db, link, owner)
    return [(owner, share)]


async def owner_links(
    db: AsyncSession, config: Config, telegram_id: int, username: str | None
) -> list[AdLink]:
    """Активные ссылки пользователя. Заодно привязывает владельца, заданного
    по @username, к его Telegram ID и зачисляет накопленное."""
    if not config.ad_links_enabled:
        return []
    conditions = [AdLink.owner_telegram_id == telegram_id]
    if username:
        conditions.append(
            (AdLink.owner_telegram_id.is_(None))
            & (func.lower(AdLink.owner_username) == username.lower())
        )
    links = list(
        await db.scalars(
            select(AdLink).where(AdLink.is_active, or_(*conditions)).order_by(AdLink.id)
        )
    )
    if links:
        owner = await db.scalar(select(BotUser).where(BotUser.telegram_id == telegram_id))
        for link in links:
            if link.owner_telegram_id is None:
                link.owner_telegram_id = telegram_id
            if owner is not None:
                await _settle(db, link, owner)
    return links


@dataclass(frozen=True, slots=True)
class LinkStats:
    link: AdLink
    users: int
    buyers: int
    purchases: int
    turnover_kopeks: int
    earned_kopeks: int


async def stats(db: AsyncSession, links: list[AdLink]) -> list[LinkStats]:
    result = []
    for link in links:
        users = await db.scalar(
            select(func.count()).select_from(BotUser).where(BotUser.ad_link_id == link.id)
        )
        buyers, purchases, turnover, earned = (
            await db.execute(
                select(
                    func.count(func.distinct(AdEarning.buyer_user_id)),
                    func.count(AdEarning.id),
                    func.coalesce(func.sum(AdEarning.amount_kopeks), 0),
                    func.coalesce(func.sum(AdEarning.commission_kopeks), 0),
                ).where(AdEarning.ad_link_id == link.id)
            )
        ).one()
        result.append(
            LinkStats(link, users or 0, buyers or 0, purchases or 0, turnover, earned)
        )
    return result
