"""Клиенты бота (баланс, персональные тарифы), скидка, рассылки, безопасность."""

from datetime import UTC, datetime, timedelta

import pyotp
import pytest
from fastapi.testclient import TestClient

from app.core import gate
from app.core.config import settings
from app.main import app
from app.services import cache, telegram
from shared.db.models import BotUser, Plan, Wallet, WalletTransaction
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


async def _make_client(telegram_id: int = 555, username: str = "ivan") -> int:
    async with SessionLocal() as db:
        user = BotUser(telegram_id=telegram_id, username=username)
        db.add(user)
        await db.commit()
        return user.id


# ── Клиенты и баланс ──────────────────────────────────────────────────
async def test_clients_list_and_search(client: TestClient) -> None:
    await _make_client(555, "ivan")
    await _make_client(777, "petr")
    assert client.get("/api/v1/bot/clients").json()["total"] == 2
    found = client.get("/api/v1/bot/clients", params={"search": "@iva"}).json()
    assert [c["telegram_id"] for c in found["items"]] == [555]
    by_id = client.get("/api/v1/bot/clients", params={"search": "777"}).json()
    assert [c["username"] for c in by_id["items"]] == ["petr"]


async def test_balance_credit_debit_with_history(client: TestClient) -> None:
    client_id = await _make_client()
    url = f"/api/v1/bot/clients/{client_id}/balance"

    r = client.post(url, json={"amount_rub": 150.5, "reason": "Компенсация", "notify": False})
    assert r.status_code == 200, r.text
    assert r.json()["balance_rub"] == 150.5

    r = client.post(url, json={"amount_rub": -50, "reason": "Ошибка начисления", "notify": False})
    body = r.json()
    assert body["balance_rub"] == 100.5
    assert [t["amount_rub"] for t in body["transactions"]] == [-50, 150.5]
    assert body["transactions"][0]["description"] == "Ошибка начисления"
    assert body["transactions"][0]["type"] == "admin_adjust"


async def test_balance_cannot_go_negative(client: TestClient) -> None:
    client_id = await _make_client()
    r = client.post(
        f"/api/v1/bot/clients/{client_id}/balance",
        json={"amount_rub": -10, "reason": "x", "notify": False},
    )
    assert r.status_code == 422
    async with SessionLocal() as db:
        assert (await db.scalar(WalletTransaction.__table__.select())) is None


async def test_balance_requires_reason(client: TestClient) -> None:
    client_id = await _make_client()
    r = client.post(
        f"/api/v1/bot/clients/{client_id}/balance", json={"amount_rub": 10, "reason": ""}
    )
    assert r.status_code == 422


async def test_balance_notifies_client(client: TestClient, monkeypatch) -> None:
    sent = []

    async def fake_send(token, chat_id, text):
        sent.append((chat_id, text))

    monkeypatch.setattr(telegram, "send_message", fake_send)
    monkeypatch.setattr(telegram, "get_me", _fake_me)
    client.put("/api/v1/bot/token", json={"token": "123456:AAEhBOweik6ad9r_QXsyDbCJgHnvQvbcd0k"})
    client_id = await _make_client()
    client.post(
        f"/api/v1/bot/clients/{client_id}/balance",
        json={"amount_rub": 100, "reason": "<b>Бонус</b>", "notify": True},
    )
    assert sent and sent[0][0] == 555
    assert "&lt;b&gt;Бонус" in sent[0][1]


async def _fake_me(token):
    return telegram.BotIdentity(id=1, username="bot", name="Bot")


