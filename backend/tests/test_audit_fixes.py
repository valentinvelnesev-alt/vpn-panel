"""Регрессии по аудиту: удаление тарифа с незавершённой оплатой, цена тарифа,
бан клиента в боте."""

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app.main import app
from shared.db.models import BotUser, Payment, PaymentStatus, Plan
from shared.db.session import SessionLocal
from tests.test_panel_api import LOGIN, PASSWORD, _db  # noqa: F401  — фикстура БД


async def _noop(*args, **kwargs):
    return None


@pytest.fixture
def client(monkeypatch):
    for target in ("app.api.v1.bot.bus.publish", "app.api.v1.broadcasts.bus.publish"):
        monkeypatch.setattr(target, _noop)
    with TestClient(app) as c:
        assert c.post("/api/v1/auth/login", json={"login": LOGIN, "password": PASSWORD}).status_code == 200
        yield c


PLAN = {"title": "Месяц", "days": 30, "price_rub": 199, "squad_uuids": ["s-1"], "hwid_limit": 3}


async def _payment(plan_id: int, status_: PaymentStatus, *, age: timedelta = timedelta(0)) -> None:
    async with SessionLocal() as db:
        user = BotUser(telegram_id=4242)
        db.add(user)
        await db.flush()
        db.add(
            Payment(
                user_id=user.id,
                provider="platega",
                external_id=f"tx-{status_}-{age.total_seconds()}",
                amount_kopeks=19900,
                purpose="plan",
                plan_id=plan_id,
                status=status_,
                created_at=datetime.now(UTC) - age,
            )
        )
        await db.commit()


async def test_plan_with_paid_unapplied_payment_cannot_be_deleted(client: TestClient) -> None:
    plan = client.post("/api/v1/bot/plans", json=PLAN).json()
    await _payment(plan["id"], PaymentStatus.PAID)

    r = client.delete(f"/api/v1/bot/plans/{plan['id']}")
    assert r.status_code == 409
    assert "Отключите" in r.json()["detail"]
    assert len(client.get("/api/v1/bot/plans").json()) == 1


async def test_plan_with_recent_pending_payment_cannot_be_deleted(client: TestClient) -> None:
    plan = client.post("/api/v1/bot/plans", json=PLAN).json()
    await _payment(plan["id"], PaymentStatus.PENDING)
    assert client.delete(f"/api/v1/bot/plans/{plan['id']}").status_code == 409


async def test_abandoned_invoice_does_not_block_deletion(client: TestClient) -> None:
    plan = client.post("/api/v1/bot/plans", json=PLAN).json()
    await _payment(plan["id"], PaymentStatus.PENDING, age=timedelta(days=3))
    assert client.delete(f"/api/v1/bot/plans/{plan['id']}").status_code == 204


def test_zero_price_plan_rejected(client: TestClient) -> None:
    assert client.post("/api/v1/bot/plans", json={**PLAN, "price_rub": 0}).status_code == 422
    assert (
        client.post(
            "/api/v1/bot/traffic-packages",
            json={"title": "10 ГБ", "traffic_gb": 10, "price_rub": 0},
        ).status_code
        == 422
    )


async def test_legacy_zero_price_plan_still_listed(client: TestClient) -> None:
    # Тарифы, сохранённые до проверки цены, должны читаться, а не ронять список.
    async with SessionLocal() as db:
        db.add(Plan(title="Старый", days=30, price_kopeks=0))
        await db.commit()
    plans = client.get("/api/v1/bot/plans").json()
    assert plans[0]["price_rub"] == 0


async def test_block_and_unblock_client(client: TestClient) -> None:
    async with SessionLocal() as db:
        user = BotUser(telegram_id=999, username="spammer")
        db.add(user)
        await db.commit()
        user_id = user.id

    body = client.post(f"/api/v1/bot/clients/{user_id}/block", json={"blocked": True}).json()
    assert body["is_blocked"] is True
    listed = client.get("/api/v1/bot/clients").json()["items"]
    assert listed[0]["is_blocked"] is True

    body = client.post(f"/api/v1/bot/clients/{user_id}/block", json={"blocked": False}).json()
    assert body["is_blocked"] is False


def test_block_requires_auth() -> None:
    with TestClient(app) as anon:
        assert anon.post("/api/v1/bot/clients/1/block", json={"blocked": True}).status_code == 401
