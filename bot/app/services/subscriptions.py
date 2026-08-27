"""Выдача и продление подписок: единая точка для триала, покупки и панели.

Пользователь Remnawave создаётся один раз и дальше только продлевается —
так у клиента не меняется ссылка подписки при каждой оплате.
"""

import logging
import secrets
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Config, PlanView
from app.services import referral
from app.services.notify import send as notify_send
from shared.db.models import BotSubscription, BotUser, Purchase
from shared.remnawave import RemnawaveClient, RemnawaveError

log = logging.getLogger("bot.subscriptions")


def client_for(config: Config) -> RemnawaveClient:
    if not config.remnawave_url or not config.remnawave_token:
        raise RemnawaveError("Remnawave не подключена в панели")
    return RemnawaveClient(config.remnawave_url, config.remnawave_token)


async def get_or_create_user(
    db: AsyncSession, telegram_id: int, **profile: object
) -> BotUser:
    user = await db.scalar(
        select(BotUser).where(BotUser.telegram_id == telegram_id)
    )
    if user is None:
        user = BotUser(telegram_id=telegram_id, **profile)
        db.add(user)
        await db.flush()
    else:
        for key, value in profile.items():
            if value:
                setattr(user, key, value)
        # Пользователь снова пишет боту — значит, не блокировал его.
        user.has_stopped_bot = False
    user.last_seen_at = datetime.now(UTC)
    return user


def _username_for(telegram_id: int) -> str:
    # Имя в Remnawave должно быть стабильным и уникальным — берём telegram_id.
    return f"tg_{telegram_id}"


def _new_key_username(telegram_id: int) -> str:
    # Для дополнительных ключей нужна ГЛОБАЛЬНО уникальная строка — иначе
    # второй ключ того же пользователя столкнётся по username с первым.
    return f"tg_{telegram_id}_{secrets.token_hex(3)}"


async def _mirror_subscription(
    db: AsyncSession,
    user: BotUser,
    *,
    remnawave_id: int,
    username: str,
    subscription_url: str | None,
    expire_at: datetime | None,
    plan_id: int | None,
) -> BotSubscription:
    """Заводит или обновляет строку `bot_subscriptions` для основной
    подписки пользователя (той, что также лежит в BotUser.remnawave_uuid).

    Нужно, чтобы «Мои подписки» показывал основной ключ наравне с
    дополнительными, купленными через `create_subscription`."""
    row = await db.scalar(
        select(BotSubscription).where(BotSubscription.remnawave_id == remnawave_id)
    )
    if row is None:
        row = BotSubscription(user_id=user.id, remnawave_id=remnawave_id, username=username)
        db.add(row)
    row.subscription_url = subscription_url
    row.expire_at = expire_at
    if plan_id is not None:
        row.plan_id = plan_id
    await db.flush()
    return row


async def grant(
    db: AsyncSession,
    config: Config,
    user: BotUser,
    *,
    days: int,
    squad_uuids: list[str],
    hwid_limit: int,
    traffic_limit_bytes: int = 0,
    source: str,
    plan_id: int | None = None,
    amount_kopeks: int = 0,
) -> BotUser:
    """Выдаёт или продлевает доступ и записывает факт выдачи."""
    now = datetime.now(UTC)
    current = user.expire_at
    if current is not None and current.tzinfo is None:
        current = current.replace(tzinfo=UTC)
    # Продление от текущей даты окончания, если подписка ещё жива.
    base = current if current and current > now else now
    expire_at = base + timedelta(days=days)

    client = client_for(config)
    try:
        if user.remnawave_uuid:
            remote = await client.update_user(
                int(user.remnawave_uuid),
                expireAt=expire_at.isoformat(),
                status="ACTIVE",
                activeInternalSquads=squad_uuids,
                hwidDeviceLimit=hwid_limit,
            )
        else:
            remote = await client.create_user(
                username=_username_for(user.telegram_id),
                expire_at=expire_at,
                internal_squad_uuids=squad_uuids,
                telegram_id=user.telegram_id,
                hwid_device_limit=hwid_limit,
                traffic_limit_bytes=traffic_limit_bytes,
                description=f"Выдано ботом: {source}",
            )
    finally:
        await client.aclose()

    user.remnawave_uuid = str(remote.id)
    user.subscription_url = remote.subscription_url
    user.expire_at = remote.expire_at or expire_at

    mirrored = await _mirror_subscription(
        db,
        user,
        remnawave_id=remote.id,
        username=remote.username,
        subscription_url=user.subscription_url,
        expire_at=user.expire_at,
        plan_id=plan_id,
    )

    db.add(
        Purchase(
            user_id=user.id,
            plan_id=plan_id,
            subscription_id=mirrored.id,
            days=days,
            amount_kopeks=amount_kopeks,
            source=source,
            expire_at=user.expire_at,
        )
    )
    log.info(
        "Выдан доступ: tg=%s дней=%s источник=%s", user.telegram_id, days, source
    )
    return user