async def test_personal_plans_crud_and_hidden_from_common_list(client: TestClient) -> None:
    client_id = await _make_client()
    plan = {"title": "VIP", "days": 90, "price_rub": 99, "hwid_limit": 10}
    created = client.post(f"/api/v1/bot/clients/{client_id}/plans", json=plan)
    assert created.status_code == 201, created.text
    plan_id = created.json()["id"]

    assert client.get("/api/v1/bot/plans").json() == []
    detail = client.get(f"/api/v1/bot/clients/{client_id}").json()
    assert [p["title"] for p in detail["personal_plans"]] == ["VIP"]

    r = client.put(
        f"/api/v1/bot/clients/{client_id}/plans/{plan_id}", json={**plan, "price_rub": 50}
    )
    assert r.json()["price_rub"] == 50

    other = await _make_client(999, "other")
    assert client.delete(f"/api/v1/bot/clients/{other}/plans/{plan_id}").status_code == 404
    assert client.delete(f"/api/v1/bot/clients/{client_id}/plans/{plan_id}").status_code == 204
    async with SessionLocal() as db:
        assert await db.get(Plan, plan_id) is None


# ── Скидка и меню ─────────────────────────────────────────────────────
def test_discount_and_menu_settings(client: TestClient) -> None:
    r = client.put("/api/v1/bot/discount", json={"percent": 20, "days": None})
    assert r.status_code == 200
    assert r.json()["discount_percent"] == 20
    assert r.json()["discount_until"] is None

    r = client.put("/api/v1/bot/discount", json={"percent": 20, "days": 7})
    until = datetime.fromisoformat(r.json()["discount_until"].replace("Z", "+00:00"))
    if until.tzinfo is None:
        until = until.replace(tzinfo=UTC)
    assert timedelta(days=6, hours=23) < until - datetime.now(UTC) <= timedelta(days=7)

    assert client.put("/api/v1/bot/discount", json={"percent": 20, "days": 0}).status_code == 422

    status = client.get("/api/v1/bot").json()
    settings_in = {
        k: status[k]
        for k in (
            "welcome_text", "support_url", "channel_url", "channel_id",
            "require_channel_sub", "trial_enabled", "trial_days", "trial_squad_uuids",
            "trial_hwid_limit", "purchase_notify_chat_id", "admin_telegram_ids",
            "privacy_policy_url", "terms_url",
        )
    }
    r = client.put(
        "/api/v1/bot/settings",
        json={
            **settings_in,
            "support_url": "   ",
            "menu_hidden": ["support", "promo"],
            "allow_multiple_subscriptions": True,
        },
    )
    body = r.json()
    assert body["menu_hidden"] == ["support", "promo"]
    assert body["allow_multiple_subscriptions"] is True
    assert body["support_url"] is None  # пустая строка не превращается в кнопку


def test_notify_chat_test_explains_errors(client: TestClient, monkeypatch) -> None:
    monkeypatch.setattr(telegram, "get_me", _fake_me)
    client.put("/api/v1/bot/token", json={"token": "123456:AAEhBOweik6ad9r_QXsyDbCJgHnvQvbcd0k"})

    async def fail(token, chat):
        raise telegram.TelegramError("Bad Request: chat not found")

    monkeypatch.setattr(telegram, "send_test_to_chat", fail)
    body = client.post("/api/v1/bot/notify-chat/test", json={"chat": "123"}).json()
    assert body["ok"] is False and "-100" in body["message"]

    async def ok(token, chat):
        return -1001234567890

    monkeypatch.setattr(telegram, "send_test_to_chat", ok)
    body = client.post("/api/v1/bot/notify-chat/test", json={"chat": "@mygroup"}).json()
    assert body == {"ok": True, "message": body["message"], "chat_id": -1001234567890}


# ── Рассылки ──────────────────────────────────────────────────────────
def test_photo_caption_limit(client: TestClient) -> None:
    long_text = "a" * 1100
    r = client.post(
        "/api/v1/broadcasts",
        json={"text": long_text, "photo_url": "https://x/uploads/a.jpg", "segment": "all"},
    )
    assert r.status_code == 422
    assert "1024" in r.text

    # HTML-теги не считаются в длину подписи.
    ok_text = "<b>" + "a" * 1000 + "</b>"
    r = client.post(
        "/api/v1/broadcasts",
        json={"text": ok_text, "photo_url": "https://x/uploads/a.jpg", "segment": "all"},
    )
    assert r.status_code == 201, r.text

    # Без фото лимит — 4096.
    r = client.post("/api/v1/broadcasts", json={"text": long_text, "segment": "all"})
    assert r.status_code == 201


