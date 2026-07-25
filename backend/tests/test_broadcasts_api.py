"""Рассылки и аналитика: сегменты, создание, отмена, живой прогресс."""

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app.main import app
from shared.db.models import (
    BotUser,
    Broadcast,
    BroadcastStatus,
    Payment,
    PaymentProvider,
    PaymentPurpose,
    PaymentStatus,
    Plan,
    Purchase,
)
from shared.db.session import SessionLocal
from tests.test_panel_api import LOGIN, PASSWORD, _db  # noqa: F401 — фикстура БД


@pytest.fixture
def client(monkeypatch):
    published: list[str] = []
    monkeypatch.setattr(
        "app.api.v1.broadcasts.bus.publish",
        lambda cmd, **kw: published.append(cmd) or _noop(),
    )
    with TestClient(app) as c:
        c.published = published  # type: ignore[attr-defined]
        c.post("/api/v1/auth/login", json={"login": LOGIN, "password": PASSWORD})
        yield c


async def _noop():
    return None


async def _seed_users() -> None:
    now = datetime.now(UTC)
    async with SessionLocal() as db:
        db.add_all(
            [
                BotUser(telegram_id=1, expire_at=now + timedelta(days=10)),  # active
                BotUser(telegram_id=2, expire_at=now - timedelta(days=5)),  # expired
                BotUser(telegram_id=3),  # no_purchase, never subscribed
                BotUser(telegram_id=4, has_stopped_bot=True),  # заблокировал бота
            ]
        )
        await db.commit()


def test_segment_counts(client: TestClient) -> None:
    import asyncio

    asyncio.get_event_loop().run_until_complete(_seed_users())

    counts = client.get("/api/v1/broadcasts/segments/counts").json()
    assert counts["all"] == 3  # без учёта заблокировавшего бота
    assert counts["active"] == 1
    assert counts["expired"] == 1
    assert counts["no_purchase"] == 3  # никто из троих не платил


def test_create_broadcast_send_now_publishes_event(client: TestClient) -> None:
    r = client.post(
        "/api/v1/broadcasts",
        json={"text": "Привет!", "segment": "all"},
    )
    assert r.status_code == 201
    body = r.json()
    assert body["status"] == "scheduled"
    assert client.published == ["broadcast_ready"]


def test_create_broadcast_scheduled_does_not_publish(client: TestClient) -> None:
    future = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
    client.post(
        "/api/v1/broadcasts", json={"text": "Позже", "segment": "all", "scheduled_at": future}
    )
    assert client.published == []


def test_scheduled_at_in_past_rejected(client: TestClient) -> None:
    past = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
    r = client.post(
        "/api/v1/broadcasts", json={"text": "х", "segment": "all", "scheduled_at": past}
    )
    assert r.status_code == 422


def test_buttons_validated(client: TestClient) -> None:
    r = client.post(
        "/api/v1/broadcasts",
        json={
            "text": "х",
            "segment": "all",
            "buttons": [{"text": "Купить", "url": "https://example.com"}],
        },
    )
    assert r.status_code == 201
    assert r.json()["buttons"] == [{"text": "Купить", "url": "https://example.com"}]


def test_cancel_scheduled_broadcast(client: TestClient) -> None:
    future = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
    created = client.post(
        "/api/v1/broadcasts", json={"text": "х", "segment": "all", "scheduled_at": future}
    ).json()

    assert client.delete(f"/api/v1/broadcasts/{created['id']}").status_code == 204
    listed = client.get("/api/v1/broadcasts").json()
    assert listed[0]["status"] == "cancelled"


async def test_cannot_cancel_already_sending(client: TestClient) -> None:
    async with SessionLocal() as db:
        b = Broadcast(
            text="x",
            segment="all",
            status=BroadcastStatus.SENDING,
            scheduled_at=datetime.now(UTC),
            created_at=datetime.now(UTC),
        )
        db.add(b)
        await db.commit()
        await db.refresh(b)
        bid = b.id

    assert client.delete(f"/api/v1/broadcasts/{bid}").status_code == 409


def test_broadcasts_require_auth() -> None:
    with TestClient(app) as anon:
        assert anon.get("/api/v1/broadcasts").status_code == 401
        assert anon.post("/api/v1/broadcasts", json={"text": "x"}).status_code == 401


def test_broadcast_ws_requires_auth() -> None:
    with TestClient(app) as anon:
        with pytest.raises(Exception):  # noqa: B017 — рукопожатие обрывается без логина
            with anon.websocket_connect("/api/v1/broadcasts/1/ws"):
                pass


