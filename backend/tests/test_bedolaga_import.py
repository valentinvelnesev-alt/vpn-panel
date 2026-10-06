"""Перенос из Bedolaga: чтение бэкапа во всех форматах и идемпотентный импорт.

Фикстура — настоящий `pg_dump --format=plain` базы со схемой Bedolaga
(таблицы users / subscriptions / tariffs с их колонками), упакованный в
архив так же, как это делает сам Bedolaga (database.sql + metadata.json).
"""

import json
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.main import app
from app.services import bedolaga_import as bi
from shared.db.models import BotSubscription, BotUser, Wallet
from shared.db.session import SessionLocal
from tests.test_panel_api import LOGIN, PASSWORD, _db  # noqa: F401  — фикстура БД

BACKUP = Path(__file__).parent / "fixtures" / "bedolaga_backup.tar.gz"


def test_reads_pg_dump_archive() -> None:
    rows = bi.read_backup(str(BACKUP))
    assert len(rows["users"]) == 6
    assert len(rows["subscriptions"]) == 3
    assert {t["name"] for t in rows["tariffs"]} == {"Стандарт", "Премиум 🚀"}
    alice = rows["users"][0]
    assert alice["first_name"] == "Алиса\tтаб"  # экранирование COPY разобрано
    assert rows["users"][1]["first_name"] == "Боб\nс переносом"
    assert rows["users"][1]["username"] is None  # \N → NULL


def test_parses_postgres_timezones() -> None:
    parsed = bi._dt("2025-03-01 09:00:00+04")
    assert parsed is not None and parsed.utcoffset().total_seconds() == 4 * 3600


async def _users() -> dict[int, BotUser]:
    async with SessionLocal() as db:
        return {u.telegram_id: u for u in await db.scalars(select(BotUser))}


async def test_import_users_balances_referrals_and_keys() -> None:
    rows = bi.read_backup(str(BACKUP))
    async with SessionLocal() as db:
        report = await bi.apply(db, rows)
        await db.commit()

    assert report.users_created == 4  # удалённый и email-only пропущены
    assert report.users_skipped == 2
    assert report.balances_moved == 2
    assert report.balance_total_kopeks == 15050 + 999
    assert report.subscriptions_imported == 3
    assert report.referrals_linked == 2

    users = await _users()
    alice, bob, blocked, legacy = users[1001], users[1002], users[1003], users[1006]
    assert alice.referral_code == "refAbC12345"
    assert bob.referred_by_id == alice.id and blocked.referred_by_id == bob.id
    assert blocked.is_blocked
    assert alice.trial_used and alice.referral_reward_paid
    assert bob.trial_used and not bob.referral_reward_paid

    async with SessionLocal() as db:
        keys = {k.user_id: k for k in await db.scalars(select(BotSubscription))}
        wallet = await db.scalar(select(Wallet).where(Wallet.user_id == alice.id))
    assert wallet.balance_kopeks == 15050
    assert keys[alice.id].remnawave_id == 501
    assert keys[alice.id].remnawave_uuid is None  # у новых панелей адресуем по id
    assert keys[alice.id].title == "Премиум 🚀" and not keys[alice.id].is_trial
    assert keys[bob.id].is_trial
    # Старый Bedolaga: аккаунт Remnawave лежал на пользователе, а не на подписке.
    assert keys[legacy.id].remnawave_id == 0
    assert keys[legacy.id].remnawave_uuid == "0b6c2f8e-1111-2222-3333-444455556666"
    assert alice.expire_at is not None  # сводка пересчитана по ключу


async def test_import_is_idempotent() -> None:
    rows = bi.read_backup(str(BACKUP))
    async with SessionLocal() as db:
        await bi.apply(db, rows)
        await db.commit()
    async with SessionLocal() as db:
        again = await bi.apply(db, rows)
        await db.commit()
    assert again.users_created == 0 and again.users_updated == 4
    assert again.balances_moved == 0 and again.subscriptions_imported == 0
    async with SessionLocal() as db:
        alice = await db.scalar(select(BotUser).where(BotUser.telegram_id == 1001))
        wallet = await db.scalar(select(Wallet).where(Wallet.user_id == alice.id))
    assert wallet.balance_kopeks == 15050  # баланс не начислен второй раз


async def test_dry_run_writes_nothing() -> None:
    rows = bi.read_backup(str(BACKUP))
    async with SessionLocal() as db:
        report = await bi.apply(db, rows, dry_run=True)
        await db.commit()
    assert report.users_created == 4
    assert await _users() == {}


def test_reads_json_and_sqlite(tmp_path) -> None:
    rows = bi.read_backup(str(BACKUP))
    as_json = tmp_path / "database.json"
    as_json.write_text(json.dumps({"metadata": {"version": "orm-1.0"}, "data": rows}))
    assert len(bi.read_backup(str(as_json))["users"]) == 6

    as_sqlite = tmp_path / "database.sqlite"
    con = sqlite3.connect(as_sqlite)
    con.execute("CREATE TABLE users (id INTEGER, telegram_id INTEGER, balance_kopeks INTEGER)")
    con.execute("INSERT INTO users VALUES (1, 42, 100)")
    con.commit()
    con.close()
    loaded = bi.read_backup(str(as_sqlite))
    assert loaded["users"][0]["telegram_id"] == 42 and loaded["subscriptions"] == []


def test_rejects_foreign_file(tmp_path) -> None:
    junk = tmp_path / "x.sql"
    junk.write_text("SELECT 1;\n")
    with pytest.raises(bi.ImportError_):
        bi.read_backup(str(junk))


def test_import_endpoint_dry_run_then_apply() -> None:
    with TestClient(app) as c:
        c.post("/api/v1/auth/login", json={"login": LOGIN, "password": PASSWORD})
        with open(BACKUP, "rb") as f:
            dry = c.post(
                "/api/v1/bot/import/bedolaga",
                files={"file": ("backup.tar.gz", f, "application/gzip")},
                data={"dry_run": "true"},
            )
        assert dry.status_code == 200, dry.text
        assert dry.json()["users_created"] == 4 and dry.json()["dry_run"] is True
        assert c.get("/api/v1/bot/clients").json()["total"] == 0

        with open(BACKUP, "rb") as f:
            done = c.post(
                "/api/v1/bot/import/bedolaga",
                files={"file": ("backup.tar.gz", f, "application/gzip")},
                data={"dry_run": "false"},
            ).json()
        assert done["balance_total_rub"] == 160.49
        assert c.get("/api/v1/bot/clients").json()["total"] == 4

        bad = c.post(
            "/api/v1/bot/import/bedolaga",
            files={"file": ("x.json", b'{"foo": 1}', "application/json")},
            data={"dry_run": "true"},
        )
        assert bad.status_code == 422
