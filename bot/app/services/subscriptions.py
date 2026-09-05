"""Выдача и продление подписок: единая точка для триала, покупки и панели.

Пользователь Remnawave создаётся один раз и дальше только продлевается —
так у клиента не меняется ссылка подписки при каждой оплате.
"""

import logging
import secrets
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Config, PlanView
from app.services import referral
from app.services.notify import send as notify_send
from shared.db.models import BotSubscription, BotUser, Purchase
from shared.remnawave import RemnawaveClient, RemnawaveError
from shared.sync import refresh_user_summary

log = logging.getLogger("bot.subscriptions")


class TrialUnavailable(Exception):
    """Причина отказа в пробном периоде — показывается пользователю как есть."""


def client_for(config: Config) -> RemnawaveClient:
    if not config.remnawave_url or not config.remnawave_token:
        raise RemnawaveError("Remnawave не подключена в панели")
    return RemnawaveClient(
        config.remnawave_url, config.remnawave_token, verify_tls=config.remnawave_verify_tls
    )


async def _create_or_adopt(client: RemnawaveClient, **kwargs):
    """POST /api/users, а при конфликте по username — подхватываем уже
    существующий аккаунт с этим именем и приводим его к нужным параметрам.

    Имя `tg_<id>` детерминировано: аккаунт мог остаться от прошлой
    установки бота или быть создан вручную в Remnawave. Раньше такой
    пользователь получал «попробуйте позже» навсегда."""
    try:
        return await client.create_user(**kwargs)
    except RemnawaveError as exc:
        if exc.status_code not in (400, 409) or "username" not in str(exc).lower():
            raise
        existing = await client.get_users_by_username(kwargs["username"])
        if not existing:
            raise
        remote = existing[0]
        log.warning("Remnawave: username %s уже занят — подхватываю аккаунт id=%s", kwargs["username"], remote.id)
        fields: dict = {
            "expireAt": kwargs["expire_at"].strftime("%Y-%m-%dT%H:%M:%S.000Z"),
            "status": "ACTIVE",
            "activeInternalSquads": kwargs["internal_squad_uuids"],
            "trafficLimitBytes": kwargs.get("traffic_limit_bytes", 0),
        }
        if kwargs.get("hwid_device_limit") is not None:
            fields["hwidDeviceLimit"] = kwargs["hwid_device_limit"]
        if kwargs.get("telegram_id") is not None:
            fields["telegramId"] = kwargs["telegram_id"]
        if kwargs.get("description"):
            fields["description"] = kwargs["description"]
        return await client.update_user(remote.id, **fields)


async def _reset_traffic_if_limited(client: RemnawaveClient, user_id: int, plan: PlanView) -> None:
    """Тариф с лимитом трафика: при оплате счётчик обнуляется, иначе
    клиент в LIMITED так и останется без доступа."""
    if plan.traffic_limit_bytes <= 0:
        return
    try:
        await client.reset_traffic(user_id)
    except RemnawaveError as exc:
        log.warning("Не удалось сбросить трафик пользователю %s: %s", user_id, exc)


async def get_or_create_user(
    db: AsyncSession, telegram_id: int, **profile: object
) -> BotUser:
    user = await db.scalar(
        select(BotUser).where(BotUser.telegram_id == telegram_id)
    )
    if user is None:
        # Два апдейта от нового пользователя приходят одновременно (сообщение
        # и нажатие кнопки) — второй INSERT упирается в уникальный индекс.
        # SAVEPOINT откатывает только вставку, а не всю сессию.
        try:
            async with db.begin_nested():
                user = BotUser(telegram_id=telegram_id, **profile)
                db.add(user)
                await db.flush()
        except IntegrityError:
            user = await db.scalar(
                select(BotUser).where(BotUser.telegram_id == telegram_id)
            )
            if user is None:  # pragma: no cover — гонка с удалением
                raise
            _apply_profile(user, profile)
    else:
        _apply_profile(user, profile)
    user.last_seen_at = datetime.now(UTC)
    return user


def _apply_profile(user: BotUser, profile: dict[str, object]) -> None:
    for key, value in profile.items():
        if value:
            setattr(user, key, value)
    # Пользователь снова пишет боту — значит, не блокировал его.
    user.has_stopped_bot = False


def _username_for(telegram_id: int) -> str:
    # Имя в Remnawave должно быть стабильным и уникальным — берём telegram_id.
    return f"tg_{telegram_id}"


def _new_key_username(telegram_id: int) -> str:
    # Для дополнительных ключей нужна ГЛОБАЛЬНО уникальная строка — иначе
    # второй ключ того же пользователя столкнётся по username с первым.
    return f"tg_{telegram_id}_{secrets.token_hex(3)}"


def _aware(value: datetime | None) -> datetime | None:
    if value is not None and value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value


