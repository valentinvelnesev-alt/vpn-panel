"""Свои подписи кнопок и тексты сообщений: проверки и API."""

import pytest
from fastapi.testclient import TestClient

from app.main import app
from shared import botcopy
from shared.botcopy import CopyError, validate_button, validate_message
from tests.test_panel_api import LOGIN, PASSWORD, _db  # noqa: F401  — фикстура БД


async def _noop(*args, **kwargs):
    return None


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr("app.api.v1.bot_texts.bus.publish", _noop)
    monkeypatch.setattr("app.api.v1.bot.bus.publish", _noop)
    with TestClient(app) as c:
        c.post("/api/v1/auth/login", json={"login": LOGIN, "password": PASSWORD})
        yield c


# ── Кнопки ────────────────────────────────────────────────────────────
def test_button_label_trimmed_and_default_is_reset() -> None:
    assert validate_button("promo", "  Код   скидки ") == "Код скидки"
    assert validate_button("promo", "") is None
    assert validate_button("promo", "Промокод") is None  # как стандартная — не храним


def test_button_label_limits_and_variables() -> None:
    assert validate_button("balance", "Мой счёт: {balance}") == "Мой счёт: {balance}"
    with pytest.raises(CopyError, match="неизвестная подстановка"):
        validate_button("promo", "Код {balance}")
    with pytest.raises(CopyError, match="длиннее"):
        validate_button("promo", "я" * 65)
    with pytest.raises(CopyError, match="значки"):
        validate_button("promo", "{@gift} Код")
    with pytest.raises(CopyError, match="Неизвестная кнопка"):
        validate_button("nope", "x")


# ── Сообщения ─────────────────────────────────────────────────────────
def test_message_accepts_telegram_markup_and_variables() -> None:
    text = "{@check} <b>Готово!</b> До <i>{until}</i>, ссылка <code>{url}</code> &amp; всё"
    assert validate_message("subscription_active", text) == text


@pytest.mark.parametrize(
    ("text", "fragment"),
    [
        ("<b>не закрыто", "не закрыт тег"),
        ("<b>текст</i>", "не к месту"),
        ("<script>x</script>", "не поддерживается"),
        ("цена < 100", "&lt;"),
        ("Tom & Jerry", "&amp;"),
        ("<a>без ссылки</a>", "нет href"),
        ("Осталось {days}", "неизвестная подстановка"),
        ("{@nonexistent} привет", "неизвестный значок"),
        ("я" * (botcopy.MAX_MESSAGE_LENGTH + 1), "длиннее"),
    ],
)
def test_broken_message_is_rejected(text: str, fragment: str) -> None:
    with pytest.raises(CopyError, match=fragment):
        validate_message("subscription_active", text)


def test_message_equal_to_default_is_not_stored() -> None:
    assert validate_message("expired", botcopy.default_message("expired")) is None
    assert validate_message("expired", "   ") is None


def test_every_default_passes_its_own_validation() -> None:
    # Стандартные тексты не должны ломаться о правила, которые мы же ввели.
    for item in botcopy.MESSAGES:
        assert validate_message(item.key, item.default + " ") is None
    for item in botcopy.BUTTONS:
        assert validate_button(item.key, item.default) is None


# ── API ───────────────────────────────────────────────────────────────
def test_registry_is_served_with_defaults_and_examples(client: TestClient) -> None:
    body = client.get("/api/v1/bot/texts").json()
    keys = {m["key"] for m in body["messages"]}
    assert {"plans_header", "subscription_active", "expired"} <= keys
    active = next(m for m in body["messages"] if m["key"] == "subscription_active")
    assert [v["name"] for v in active["variables"]] == ["until", "left", "url"]
    assert active["value"] is None and "{@check}" in active["default"]
    assert body["icons"]["check"] == "✅"
    assert any(b["key"] == "balance" and b["variables"] for b in body["buttons"])


def test_save_and_reset_texts(client: TestClient) -> None:
    r = client.put(
        "/api/v1/bot/texts", json={"values": {"expired": "<b>Конец</b>", "plans_header": ""}}
    )
    assert r.status_code == 200
    values = {m["key"]: m["value"] for m in r.json()["messages"]}
    assert values["expired"] == "<b>Конец</b>" and values["plans_header"] is None

    # Пустой словарь — всё вернулось к стандартному.
    r = client.put("/api/v1/bot/texts", json={"values": {}})
    assert all(m["value"] is None for m in r.json()["messages"])


def test_broken_text_is_not_saved(client: TestClient) -> None:
    r = client.put("/api/v1/bot/texts", json={"values": {"expired": "<b>не закрыто"}})
    assert r.status_code == 422 and "не закрыт" in r.json()["detail"]
    assert all(m["value"] is None for m in client.get("/api/v1/bot/texts").json()["messages"])


def test_button_labels_saved_with_menu_settings(client: TestClient) -> None:
    status = client.get("/api/v1/bot").json()
    keys = (
        "welcome_text", "support_url", "channel_url", "channel_id", "require_channel_sub",
        "trial_enabled", "trial_days", "trial_squad_uuids", "trial_hwid_limit", "trial_traffic_gb",
        "purchase_notify_chat_id", "admin_telegram_ids", "privacy_policy_url", "terms_url",
        "allow_multiple_subscriptions", "menu_hidden", "menu_columns", "menu_styles", "menu_mode",
    )
    payload = {k: status[k] for k in keys}
    ok = client.put(
        "/api/v1/bot/settings",
        json={**payload, "menu_labels": {"promo": "Код скидки", "buy": "Купить подписку"}},
    )
    assert ok.status_code == 200
    assert ok.json()["menu_labels"] == {"promo": "Код скидки"}  # «как стандартная» не хранится
    labels = {b["key"]: b["value"] for b in client.get("/api/v1/bot/texts").json()["buttons"]}
    assert labels["promo"] == "Код скидки" and labels["profile"] is None

    bad = client.put("/api/v1/bot/settings", json={**payload, "menu_labels": {"promo": "{x}"}})
    assert bad.status_code == 422


def test_texts_require_auth() -> None:
    with TestClient(app) as anon:
        assert anon.get("/api/v1/bot/texts").status_code == 401
        assert anon.put("/api/v1/bot/texts", json={"values": {}}).status_code == 401