def test_broadcast_ws_streams_progress(client: TestClient) -> None:
    import asyncio

    async def seed() -> int:
        async with SessionLocal() as db:
            b = Broadcast(
                text="x",
                segment="all",
                status=BroadcastStatus.SENDING,
                total_recipients=10,
                sent_count=3,
                failed_count=1,
                scheduled_at=datetime.now(UTC),
                created_at=datetime.now(UTC),
            )
            db.add(b)
            await db.commit()
            await db.refresh(b)
            return b.id

    broadcast_id = asyncio.get_event_loop().run_until_complete(seed())

    with client.websocket_connect(f"/api/v1/broadcasts/{broadcast_id}/ws") as ws:
        data = ws.receive_json()
        assert data["sent_count"] == 3
        assert data["failed_count"] == 1
        assert data["status"] == "sending"


# ── Аналитика ─────────────────────────────────────────────────────────
async def _seed_analytics() -> None:
    now = datetime.now(UTC)
    async with SessionLocal() as db:
        user1 = BotUser(telegram_id=101, trial_used=True)
        user2 = BotUser(telegram_id=102, trial_used=True)
        user3 = BotUser(telegram_id=103, trial_used=False)
        db.add_all([user1, user2, user3])
        await db.flush()

        plan = Plan(title="Месяц", days=30, price_kopeks=19900)
        db.add(plan)
        await db.flush()

        db.add_all(
            [
                Purchase(
                    user_id=user1.id,
                    plan_id=plan.id,
                    days=30,
                    amount_kopeks=19900,
                    source="platega",
                    expire_at=now,
                ),
                Purchase(
                    user_id=user2.id, days=3, amount_kopeks=0, source="trial", expire_at=now
                ),
            ]
        )
        db.add(
            Payment(
                user_id=user1.id,
                provider=PaymentProvider.PLATEGA,
                external_id="tx-1",
                amount_kopeks=19900,
                purpose=PaymentPurpose.PLAN,
                status=PaymentStatus.PAID,
                paid_at=now,
            )
        )
        await db.commit()


def test_analytics_overview(client: TestClient) -> None:
    import asyncio

    asyncio.get_event_loop().run_until_complete(_seed_analytics())

    body = client.get("/api/v1/analytics/overview").json()
    assert sum(d["value"] for d in body["revenue_daily"]) == 199.0
    assert sum(d["value"] for d in body["new_users_daily"]) == 3
    assert body["trial_conversion"] == {"trial_users": 2, "converted": 1, "rate": 50.0}
    assert body["top_plans"][0]["title"] == "Месяц"
    assert body["top_plans"][0]["purchases"] == 1
    assert body["top_plans"][0]["revenue_rub"] == 199.0


def test_analytics_export_csv(client: TestClient) -> None:
    import asyncio

    asyncio.get_event_loop().run_until_complete(_seed_analytics())

    r = client.get("/api/v1/analytics/export/payments.csv")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/csv")
    assert "tx-1" in r.text
    assert "199.00" in r.text


# ── Загрузка фото ─────────────────────────────────────────────────────
def test_upload_photo_accepts_image(client: TestClient) -> None:
    r = client.post(
        "/api/v1/broadcasts/upload-photo",
        files={"file": ("pic.jpg", b"\xff\xd8\xff\xe0fake-jpeg", "image/jpeg")},
    )
    assert r.status_code == 200
    assert r.json()["url"].endswith(".jpg")
    assert "/uploads/" in r.json()["url"]


def test_upload_photo_rejects_wrong_type(client: TestClient) -> None:
    r = client.post(
        "/api/v1/broadcasts/upload-photo",
        files={"file": ("doc.pdf", b"%PDF-1.4", "application/pdf")},
    )
    assert r.status_code == 422


def test_upload_photo_rejects_too_large(client: TestClient) -> None:
    big = b"0" * (8 * 1024 * 1024 + 1)
    r = client.post(
        "/api/v1/broadcasts/upload-photo",
        files={"file": ("pic.jpg", big, "image/jpeg")},
    )
    assert r.status_code == 413


def test_upload_photo_requires_auth() -> None:
    with TestClient(app) as anon:
        r = anon.post(
            "/api/v1/broadcasts/upload-photo",
            files={"file": ("pic.jpg", b"x", "image/jpeg")},
        )
        assert r.status_code == 401
