"""Перенос из STEALTHNET: настоящий `pg_dump -F p --no-owner --no-acl --clean
--if-exists` (как делает сам STEALTHNET) базы с таблицами Prisma-схемы.

Ключи сопоставляются с Remnawave — здесь она подменена индексом с
пользователями, которых «видит» панель.
"""

from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.api.v1 import migration
from app.main import app
from app.services import bedolaga_import as bi
from app.services import stealthnet_import as sn
from shared.db.models import BotSubscription, BotUser, Wallet
from shared.db.session import SessionLocal
from shared.remnawave.models import User as RemoteUser
from tests.test_panel_api import LOGIN, PASSWORD, _db  # noqa: F401  — фикстура БД

BACKUP = Path(__file__).parent / "fixtures" / "stealthnet-backup-2026-10-06T12-00-00.sql"


def _remote(id_, uuid, short=None, tg=None) -> RemoteUser:
    return RemoteUser.model_validate(
        {
            "id": id_,
            "uuid": uuid,
            "shortUuid": short,
            "username": f"rw_{id_}",
            "status": "ACTIVE",
            "telegramId": tg,
            "expireAt": "2027-01-01T00:00:00Z",
            "subscriptionUrl": f"https://sub.example/{id_}",
        }
    )


REMOTE = sn.RemoteIndex.build(
    [
        _remote(701, "uuid-a1", "shortA1", 2001),
        _remote(702, "uuid-b1-real", "shortB1", 2002),  # найдётся по короткому uuid
        _remote(703, "uuid-gift"),  # подарок, переданный ck_gift
        _remote(704, "uuid-legacy-alice", tg=2001),  # старый основной ключ клиента
        _remote(705, "uuid-extra-bob", tg=2002),  # есть только в Remnawave
        _remote(706, "uuid-unclaimed"),  # не забранный подарок — не переносим
    ]
)


def test_detects_stealthnet_and_currency() -> None:
    rows = bi.read_backup(str(BACKUP))
    assert bi.detect_source(rows) == "stealthnet"
    assert sn.store_currency(rows) == "usd"
    assert len(rows["clients"]) == 4
    assert rows["clients"][3]["telegram_username"] == "gift\tee"


async def test_import_with_rate_and_remnawave_keys() -> None:
    rows = bi.read_backup(str(BACKUP))
    async with SessionLocal() as db:
        report = await sn.apply(db, rows, rub_rate=90, remote=REMOTE)
        await db.commit()

    assert report.users_created == 3 and report.users_skipped == 1  # email-only
    assert report.balances_moved == 2
    assert report.balance_total_kopeks == round(12.5 * 90 * 100) + round(1.99 * 90 * 100)
    assert report.subscriptions_imported == 5
    assert report.referrals_linked == 2
    assert any("uuid-missing" in w for w in report.warnings)
    assert any("подарочных" in w for w in report.warnings)

    async with SessionLocal() as db:
        users = {u.telegram_id: u for u in await db.scalars(select(BotUser))}
        keys = list(await db.scalars(select(BotSubscription)))
        alice_wallet = await db.scalar(
            select(Wallet.balance_kopeks).where(Wallet.user_id == users[2001].id)
        )
    alice, bob, gift = users[2001], users[2002], users[2004]
    assert alice_wallet == 112500
    assert alice.referral_code == "REF-AB12CD34" and alice.referral_reward_paid
    assert bob.referred_by_id == alice.id and gift.referred_by_id == bob.id
    assert bob.discount_percent == 15 and bob.has_stopped_bot and not bob.referral_reward_paid
    assert gift.is_blocked

    owners = {}
    for key in keys:
        owners.setdefault(key.user_id, set()).add(key.remnawave_id)
    assert owners[alice.id] == {701, 704}
    assert owners[bob.id] == {702, 705}
    assert owners[gift.id] == {703}
    assert all(k.remnawave_uuid is None for k in keys)  # новые панели — по id
    trial = next(k for k in keys if k.remnawave_id == 702)
    assert trial.is_trial and trial.title == "Триал"


async def test_import_is_idempotent() -> None:
    rows = bi.read_backup(str(BACKUP))
    for _ in range(2):
        async with SessionLocal() as db:
            last = await sn.apply(db, rows, rub_rate=90, remote=REMOTE)
            await db.commit()
    assert last.users_created == 0 and last.balances_moved == 0
    assert last.subscriptions_imported == 0


async def test_without_remnawave_keys_are_left_for_start() -> None:
    rows = bi.read_backup(str(BACKUP))
    async with SessionLocal() as db:
        report = await sn.apply(db, rows, rub_rate=90, remote=None)
        await db.commit()
        users = list(await db.scalars(select(BotUser)))
    assert report.subscriptions_imported == 0
    assert any("/start" in w for w in report.warnings)
    assert all(not u.remnawave_synced for u in users)


def test_endpoint_requires_rate_for_foreign_currency(monkeypatch) -> None:
    async def fake_index(db):
        return REMOTE

    monkeypatch.setattr(migration, "_remote_index", fake_index)
    with TestClient(app) as c:
        c.post("/api/v1/auth/login", json={"login": LOGIN, "password": PASSWORD})

        def upload(**data):
            with open(BACKUP, "rb") as f:
                return c.post(
                    "/api/v1/bot/import",
                    files={"file": (BACKUP.name, f, "application/sql")},
                    data=data,
                )

        dry = upload(dry_run="true").json()
        assert dry["source"] == "stealthnet" and dry["needs_rate"] is True
        assert dry["currency"] == "usd"

        assert upload(dry_run="false").status_code == 422  # без курса нельзя
        done = upload(dry_run="false", rub_rate="90").json()
        assert done["balance_total_rub"] == 1125 + 179.1
        assert done["subscriptions_imported"] == 5
