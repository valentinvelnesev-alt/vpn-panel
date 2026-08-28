"""Конфигурация бота, прочитанная из БД.

Ни токена, ни цен, ни текстов в .env нет — всё правится в панели, поэтому
конфиг это снимок строки bot_config плюс список тарифов.
"""

from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from shared.crypto import decrypt
from shared.db.models import BotConfig, EmojiMode
from shared.db.models import Plan as PlanRow


@dataclass(frozen=True, slots=True)
class PlanView:
    id: int
    title: str
    days: int
    price_kopeks: int
    squad_uuids: list[str]
    hwid_limit: int
    traffic_limit_bytes: int
    category_id: int | None = None
    category_title: str | None = None

    @property
    def price_rub(self) -> float:
        return self.price_kopeks / 100


def plan_view(row: PlanRow) -> PlanView:
    """Строит PlanView из любой строки Plan, включая уже отключённые —
    начатая оплата должна довестись до конца на изначальных условиях."""
    return PlanView(
        id=row.id,
        title=row.title,
        days=row.days,
        price_kopeks=row.price_kopeks,
        squad_uuids=list(row.squad_uuids or []),
        hwid_limit=row.hwid_limit,
        traffic_limit_bytes=row.traffic_limit_bytes,
        category_id=row.category_id,
        category_title=row.category.title if row.category else None,
    )


@dataclass(frozen=True, slots=True)
class Config:
    token: str | None
    enabled: bool
    emoji_mode: EmojiMode
    premium_emoji: dict[str, str]

    brand: str
    welcome_text: str | None
    support_url: str | None
    channel_url: str | None
    channel_id: str | None
    require_channel_sub: bool

    trial_enabled: bool
    trial_days: int
    trial_squad_uuids: list[str]
    trial_hwid_limit: int

    referral_enabled: bool = False
    referral_reward_days: int = 3
    referral_bonus_days: int = 1

    referral_commission_enabled: bool = False
    referral_level1_percent: int = 25
    referral_level2_percent: int = 5

    purchase_notify_chat_id: int | None = None
    admin_telegram_ids: list[int] = field(default_factory=list)

    privacy_policy_url: str | None = None
    terms_url: str | None = None

    plans: list[PlanView] = field(default_factory=list)

    remnawave_url: str | None = None
    remnawave_token: str | None = None

    platega_enabled: bool = False
    platega_merchant_id: str | None = None
    platega_secret: str | None = None
    rollypay_enabled: bool = False
    rollypay_api_key: str | None = None
    cryptobot_enabled: bool = False
    cryptobot_token: str | None = None
    stars_enabled: bool = False

    loaded_at: datetime | None = None

    @property
    def can_run(self) -> bool:
        return bool(self.enabled and self.token)


async def load(db: AsyncSession) -> Config:
    row = await db.get(BotConfig, 1)
    if row is None:
        row = BotConfig(id=1)

    from sqlalchemy.orm import selectinload

    plans = await db.scalars(
        select(PlanRow)
        .options(selectinload(PlanRow.category))
        .where(PlanRow.is_active.is_(True))
        .order_by(PlanRow.sort_order, PlanRow.days)
    )

    from shared.db.models import Setting

    keys = [
        "remnawave_url",
        "remnawave_token",
        "payment_platega_enabled",
        "payment_platega_merchant_id",
        "payment_platega_secret",
        "payment_rollypay_enabled",
        "payment_rollypay_api_key",
        "payment_cryptobot_enabled",
        "payment_cryptobot_token",
        "payment_stars_enabled",
    ]
    settings_rows = await db.scalars(select(Setting).where(Setting.key.in_(keys)))
    raw = {
        s.key: (decrypt(s.value) if s.is_secret and s.value else s.value)
        for s in settings_rows
    }

    def flag(key: str) -> bool:
        return raw.get(key) == "true"

    return Config(
        token=decrypt(row.token_encrypted) if row.token_encrypted else None,
        enabled=row.enabled,
        emoji_mode=EmojiMode(row.emoji_mode),
        premium_emoji=row.premium_emoji or {},
        brand=(row.bot_name or "VPN"),
        welcome_text=row.welcome_text,
        support_url=row.support_url,
        channel_url=row.channel_url,
        channel_id=row.channel_id,
        require_channel_sub=row.require_channel_sub,
        trial_enabled=row.trial_enabled,
        trial_days=row.trial_days,
        trial_squad_uuids=list(row.trial_squad_uuids or []),
        trial_hwid_limit=row.trial_hwid_limit,
        referral_enabled=row.referral_enabled,
        referral_reward_days=row.referral_reward_days,
        referral_bonus_days=row.referral_bonus_days,
        referral_commission_enabled=row.referral_commission_enabled,
        referral_level1_percent=row.referral_level1_percent,
        referral_level2_percent=row.referral_level2_percent,
        purchase_notify_chat_id=row.purchase_notify_chat_id,
        admin_telegram_ids=list(row.admin_telegram_ids or []),
        privacy_policy_url=row.privacy_policy_url,
        terms_url=row.terms_url,
        plans=[plan_view(p) for p in plans],
        remnawave_url=raw.get("remnawave_url"),
        remnawave_token=raw.get("remnawave_token"),
        platega_enabled=flag("payment_platega_enabled"),
        platega_merchant_id=raw.get("payment_platega_merchant_id"),
        platega_secret=raw.get("payment_platega_secret"),
        rollypay_enabled=flag("payment_rollypay_enabled"),
        rollypay_api_key=raw.get("payment_rollypay_api_key"),
        cryptobot_enabled=flag("payment_cryptobot_enabled"),
        cryptobot_token=raw.get("payment_cryptobot_token"),
        stars_enabled=flag("payment_stars_enabled"),
        loaded_at=datetime.now(),
    )
