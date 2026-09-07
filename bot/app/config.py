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
from shared.db.models import TrafficPackage as TrafficPackageRow


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

    @property
    def full_title(self) -> str:
        """«VPN + LTE · Месяц» — тарифы с одинаковым названием в разных
        категориях иначе неразличимы на экране оплаты."""
        return f"{self.category_title} · {self.title}" if self.category_title else self.title


async def load_plan(db: AsyncSession, plan_id: int | None) -> PlanView | None:
    """Тариф по id вместе с категорией — включая отключённые.

    `db.get(Plan)` + `plan_view` не годится: `row.category` подгружался бы
    лениво уже из async-кода и падал с MissingGreenlet у любого тарифа с
    категорией — то есть при применении каждой оплаты."""
    if plan_id is None:
        return None
    from sqlalchemy.orm import selectinload

    row = await db.scalar(
        select(PlanRow).options(selectinload(PlanRow.category)).where(PlanRow.id == plan_id)
    )
    return plan_view(row) if row is not None else None


@dataclass(frozen=True, slots=True)
class TrafficPackageView:
    id: int
    title: str
    traffic_gb: int
    price_kopeks: int

    @property
    def traffic_bytes(self) -> int:
        return self.traffic_gb * 1024**3


def traffic_view(row: TrafficPackageRow) -> TrafficPackageView:
    return TrafficPackageView(
        id=row.id,
        title=row.title,
        traffic_gb=row.traffic_gb,
        price_kopeks=row.price_kopeks,
    )


async def load_traffic_package(db: AsyncSession, package_id: int | None):
    """Пакет по id, включая отключённый: начатая оплата должна довестись
    до конца на изначальных условиях."""
    if package_id is None:
        return None
    row = await db.get(TrafficPackageRow, package_id)
    return traffic_view(row) if row is not None else None


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


def discounted_kopeks(price_kopeks: int, discount_percent: int) -> int:
    """Цена со скидкой, не ниже 1 ₽ — нулевой счёт провайдеры не примут."""
    percent = max(0, min(int(discount_percent or 0), 100))
    if percent == 0:
        return price_kopeks
    return max(100, round(price_kopeks * (100 - percent) / 100))


def format_rub(kopeks: int) -> str:
    rub = kopeks / 100
    return f"{rub:.0f} ₽" if kopeks % 100 == 0 else f"{rub:.2f} ₽"


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
    traffic_packages: list[TrafficPackageView] = field(default_factory=list)

    remnawave_url: str | None = None
    remnawave_token: str | None = None
    remnawave_verify_tls: bool = True
    # Страница-редиректор развёрнута на домене подписок (её ставит владелец
    # на своём сервере Remnawave). Пока флага нет, «Подключиться» ведёт на
    # саму ссылку подписки — рабочее поведение по умолчанию.
    subscription_redirect: bool = False

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

    # Провайдер «готов», когда включён И заполнены реквизиты. Кнопка без
    # реквизитов вела в тупик «оплата сейчас недоступна».
    @property
    def platega_ready(self) -> bool:
        return bool(self.platega_enabled and self.platega_merchant_id and self.platega_secret)

    @property
    def rollypay_ready(self) -> bool:
        return bool(self.rollypay_enabled and self.rollypay_api_key)

    @property
    def cryptobot_ready(self) -> bool:
        return bool(self.cryptobot_enabled and self.cryptobot_token)

    @property
    def any_payment_ready(self) -> bool:
        return (
            self.platega_ready
            or self.rollypay_ready
            or self.cryptobot_ready
            or self.stars_enabled
        )

    @property
    def referral_visible(self) -> bool:
        return self.referral_enabled or self.referral_commission_enabled

    def plan(self, plan_id: int) -> PlanView | None:
        return next((p for p in self.plans if p.id == plan_id), None)

    def traffic_package(self, package_id: int) -> TrafficPackageView | None:
        return next((p for p in self.traffic_packages if p.id == package_id), None)


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

    packages = await db.scalars(
        select(TrafficPackageRow)
        .where(TrafficPackageRow.is_active.is_(True))
        .order_by(TrafficPackageRow.sort_order, TrafficPackageRow.traffic_gb)
    )

    from shared.db.models import Setting

    keys = [
        "remnawave_url",
        "remnawave_token",
        "remnawave_verify_tls",
        "subscription_redirect_enabled",
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
        traffic_packages=[traffic_view(p) for p in packages],
        remnawave_url=raw.get("remnawave_url"),
        remnawave_token=raw.get("remnawave_token"),
        remnawave_verify_tls=raw.get("remnawave_verify_tls") != "false",
        subscription_redirect=flag("subscription_redirect_enabled"),
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
