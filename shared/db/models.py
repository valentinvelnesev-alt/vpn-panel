from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from shared.db.base import Base, TimestampMixin


class Admin(Base, TimestampMixin):
    """Администратор панели. В Free-редакции разрешён только один."""

    __tablename__ = "admins"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    login: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    is_owner: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    # 2FA (TOTP). В режиме ip панель требует его включить.
    totp_secret: Mapped[str | None] = mapped_column(String(64), default=None)
    totp_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    last_login_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None
    )

    refresh_tokens: Mapped[list["RefreshToken"]] = relationship(
        back_populates="admin", cascade="all, delete-orphan"
    )


class RefreshToken(Base, TimestampMixin):
    """Refresh-токен сессии. Хранится хэш — утечка БД не даёт войти."""

    __tablename__ = "refresh_tokens"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    admin_id: Mapped[int] = mapped_column(
        ForeignKey("admins.id", ondelete="CASCADE"), nullable=False, index=True
    )
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None
    )
    user_agent: Mapped[str | None] = mapped_column(String(255), default=None)
    ip: Mapped[str | None] = mapped_column(String(45), default=None)

    admin: Mapped[Admin] = relationship(back_populates="refresh_tokens")


class AuditLog(Base):
    """Журнал действий: кто, что и когда менял в панели."""

    __tablename__ = "audit_log"
    __table_args__ = (Index("ix_audit_log_created_at_desc", "created_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    admin_id: Mapped[int | None] = mapped_column(
        ForeignKey("admins.id", ondelete="SET NULL"), default=None
    )
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    target: Mapped[str | None] = mapped_column(String(128), default=None)
    details: Mapped[dict[str, Any] | None] = mapped_column(JSON, default=None)
    ip: Mapped[str | None] = mapped_column(String(45), default=None)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


class Setting(Base, TimestampMixin):
    """Конфигурация панели: подключение к Remnawave, бренд, бот и прочее.

    Ключ-значение вместо .env — пользователь правит всё через UI, без ssh.
    Секреты (токены, ключи API) шифруются Fernet перед записью: колонка
    `is_secret` помечает такие записи, наружу они отдаются маскированными.
    """

    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str | None] = mapped_column(Text, default=None)
    is_secret: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)


# ══ Telegram-бот ══════════════════════════════════════════════════════
class EmojiMode(StrEnum):
    """Набор эмодзи в сообщениях бота.

    PREMIUM использует кастомные анимированные эмодзи Telegram. Они
    работают, только если у аккаунта, создавшего бота в @BotFather,
    активен Telegram Premium, — панель проверяет это перед включением.
    """

    PLAIN = "plain"
    PREMIUM = "premium"


class BotState(StrEnum):
    STOPPED = "stopped"
    RUNNING = "running"
    ERROR = "error"


