"""Перенос клиентов из STEALTHNET (remnawave-STEALTHNET-Bot).

Источник — штатный бэкап STEALTHNET: stealthnet-backup-*.sql, обычный
`pg_dump -F p` (разбирается тем же читателем, что и бэкап Bedolaga, см.
bedolaga_import.read_backup). Схема — Prisma: клиенты в `clients` с
текстовыми id (cuid), подписки в `subscriptions`, настройки в
`system_settings`.

Особенности, из-за которых это не копия импорта Bedolaga:
- баланс хранится в валюте магазина (`default_currency`, по умолчанию USD),
  у нас — в рублях: нужен курс, админ вводит его в панели;
- ключи записаны по uuid, а новые Remnawave адресуют пользователя числовым
  id. Поэтому ключи сопоставляются с самой Remnawave (по uuid, короткому
  uuid или telegram id) — так получаются правильные id для любой версии
  панели. Нет связи с Remnawave — ключи не переносим: бот подтянет их сам
  при первом /start клиента (link_from_remnawave);
- подарочные подписки, которые ещё никто не забрал, не переносятся — у них
  нет владельца-пользователя.

Повторный запуск ничего не задваивает, как и у Bedolaga.
"""

from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.bedolaga_import import (
    Report,
    _balance_moved,
    _bool,
    _dt,
    _int,
)
from shared.db.models import BotSubscription, BotUser, Wallet, WalletTransaction, WalletTxType
from shared.remnawave.models import User as RemoteUser
from shared.sync import refresh_user_summary

BALANCE_MARK = "Перенос баланса из STEALTHNET"
# Подарочные подписки, ещё не отданные получателю.
_UNCLAIMED_GIFTS = {"GIFT_RESERVED", "GIFT_CODE_ACTIVE"}


@dataclass
class RemoteIndex:
    """Все пользователи Remnawave, разложенные по способам поиска."""

    by_uuid: dict[str, RemoteUser] = field(default_factory=dict)
    by_short: dict[str, RemoteUser] = field(default_factory=dict)
    by_telegram: dict[int, list[RemoteUser]] = field(default_factory=dict)

    @classmethod
    def build(cls, users: list[RemoteUser]) -> "RemoteIndex":
        index = cls()
        for user in users:
            if user.uuid:
                index.by_uuid[user.uuid] = user
            if user.short_uuid:
                index.by_short[user.short_uuid] = user
            if user.telegram_id is not None:
                index.by_telegram.setdefault(user.telegram_id, []).append(user)
        return index


async def load_remote_index(client) -> RemoteIndex:
    """Выкачивает всех пользователей Remnawave постранично."""
    users: list[RemoteUser] = []
    start, size = 0, 500
    while True:
        page = await client.get_users(start=start, size=size)
        users.extend(page.users)
        start += size
        if not page.users or start >= page.total:
            break
    return RemoteIndex.build(users)


def store_currency(rows: dict[str, list[dict]]) -> str:
    for row in rows.get("system_settings", []):
        if row.get("key") == "default_currency" and row.get("value"):
            return str(row["value"]).strip().lower()
    return "usd"


