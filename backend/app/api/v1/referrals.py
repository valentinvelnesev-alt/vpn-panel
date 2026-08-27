from datetime import UTC, datetime

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from app.api.deps import CurrentAdmin, DbSession
from shared.db.models import AuditLog, BotConfig, BotUser, ReferralReward

router = APIRouter(prefix="/bot/referral", tags=["referral"])


class ReferralSettingsIn(BaseModel):
    referral_enabled: bool = False
    referral_reward_days: int = Field(default=3, ge=0, le=365)
    referral_bonus_days: int = Field(default=1, ge=0, le=365)

    # Денежная 2-уровневая комиссия на баланс — начисляется с каждой оплаты
    # приглашённого, независимо от разового бонуса в днях выше.
    referral_commission_enabled: bool = False
    referral_level1_percent: int = Field(default=25, ge=0, le=100)
    referral_level2_percent: int = Field(default=5, ge=0, le=100)


class ReferralSettingsOut(ReferralSettingsIn):
    pass


async def _config(db: DbSession) -> BotConfig:
    row = await db.get(BotConfig, 1)
    if row is None:
        row = BotConfig(id=1)
        db.add(row)
        await db.flush()
    return row


@router.get("", response_model=ReferralSettingsOut)
async def get_settings(admin: CurrentAdmin, db: DbSession) -> ReferralSettingsOut:
    row = await _config(db)
    return ReferralSettingsOut(
        referral_enabled=row.referral_enabled,
        referral_reward_days=row.referral_reward_days,
        referral_bonus_days=row.referral_bonus_days,
        referral_commission_enabled=row.referral_commission_enabled,
        referral_level1_percent=row.referral_level1_percent,
        referral_level2_percent=row.referral_level2_percent,
    )


@router.put("", response_model=ReferralSettingsOut)
async def save_settings(
    data: ReferralSettingsIn, admin: CurrentAdmin, db: DbSession, request: Request
) -> ReferralSettingsOut:
    row = await _config(db)
    row.referral_enabled = data.referral_enabled
    row.referral_reward_days = data.referral_reward_days
    row.referral_bonus_days = data.referral_bonus_days
    row.referral_commission_enabled = data.referral_commission_enabled
    row.referral_level1_percent = data.referral_level1_percent
    row.referral_level2_percent = data.referral_level2_percent

    db.add(
        AuditLog(
            admin_id=admin.id,
            action="referral.settings",
            ip=request.client.host if request.client else None,
            created_at=datetime.now(UTC),
        )
    )
    from shared import bus

    await db.commit()  # видно другим сессиям ДО pub/sub-уведомления бота
    await bus.publish(bus.CMD_RELOAD)
    return await get_settings(admin, db)


class TopReferrer(BaseModel):
    telegram_id: int
    username: str | None
    invited: int
    rewarded_days: int


class ReferralStatsOut(BaseModel):
    total_referred: int
    total_rewards_days: int
    top: list[TopReferrer]


@router.get("/stats", response_model=ReferralStatsOut)
async def stats(admin: CurrentAdmin, db: DbSession) -> ReferralStatsOut:
    total_referred = await db.scalar(
        select(func.count()).select_from(BotUser).where(BotUser.referred_by_id.is_not(None))
    ) or 0
    total_days = await db.scalar(
        select(func.coalesce(func.sum(ReferralReward.days), 0))
    ) or 0

    referrer_counts = await db.execute(
        select(
            BotUser.referred_by_id,
            func.count(BotUser.id).label("invited"),
        )
        .where(BotUser.referred_by_id.is_not(None))
        .group_by(BotUser.referred_by_id)
        .order_by(func.count(BotUser.id).desc())
        .limit(10)
    )
    top: list[TopReferrer] = []
    for referrer_id, invited in referrer_counts:
        referrer = await db.get(BotUser, referrer_id)
        if referrer is None:
            continue
        rewarded = await db.scalar(
            select(func.coalesce(func.sum(ReferralReward.days), 0)).where(
                ReferralReward.referrer_user_id == referrer_id
            )
        )
        top.append(
            TopReferrer(
                telegram_id=referrer.telegram_id,
                username=referrer.username,
                invited=invited,
                rewarded_days=rewarded or 0,
            )
        )

    return ReferralStatsOut(
        total_referred=total_referred, total_rewards_days=total_days, top=top
    )