def test_broadcast_button_needs_link(client: TestClient) -> None:
    r = client.post(
        "/api/v1/broadcasts",
        json={"text": "hi", "segment": "all", "buttons": [{"text": "Go", "url": "example.com"}]},
    )
    assert r.status_code == 422


def test_broadcast_test_requires_admin_ids(client: TestClient, monkeypatch) -> None:
    monkeypatch.setattr(telegram, "get_me", _fake_me)
    client.put("/api/v1/bot/token", json={"token": "123456:AAEhBOweik6ad9r_QXsyDbCJgHnvQvbcd0k"})
    assert client.post("/api/v1/broadcasts/test", json={"text": "hi"}).status_code == 409


# ── Название панели ───────────────────────────────────────────────────
def test_public_brand_defaults_to_neutral_title(client: TestClient) -> None:
    with TestClient(app) as anon:
        assert anon.get("/api/v1/settings/brand/public").json()["title"] == "Panel"


# ── Безопасность ──────────────────────────────────────────────────────
class FakeRedis:
    def __init__(self):
        self.data: dict[str, int] = {}

    async def get(self, key):
        return self.data.get(key)

    async def incr(self, key):
        self.data[key] = self.data.get(key, 0) + 1
        return self.data[key]

    async def expire(self, key, ttl):
        return True

    async def delete(self, *keys):
        for k in keys:
            self.data.pop(k, None)


def test_login_rate_limit(monkeypatch) -> None:
    fake = FakeRedis()
    monkeypatch.setattr(cache, "get_redis", lambda: fake)
    monkeypatch.setattr(settings, "login_max_failures", 3)
    with TestClient(app) as c:
        for _ in range(3):
            r = c.post("/api/v1/auth/login", json={"login": LOGIN, "password": "wrong"})
            assert r.status_code == 401
        # Даже верный пароль не принимается, пока окно не истекло.
        r = c.post("/api/v1/auth/login", json={"login": LOGIN, "password": PASSWORD})
        assert r.status_code == 429


def test_successful_login_resets_counter(monkeypatch) -> None:
    fake = FakeRedis()
    monkeypatch.setattr(cache, "get_redis", lambda: fake)
    monkeypatch.setattr(settings, "login_max_failures", 3)
    with TestClient(app) as c:
        c.post("/api/v1/auth/login", json={"login": LOGIN, "password": "wrong"})
        c.post("/api/v1/auth/login", json={"login": LOGIN, "password": "wrong"})
        assert c.post("/api/v1/auth/login", json={"login": LOGIN, "password": PASSWORD}).status_code == 200
        assert fake.data == {}


def test_secret_path_hides_panel(monkeypatch) -> None:
    monkeypatch.setattr(settings, "panel_secret_path", "s3cret")
    with TestClient(app) as c:
        # Без пропуска: API и SPA отвечают пустым 404, даже вход.
        r = c.post("/api/v1/auth/login", json={"login": LOGIN, "password": PASSWORD})
        assert r.status_code == 404 and r.content == b""
        assert c.get("/api/v1/gate", headers={"X-Forwarded-Uri": "/"}).status_code == 404
        # Вебхуки и healthcheck открыты.
        assert c.get("/api/v1/health").status_code != 404

        # Зашли по секретному адресу — получили пропуск и редирект на «/».
        r = c.get(
            "/api/v1/gate",
            headers={"X-Forwarded-Uri": "/s3cret/"},
            follow_redirects=False,
        )
        assert r.status_code == 302 and r.headers["location"] == "/"
        assert gate.GATE_COOKIE in r.cookies

        assert c.get("/api/v1/gate", headers={"X-Forwarded-Uri": "/assets/x.js"}).status_code == 204
        r = c.post("/api/v1/auth/login", json={"login": LOGIN, "password": PASSWORD})
        assert r.status_code == 200


