"""Перенос клиентов из бота Bedolaga (remnawave-bedolaga-telegram-bot).

Источник — штатный бэкап Bedolaga: архив backup_*.tar.gz, который он сам
шлёт админу в Telegram. Внутри metadata.json и база в одном из форматов:
database.sql (pg_dump --format=plain), database.json (ORM-выгрузка) или
database.sqlite. Принимаем и сам архив, и отдельный файл базы.

Что переносим — то, что принадлежит клиенту и не восстановится само:
- пользователей (по telegram_id; email-only и удалённые пропускаем);
- баланс кошелька — одной операцией «Перенос из Bedolaga» в истории;
- реферальные связи и коды — старые ссылки t.me/bot?start=refXXXX работают;
- ключи Remnawave — срок, ссылка, название тарифа. Сами аккаунты в
  Remnawave не трогаем: бот подключается к той же панели.

Импорт идемпотентный: повторный запуск никого не дублирует и не начисляет
баланс второй раз (по метке в истории операций).
"""

import io
import json
import logging
import os
import sqlite3
import tarfile
import tempfile
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from shared.db.models import (
    BotSubscription,
    BotUser,
    Wallet,
    WalletTransaction,
    WalletTxType,
)
from shared.sync import refresh_user_summary

log = logging.getLogger("bedolaga_import")

# Таблицы, которые читаем из дампа: users — Bedolaga, clients и
# system_settings — STEALTHNET (см. stealthnet_import.py); остальные общие.
TABLES = ("users", "clients", "subscriptions", "tariffs", "system_settings")
BALANCE_MARK = "Перенос баланса из Bedolaga"


class ImportError_(Exception):
    """Понятная админу причина, по которой файл не прочитать."""


@dataclass
class Report:
    users_created: int = 0
    users_updated: int = 0
    users_skipped: int = 0
    balances_moved: int = 0
    balance_total_kopeks: int = 0
    subscriptions_imported: int = 0
    referrals_linked: int = 0
    warnings: list[str] = field(default_factory=list)


# ── Чтение бэкапа ─────────────────────────────────────────────────────
def read_backup(path: str) -> dict[str, list[dict]]:
    """Строки нужных таблиц из архива или файла базы."""
    rows = _read_any(path)
    if not rows.get("users") and not rows.get("clients"):
        raise ImportError_(
            "В файле нет клиентов — это точно бэкап Bedolaga или STEALTHNET?"
        )
    return rows


def detect_source(rows: dict[str, list[dict]]) -> str:
    """Чей это бэкап: у STEALTHNET клиенты в таблице clients, у Bedolaga — users."""
    return "stealthnet" if rows.get("clients") else "bedolaga"


def _read_any(path: str) -> dict[str, list[dict]]:
    if tarfile.is_tarfile(path):
        with tarfile.open(path) as tar, tempfile.TemporaryDirectory() as tmp:
            names = {os.path.basename(m.name): m for m in tar.getmembers() if m.isfile()}
            for candidate in ("database.json", "database.sql", "database.sqlite"):
                member = names.get(candidate)
                if member is None:
                    continue
                if candidate == "database.sql":
                    source = tar.extractfile(member)
                    return _read_sql(io.TextIOWrapper(source, encoding="utf-8", errors="replace"))
                target = os.path.join(tmp, candidate)
                with tar.extractfile(member) as src, open(target, "wb") as dst:
                    while chunk := src.read(1 << 20):
                        dst.write(chunk)
                return _read_any(target)
            raise ImportError_(
                "В архиве нет базы (database.sql / database.json / database.sqlite) — "
                "это точно бэкап Bedolaga?"
            )

    with open(path, "rb") as f:
        head = f.read(16)
    if head.startswith(b"SQLite format 3"):
        return _read_sqlite(path)
    if head.lstrip().startswith(b"{"):
        with open(path, encoding="utf-8") as f:
            return _read_json(json.load(f))
    with open(path, encoding="utf-8", errors="replace") as f:
        return _read_sql(f)


