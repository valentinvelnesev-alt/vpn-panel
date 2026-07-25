"""Управление ботом из панели: токен, старт/стоп, эмодзи, тарифы."""

import httpx
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services import telegram
from shared.crypto import decrypt
from shared.db.models import BotConfig, EmojiMode
from shared.db.session import SessionLocal
from tests.test_panel_api import LOGIN, PASSWORD, _db  # noqa: F401  — фикстура БД

TOKEN = "123456:AAEhBOweik6ad9r_QXsyDbCJgHnvQvbcd0k"
OTHER_TOKEN = "999999:BBFhBOweik6ad9r_QXsyDbCJgHnvQvbcd0k"

# Команды в Redis никуда не уходят — в тестах его нет; проверяем, что это
# не мешает (панель обязана переживать недоступность Redis).
PUBLISHED: list[str] = []


@pytest.fixture
def client(monkeypatch):
    async def fake_get_me(token: str):
        if token == TOKEN:
            return telegram.BotIdentity(id=111, username="my_vpn_bot", name="My VPN")
        if token == OTHER_TOKEN:
            return telegram.BotIdentity(id=222, username="second_bot", name="Second")
        raise telegram.TelegramError("Unauthorized")

    async def fake_publish(command: str, **payload):
        PUBLISHED.append(command)

    monkeypatch.setattr(telegram, "get_me", fake_get_me)
    monkeypatch.setattr("app.api.v1.bot.bus.publish", fake_publish)
    PUBLISHED.clear()

    with TestClient(app) as c:
        c.post("/api/v1/auth/login", json={"login": LOGIN, "password": PASSWORD})
        yield c


def test_status_empty(client: TestClient) -> None:
    body = client.get("/api/v1/bot").json()
    assert body["configured"] is False
    assert body["state"] == "stopped"
    assert body["emoji_mode"] == "plain"


def test_token_check_reports_bot(client: TestClient) -> None:
    ok = client.post("/api/v1/bot/token/check", json={"token": TOKEN}).json()
    assert ok["ok"] is True
    assert ok["bot_username"] == "my_vpn_bot"

    bad = client.post(
        "/api/v1/bot/token/check", json={"token": "111111:definitely-wrong"}
    ).json()
    assert bad["ok"] is False


def test_token_saved_encrypted_and_masked(client: TestClient) -> None:
    body = client.put("/api/v1/bot/token", json={"token": TOKEN}).json()
    assert body["configured"] is True
    assert body["bot_username"] == "my_vpn_bot"
    # Токен наружу не отдаём.
    assert TOKEN not in str(body)
    assert "•" in body["token_masked"]


async def test_token_stored_encrypted_in_db(client: TestClient) -> None:
    client.put("/api/v1/bot/token", json={"token": TOKEN})
    async with SessionLocal() as db:
        row = await db.get(BotConfig, 1)
        assert row.token_encrypted != TOKEN  # в БД лежит шифротекст
        assert decrypt(row.token_encrypted) == TOKEN


def test_start_requires_token(client: TestClient) -> None:
    assert client.post("/api/v1/bot/start").status_code == 409


def test_start_and_stop(client: TestClient) -> None:
    client.put("/api/v1/bot/token", json={"token": TOKEN})

    started = client.post("/api/v1/bot/start").json()
    assert started["enabled"] is True
    assert "start" in PUBLISHED

    stopped = client.post("/api/v1/bot/stop").json()
    assert stopped["enabled"] is False
    assert stopped["state"] == "stopped"
    assert "stop" in PUBLISHED


def test_changing_bot_resets_premium_mode(client: TestClient, monkeypatch) -> None:
    """Новый бот — новый аккаунт: прежнее разрешение на премиум не действует."""

    async def ok_check(token, chat_id, emoji_id):
        return None

    monkeypatch.setattr(telegram, "check_premium_emoji", ok_check)

    client.put("/api/v1/bot/token", json={"token": TOKEN})
    enabled = client.put(
        "/api/v1/bot/emoji",
        json={
            "mode": "premium",
            "premium_emoji": {"check": "5237699328843200968"},
            "test_chat_id": 42,
        },
    ).json()
    assert enabled["ok"] is True
    assert enabled["status"]["emoji_mode"] == "premium"

    switched = client.put("/api/v1/bot/token", json={"token": OTHER_TOKEN}).json()
    assert switched["bot_username"] == "second_bot"
    assert switched["emoji_mode"] == "plain"
    assert switched["premium_available"] is False