async def apply(
    db: AsyncSession,
    rows: dict[str, list[dict]],
    *,
    rub_rate: float,
    remote: RemoteIndex | None,
    dry_run: bool = False,
) -> Report:
    report = Report()
    tariffs = {t.get("id"): t.get("name") for t in rows.get("tariffs", [])}
    clients = rows.get("clients", [])

    # Кто реально пользуется подпиской: у переданного подарка — получатель.
    subs_by_client: dict[str, list[dict]] = {}
    skipped_gifts = 0
    for sub in rows.get("subscriptions", []):
        gift = sub.get("gift_status")
        if gift in _UNCLAIMED_GIFTS:
            skipped_gifts += 1
            continue
        holder = sub.get("gifted_to_client_id") if gift == "GIFTED" else None
        subs_by_client.setdefault(holder or sub.get("owner_id"), []).append(sub)
    if skipped_gifts:
        report.warnings.append(f"Не забранных подарочных подписок пропущено: {skipped_gifts}")

    by_source_id: dict[str, BotUser] = {}
    referrer_of: dict[str, str] = {}

    for row in clients:
        telegram_id = _int(row.get("telegram_id"))
        source_id = row.get("id")
        if telegram_id is None:
            report.users_skipped += 1  # регистрация по email/Google — не из Telegram
            continue

        user = await db.scalar(select(BotUser).where(BotUser.telegram_id == telegram_id))
        if user is None:
            user = BotUser(telegram_id=telegram_id)
            created = _dt(row.get("created_at"))
            if created:
                user.created_at = created
            db.add(user)
            report.users_created += 1
        else:
            report.users_updated += 1

        user.username = user.username or (row.get("telegram_username") or None)
        lang = row.get("preferred_lang")
        user.language_code = user.language_code or (lang[:8] if lang else None)
        if _bool(row.get("is_blocked")):
            user.is_blocked = True
        if _bool(row.get("telegram_unreachable")):
            user.has_stopped_bot = True

        own_subs = subs_by_client.get(source_id, [])
        paid_before = any(not s.get("trial_id") and s.get("tariff_id") for s in own_subs)
        if _bool(row.get("trial_used")) or own_subs:
            user.trial_used = True
        if paid_before:
            user.referral_reward_paid = True
        # Разовая персональная скидка — как наша скидка из промокода (сгорает
        # после покупки). Бессрочную скидку админа так не перенести.
        if _bool(row.get("personal_discount_is_one_time")):
            percent = int(round(float(row.get("personal_discount_percent") or 0)))
            if 0 < percent <= 100 and not user.discount_percent:
                user.discount_percent = percent
        await db.flush()

        code = (row.get("referral_code") or "").strip()
        if code and not user.referral_code and len(code) <= 16:
            taken = await db.scalar(select(BotUser.id).where(BotUser.referral_code == code))
            if taken is None:
                user.referral_code = code

        balance = round(float(row.get("balance") or 0) * rub_rate * 100)
        if balance > 0 and not await _balance_moved(db, user, BALANCE_MARK):
            wallet = await db.scalar(select(Wallet).where(Wallet.user_id == user.id))
            if wallet is None:
                wallet = Wallet(user_id=user.id, balance_kopeks=0)
                db.add(wallet)
                await db.flush()
            wallet.balance_kopeks += balance
            db.add(
                WalletTransaction(
                    wallet_id=wallet.id,
                    amount_kopeks=balance,
                    type=WalletTxType.ADMIN_ADJUST,
                    description=BALANCE_MARK,
                )
            )
            report.balances_moved += 1
            report.balance_total_kopeks += balance

        if remote is not None:
            imported = await _import_keys(db, user, row, own_subs, tariffs, remote, report)
            report.subscriptions_imported += imported
            if imported:
                await db.flush()
                await refresh_user_summary(db, user)
            user.remnawave_synced = True
        else:
            # Ключи подтянутся из Remnawave при первом /start клиента.
            user.remnawave_synced = False

        if source_id:
            by_source_id[source_id] = user
            if row.get("referrer_id"):
                referrer_of[source_id] = row["referrer_id"]

    for source_id, ref_id in referrer_of.items():
        user, referrer = by_source_id.get(source_id), by_source_id.get(ref_id)
        if user is None or referrer is None or user.id == referrer.id:
            continue
        if user.referred_by_id is None:
            user.referred_by_id = referrer.id
            report.referrals_linked += 1

    if remote is None and clients:
        report.warnings.append(
            "Нет связи с Remnawave — ключи не перенесены. Бот подтянет их сам, "
            "когда клиент нажмёт /start, или запустите перенос ещё раз."
        )

    await db.flush()
    if dry_run:
        await db.rollback()
    return report


async def _import_keys(
    db: AsyncSession,
    user: BotUser,
    client_row: dict,
    subs: list[dict],
    tariffs: dict,
    remote: RemoteIndex,
    report: Report,
) -> int:
    """Ключи клиента: из его подписок STEALTHNET, а также любые аккаунты
    Remnawave с его telegram id, которых в бэкапе не оказалось."""
    found: list[tuple[RemoteUser, dict | None]] = []
    seen: set[int | str] = set()

    def remember(remote_user: RemoteUser | None, sub: dict | None) -> None:
        if remote_user is None:
            return
        key = remote_user.id if remote_user.id is not None else remote_user.uuid
        if key in seen:
            return
        seen.add(key)
        found.append((remote_user, sub))

    for sub in subs:
        match = remote.by_uuid.get(sub.get("remnawave_uuid") or "") or remote.by_short.get(
            sub.get("short_uuid") or ""
        )
        if match is None and (sub.get("remnawave_uuid") or sub.get("short_uuid")):
            report.warnings.append(
                f"Ключ {sub.get('remnawave_uuid') or sub.get('short_uuid')} не найден в Remnawave"
            )
        remember(match, sub)
    remember(remote.by_uuid.get(client_row.get("remnawave_uuid") or ""), None)
    for remote_user in remote.by_telegram.get(user.telegram_id, []):
        remember(remote_user, None)

    imported = 0
    for remote_user, sub in found:
        exists = None
        if remote_user.id is not None:
            exists = await db.scalar(
                select(BotSubscription.id).where(BotSubscription.remnawave_id == remote_user.id)
            )
        elif remote_user.uuid:
            exists = await db.scalar(
                select(BotSubscription.id).where(
                    BotSubscription.remnawave_uuid == remote_user.uuid
                )
            )
        if exists is not None:
            continue
        title = tariffs.get(sub.get("tariff_id")) if sub else None
        db.add(
            BotSubscription(
                user_id=user.id,
                remnawave_id=remote_user.id or 0,
                # uuid нужен только старым панелям без числового id.
                remnawave_uuid=remote_user.uuid if remote_user.id is None else None,
                username=remote_user.username[:64],
                subscription_url=remote_user.subscription_url,
                expire_at=remote_user.expire_at or (_dt(sub.get("expire_at")) if sub else None),
                title=str(title)[:64] if title else None,
                is_trial=bool(sub and sub.get("trial_id")),
            )
        )
        imported += 1
    return imported