def _read_json(dump: dict) -> dict[str, list[dict]]:
    data = dump.get("data") if isinstance(dump, dict) else None
    if not isinstance(data, dict):
        raise ImportError_("JSON не похож на выгрузку бота: нет раздела data")
    return {table: list(data.get(table) or []) for table in TABLES}


def _read_sqlite(path: str) -> dict[str, list[dict]]:
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    try:
        result = {}
        for table in TABLES:
            try:
                result[table] = [dict(r) for r in con.execute(f"SELECT * FROM {table}")]
            except sqlite3.OperationalError:
                result[table] = []
        return result
    finally:
        con.close()


def _unescape_copy(value: str) -> str | None:
    """Поле в текстовом формате COPY: \\N — NULL, обратные слэши — escape."""
    if value == "\\N":
        return None
    if "\\" not in value:
        return value
    out, i = [], 0
    mapping = {"t": "\t", "n": "\n", "r": "\r", "b": "\b", "f": "\f", "v": "\v", "\\": "\\"}
    while i < len(value):
        ch = value[i]
        if ch == "\\" and i + 1 < len(value):
            nxt = value[i + 1]
            out.append(mapping.get(nxt, nxt))
            i += 2
        else:
            out.append(ch)
            i += 1
    return "".join(out)


def _read_sql(lines: Iterator[str]) -> dict[str, list[dict]]:
    """Достаёт данные из блоков `COPY public.<table> (...) FROM stdin;` —
    так pg_dump пишет строки. Поднимать Postgres ради этого не нужно."""
    result: dict[str, list[dict]] = {table: [] for table in TABLES}
    current: str | None = None
    columns: list[str] = []
    for line in lines:
        if current is None:
            if not line.startswith("COPY "):
                continue
            head = line[5:]
            name = head.split(" ", 1)[0].split(".")[-1].strip('"')
            if name in TABLES:
                current = name
                inside = head[head.index("(") + 1 : head.index(")")]
                columns = [c.strip().strip('"') for c in inside.split(",")]
            continue
        if line.rstrip("\n") == "\\.":
            current = None
            continue
        values = line.rstrip("\n").split("\t")
        result[current].append(
            {col: _unescape_copy(v) for col, v in zip(columns, values, strict=False)}
        )
    return result


# ── Значения ──────────────────────────────────────────────────────────
def _int(value) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _bool(value) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).lower() in ("t", "true", "1")


def _dt(value) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        parsed = value
    else:
        text = str(value).replace("Z", "+00:00")
        # pg_dump пишет «2026-01-02 03:04:05.123+05»: дополним зону до ±HH:MM.
        if len(text) > 3 and text[-3] in "+-" and text[-2:].isdigit():
            text += ":00"
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