class BotConfig(Base, TimestampMixin):
    """Единственная строка с настройками бота — правится из панели.

    Токен хранится зашифрованным. Смена токена не требует перезапуска
    контейнера: панель шлёт боту команду через Redis.
    """

    __tablename__ = "bot_config"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)

    token_encrypted: Mapped[str | None] = mapped_column(Text, default=None)
    bot_username: Mapped[str | None] = mapped_column(String(64), default=None)
    bot_name: Mapped[str | None] = mapped_column(String(128), default=None)
    bot_id: Mapped[int | None] = mapped_column(BigInteger, default=None)

    enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    state: Mapped[BotState] = mapped_column(
        String(16), default=BotState.STOPPED, nullable=False
    )
    state_message: Mapped[str | None] = mapped_column(Text, default=None)
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None
    )

    emoji_mode: Mapped[EmojiMode] = mapped_column(
        String(16), default=EmojiMode.PLAIN, nullable=False
    )
    # Результат последней проверки Premium — чтобы UI объяснял отказ.
    premium_checked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None
    )
    premium_available: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False
    )
    # «иконка → id премиум-эмодзи». Пусто = премиум-режим рисует обычные
    # символы: универсальных id не существует, их задаёт владелец панели.
    premium_emoji: Mapped[dict[str, str]] = mapped_column(
        JSON, default=dict, nullable=False
    )

    # Приветствие, поддержка, обязательная подписка на канал.
    welcome_text: Mapped[str | None] = mapped_column(Text, default=None)
    support_url: Mapped[str | None] = mapped_column(String(255), default=None)
    channel_url: Mapped[str | None] = mapped_column(String(255), default=None)
    channel_id: Mapped[str | None] = mapped_column(String(64), default=None)
    require_channel_sub: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False
    )

    trial_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    trial_days: Mapped[int] = mapped_column(Integer, default=3, nullable=False)
    trial_squad_uuids: Mapped[list[str]] = mapped_column(
        JSON, default=list, nullable=False
    )
    trial_hwid_limit: Mapped[int] = mapped_column(Integer, default=3, nullable=False)

    referral_enabled: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False
    )
    # Дни, которые получает пригласивший, когда приглашённый совершает
    # первую оплату (не за сам факт регистрации — иначе это открывает
    # накрутку пустыми аккаунтами).
    referral_reward_days: Mapped[int] = mapped_column(
        Integer, default=3, nullable=False
    )
    # Бонусные дни самому приглашённому за переход по ссылке.
    referral_bonus_days: Mapped[int] = mapped_column(
        Integer, default=1, nullable=False
    )

    # Денежная 2-уровневая комиссия рефереру на баланс — начисляется с
    # КАЖДОЙ оплаты приглашённого (в отличие от разового referral_reward_days
    # за первую покупку). Уровень 2 — комиссия того, кто пригласил реферера.
    referral_commission_enabled: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False
    )
    referral_level1_percent: Mapped[int] = mapped_column(
        Integer, default=25, nullable=False
    )
    referral_level2_percent: Mapped[int] = mapped_column(
        Integer, default=5, nullable=False
    )

    # Уведомление о каждой продаже в отдельный чат/группу (бот должен
    # состоять в ней участником). 0/None — уведомления отключены.
    purchase_notify_chat_id: Mapped[int | None] = mapped_column(
        BigInteger, default=None
    )

    # Алерты о падении нод (Pro) — шлются этому чату из бота.
    node_alerts_enabled: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False
    )
    node_alerts_chat_id: Mapped[int | None] = mapped_column(BigInteger, default=None)


class PlanCategory(Base, TimestampMixin):
    """Категория тарифов (например «VPN», «VPN + LTE») — заводится в панели.

    Раньше в боте типы тарифов были константами в коде (VPN / VPN+LTE).
    Здесь админ сам создаёт, переименовывает и удаляет любые категории —
    бот показывает их вкладками в меню тарифов.
    """

    __tablename__ = "bot_plan_categories"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    title: Mapped[str] = mapped_column(String(64), nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    plans: Mapped[list["Plan"]] = relationship(back_populates="category")


class Plan(Base, TimestampMixin):
    """Тариф в боте: срок, цена, сквады Remnawave и лимит устройств.

    В старом боте всё это было константами в config.py, а squad-UUID —
    захардкожены в коде. Здесь каждый тариф настраивается из панели.
    """

    __tablename__ = "bot_plans"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    title: Mapped[str] = mapped_column(String(64), nullable=False)
    days: Mapped[int] = mapped_column(Integer, nullable=False)
    price_kopeks: Mapped[int] = mapped_column(Integer, nullable=False)

    squad_uuids: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    hwid_limit: Mapped[int] = mapped_column(Integer, default=3, nullable=False)
    traffic_limit_bytes: Mapped[int] = mapped_column(
        BigInteger, default=0, nullable=False
    )

    # Не обязательна: тариф без категории просто не попадает ни в одну
    # вкладку и показывается в общем списке (для панелей с одной категорией).
    category_id: Mapped[int | None] = mapped_column(
        ForeignKey("bot_plan_categories.id", ondelete="SET NULL"), default=None
    )
    category: Mapped[PlanCategory | None] = relationship(back_populates="plans")

    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    @property
    def price_rub(self) -> float:
        return self.price_kopeks / 100


class BotUser(Base, TimestampMixin):
    """Пользователь бота. Связь с Remnawave — по uuid, выданному при покупке.

    Привязка идёт к telegram_id, поэтому смена токена бота не теряет базу
    клиентов: тот же человек в новом боте остаётся тем же пользователем.
    """

    __tablename__ = "bot_users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    telegram_id: Mapped[int] = mapped_column(
        BigInteger, unique=True, nullable=False, index=True
    )
    username: Mapped[str | None] = mapped_column(String(64), default=None)
    first_name: Mapped[str | None] = mapped_column(String(128), default=None)
    language_code: Mapped[str | None] = mapped_column(String(8), default=None)

    remnawave_uuid: Mapped[str | None] = mapped_column(String(64), default=None)
    subscription_url: Mapped[str | None] = mapped_column(Text, default=None)
    expire_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None
    )

    trial_used: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_blocked: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # Пользователь заблокировал бота — рассылка на него не тратится.
    has_stopped_bot: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False
    )
    last_seen_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None
    )

    # Свой код для реферальной ссылки — стабилен даже после смены бота.
    referral_code: Mapped[str | None] = mapped_column(
        String(16), unique=True, default=None
    )
    referred_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("bot_users.id", ondelete="SET NULL"), default=None
    )
    referral_reward_paid: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False
    )

    auto_renew_enabled: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False
    )
    auto_renew_plan_id: Mapped[int | None] = mapped_column(
        ForeignKey("bot_plans.id", ondelete="SET NULL"), default=None
    )

    purchases: Mapped[list["Purchase"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )


class Purchase(Base, TimestampMixin):
    """Факт выдачи подписки: покупка, триал или ручная выдача из панели."""

    __tablename__ = "bot_purchases"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("bot_users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    plan_id: Mapped[int | None] = mapped_column(
        ForeignKey("bot_plans.id", ondelete="SET NULL"), default=None
    )

    days: Mapped[int] = mapped_column(Integer, nullable=False)
    amount_kopeks: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    expire_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None
    )

    user: Mapped[BotUser] = relationship(back_populates="purchases")


