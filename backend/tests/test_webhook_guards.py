"""Защита вебхуков: лимит запросов с одного IP и подпись RollyPay."""

import hashlib
import hmac
import time

import pytest
from fastapi.testclient import TestClient

from app.api.v1 import webhooks
from app.main import app
from tests.test_panel_api import LOGIN, PASSWORD, _db  # noqa: F401 — фикстура БД


@pytest.fixture
def client():
    webhooks._reset_rate_limit()
    with TestClient(app) as c:
        c.post("/api/v1/auth/login", json={"login": LOGIN, "password": PASSWORD})
        yield c
    webhooks._reset_rate_limit()


def _sign(secret: str, body: bytes, timestamp: str) -> str:
    mac = hmac.new(secret.encode(), digestmod=hashlib.sha256)
    mac.update(timestamp.encode())
    mac.update(b".")
    mac.update(body)
    return mac.hexdigest()


def test_rate_limit_blocks_flood(client: TestClient) -> None:
    for _ in range(webhooks.RATE_LIMIT):
        assert client.post("/api/v1/webhooks/platega", json={}).status_code == 200
    assert client.post("/api/v1/webhooks/platega", json={}).status_code == 429


def test_rollypay_without_secret_accepts_unsigned(client: TestClient) -> None:
    r = client.post("/api/v1/webhooks/rollypay", json={"payment_id": "ghost"})
    assert r.status_code == 200
    assert r.json() == {"ok": False, "reason": "payment not found"}


def test_rollypay_with_secret_rejects_bad_signature(client: TestClient) -> None:
    client.put(
        "/api/v1/payments/providers/rollypay",
        json={"enabled": True, "api_key": "k", "signing_secret": "whsec"},
    )
    r = client.post(
        "/api/v1/webhooks/rollypay",
        content=b'{"payment_id": "ghost"}',
        headers={"Content-Type": "application/json", "X-Signature": "bad", "X-Timestamp": str(int(time.time()))},
    )
    assert r.status_code == 403
    # Без заголовков подписи — тоже отказ.
    assert client.post("/api/v1/webhooks/rollypay", json={"payment_id": "ghost"}).status_code == 403


def test_rollypay_with_secret_accepts_valid_signature(client: TestClient) -> None:
    client.put(
        "/api/v1/payments/providers/rollypay",
        json={"enabled": True, "api_key": "k", "signing_secret": "whsec"},
    )
    body = b'{"payment_id": "ghost"}'
    ts = str(int(time.time()))
    r = client.post(
        "/api/v1/webhooks/rollypay",
        content=body,
        headers={
            "Content-Type": "application/json",
            "X-Signature": _sign("whsec", body, ts),
            "X-Timestamp": ts,
        },
    )
    assert r.status_code == 200
    assert r.json() == {"ok": False, "reason": "payment not found"}


def test_rollypay_signature_rejects_stale_timestamp() -> None:
    body = b"{}"
    ts = str(int(time.time()) - 3600)
    assert not webhooks.verify_rollypay_signature("s", body, ts, _sign("s", body, ts))