# ── Перенос ───────────────────────────────────────────────────────────
async def apply(db: AsyncSession, rows: dict[str, list[dict]], *, dry_run: bool = False) -> Report:
    report = Report()
    tariffs = {_int(t.get("id")): t.get("name") for t in rows.get("tariffs", [])}

    subs_by_user: dict[int, list[dict]] = {}
    for sub in rows.get("subscriptions", []):
        subs_by_user.setdefault(_int(sub.get("user_id")), []).append(sub)

    by_source_id: dict[int, BotUser] = {}
    referrer_of: dict[int, int] = {}

    for row in rows.get("users", []):
        telegram_id = _int(row.get("telegram_id"))
        source_id = _int(row.get("id"))
        if telegram_id is None or row.get("status") == "deleted":
            report.users_skipped += 1
            continue

        user = await db.scalar(select(BotUser).where(BotUser.telegram_id == telegram_id))
        if user is None:
            user = BotUser(
                telegram_id=telegram_id,
                created_at=_dt(row.get("created_at")) or datetime.now(UTC),
            )
            db.add(user)
            report.users_created += 1
        else:
            report.users_updated += 1

        user.username = user.username or row.get("username")
        user.first_name = user.first_name or row.get("first_name")
        user.language_code = user.language_code or (row.get("language") or None)
        if user.language_code:
            user.language_code = user.language_code[:8]
        user.last_seen_at = user.last_seen_at or _dt(row.get("last_activity"))
        if row.get("status") == "blocked":
            user.is_blocked = True
        paid_before = _bool(row.get("has_had_paid_subscription"))
        own_subs = subs_by_user.get(source_id, [])
        # Кто уже пользовался Bedolaga, второй раз триал не получает, а за
        # уже сделанную первую оплату реферер повторно не награждается.
        if paid_before or own_subs:
            user.trial_used = True
        if paid_before:
            user.referral_reward_paid = True
        await db.flush()

        code = (row.get("referral_code") or "").strip()
        if code and not user.referral_code and len(code) <= 16:
            taken = await db.scalar(select(BotUser.id).where(BotUser.referral_code == code))
            if taken is None:
                user.referral_code = code

        balance = _int(row.get("balance_kopeks")) or 0
        if balance > 0 and not await _balance_moved(db, user):
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

        imported = await _import_subscriptions(db, user, row, own_subs, tariffs)
        report.subscriptions_imported += imported
        if imported:
            await db.flush()
            await refresh_user_summary(db, user)
        # Ключи, которых не оказалось в бэкапе, бот подтянет из Remnawave
        # по telegram_id при первом /start (см. link_from_remnawave).
        user.remnawave_synced = bool(imported)

        if source_id is not None:
            by_source_id[source_id] = user
            ref = _int(row.get("referred_by_id"))
            if ref is not None:
                referrer_of[source_id] = ref

    for source_id, ref_source_id in referrer_of.items():
        user, referrer = by_source_id.get(source_id), by_source_id.get(ref_source_id)
        if user is None or referrer is None or user.id == referrer.id:
            continue
        if user.referred_by_id is None:
            user.referred_by_id = referrer.id
            report.referrals_linked += 1

    await db.flush()
    if dry_run:
        await db.rollback()
    return report


async def _balance_moved(db: AsyncSession, user: BotUser, mark: str = BALANCE_MARK) -> bool:
    found = await db.scalar(
        select(WalletTransaction.id)
        .join(Wallet, Wallet.id == WalletTransaction.wallet_id)
        .where(Wallet.user_id == user.id, WalletTransaction.description == mark)
        .limit(1)
    )
    return found is not None


async def _import_subscriptions(
    db: AsyncSession, user: BotUser, row: dict, subs: list[dict], tariffs: dict
) -> int:
    imported = 0
    for sub in subs:
        remote_id = _int(sub.get("remnawave_id"))
        remote_uuid = sub.get("remnawave_uuid") or None
        if remote_id is None and remote_uuid is None and len(subs) == 1:
            # Старые версии Bedolaga хранили аккаунт Remnawave на пользователе.
            remote_id = _int(row.get("remnawave_id"))
            remote_uuid = row.get("remnawave_uuid") or None
        if remote_id is None and remote_uuid is None:
            continue
        if remote_id is not None:
            # Новые панели адресуют пользователя числовым id; uuid у Bedolaga
            # в этом случае исторический и для запросов не годится.
            remote_uuid = None

        exists = None
        if remote_id is not None:
            exists = await db.scalar(
                select(BotSubscription.id).where(BotSubscription.remnawave_id == remote_id)
            )
        elif remote_uuid:
            exists = await db.scalar(
                select(BotSubscription.id).where(BotSubscription.remnawave_uuid == remote_uuid)
            )
        if exists is not None:
            continue

        db.add(
            BotSubscription(
                user_id=user.id,
                remnawave_id=remote_id or 0,
                remnawave_uuid=remote_uuid,
                username=(sub.get("remnawave_short_uuid") or f"bedolaga_{sub.get('id')}")[:64],
                subscription_url=sub.get("subscription_url") or None,
                expire_at=_dt(sub.get("end_date")),
                title=(tariffs.get(_int(sub.get("tariff_id"))) or None) and str(
                    tariffs[_int(sub.get("tariff_id"))]
                )[:64],
                is_trial=_bool(sub.get("is_trial")),
            )
        )
        imported += 1
    return imported