async def grant_trial(db: AsyncSession, config: Config, user: BotUser) -> BotUser:
    # Приведённый по реферальной ссылке получает бонусные дни сразу к
    # триалу — отдельный вызов Remnawave тут не нужен, сквады те же.
    bonus = config.referral_bonus_days if user.referred_by_id else 0
    user = await grant(
        db,
        config,
        user,
        days=config.trial_days + bonus,
        squad_uuids=config.trial_squad_uuids,
        hwid_limit=config.trial_hwid_limit,
        source="trial",
    )
    user.trial_used = True
    return user


async def grant_bonus_days(
    db: AsyncSession, config: Config, user: BotUser, days: int, *, source: str
) -> BotUser:
    """Добавляет дни без изменения тарифных сквадов — для промокодов и
    реферальных наград.

    В отличие от `grant`, не трогает сквады существующего пользователя:
    иначе бонус мог бы понизить платящего клиента до триальных серверов.
    Аккаунт создаётся только если его ещё не было (тогда используются
    триальные сквады как самый общий доступ по умолчанию).
    """
    now = datetime.now(UTC)
    current = user.expire_at
    if current is not None and current.tzinfo is None:
        current = current.replace(tzinfo=UTC)
    base = current if current and current > now else now
    expire_at = base + timedelta(days=days)

    client = client_for(config)
    try:
        if user.remnawave_uuid:
            remote = await client.extend_expiration(int(user.remnawave_uuid), expire_at)
        else:
            remote = await client.create_user(
                username=_username_for(user.telegram_id),
                expire_at=expire_at,
                internal_squad_uuids=config.trial_squad_uuids,
                telegram_id=user.telegram_id,
                hwid_device_limit=config.trial_hwid_limit,
                description=f"Выдано ботом: {source}",
            )
    finally:
        await client.aclose()

    user.remnawave_uuid = str(remote.id)
    user.subscription_url = remote.subscription_url
    user.expire_at = remote.expire_at or expire_at

    mirrored = await _mirror_subscription(
        db,
        user,
        remnawave_id=remote.id,
        username=remote.username,
        subscription_url=user.subscription_url,
        expire_at=user.expire_at,
        plan_id=None,
    )

    db.add(
        Purchase(
            user_id=user.id,
            subscription_id=mirrored.id,
            days=days,
            source=source,
            expire_at=user.expire_at,
        )
    )
    log.info("Начислены бонусные дни: tg=%s дней=%s источник=%s", user.telegram_id, days, source)
    return user


async def grant_plan(
    db: AsyncSession, config: Config, user: BotUser, plan: PlanView, *, source: str
) -> BotUser:
    return await grant(
        db,
        config,
        user,
        days=plan.days,
        squad_uuids=plan.squad_uuids,
        hwid_limit=plan.hwid_limit,
        traffic_limit_bytes=plan.traffic_limit_bytes,
        source=source,
        plan_id=plan.id,
        amount_kopeks=plan.price_kopeks,
    )


async def list_subscriptions(db: AsyncSession, user: BotUser) -> list[BotSubscription]:
    """Все ключи пользователя, новые сверху — экран «Мои подписки»."""
    rows = await db.scalars(
        select(BotSubscription)
        .where(BotSubscription.user_id == user.id)
        .order_by(BotSubscription.created_at.desc())
    )
    return list(rows)


async def create_subscription(
    db: AsyncSession, config: Config, user: BotUser, plan: PlanView, *, source: str
) -> BotSubscription:
    """Покупка тарифа как в исходном боте: КАЖДАЯ покупка заводит новый
    независимый ключ (свой аккаунт Remnawave), а не продлевает старый.

    Первый ключ пользователя дополнительно становится «основным»
    (BotUser.remnawave_uuid/expire_at) — на нём по-прежнему держатся
    триал-гейт, автопродление и напоминания об истечении, рассчитанные на
    единственную подписку. Второй и последующие ключи существуют только
    как отдельные строки `bot_subscriptions`."""
    expire_at = datetime.now(UTC) + timedelta(days=plan.days)

    client = client_for(config)
    try:
        remote = await client.create_user(
            username=_new_key_username(user.telegram_id),
            expire_at=expire_at,
            internal_squad_uuids=plan.squad_uuids,
            telegram_id=user.telegram_id,
            hwid_device_limit=plan.hwid_limit,
            traffic_limit_bytes=plan.traffic_limit_bytes,
            description=f"Выдано ботом: {source}",
        )
    finally:
        await client.aclose()

    subscription = BotSubscription(
        user_id=user.id,
        remnawave_id=remote.id,
        username=remote.username,
        subscription_url=remote.subscription_url,
        expire_at=remote.expire_at or expire_at,
        plan_id=plan.id,
    )
    db.add(subscription)
    await db.flush()

    is_first_ever = user.remnawave_uuid is None
    if is_first_ever:
        user.remnawave_uuid = str(remote.id)
        user.subscription_url = remote.subscription_url
        user.expire_at = subscription.expire_at

    db.add(
        Purchase(
            user_id=user.id,
            plan_id=plan.id,
            subscription_id=subscription.id,
            days=plan.days,
            amount_kopeks=plan.price_kopeks,
            source=source,
            expire_at=subscription.expire_at,
        )
    )
    log.info(
        "Создан новый ключ: tg=%s тариф=%s источник=%s", user.telegram_id, plan.title, source
    )
    return subscription


