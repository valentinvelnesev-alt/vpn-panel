"""Платёжные провайдеры, промокоды, рефералка, вебхуки — со стороны панели."""

import httpx
import pytest
from fastapi.testclient import TestClient

from app.main import app
from shared.db.models import Payment, PaymentProvider, PaymentPurpose, PaymentStatus
from shared.db.session import SessionLocal
from tests.test_panel_api import LOGIN, PASSWORD, _db  # noqa: F401 — фикстура БД


@pytest.fixture
def client():
    with TestClient(app) as c:
        c.post("/api/v1/auth/login", json={"login": LOGIN, "password": PASSWORD})
        yield c


# ── Провайдеры ────────────────────────────────────────────────────────
def test_providers_start_disabled(client: TestClient) -> None:
    body = client.get("/api/v1/payments/providers").json()
    assert body == {
        "platega_enabled": False,
        "platega_merchant_id": None,
        "platega_secret_masked": None,
        "cryptobot_enabled": False,
        "cryptobot_token_masked": None,
        "stars_enabled": False,
    }


def test_save_platega_masks_secret(client: TestClient) -> None:
    body = client.put(
        "/api/v1/payments/providers/platega",
        json={"enabled": True, "merchant_id": "m-1", "secret": "super-secret-value"},
    ).json()
    assert body["platega_enabled"] is True
    assert body["platega_merchant_id"] == "m-1"
    assert "super-secret-value" not in str(body)
    assert "•" in body["platega_secret_masked"]


def test_save_cryptobot_and_stars(client: TestClient) -> None:
    cb = client.put(
        "/api/v1/payments/providers/cryptobot",
        json={"enabled": True, "token": "12345:AA-token"},
    ).json()
    assert cb["cryptobot_enabled"] is True
    assert "•" in cb["cryptobot_token_masked"]

    stars = client.put(
        "/api/v1/payments/providers/stars", json={"enabled": True}
    ).json()
    assert stars["stars_enabled"] is True


def test_empty_secret_keeps_previous(client: TestClient) -> None:
    client.put(
        "/api/v1/payments/providers/platega",
        json={"enabled": True, "merchant_id": "m-1", "secret": "first-secret"},
    )
    body = client.put(
        "/api/v1/payments/providers/platega",
        json={"enabled": False, "merchant_id": "m-1", "secret": ""},
    ).json()
    assert body["platega_enabled"] is False
    assert body["platega_secret_masked"] is not None  # секрет не стёрся


# ── Промокоды ─────────────────────────────────────────────────────────
def test_promo_requires_reward(client: TestClient) -> None:
    r = client.post(
        "/api/v1/bot/promo-codes",
        json={"code": "EMPTY", "bonus_days": 0, "discount_percent": 0},
    )
    assert r.status_code == 422


def test_promo_crud(client: TestClient) -> None:
    created = client.post(
        "/api/v1/bot/promo-codes",
        json={"code": "welcome10", "bonus_days": 3, "discount_percent": 0},
    ).json()
    assert created["code"] == "WELCOME10"  # приводится к верхнему регистру

    dup = client.post(
        "/api/v1/bot/promo-codes",
        json={"code": "WELCOME10", "bonus_days": 1, "discount_percent": 0},
    )
    assert dup.status_code == 409

    updated = client.patch(
        f"/api/v1/bot/promo-codes/{created['id']}",
        json={
            "code": "ignored",
            "bonus_days": 5,
            "discount_percent": 0,
            "is_active": False,
        },
    ).json()
    assert updated["bonus_days"] == 5
    assert updated["is_active"] is False
    assert updated["code"] == "WELCOME10"  # код не переименовывается

    assert len(client.get("/api/v1/bot/promo-codes").json()) == 1
    assert (
        client.delete(f"/api/v1/bot/promo-codes/{created['id']}").status_code == 204
    )
    assert client.get("/api/v1/bot/promo-codes").json() == []


# ── Рефералка ─────────────────────────────────────────────────────────
def test_referral_settings_roundtrip(client: TestClient) -> None:
    body = client.put(
        "/api/v1/bot/referral",
        json={
            "referral_enabled": True,
            "referral_reward_days": 7,
            "referral_bonus_days": 2,
        },
    ).json()
    assert body["referral_enabled"] is True
    assert client.get("/api/v1/bot/referral").json() == body


def test_referral_stats_empty(client: TestClient) -> None:
    body = client.get("/api/v1/bot/referral/stats").json()
    assert body == {"total_referred": 0, "total_rewards_days": 0, "top": []}