def test_premium_rejected_without_telegram_premium(
    client: TestClient, monkeypatch
) -> None:
    """Без Premium у владельца бота Telegram откажет — режим не включаем."""

    async def failing(token, chat_id, emoji_id):
        raise telegram.TelegramError(
            "Bad Request: CUSTOM_EMOJI_INVALID"
        )

    monkeypatch.setattr(telegram, "check_premium_emoji", failing)
    client.put("/api/v1/bot/token", json={"token": TOKEN})

    body = client.put(
        "/api/v1/bot/emoji",
        json={
            "mode": "premium",
            "premium_emoji": {"check": "123"},
            "test_chat_id": 42,
        },
    ).json()

    assert body["ok"] is False
    assert "Telegram Premium" in body["message"]
    assert body["status"]["emoji_mode"] == "plain"


def test_premium_requires_emoji_map_and_chat(client: TestClient) -> None:
    client.put("/api/v1/bot/token", json={"token": TOKEN})
    # Универсальных id премиум-эмодзи не существует — без карты не включаем.
    assert (
        client.put(
            "/api/v1/bot/emoji", json={"mode": "premium", "premium_emoji": {}}
        ).status_code
        == 422
    )
    assert (
        client.put(
            "/api/v1/bot/emoji",
            json={"mode": "premium", "premium_emoji": {"check": "1"}},
        ).status_code
        == 422
    )


def test_plain_mode_always_allowed(client: TestClient) -> None:
    body = client.put("/api/v1/bot/emoji", json={"mode": "plain"}).json()
    assert body["ok"] is True
    assert body["status"]["emoji_mode"] == "plain"


def test_settings_roundtrip(client: TestClient) -> None:
    payload = {
        "welcome_text": "{@shield} Привет!",
        "support_url": "https://t.me/support",
        "channel_url": "https://t.me/channel",
        "channel_id": "@channel",
        "require_channel_sub": True,
        "trial_enabled": True,
        "trial_days": 5,
        "trial_squad_uuids": ["s-1"],
        "trial_hwid_limit": 2,
    }
    body = client.put("/api/v1/bot/settings", json=payload).json()
    assert body["trial_days"] == 5
    assert body["trial_squad_uuids"] == ["s-1"]
    assert body["require_channel_sub"] is True
    assert "reload" in PUBLISHED


def test_plans_crud(client: TestClient) -> None:
    created = client.post(
        "/api/v1/bot/plans",
        json={
            "title": "Месяц",
            "days": 30,
            "price_rub": 199.99,
            "squad_uuids": ["s-1"],
            "hwid_limit": 5,
        },
    )
    assert created.status_code == 201
    plan = created.json()
    # Цена хранится в копейках, поэтому копейка не теряется.
    assert plan["price_rub"] == 199.99

    updated = client.put(
        f"/api/v1/bot/plans/{plan['id']}",
        json={**{k: v for k, v in plan.items() if k != "id"}, "price_rub": 149},
    ).json()
    assert updated["price_rub"] == 149

    assert len(client.get("/api/v1/bot/plans").json()) == 1
    assert client.delete(f"/api/v1/bot/plans/{plan['id']}").status_code == 204
    assert client.get("/api/v1/bot/plans").json() == []
    assert client.delete(f"/api/v1/bot/plans/{plan['id']}").status_code == 404


def test_bot_endpoints_require_auth() -> None:
    with TestClient(app) as anon:
        assert anon.get("/api/v1/bot").status_code == 401
        assert anon.post("/api/v1/bot/start").status_code == 401
        assert anon.get("/api/v1/bot/plans").status_code == 401