async def extend_subscription(
    db: AsyncSession, config: Config, subscription: BotSubscription, plan: PlanView
) -> BotSubscription:
    """Продление КОНКРЕТНОГО ключа — из экрана «Мои подписки» → ключ →
    «Продлить». В отличие от `create_subscription`, не заводит новый
    аккаунт Remnawave, а расширяет срок действия существующего."""
    now = datetime.now(UTC)
    current = subscription.expire_at
    if current is not None and current.tzinfo is None:
        current = current.replace(tzinfo=UTC)
    base = current if current and current > now else now
    expire_at = base + timedelta(days=plan.days)

    client = client_for(config)
    try:
        remote = await client.update_user(
            subscription.remnawave_id,
            expireAt=expire_at.isoformat(),
            status="ACTIVE",
            activeInternalSquads=plan.squad_uuids,
            hwidDeviceLimit=plan.hwid_limit,
        )
    finally:
        await client.aclose()

    subscription.subscription_url = remote.subscription_url
    subscription.expire_at = remote.expire_at or expire_at
    subscription.plan_id = plan.id

    user = await db.get(BotUser, subscription.user_id)
    if user is not None and user.remnawave_uuid == str(subscription.remnawave_id):
        # Это основной ключ пользователя — держим зеркало в актуальном виде.
        user.subscription_url = subscription.subscription_url
        user.expire_at = subscription.expire_at

    db.add(
        Purchase(
            user_id=subscription.user_id,
            plan_id=plan.id,
            subscription_id=subscription.id,
            days=plan.days,
            amount_kopeks=plan.price_kopeks,
            source="renewal",
            expire_at=subscription.expire_at,
        )
    )
    log.info(
        "Продлён ключ #%s: тариф=%s до %s", subscription.id, plan.title, subscription.expire_at
    )
    return subscription


async def after_paid_purchase(
    db: AsyncSession,
    config: Config,
    user: BotUser,
    amount_kopeks: int,
    *,
    plan_title: str = "тариф",
) -> list[tuple[BotUser, int]]:
    """Единая точка после успешной оплаты тарифа (не пополнения баланса):
    начисляет денежную комиссию рефереру(ам) и шлёт уведомление о продаже
    в чат, если он настроен в панели. Не трогает разовый бонус в днях —
    им по-прежнему занимается `apply_referral_reward`.

    Возвращает список (реферер, начислено копеек) — вызывающий код сам
    решает, как и когда доставить эти уведомления (сразу или пачкой).
    """
    awarded = await referral.award_commission(db, config, user, amount_kopeks)

    if config.purchase_notify_chat_id and config.token:
        uname = f"@{user.username}" if user.username else str(user.telegram_id)
        text = (
            f"💰 Новая продажа\n"
            f"Пользователь: {uname}\n"
            f"Тариф: {plan_title}\n"
            f"Сумма: {amount_kopeks / 100:.2f} ₽"
        )
        await notify_send(config.token, config.purchase_notify_chat_id, text)

    return awarded


async def apply_referral_reward(
    db: AsyncSession, config: Config, user: BotUser
) -> tuple[BotUser, int] | None:
    """После успешной оплаты начисляет дни рефереру, если это первая покупка.

    Возвращает (обновлённый реферер, дни) для отправки уведомления —
    само уведомление шлёт вызывающий код: у него разный доступ к боту
    (живой polling или разовое сообщение по токену).
    """
    reward = await referral.reward_if_first_purchase(db, config, user)
    if reward is None:
        return None
    referrer = await db.get(BotUser, reward.referrer_user_id)
    if referrer is None:
        return None
    referrer = await grant_bonus_days(
        db, config, referrer, reward.days, source="referral_reward"
    )
    return referrer, reward.days


def is_active(user: BotUser) -> bool:
    if user.expire_at is None:
        return False
    expire = user.expire_at
    if expire.tzinfo is None:
        expire = expire.replace(tzinfo=UTC)
    return expire > datetime.now(UTC)