class ExpiryNotification(Base):
    """Отметка об отправленном напоминании — защита от повторов.

    Уникальность по (пользователь, окно, дата истечения): продлил подписку —
    появится новая дата, и напоминания придут заново.
    """

    __tablename__ = "bot_expiry_notifications"
    __table_args__ = (
        UniqueConstraint(
            "user_id", "window", "expire_at", name="uq_expiry_notification"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("bot_users.id", ondelete="CASCADE"), nullable=False
    )
    window: Mapped[str] = mapped_column(String(16), nullable=False)
    expire_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    sent_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


# ══ Монетизация ═══════════════════════════════════════════════════════
class PaymentProvider(StrEnum):
    PLATEGA = "platega"
    ROLLYPAY = "rollypay"
    CRYPTOBOT = "cryptobot"
    STARS = "stars"


class PaymentPurpose(StrEnum):
    TOPUP = "topup"
    PLAN = "plan"


class PaymentStatus(StrEnum):
    PENDING = "pending"
    PAID = "paid"
    FAILED = "failed"
    EXPIRED = "expired"


class WalletTxType(StrEnum):
    TOPUP = "topup"
    PURCHASE = "purchase"
    REFUND = "refund"
    REFERRAL_REWARD = "referral_reward"
    ADMIN_ADJUST = "admin_adjust"
    AUTO_RENEWAL = "auto_renewal"


class Wallet(Base, TimestampMixin):
    """Внутренний баланс пользователя бота — в копейках, без учёта валют."""

    __tablename__ = "bot_wallets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("bot_users.id", ondelete="CASCADE"), unique=True, nullable=False
    )
    balance_kopeks: Mapped[int] = mapped_column(Integer, default=0, nullable=False)


