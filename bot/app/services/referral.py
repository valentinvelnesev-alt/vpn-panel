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
from shared.db.models import BotUser, ReferralReward

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
