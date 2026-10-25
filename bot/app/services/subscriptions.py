"""Выдача и продление подписок: единая точка для триала, покупки и панели.

Пользователь Remnawave создаётся один раз и дальше только продлевается —
так у клиента не меняется ссылка подписки при каждой оплате.
"""

import html
import logging
import secrets
import time
from datetime import UTC, datetime, timedelta
from urllib.parse import urlsplit

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Config, PlanView, TrafficPackageView
from app.services import adlinks, referral
from app.services.notify import send_sales as notify_sales
from shared.db.models import BotSubscription, BotUser, Purchase
from shared.panel import XUI, PanelClient, make_client, panel_name, panel_type
from shared.remnawave import RemnawaveClient, RemnawaveError
from shared.remnawave.models import User as RemoteUser
from shared.sync import forget_remote_key, refresh_user_summary, stored_ref
from shared.xui import XuiClient

log = logging.getLogger("bot.subscriptions")


class TrialUnavailable(Exception):
    """Причина отказа в пробном периоде — показывается пользователю как есть."""


def client_for(config: Config) -> PanelClient:
    if not config.remnawave_url or not config.remnawave_token:
        raise RemnawaveError(f"{panel_name()} не подключена в панели")
    return make_client(
        config.remnawave_url, config.remnawave_token, verify_tls=config.remnawave_verify_tls
    )


async def config_files(config: Config, ref: int | str) -> list:
    """Файлы .conf (WireGuard/AmneziaWG) для ключа — только у 3x-ui.

    Роутерам нужен именно файл, а не ссылка подписки. Недоступность панели
    не должна ломать экран ключа, поэтому при сбое — пустой список."""
    if panel_type() != XUI:
        return []
    try:
        client = client_for(config)
        try:
            return await client.get_config_files(ref)
        finally:
            await client.aclose()
    except RemnawaveError as exc:
        log.warning("Не удалось получить конфиг ключа %s: %s", ref, exc)
        return []


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
        log.warning(
            "Remnawave: username %s уже занят — подхватываю аккаунт %s",
            kwargs["username"],
            remote.ref,
        )
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
        return await client.update_user(remote.ref, **fields)


_SOURCE_LABEL = {
    "trial": "пробный период",
    "wallet": "оплата с баланса",
    "auto_renewal": "автопродление с баланса",
    "renewal": "продление",
    "referral_reward": "награда за друга",
}


def describe_grant(user: BotUser | None, source: str, *, note: str = "") -> str:
    """Заметка к аккаунту в панели: кто и за что получил доступ.

    Раньше писалось сухое «Выдано ботом: wallet» — в списке пользователей
    панели нельзя было понять, чей это ключ и что значит wallet."""
    label = _SOURCE_LABEL.get(source, source)
    who = ""
    if user is not None:
        who = " ".join(
            part
            for part in (user.first_name, f"@{user.username}" if user.username else "")
            if part
        )
    text = f"Бот: {who or 'клиент'} — {label}"
    return f"{text} ({note})" if note else text


async def _update_or_recreate(client, ref, create_kwargs: dict, **fields):
    """PATCH пользователя, а если его удалили в панели (404) — создаём заново.

    Ключ мог пропасть из Remnawave (чистка тестовых, ручное удаление), а
    оплата за него уже пришла или списывается с баланса: вместо вечного
    «не удалось выдать доступ» поднимаем аккаунт заново под тем же именем."""
    try:
        return await client.update_user(ref, **fields)
    except RemnawaveError as exc:
        if exc.status_code != 404:
            raise
        log.warning("Ключ %s не найден в панели — создаю заново: %s", ref, create_kwargs["username"])
        return await _create_or_adopt(client, **create_kwargs)


def _relink(subscription: BotSubscription, remote: RemoteUser) -> None:
    """После пересоздания ключа в панели у него новые id/uuid — обновляем."""
    if remote.uuid:
        subscription.remnawave_uuid = remote.uuid
    if remote.id is not None:
        subscription.remnawave_id = remote.id


