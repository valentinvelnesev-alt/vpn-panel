"""Поддержка: режимы, подключение бота, проверка группы."""

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services import telegram
from app.services.links import normalize_support_link
from shared.crypto import decrypt
from shared.db.models import BotConfig, SupportTicket
from shared.db.session import SessionLocal
from tests.test_panel_api import LOGIN, PASSWORD, _db  # noqa: F401  — фикстура БД

TOKEN = "777000:AAEhBOweik6ad9r_QXsyDbCJgHnvQvbcd0k"
CHAT = -1001234567890
published: list[str] = []


@pytest.fixture
def client(monkeypatch):
    async def fake_get_me(token: str):
        if token == TOKEN:
            return telegram.BotIdentity(id=777, username="my_support_bot", name="Поддержка")
        if token == "999000:SALESSALESSALESSALESSALESSALESSALES":
            return telegram.BotIdentity(id=999, username="sales_bot", name="Продажи")
        raise telegram.TelegramError("Unauthorized")

    async def fake_publish(command: str, **payload):
        published.append(command)

    monkeypatch.setattr(telegram, "get_me", fake_get_me)
    monkeypatch.setattr("app.api.v1.support.bus.publish", fake_publish)
    published.clear()
    with TestClient(app) as c:
        c.post("/api/v1/auth/login", json={"login": LOGIN, "password": PASSWORD})
        yield c


def _connect(client: TestClient) -> None:
    assert client.put("/api/v1/bot/support/token", json={"token": TOKEN}).status_code == 200


# ── Ссылки ────────────────────────────────────────────────────────────
@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("@my_support", "https://t.me/my_support"),
        ("my_support", "https://t.me/my_support"),
        ("t.me/my_support", "https://t.me/my_support"),
        ("https://t.me/+AbCdEf", "https://t.me/+AbCdEf"),
        ("https://example.com/help", "https://example.com/help"),
        ("  ", None),
        (None, None),
    ],
)
def test_support_link_normalized(raw, expected) -> None:
    assert normalize_support_link(raw) == expected


def test_support_link_rejects_garbage() -> None:
    with pytest.raises(ValueError, match="@username"):
        normalize_support_link("мне нужна помощь")


def test_menu_settings_also_accept_username(client: TestClient) -> None:
    status = client.get("/api/v1/bot").json()
    status["support_url"] = "@help_me"
    r = client.put("/api/v1/bot/settings", json={k: status[k] for k in (
        "welcome_text", "support_url", "channel_url", "channel_id", "require_channel_sub",
        "trial_enabled", "trial_days", "trial_squad_uuids", "trial_hwid_limit", "trial_traffic_gb",
        "purchase_notify_chat_id", "admin_telegram_ids", "privacy_policy_url", "terms_url",
        "allow_multiple_subscriptions", "menu_hidden", "menu_columns", "menu_styles", "menu_mode",
    )})
    assert r.status_code == 200, r.text
    assert r.json()["support_url"] == "https://t.me/help_me"


# ── Режим «ссылка» ────────────────────────────────────────────────────
def test_defaults_to_link_mode(client: TestClient) -> None:
    body = client.get("/api/v1/bot/support").json()
    assert body["mode"] == "link" and body["chat_id"] is None
    assert body["state"] == "stopped" and body["open_tickets"] == 0


def test_save_link_mode(client: TestClient) -> None:
    r = client.put(
        "/api/v1/bot/support",
        json={"mode": "link", "link": "@my_support", "chat_id": None, "admin_ids": []},
    )
    assert r.status_code == 200
    assert r.json()["link"] == "https://t.me/my_support"
    assert client.get("/api/v1/bot").json()["support_url"] == "https://t.me/my_support"
    assert "reload" in published


def test_bad_link_is_422(client: TestClient) -> None:
    r = client.put(
        "/api/v1/bot/support", json={"mode": "link", "link": "помогите", "admin_ids": []}
    )
    assert r.status_code == 422


# ── Режим «бот» ───────────────────────────────────────────────────────
def test_bot_mode_requires_token_then_group(client: TestClient) -> None:
    body = {"mode": "bot", "link": None, "chat_id": CHAT, "admin_ids": []}
    r = client.put("/api/v1/bot/support", json=body)
    assert r.status_code == 422 and "токен" in r.json()["detail"]

    _connect(client)
    r = client.put("/api/v1/bot/support", json={**body, "chat_id": None})
    assert r.status_code == 422 and "ID группы" in r.json()["detail"]

    r = client.put("/api/v1/bot/support", json={**body, "chat_id": 123456789})
    assert r.status_code == 422 and "-100" in r.json()["detail"]

    r = client.put("/api/v1/bot/support", json={**body, "admin_ids": [5, 3, 5]})
    assert r.status_code == 200
    out = r.json()
    assert (out["mode"], out["chat_id"], out["admin_ids"]) == ("bot", CHAT, [3, 5])