def _base_for_extension(current: datetime | None) -> datetime:
    """Продление считается от даты окончания, если подписка ещё жива."""
    now = datetime.now(UTC)
    current = _aware(current)
    return current if current and current > now else now


async def _mirror_subscription(
    db: AsyncSession,
    user: BotUser,
    *,
    remnawave_id: int,
    username: str,
    subscription_url: str | None,
    expire_at: datetime | None,
    plan_id: int | None,
    title: str | None = None,
    is_trial: bool | None = None,
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
    if title is not None:
        row.title = title
    if is_trial is not None:
        row.is_trial = is_trial
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
    plan_title: str | None = None,
    amount_kopeks: int = 0,
) -> BotUser:
    """Выдаёт или продлевает доступ на основном ключе и записывает факт выдачи."""
    expire_at = _base_for_extension(user.expire_at) + timedelta(days=days)

    client = client_for(config)
    try:
        if user.remnawave_uuid:
            remote = await client.update_user(
                int(user.remnawave_uuid),
                expireAt=expire_at.isoformat(),
                status="ACTIVE",
                activeInternalSquads=squad_uuids,
                hwidDeviceLimit=hwid_limit,
                trafficLimitBytes=traffic_limit_bytes,
            )
        else:
            remote = await _create_or_adopt(
                client,
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
        title=plan_title,
        is_trial=False if plan_id is not None else None,
    )

    db.add(
        Purchase(
            user_id=user.id,
            plan_id=plan_id,
            plan_title=plan_title,
            subscription_id=mirrored.id,
            days=days,
            amount_kopeks=amount_kopeks,
            source=source,
            expire_at=user.expire_at,
        )
    )
    await refresh_user_summary(db, user)
    log.info(
        "Выдан доступ: tg=%s дней=%s источник=%s", user.telegram_id, days, source
    )
    return user


async def subscription_count(db: AsyncSession, user: BotUser) -> int:
    return (
        await db.scalar(
            select(func.count())
            .select_from(BotSubscription)
            .where(BotSubscription.user_id == user.id)
        )
    ) or 0


async def trial_available(db: AsyncSession, config: Config, user: BotUser) -> bool:
    """Пробный период — только для тех, у кого ещё нет ни одного ключа.

    Раньше проверялся лишь флаг `trial_used`, поэтому купивший подписку
    мог нажать «Попробовать бесплатно» — и пробный период правил его
    оплаченный ключ: добавлял себе дни и сбрасывал сквады с лимитом
    устройств на пробные.
    """
    if not config.trial_enabled or user.trial_used:
        return False
    return await subscription_count(db, user) == 0


