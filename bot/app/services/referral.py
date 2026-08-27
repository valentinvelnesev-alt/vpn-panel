"""Реферальная программа: код за пользователем, бонус за первую оплату.

Награда рефереру начисляется не за регистрацию приглашённого, а за его
первую покупку — иначе накрутка пустыми аккаунтами превращается в бесплатные
дни. Уникальный индекс на referred_user_id не даёт начислить дважды.
"""

import logging
import secrets
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Config
from app.services import wallet
from shared.db.models import BotUser, ReferralReward, WalletTxType

log = logging.getLogger("bot.referral")

_ALPHABET = "23456789ABCDEFGHJKLMNPQRSTUVWXYZ"  # без похожих символов


async def ensure_code(db: AsyncSession, user: BotUser) -> str:
    if user.referral_code:
        return user.referral_code
    for _ in range(10):
        candidate = "".join(secrets.choice(_ALPHABET) for _ in range(6))
        exists = await db.scalar(
            select(BotUser.id).where(BotUser.referral_code == candidate)
        )
        if exists is None:
            user.referral_code = candidate
            await db.flush()
            return candidate
    raise RuntimeError("не удалось подобрать свободный реферальный код")


async def attach_referrer(
    db: AsyncSession, config: Config, user: BotUser, referrer_code: str
) -> BotUser | None:
    """Привязывает нового пользователя к пригласившему.

    Только для новых: у пользователя, который уже что-то делал в боте,
    менять реферера поздно и небезопасно (пришёл бы за бонусом задним числом).
    """
    if not config.referral_enabled or user.referred_by_id is not None:
        return None
    if user.trial_used or user.expire_at is not None:
        return None  # уже не «новый» пользователь

    referrer = await db.scalar(
        select(BotUser).where(BotUser.referral_code == referrer_code.strip().upper())
    )
    if referrer is None or referrer.id == user.id:
        return None

    user.referred_by_id = referrer.id
    log.info("Пользователь %s пришёл по ссылке %s", user.telegram_id, referrer_code)
    return referrer


async def reward_if_first_purchase(
    db: AsyncSession, config: Config, user: BotUser
) -> ReferralReward | None:
    """Вызывается после успешной оплаты. Начисляет дни рефереру один раз."""
    if not config.referral_enabled:
        return None
    if user.referred_by_id is None or user.referral_reward_paid:
        return None

    reward = ReferralReward(
        referrer_user_id=user.referred_by_id,
        referred_user_id=user.id,
        days=config.referral_reward_days,
        created_at=datetime.now(UTC),
    )
    try:
        async with db.begin_nested():
            db.add(reward)
            await db.flush()
    except IntegrityError:
        return None  # уже начислено — например, гонка двух вебхуков

    user.referral_reward_paid = True
    return reward


async def award_commission(
    db: AsyncSession, config: Config, user: BotUser, amount_kopeks: int
) -> list[tuple[BotUser, int]]:
    """Денежная 2-уровневая комиссия на баланс — с КАЖДОЙ оплаты (не только
    первой). Уровень 1 — прямой пригласивший, уровень 2 — тот, кто пригласил
    его. Комиссия не начисляется за пополнение баланса, только за тариф —
    вызывающий код решает это, передавая сюда только суммы покупок.

    Возвращает список (реферер, начислено копеек) — для уведомлений в боте.
    """
    if not config.referral_commission_enabled or amount_kopeks <= 0:
        return []
    if user.referred_by_id is None:
        return []

    awarded: list[tuple[BotUser, int]] = []

    level1 = await db.get(BotUser, user.referred_by_id)
    if level1 is not None and config.referral_level1_percent > 0:
        share = amount_kopeks * config.referral_level1_percent // 100
        if share > 0:
            await wallet.credit(
                db,
                level1,
                share,
                WalletTxType.REFERRAL_REWARD,
                description=f"Комиссия 1 уровня с покупки {user.telegram_id}",
            )
            awarded.append((level1, share))

        if level1.referred_by_id is not None and config.referral_level2_percent > 0:
            level2 = await db.get(BotUser, level1.referred_by_id)
            share2 = amount_kopeks * config.referral_level2_percent // 100
            if level2 is not None and share2 > 0:
                await wallet.credit(
                    db,
                    level2,
                    share2,
                    WalletTxType.REFERRAL_REWARD,
                    description=f"Комиссия 2 уровня с покупки {user.telegram_id}",
                )
                awarded.append((level2, share2))

    return awarded