async def test_token_stored_encrypted_and_masked(client: TestClient) -> None:
    _connect(client)
    body = client.get("/api/v1/bot/support").json()
    assert body["bot_username"] == "my_support_bot"
    assert TOKEN not in str(body) and "•" in body["token_masked"]
    async with SessionLocal() as db:
        row = await db.get(BotConfig, 1)
        assert row.support_token_encrypted != TOKEN
        assert decrypt(row.support_token_encrypted) == TOKEN


def test_bad_token_rejected(client: TestClient) -> None:
    r = client.put("/api/v1/bot/support/token", json={"token": "111111:wrong-token-value"})
    assert r.status_code == 422


async def test_sales_bot_token_cannot_be_used_for_support(client: TestClient) -> None:
    async with SessionLocal() as db:
        db.add(BotConfig(id=1, bot_id=999))
        await db.commit()
    r = client.put(
        "/api/v1/bot/support/token", json={"token": "999000:SALESSALESSALESSALESSALESSALESSALES"}
    )
    assert r.status_code == 422 and "основного бота" in r.json()["detail"]


def test_removing_token_switches_back_to_link_mode(client: TestClient) -> None:
    _connect(client)
    client.put("/api/v1/bot/support", json={"mode": "bot", "chat_id": CHAT, "admin_ids": []})
    body = client.delete("/api/v1/bot/support/token").json()
    assert body["mode"] == "link" and body["token_masked"] is None
    assert client.get("/api/v1/bot").json()["support_mode"] == "link"


# ── Проверка группы ───────────────────────────────────────────────────
def _patch_telegram(monkeypatch, *, is_forum=True, member=None, chat_error=None):
    async def get_chat(token, chat_id):
        if chat_error:
            raise telegram.TelegramError(chat_error)
        return {"id": chat_id, "title": "Поддержка", "is_forum": is_forum}

    async def get_member(token, chat_id, user_id):
        assert user_id == 777
        return member or {"status": "administrator", "can_manage_topics": True}

    monkeypatch.setattr(telegram, "get_chat", get_chat)
    monkeypatch.setattr(telegram, "get_chat_member", get_member)


def _ready_client(client: TestClient) -> None:
    _connect(client)
    client.put("/api/v1/bot/support", json={"mode": "bot", "chat_id": CHAT, "admin_ids": []})


def test_check_everything_ok(client: TestClient, monkeypatch) -> None:
    _patch_telegram(monkeypatch)
    _ready_client(client)
    body = client.post("/api/v1/bot/support/check").json()
    assert body["ready"] is True
    assert [i["ok"] for i in body["items"]] == [True, True, True, True]


def test_check_tells_to_enable_topics(client: TestClient, monkeypatch) -> None:
    _patch_telegram(monkeypatch, is_forum=False)
    _ready_client(client)
    body = client.post("/api/v1/bot/support/check").json()
    topics = next(i for i in body["items"] if i["key"] == "topics")
    assert body["ready"] is False and topics["ok"] is False and "Темы" in topics["hint"]


def test_check_tells_to_give_manage_topics_right(client: TestClient, monkeypatch) -> None:
    _patch_telegram(monkeypatch, member={"status": "administrator", "can_manage_topics": False})
    _ready_client(client)
    admin = next(i for i in client.post("/api/v1/bot/support/check").json()["items"] if i["key"] == "admin")
    assert admin["ok"] is False and "Управление темами" in admin["hint"]


def test_check_bot_not_in_group(client: TestClient, monkeypatch) -> None:
    _patch_telegram(monkeypatch, chat_error="Bad Request: chat not found")
    _ready_client(client)
    chat = next(i for i in client.post("/api/v1/bot/support/check").json()["items"] if i["key"] == "chat")
    assert chat["ok"] is False and "-100" in chat["hint"]


def test_check_without_setup_explains_first_step(client: TestClient) -> None:
    body = client.post("/api/v1/bot/support/check").json()
    assert body["ready"] is False
    assert body["items"][0]["key"] == "token" and body["items"][0]["ok"] is False


def test_test_message_requires_setup_then_sends(client: TestClient, monkeypatch) -> None:
    assert client.post("/api/v1/bot/support/test").status_code == 409
    sent: list[tuple[int, str]] = []

    async def send_plain(token, chat_id, text):
        sent.append((chat_id, text))

    monkeypatch.setattr(telegram, "send_plain", send_plain)
    _ready_client(client)
    assert client.post("/api/v1/bot/support/test").json()["ok"] is True
    assert sent and sent[0][0] == CHAT


async def test_ticket_counters(client: TestClient) -> None:
    from datetime import UTC, datetime

    async with SessionLocal() as db:
        for status in ("open", "open", "closed"):
            db.add(
                SupportTicket(
                    user_telegram_id=1, chat_id=CHAT, thread_id=1, status=status,
                    created_at=datetime.now(UTC),
                )
            )
        await db.commit()
    body = client.get("/api/v1/bot/support").json()
    assert (body["open_tickets"], body["total_tickets"]) == (2, 3)


def test_support_requires_auth() -> None:
    with TestClient(app) as anon:
        assert anon.get("/api/v1/bot/support").status_code == 401
        assert anon.put("/api/v1/bot/support/token", json={"token": TOKEN}).status_code == 401