async def _reset_traffic_if_limited(client: RemnawaveClient, ref, plan: PlanView) -> None:
    """Тариф с лимитом трафика: при оплате счётчик обнуляется, иначе
    клиент в LIMITED так и останется без доступа."""
    if plan.traffic_limit_bytes <= 0:
        return
    try:
        await client.reset_traffic(ref)
    except RemnawaveError as exc:
        log.warning("Не удалось сбросить трафик пользователю %s: %s", ref, exc)


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


def _new_key_username(telegram_id: int, payment_id: int | None = None) -> str:
    # Для дополнительных ключей нужна ГЛОБАЛЬНО уникальная строка — иначе
    # второй ключ того же пользователя столкнётся по username с первым.
    # Ключ по внешней оплате называется по её id: если после создания ключа
    # упала запись в БД, повторная обработка той же оплаты наткнётся на
    # занятое имя и подхватит уже созданный аккаунт, а не заведёт второй.
    if payment_id is not None:
        return f"tg_{telegram_id}_p{payment_id}"
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
    remote: RemoteUser,
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
    row = None
    if remote.uuid:
        row = await db.scalar(
            select(BotSubscription).where(BotSubscription.remnawave_uuid == remote.uuid)
        )
    if row is None and remote.id is not None:
        row = await db.scalar(
            select(BotSubscription).where(BotSubscription.remnawave_id == remote.id)
        )
    if row is None:
        row = BotSubscription(
            user_id=user.id, remnawave_id=remote.id or 0, username=remote.username
        )
        db.add(row)
    if remote.uuid:
        row.remnawave_uuid = remote.uuid
    if remote.id is not None:
        row.remnawave_id = remote.id
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
        create_kwargs = dict(
            username=_username_for(user.telegram_id),
            expire_at=expire_at,
            internal_squad_uuids=squad_uuids,
            telegram_id=user.telegram_id,
            hwid_device_limit=hwid_limit,
            traffic_limit_bytes=traffic_limit_bytes,
            description=describe_grant(user, source),
        )
        if user.remnawave_uuid:
            remote = await _update_or_recreate(
                client,
                stored_ref(user.remnawave_uuid),
                create_kwargs,
                expireAt=expire_at.isoformat(),
                status="ACTIVE",
                activeInternalSquads=squad_uuids,
                hwidDeviceLimit=hwid_limit,
                trafficLimitBytes=traffic_limit_bytes,
            )
        else:
            remote = await _create_or_adopt(client, **create_kwargs)
    finally:
        await client.aclose()

    user.remnawave_uuid = str(remote.ref)
    user.subscription_url = remote.subscription_url
    user.expire_at = remote.expire_at or expire_at

    mirrored = await _mirror_subscription(
        db,
        user,
        remote=remote,
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
    # Бонусные дни — часть программы «дни за друга», не денежной комиссии.
    bonus = config.referral_bonus_days if user.referred_by_id and config.referral_enabled else 0
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
            traffic_limit_bytes=config.trial_traffic_gb * 1024**3,
            description=describe_grant(user, "trial"),
        )
    finally:
        await client.aclose()

    mirrored = await _mirror_subscription(
        db,
        user,
        remote=remote,
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
        create_kwargs = dict(
            username=_username_for(user.telegram_id),
            expire_at=expire_at,
            internal_squad_uuids=config.trial_squad_uuids,
            telegram_id=user.telegram_id,
            hwid_device_limit=config.trial_hwid_limit,
            description=describe_grant(user, source),
        )
        if user.remnawave_uuid:
            remote = await _update_or_recreate(
                client,
                stored_ref(user.remnawave_uuid),
                create_kwargs,
                expireAt=expire_at.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
            )
        else:
            remote = await _create_or_adopt(client, **create_kwargs)
    finally:
        await client.aclose()

    user.remnawave_uuid = str(remote.ref)
    user.subscription_url = remote.subscription_url
    user.expire_at = remote.expire_at or expire_at

    mirrored = await _mirror_subscription(
        db,
        user,
        remote=remote,
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


async def reconcile_deleted_clients(
    db: AsyncSession, config: Config, user: BotUser | None = None
) -> int:
    """Remove stale local keys only after a successful full 3x-ui snapshot."""
    if panel_type() != XUI:
        return 0
    query = select(BotSubscription).with_for_update()
    if user is not None:
        query = query.where(BotSubscription.user_id == user.id)
    rows = list(await db.scalars(query))
    if not rows and user is not None:
        return 0
    client = client_for(config)
    try:
        if not isinstance(client, XuiClient):
            return 0
        snapshot = await client._all_rows()
        # Do not interpret a malformed response as an empty panel.
        if any(not r.get("email") for r in snapshot):
            raise RemnawaveError("3x-ui: некорректный список клиентов")
        by_name = {r["email"]: r for r in snapshot}
        by_id = {r["id"]: r for r in snapshot if isinstance(r.get("id"), int)}
        by_sub_id = {r["subId"]: r for r in snapshot if r.get("subId")}
        removed = 0
        owners = set()
        matched = set()
        for row in rows:
            owner = await db.get(BotUser, row.user_id)
            key = urlsplit(row.subscription_url).path.rstrip("/").rsplit("/", 1)[-1] if row.subscription_url else None
            rec = by_sub_id.get(key) or by_name.get(row.remnawave_uuid)
            if rec is None and row.remnawave_id:
                candidate = by_id.get(row.remnawave_id)
                if candidate and owner and str(candidate.get("tgId")) == str(owner.telegram_id):
                    rec = candidate
            if rec is None:
                await forget_remote_key(db, remnawave_id=None, remnawave_uuid=row.remnawave_uuid)
                removed += 1
                if owner:
                    owner.remnawave_synced = False
                continue
            remote = await client._from_list_row(rec)
            matched.add(remote.username)
            row.username = remote.username
            row.remnawave_uuid = remote.uuid
            row.remnawave_id = remote.id or 0
            row.subscription_url = remote.subscription_url
            row.expire_at = remote.expire_at
            owners.add(row.user_id)
        if user is None:
            known_users = {u.telegram_id: u for u in await db.scalars(select(BotUser))}
            for rec in snapshot:
                owner = known_users.get(int(rec.get("tgId") or 0))
                if owner is None or rec["email"] in matched:
                    continue
                remote = await client._from_list_row(rec)
                db.add(BotSubscription(
                    user_id=owner.id, remnawave_id=remote.id or 0,
                    remnawave_uuid=remote.uuid, username=remote.username,
                    subscription_url=remote.subscription_url, expire_at=remote.expire_at,
                ))
                owners.add(owner.id)
        await db.flush()
        for owner_id in owners:
            await refresh_user_summary(db, await db.get(BotUser, owner_id))
    finally:
        await client.aclose()
    return removed


# Сверка с 3x-ui тянет весь список клиентов панели. На каждое открытие
# меню это лишняя нагрузка на больших базах: для одного клиента достаточно
# раза в минуту (полная сверка всех и так идёт фоном, см. panel_sync).
RECONCILE_EVERY = 60.0
_last_reconcile: dict[int, float] = {}


def _reconcile_due(user_id: int) -> bool:
    if panel_type() != XUI:
        return True  # для Remnawave сверка — пустая операция
    now = time.monotonic()
    if now - _last_reconcile.get(user_id, float("-inf")) < RECONCILE_EVERY:
        return False
    if len(_last_reconcile) > 50_000:
        _last_reconcile.clear()
    _last_reconcile[user_id] = now
    return True


async def link_from_remnawave(
    db: AsyncSession, config: Config, user: BotUser, *, force: bool = False
) -> list[BotSubscription]:
    """Подхватывает уже существующие в Remnawave аккаунты пользователя.

    Аккаунт мог быть заведён вне бота — вручную в панели или при миграции —
    и иметь telegram_id. Без этой привязки бот «не узнаёт» человека и на
    первой же выдаче создаёт второй аккаунт Remnawave на тот же telegram_id.
    Здесь ищем по telegram_id и заводим на найденное строки bot_subscriptions.

    Идемпотентна: повторно не импортирует уже привязанные ключи, а флаг
    remnawave_synced не даёт ходить в Remnawave на каждый /start.
    """
    if force or _reconcile_due(user.id):
        try:
            await reconcile_deleted_clients(db, config, user)
        except RemnawaveError as exc:
            log.warning("Проверка удалённых ключей недоступна: %s", exc)
    if not force:
        if user.remnawave_synced and panel_type() != XUI:
            return []
        if await subscription_count(db, user) and panel_type() != XUI:
            user.remnawave_synced = True
            return []

    try:
        client = client_for(config)
    except RemnawaveError:
        return []  # Remnawave не подключена — синхронизировать нечего
    try:
        remotes = await client.get_users_by_telegram_id(user.telegram_id)
    except RemnawaveError as exc:
        # Сеть/панель недоступны — не ставим флаг, попробуем в другой раз.
        log.warning("Синхронизация Remnawave для tg=%s не удалась: %s", user.telegram_id, exc)
        return []
    finally:
        await client.aclose()

    imported: list[BotSubscription] = []
    for remote in remotes:
        exists = None
        if remote.uuid:
            exists = await db.scalar(
                select(BotSubscription).where(BotSubscription.remnawave_uuid == remote.uuid)
            )
        if exists is None and remote.id is not None:
            exists = await db.scalar(
                select(BotSubscription).where(BotSubscription.remnawave_id == remote.id)
            )
        if exists is not None:
            continue  # ключ уже привязан к какому-то пользователю бота
        row = BotSubscription(
            user_id=user.id,
            remnawave_id=remote.id or 0,
            remnawave_uuid=remote.uuid,
            username=remote.username,
            subscription_url=remote.subscription_url,
            expire_at=remote.expire_at,
            plan_id=None,
        )
        db.add(row)
        imported.append(row)

    await db.flush()
    user.remnawave_synced = True
    if imported:
        await refresh_user_summary(db, user)
        log.info(
            "Импортировано из Remnawave для tg=%s: %s ключей", user.telegram_id, len(imported)
        )
    return imported


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
    payment_id: int | None = None,
) -> BotSubscription:
    """Покупка тарифа как в исходном боте: КАЖДАЯ покупка заводит новый
    независимый ключ (свой аккаунт Remnawave), а не продлевает старый.

    Исключение — пробный ключ: если у пользователя есть только триал,
    покупка превращает его в платный (сквады, лимит устройств и срок
    тарифа), иначе меню и напоминания продолжали бы жить по датам триала.
    """
    paid = plan.price_kopeks if amount_kopeks is None else amount_kopeks
    expire_at = datetime.now(UTC) + timedelta(days=plan.days)

    # Дедуп: если бот ещё не знает этого пользователя, но в Remnawave уже есть
    # аккаунт с его telegram_id — продлеваем его выбранным тарифом, а не
    # заводим второй аккаунт на тот же telegram_id.
    if await subscription_count(db, user) == 0:
        adopted = await link_from_remnawave(db, config, user, force=True)
        if adopted and any(r.expire_at is not None for r in adopted):
            target = max(adopted, key=lambda r: _aware(r.expire_at) or datetime.min.replace(tzinfo=UTC))
            return await extend_subscription(
                db, config, target, plan, source=source, amount_kopeks=paid
            )

    trial = await _trial_key(db, user)

    client = client_for(config)
    try:
        create_kwargs = dict(
            username=_new_key_username(user.telegram_id, payment_id),
            expire_at=expire_at,
            internal_squad_uuids=plan.squad_uuids,
            telegram_id=user.telegram_id,
            hwid_device_limit=plan.hwid_limit,
            traffic_limit_bytes=plan.traffic_limit_bytes,
            description=describe_grant(user, source),
        )
        if trial is not None:
            create_kwargs["username"] = trial.username
            create_kwargs["description"] = describe_grant(user, source, note="с пробного")
            remote = await _update_or_recreate(
                client,
                trial.remote_ref,
                create_kwargs,
                expireAt=expire_at.isoformat(),
                status="ACTIVE",
                activeInternalSquads=plan.squad_uuids,
                hwidDeviceLimit=plan.hwid_limit,
                trafficLimitBytes=plan.traffic_limit_bytes,
                description=create_kwargs["description"],
            )
            await _reset_traffic_if_limited(client, remote.ref, plan)
        else:
            remote = await _create_or_adopt(client, **create_kwargs)
    finally:
        await client.aclose()

    if trial is not None:
        subscription = trial
        _relink(subscription, remote)
        subscription.subscription_url = remote.subscription_url or trial.subscription_url
        subscription.expire_at = remote.expire_at or expire_at
        subscription.plan_id = plan.id
        subscription.title = plan.title
        subscription.is_trial = False
    else:
        subscription = BotSubscription(
            user_id=user.id,
            remnawave_id=remote.id or 0,
            remnawave_uuid=remote.uuid,
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
    expire_at = (
        None if panel_type() == XUI and subscription.expire_at is None
        else _base_for_extension(subscription.expire_at) + timedelta(days=plan.days)
    )

    owner = await db.get(BotUser, subscription.user_id)
    description = describe_grant(owner, source)
    client = client_for(config)
    try:
        create_kwargs = dict(
            username=subscription.username,
            expire_at=expire_at or _base_for_extension(None) + timedelta(days=plan.days),
            internal_squad_uuids=plan.squad_uuids,
            telegram_id=owner.telegram_id if owner else None,
            hwid_device_limit=plan.hwid_limit,
            traffic_limit_bytes=plan.traffic_limit_bytes,
            description=description,
        )
        remote = await _update_or_recreate(
            client,
            subscription.remote_ref,
            create_kwargs,
            expireAt=expire_at.isoformat() if expire_at else None,
            status="ACTIVE",
            activeInternalSquads=plan.squad_uuids,
            hwidDeviceLimit=plan.hwid_limit,
            trafficLimitBytes=plan.traffic_limit_bytes,
        )
        _relink(subscription, remote)
        await _reset_traffic_if_limited(client, remote.ref, plan)
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


class TrafficUnavailable(Exception):
    """Причина отказа в докупке трафика — показывается пользователю."""


async def add_traffic(
    db: AsyncSession,
    config: Config,
    subscription: BotSubscription,
    package: TrafficPackageView,
    *,
    source: str,
    amount_kopeks: int | None = None,
) -> int:
    """Поднимает лимит трафика ключа на размер пакета. Возвращает новый лимит.

    Отдельной операции «добавить трафик» в Remnawave нет — есть только
    лимит пользователя, поэтому читаем текущий и прибавляем. Значение 0
    означает безлимит: к нему прибавлять нечего, и продавать пакет тоже.
    """
    paid = package.price_kopeks if amount_kopeks is None else amount_kopeks

    client = client_for(config)
    try:
        try:
            remote = await client.get_user(subscription.remote_ref)
        except RemnawaveError as exc:
            if exc.status_code == 404:
                raise TrafficUnavailable(
                    "Этот ключ удалён на сервере — напишите в поддержку"
                ) from exc
            raise
        if remote.traffic_limit_bytes <= 0:
            raise TrafficUnavailable("На этой подписке трафик безлимитный — докупать нечего")
        new_limit = remote.traffic_limit_bytes + package.traffic_bytes
        await client.update_user(subscription.remote_ref, trafficLimitBytes=new_limit)
    finally:
        await client.aclose()

    db.add(
        Purchase(
            user_id=subscription.user_id,
            plan_title=f"+{package.traffic_gb} ГБ трафика",
            subscription_id=subscription.id,
            days=0,
            amount_kopeks=paid,
            source=source,
            expire_at=subscription.expire_at,
        )
    )
    log.info(
        "Докуплен трафик: ключ #%s +%s ГБ, новый лимит %s байт",
        subscription.id,
        package.traffic_gb,
        new_limit,
    )
    return new_limit


SALE_KIND_LABEL = {
    "purchase": "💰 Новая покупка",
    "renewal": "🔄 Продление подписки",
    "auto_renewal": "🔁 Автопродление с баланса",
    "traffic": "📶 Докупка трафика",
}

_METHOD_LABEL = {
    "platega": "Platega (СБП / карта)",
    "rollypay": "RollyPay (СБП)",
    "cispay": "cisPay (СБП / карта / крипта)",
    "cryptobot": "CryptoBot",
    "stars": "Telegram Stars",
    "wallet": "баланс",
    "auto_renewal": "баланс (автопродление)",
}


def sale_text(
    user: BotUser, *, kind: str, plan_title: str, amount_kopeks: int, method: str | None = None
) -> str:
    """Текст уведомления о продаже для чата из панели (HTML)."""
    who = f"@{user.username}" if user.username else (user.first_name or "—")
    lines = [
        f"<b>{SALE_KIND_LABEL.get(kind, SALE_KIND_LABEL['purchase'])}</b>",
        f"Пользователь: {html.escape(who)} (<code>{user.telegram_id}</code>)",
        f"Тариф: {html.escape(plan_title)}",
        f"Сумма: {amount_kopeks / 100:.2f} ₽",
    ]
    if method:
        lines.append(f"Способ оплаты: {html.escape(_METHOD_LABEL.get(method, method))}")
    return "\n".join(lines)


async def after_paid_purchase(
    db: AsyncSession,
    config: Config,
    user: BotUser,
    amount_kopeks: int,
    *,
    plan_title: str = "тариф",
    kind: str = "purchase",
    method: str | None = None,
) -> list[tuple[BotUser, int]]:
    """Единая точка после успешной оплаты тарифа (не пополнения баланса):
    начисляет денежную комиссию рефереру(ам) и шлёт уведомление о продаже
    в чат, если он настроен в панели — о покупках, продлениях,
    автопродлениях и докупке трафика. Не трогает разовый бонус в днях —
    им по-прежнему занимается `apply_referral_reward`.

    Возвращает список (реферер, начислено копеек) — вызывающий код сам
    решает, как и когда доставить эти уведомления (сразу или пачкой).
    """
    awarded = await referral.award_commission(db, config, user, amount_kopeks)
    awarded += await adlinks.credit_commission(db, config, user, amount_kopeks)

    if config.purchase_notify_chat_id and config.token:
        text = sale_text(
            user, kind=kind, plan_title=plan_title, amount_kopeks=amount_kopeks, method=method
        )
        await notify_sales(config.token, config.purchase_notify_chat_id, text)

    return awarded


async def renew_target(db: AsyncSession, user: BotUser) -> BotSubscription | None:
    """Ключ, который продлевает «Купить», когда несколько подписок на
    клиента не разрешены: тот, что живёт дольше всех (его же видно в шапке
    меню, см. shared/sync.py)."""
    return await db.scalar(
        select(BotSubscription)
        .where(BotSubscription.user_id == user.id)
        .order_by(BotSubscription.expire_at.desc().nulls_last(), BotSubscription.id.desc())
        .limit(1)
    )


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
        return bool(user.remnawave_uuid)
    expire = _aware(user.expire_at)
    return expire > datetime.now(UTC)