# ── Вебхуки ───────────────────────────────────────────────────────────
async def _seed_payment(provider: PaymentProvider, external_id: str, amount=19900):
    from shared.db.models import BotUser

    async with SessionLocal() as db:
        user = BotUser(telegram_id=999)
        db.add(user)
        await db.flush()
        payment = Payment(
            user_id=user.id,
            provider=provider,
            external_id=external_id,
            amount_kopeks=amount,
            purpose=PaymentPurpose.TOPUP,
        )
        db.add(payment)
        await db.commit()
        return payment.id


async def test_platega_webhook_confirms_and_marks_paid(client: TestClient, monkeypatch) -> None:
    client.put(
        "/api/v1/payments/providers/platega",
        json={"enabled": True, "merchant_id": "m-1", "secret": "sec"},
    )
    payment_id = await _seed_payment(PaymentProvider.PLATEGA, "tx-1")

    from shared.payments import platega as platega_module

    async def fake_get_transaction(self, transaction_id):
        assert transaction_id == "tx-1"
        return {"status": "CONFIRMED"}

    monkeypatch.setattr(
        platega_module.PlategaClient, "get_transaction", fake_get_transaction
    )

    published = []
    monkeypatch.setattr(
        "app.api.v1.webhooks.bus.publish",
        lambda cmd, **kw: published.append((cmd, kw)) or _noop(),
    )

    r = client.post("/api/v1/webhooks/platega", json={"id": "tx-1"})
    assert r.json() == {"ok": True, "applied": True}

    async with SessionLocal() as db:
        payment = await db.get(Payment, payment_id)
        assert payment.status == PaymentStatus.PAID
    assert published[0][0] == "payment_completed"


async def test_platega_webhook_not_paid_yet(client: TestClient, monkeypatch) -> None:
    client.put(
        "/api/v1/payments/providers/platega",
        json={"enabled": True, "merchant_id": "m-1", "secret": "sec"},
    )
    payment_id = await _seed_payment(PaymentProvider.PLATEGA, "tx-2")

    from shared.payments import platega as platega_module

    async def fake_get_transaction(self, transaction_id):
        return {"status": "PENDING"}

    monkeypatch.setattr(
        platega_module.PlategaClient, "get_transaction", fake_get_transaction
    )

    r = client.post("/api/v1/webhooks/platega", json={"id": "tx-2"})
    assert r.json() == {"ok": True, "applied": False}

    async with SessionLocal() as db:
        payment = await db.get(Payment, payment_id)
        assert payment.status == PaymentStatus.PENDING


def test_platega_webhook_unknown_payment(client: TestClient) -> None:
    r = client.post("/api/v1/webhooks/platega", json={"id": "ghost"})
    assert r.json() == {"ok": False, "reason": "payment not found"}


async def test_cryptobot_webhook_confirms(client: TestClient, monkeypatch) -> None:
    client.put(
        "/api/v1/payments/providers/cryptobot",
        json={"enabled": True, "token": "tok"},
    )
    payment_id = await _seed_payment(PaymentProvider.CRYPTOBOT, "inv-1")

    from shared.payments import cryptobot as cryptobot_module

    async def fake_get_invoice(self, invoice_id):
        assert invoice_id == "inv-1"
        return {"status": "paid"}

    monkeypatch.setattr(
        cryptobot_module.CryptoBotClient, "get_invoice", fake_get_invoice
    )
    monkeypatch.setattr(
        "app.api.v1.webhooks.bus.publish", lambda cmd, **kw: _noop()
    )

    r = client.post(
        "/api/v1/webhooks/cryptobot",
        json={"update_type": "invoice_paid", "payload": {"invoice_id": "inv-1"}},
    )
    assert r.json() == {"ok": True, "applied": True}

    async with SessionLocal() as db:
        payment = await db.get(Payment, payment_id)
        assert payment.status == PaymentStatus.PAID


async def test_webhook_is_idempotent(client: TestClient, monkeypatch) -> None:
    """Провайдеры повторяют колбэки — повторная доставка не должна дублировать событие."""
    client.put(
        "/api/v1/payments/providers/platega",
        json={"enabled": True, "merchant_id": "m-1", "secret": "sec"},
    )
    await _seed_payment(PaymentProvider.PLATEGA, "tx-3")

    from shared.payments import platega as platega_module

    async def fake_get_transaction(self, transaction_id):
        return {"status": "CONFIRMED"}

    monkeypatch.setattr(
        platega_module.PlategaClient, "get_transaction", fake_get_transaction
    )

    published = []
    monkeypatch.setattr(
        "app.api.v1.webhooks.bus.publish",
        lambda cmd, **kw: published.append(cmd) or _noop(),
    )

    client.post("/api/v1/webhooks/platega", json={"id": "tx-3"})
    client.post("/api/v1/webhooks/platega", json={"id": "tx-3"})
    assert len(published) == 1


async def _noop():
    return None