class WalletTransaction(Base, TimestampMixin):
    """Движение по балансу — как выписка, а не просто текущее число."""

    __tablename__ = "bot_wallet_transactions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    wallet_id: Mapped[int] = mapped_column(
        ForeignKey("bot_wallets.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # Положительное — пополнение, отрицательное — списание.
    amount_kopeks: Mapped[int] = mapped_column(Integer, nullable=False)
    type: Mapped[WalletTxType] = mapped_column(String(24), nullable=False)
    description: Mapped[str | None] = mapped_column(String(255), default=None)


class Payment(Base, TimestampMixin):
    """Внешняя оплата: пополнение кошелька или прямая покупка тарифа.

    external_id — идентификатор транзакции у провайдера, по нему находим
    платёж, когда приходит вебхук.
    """

    __tablename__ = "bot_payments"
    __table_args__ = (
        UniqueConstraint("provider", "external_id", name="uq_payment_external"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("bot_users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    provider: Mapped[PaymentProvider] = mapped_column(String(16), nullable=False)
    external_id: Mapped[str] = mapped_column(String(128), nullable=False)

    amount_kopeks: Mapped[int] = mapped_column(Integer, nullable=False)
    purpose: Mapped[PaymentPurpose] = mapped_column(String(16), nullable=False)
    plan_id: Mapped[int | None] = mapped_column(
        ForeignKey("bot_plans.id", ondelete="SET NULL"), default=None
    )
    status: Mapped[PaymentStatus] = mapped_column(
        String(16), default=PaymentStatus.PENDING, nullable=False
    )
    paid_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None
    )
    # Момент, когда бот применил оплату (зачислил/продлил). Отдельно от
    # paid_at: если Redis-уведомление потерялось, воркер бота находит все
    # payment.status=paid c applied_at IS NULL и обрабатывает их сам —
    # деньги не должны теряться из-за недоставленного pub/sub-сообщения.
    applied_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None
    )
    # Сырое тело последнего колбэка провайдера — пригодится для разбора спорных случаев.
    raw_payload: Mapped[dict | None] = mapped_column(JSON, default=None)


class PromoCode(Base, TimestampMixin):
    """Промокод: бонусные дни или скидка на покупку тарифа."""

    __tablename__ = "bot_promo_codes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)

    bonus_days: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    discount_percent: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    max_uses: Mapped[int | None] = mapped_column(Integer, default=None)
    uses_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class PromoCodeActivation(Base):
    """Факт использования — один промокод на пользователя, не больше."""

    __tablename__ = "bot_promo_code_activations"
    __table_args__ = (
        UniqueConstraint("promo_code_id", "user_id", name="uq_promo_activation"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    promo_code_id: Mapped[int] = mapped_column(
        ForeignKey("bot_promo_codes.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[int] = mapped_column(
        ForeignKey("bot_users.id", ondelete="CASCADE"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


class ReferralReward(Base):
    """Начисление рефереру — по факту первой оплаты приглашённого."""

    __tablename__ = "bot_referral_rewards"
    __table_args__ = (
        UniqueConstraint("referred_user_id", name="uq_referral_reward_once"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    referrer_user_id: Mapped[int] = mapped_column(
        ForeignKey("bot_users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    referred_user_id: Mapped[int] = mapped_column(
        ForeignKey("bot_users.id", ondelete="CASCADE"), nullable=False
    )
    days: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


# ══ Рассылки ═══════════════════════════════════════════════════════════
class BroadcastSegment(StrEnum):
    ALL = "all"
    ACTIVE = "active"
    EXPIRED = "expired"
    NO_PURCHASE = "no_purchase"


class BroadcastStatus(StrEnum):
    SCHEDULED = "scheduled"
    SENDING = "sending"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class Broadcast(Base):
    """Рассылка: текст/фото/кнопки, сегмент получателей, план или факт отправки.

    Отправляет бот (у него живой Bot-инстанс), панель только заводит запись
    и читает прогресс — так разделение обязанностей остаётся тем же, что и
    у платежей.
    """

    __tablename__ = "broadcasts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    photo_url: Mapped[str | None] = mapped_column(String(512), default=None)
    buttons: Mapped[list[dict]] = mapped_column(JSON, default=list, nullable=False)
    segment: Mapped[BroadcastSegment] = mapped_column(String(16), nullable=False)

    status: Mapped[BroadcastStatus] = mapped_column(
        String(16), default=BroadcastStatus.SCHEDULED, nullable=False
    )
    scheduled_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )

    total_recipients: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    sent_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    failed_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    created_by_admin_id: Mapped[int | None] = mapped_column(
        ForeignKey("admins.id", ondelete="SET NULL"), default=None
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None
    )
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None
    )
