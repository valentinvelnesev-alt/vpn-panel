"""cisPay: настройки, подпись вебхука и подтверждение оплаты перезапросом статуса."""

import hashlib
import hmac
import json

import pytest
from fastapi.testclient import TestClient

from app.api.v1 import webhooks
from app.main import app
from shared.db.models import BotUser, Payment, PaymentStatus
from shared.db.session import SessionLocal
from shared.payments import cispay
from tests.test_panel_api import LOGIN, PASSWORD, _db  # noqa: F401 — фикстура БД

KEY = "cis_sec_test"
SHOP = "0190a1b2-0000-7000-8000-000000000001"


async def _noop(*args, **kwargs):
    return None


@pytest.fixture
def client(monkeypatch):
    webhooks._reset_rate_limit()
    monkeypatch.setattr("shared.bus.publish", _noop)
    published: list[int] = []

    async def fake_publish(command, **payload):
        if command == webhooks.bus.EVENT_PAYMENT_COMPLETED:
            published.append(payload.get("payment_id"))

    monkeypatch.setattr(webhooks.bus, "publish", fake_publish)
    with TestClient(app) as c:
        c.post("/api/v1/auth/login", json={"login": LOGIN, "password": PASSWORD})
        c.published = published  # type: ignore[attr-defined]
        yield c
    webhooks._reset_rate_limit()


def _configure(client: TestClient) -> None:
    r = client.put(
        "/api/v1/payments/providers/cispay",
        json={"enabled": True, "shop_id": SHOP, "api_key": KEY},
    )
    assert r.status_code == 200, r.text


def _post(client: TestClient, body: dict, *, key: str = KEY):
    raw = json.dumps(body, separators=(",", ":")).encode()
    signature = hmac.new(key.encode(), raw, hashlib.sha256).hexdigest()
    return client.post(
        "/api/v1/webhooks/cispay",
        content=raw,
        headers={"Content-Type": "application/json", "X-Signature": signature},
    )


async def _pending_payment() -> int:
    async with SessionLocal() as db:
        user = BotUser(telegram_id=111)
        db.add(user)
        await db.flush()
        payment = Payment(
            user_id=user.id,
            provider="cispay",
            external_id="tx-1",
            amount_kopeks=19900,
            purpose="topup",
        )
        db.add(payment)
        await db.commit()
        return payment.id


def test_settings_masked_and_required(client: TestClient) -> None:
    bad = client.put("/api/v1/payments/providers/cispay", json={"enabled": True})
    assert bad.status_code == 422
    _configure(client)
    body = client.get("/api/v1/payments/providers").json()
    assert body["cispay_enabled"] is True
    assert body["cispay_shop_id"] == SHOP
    assert KEY not in str(body) and "•" in body["cispay_api_key_masked"]


def test_webhook_rejects_bad_signature(client: TestClient) -> None:
    _configure(client)
    assert _post(client, {"id": "tx-1", "status": "PAID"}, key="wrong").status_code == 403


async def test_paid_webhook_is_confirmed_by_status_request(client: TestClient, monkeypatch) -> None:
    _configure(client)
    payment_id = await _pending_payment()
    asked: list[str] = []

    async def fake_get_payment(self, payment_ref: str) -> dict:
        asked.append(payment_ref)
        return {"id": payment_ref, "status": "PAID"}

    monkeypatch.setattr(cispay.CisPayClient, "get_payment", fake_get_payment)
    r = _post(client, {"id": "tx-1", "status": "PAID", "amount": 19900})
    assert r.json() == {"ok": True, "applied": True}
    assert asked == ["tx-1"]
    assert client.published == [payment_id]  # type: ignore[attr-defined]
    async with SessionLocal() as db:
        assert (await db.get(Payment, payment_id)).status == PaymentStatus.PAID


async def test_forged_paid_status_is_not_trusted(client: TestClient, monkeypatch) -> None:
    _configure(client)
    payment_id = await _pending_payment()

    async def still_pending(self, payment_ref: str) -> dict:
        return {"id": payment_ref, "status": "PENDING"}

    monkeypatch.setattr(cispay.CisPayClient, "get_payment", still_pending)
    assert _post(client, {"id": "tx-1", "status": "PAID"}).json() == {"ok": True, "applied": False}
    async with SessionLocal() as db:
        assert (await db.get(Payment, payment_id)).status == PaymentStatus.PENDING


def test_check_reports_active_methods(client: TestClient, monkeypatch) -> None:
    async def fake_capabilities(self) -> dict:
        return {
            "store_name": "Deal VPN",
            "is_active": True,
            "payment_methods": [
                {"payment_method": "SBP", "is_active": True},
                {"payment_method": "CARD", "is_active": False},
            ],
        }

    monkeypatch.setattr(cispay.CisPayClient, "capabilities", fake_capabilities)
    r = client.post(
        "/api/v1/payments/providers/cispay/check",
        json={"enabled": True, "shop_id": SHOP, "api_key": KEY},
    ).json()
    assert r["ok"] is True and "СБП" in r["message"] and "карта" not in r["message"]