async def grant_trial(db: AsyncSession, config: Config, user: BotUser) -> BotUser:
    """Заводит ОТДЕЛЬНЫЙ пробный ключ. Существующие ключи не трогает."""
    if not config.trial_enabled:
        raise TrialUnavailable("Пробный период сейчас недоступен")
    if user.trial_used:
        raise TrialUnavailable("Пробный период уже использован")
    if await subscription_count(db, user):
        raise TrialUnavailable(
            "Пробный период доступен только до первой подписки"
        )

    # Приведённый по реферальной ссылке получает бонусные дни сразу к
    # триалу — отдельный вызов Remnawave тут не нужен, сквады те же.
    bonus = config.referral_bonus_days if user.referred_by_id else 0
    days = config.trial_days + bonus
    expire_at = datetime.now(UTC) + timedelta(days=days)

    client = client_for(config)
    try:
        remote = await _create_or_adopt(
            client,
            username=_username_for(user.telegram_id),
            expire_at=expire_at,
            internal_squad_uuids=config.trial_squad_uuids,
            telegram_id=user.telegram_id,
            hwid_device_limit=config.trial_hwid_limit,
            description="Выдано ботом: trial",
        )
    finally:
        await client.aclose()

    mirrored = await _mirror_subscription(
        db,
        user,
        remnawave_id=remote.id,
        username=remote.username,
        subscription_url=remote.subscription_url,
        expire_at=remote.expire_at or expire_at,
        plan_id=None,
        is_trial=True,
    )
    db.add(
        Purchase(
            user_id=user.id,
            subscription_id=mirrored.id,
            days=days,
            source="trial",
            expire_at=mirrored.expire_at,
        )
    )
    user.trial_used = True
    await refresh_user_summary(db, user)
    log.info("Выдан пробный период: tg=%s дней=%s", user.telegram_id, days)
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
    expire_at = _base_for_extension(user.expire_at) + timedelta(days=days)

    client = client_for(config)
    try:
        if user.remnawave_uuid:
            remote = await client.extend_expiration(int(user.remnawave_uuid), expire_at)
        else:
            remote = await _create_or_adopt(
                client,
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
    await refresh_user_summary(db, user)
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
        plan_title=plan.title,
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


async def _trial_key(db: AsyncSession, user: BotUser) -> BotSubscription | None:
    """Пробный ключ пользователя, если он у него единственный: при первой
    покупке его апгрейдим, а не оставляем висеть рядом с платным (так же
    поступает исходный бот — там пробный ключ удаляется)."""
    rows = await list_subscriptions(db, user)
    if len(rows) != 1 or not rows[0].is_trial:
        return None
    return rows[0]


async def create_subscription(
    db: AsyncSession,
    config: Config,
    user: BotUser,
    plan: PlanView,
    *,
    source: str,
    amount_kopeks: int | None = None,
) -> BotSubscription:
    """Покупка тарифа как в исходном боте: КАЖДАЯ покупка заводит новый
    независимый ключ (свой аккаунт Remnawave), а не продлевает старый.

    Исключение — пробный ключ: если у пользователя есть только триал,
    покупка превращает его в платный (сквады, лимит устройств и срок
    тарифа), иначе меню и напоминания продолжали бы жить по датам триала.
    """
    paid = plan.price_kopeks if amount_kopeks is None else amount_kopeks
    expire_at = datetime.now(UTC) + timedelta(days=plan.days)

    trial = await _trial_key(db, user)

    client = client_for(config)
    try:
        if trial is not None:
            remote = await client.update_user(
                trial.remnawave_id,
                expireAt=expire_at.isoformat(),
                status="ACTIVE",
                activeInternalSquads=plan.squad_uuids,
                hwidDeviceLimit=plan.hwid_limit,
                trafficLimitBytes=plan.traffic_limit_bytes,
                description=f"Выдано ботом: {source} (апгрейд с триала)",
            )
            await _reset_traffic_if_limited(client, trial.remnawave_id, plan)
        else:
            remote = await _create_or_adopt(
                client,
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

    if trial is not None:
        subscription = trial
        subscription.subscription_url = remote.subscription_url or trial.subscription_url
        subscription.expire_at = remote.expire_at or expire_at
        subscription.plan_id = plan.id
        subscription.title = plan.title
        subscription.is_trial = False
    else:
        subscription = BotSubscription(
            user_id=user.id,
            remnawave_id=remote.id,
            username=remote.username,
            subscription_url=remote.subscription_url,
            expire_at=remote.expire_at or expire_at,
            plan_id=plan.id,
            title=plan.title,
        )
        db.add(subscription)
    await db.flush()

    db.add(
        Purchase(
            user_id=user.id,
            plan_id=plan.id,
            plan_title=plan.title,
            subscription_id=subscription.id,
            days=plan.days,
            amount_kopeks=paid,
            source=source,
            expire_at=subscription.expire_at,
        )
    )
    await refresh_user_summary(db, user)
    log.info(
        "%s: tg=%s тариф=%s источник=%s",
        "Апгрейд триала" if trial is not None else "Создан новый ключ",
        user.telegram_id,
        plan.title,
        source,
    )
    return subscription


async def extend_subscription(
    db: AsyncSession,
    config: Config,
    subscription: BotSubscription,
    plan: PlanView,
    *,
    source: str = "renewal",
    amount_kopeks: int | None = None,
) -> BotSubscription:
    """Продление КОНКРЕТНОГО ключа — из экрана «Мои подписки» → ключ →
    «Продлить». В отличие от `create_subscription`, не заводит новый
    аккаунт Remnawave, а расширяет срок действия существующего."""
    paid = plan.price_kopeks if amount_kopeks is None else amount_kopeks
    expire_at = _base_for_extension(subscription.expire_at) + timedelta(days=plan.days)

    client = client_for(config)
    try:
        remote = await client.update_user(
            subscription.remnawave_id,
            expireAt=expire_at.isoformat(),
            status="ACTIVE",
            activeInternalSquads=plan.squad_uuids,
            hwidDeviceLimit=plan.hwid_limit,
            trafficLimitBytes=plan.traffic_limit_bytes,
        )
        await _reset_traffic_if_limited(client, subscription.remnawave_id, plan)
    finally:
        await client.aclose()

    subscription.subscription_url = remote.subscription_url or subscription.subscription_url
    subscription.expire_at = remote.expire_at or expire_at
    subscription.plan_id = plan.id
    subscription.title = plan.title
    subscription.is_trial = False

    db.add(
        Purchase(
            user_id=subscription.user_id,
            plan_id=plan.id,
            plan_title=plan.title,
            subscription_id=subscription.id,
            days=plan.days,
            amount_kopeks=paid,
            source=source,
            expire_at=subscription.expire_at,
        )
    )
    user = await db.get(BotUser, subscription.user_id)
    if user is not None:
        await refresh_user_summary(db, user)
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
    expire = _aware(user.expire_at)
    return expire > datetime.now(UTC)