def test_gate_open_when_not_configured() -> None:
    with TestClient(app) as c:
        assert c.get("/api/v1/gate", headers={"X-Forwarded-Uri": "/"}).status_code == 204


def test_totp_setup_enable_and_login(client: TestClient) -> None:
    setup = client.post("/api/v1/auth/totp/setup").json()
    assert setup["otpauth_uri"].startswith("otpauth://")
    assert setup["qr_svg"].startswith("<svg")

    assert client.post("/api/v1/auth/totp/enable", json={"code": "000000"}).status_code == 422
    code = pyotp.TOTP(setup["secret"]).now()
    assert client.post("/api/v1/auth/totp/enable", json={"code": code}).json()["totp_enabled"]

    with TestClient(app) as c:
        r = c.post("/api/v1/auth/login", json={"login": LOGIN, "password": PASSWORD})
        assert r.status_code == 401 and "двухфактор" in r.json()["detail"]
        r = c.post(
            "/api/v1/auth/login",
            json={"login": LOGIN, "password": PASSWORD, "totp_code": pyotp.TOTP(setup["secret"]).now()},
        )
        assert r.status_code == 200

    r = client.post(
        "/api/v1/auth/totp/disable",
        json={"password": PASSWORD, "code": pyotp.TOTP(setup["secret"]).now()},
    )
    assert r.json()["totp_enabled"] is False


async def test_wallet_row_created_once(client: TestClient) -> None:
    client_id = await _make_client()
    for _ in range(2):
        client.post(
            f"/api/v1/bot/clients/{client_id}/balance",
            json={"amount_rub": 1, "reason": "x", "notify": False},
        )
    async with SessionLocal() as db:
        rows = (await db.scalars(Wallet.__table__.select())).all()
        assert len(rows) == 1


def test_totp_prompt_is_not_an_error(client: TestClient) -> None:
    setup = client.post("/api/v1/auth/totp/setup").json()
    client.post("/api/v1/auth/totp/enable", json={"code": pyotp.TOTP(setup["secret"]).now()})
    with TestClient(app) as c:
        first = c.post("/api/v1/auth/login", json={"login": LOGIN, "password": PASSWORD})
        assert first.headers.get("x-totp-required") == "1"
        assert "Неверный" not in first.json()["detail"]
        wrong = c.post(
            "/api/v1/auth/login",
            json={"login": LOGIN, "password": PASSWORD, "totp_code": "000000"},
        )
        assert "Неверный" in wrong.json()["detail"]


def test_premium_emoji_toggle_uses_builtin_icons(client: TestClient, monkeypatch) -> None:
    monkeypatch.setattr(telegram, "get_me", _fake_me)
    client.put("/api/v1/bot/token", json={"token": "123456:AAEhBOweik6ad9r_QXsyDbCJgHnvQvbcd0k"})
    checked = []

    async def fake_check(token, chat_id, emoji_id):
        checked.append((chat_id, emoji_id))

    monkeypatch.setattr(telegram, "check_premium_emoji", fake_check)

    # Без администратора бота проверочное сообщение отправить некуда.
    assert client.put("/api/v1/bot/emoji", json={"mode": "premium"}).status_code == 422

    status = client.get("/api/v1/bot").json()
    settings_in = {k: status[k] for k in ("trial_squad_uuids",)}
    client.put("/api/v1/bot/settings", json={**settings_in, "admin_telegram_ids": [111]})
    body = client.put("/api/v1/bot/emoji", json={"mode": "premium"}).json()
    assert body["ok"] is True and body["status"]["emoji_mode"] == "premium"
    assert checked and checked[0][0] == 111

    body = client.put("/api/v1/bot/emoji", json={"mode": "plain"}).json()
    assert body["status"]["emoji_mode"] == "plain"
