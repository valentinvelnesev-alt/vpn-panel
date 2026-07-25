"""Смоук-тесты панели: авторизация, настройки, дашборд, ноды, пользователи.

Remnawave подменяется httpx-транспортом, отдающим ответы в формате спеки, —
так проверяется и разбор моделей, и поведение при её недоступности.
"""

import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from fastapi.testclient import TestClient

from app.core.security import hash_password
from shared.db.base import Base
from shared.db.models import Admin
from app.db.session import SessionLocal, engine
from app.main import app

LOGIN, PASSWORD = "root", "correct-horse-battery"

NODE = {
    "uuid": "n-1",
    "name": "Amsterdam",
    "address": "10.0.0.1",
    "countryCode": "NL",
    "isConnected": True,
    "isDisabled": False,
    "isConnecting": False,
    "usersOnline": 12,
    "trafficUsedBytes": 1024,
    "trafficLimitBytes": 0,
    "xrayUptime": 3600,
    "viewPosition": 0,
    "versions": {"xray": "1.8.4", "node": "2.0.0"},
}

USER = {
    "uuid": "u-1",
    "username": "client01",
    "status": "ACTIVE",
    "expireAt": "2027-01-01T00:00:00.000Z",
    "telegramId": 12345,
    "email": "a@b.c",
    "trafficLimitBytes": 0,
    "trafficLimitStrategy": "NO_RESET",
    "subscriptionUrl": "https://sub.example/abc",
    "activeInternalSquads": [{"uuid": "s-1", "name": "Main"}],
    "userTraffic": {"usedTrafficBytes": 500, "onlineAt": "2026-07-01T10:00:00.000Z"},
}

STATS = {
    "users": {"totalUsers": 3, "statusCounts": {"ACTIVE": 2, "EXPIRED": 1}},
    "onlineStats": {"onlineNow": 1, "lastDay": 2, "lastWeek": 3, "neverOnline": 0},
    "nodes": {"totalOnline": 1, "totalBytesLifetime": "999"},
    "uptime": 100,
    "memory": {},
}


def fake_remnawave(request: httpx.Request) -> httpx.Response:
    path = request.url.path

    # Не-GET разбираем первым: у /api/users совпадают путь GET и PATCH.
    if request.method == "PATCH" and path == "/api/users":
        body = json.loads(request.content)
        # Remnawave принимает сквады как список UUID, но в ответе отдаёт
        # объекты — заглушка обязана вести себя так же, иначе тест не
        # поймает несоответствие форматов.
        if isinstance(body.get("activeInternalSquads"), list):
            body["activeInternalSquads"] = [
                {"uuid": u, "name": "Main"} for u in body["activeInternalSquads"]
            ]
        return httpx.Response(200, json={"response": {**USER, **body}})
    if path in ("/api/hwid/devices/delete", "/api/hwid/devices/delete-all"):
        return httpx.Response(200, json={"response": {"ok": True}})

    routes = {
        "/api/system/metadata": {"version": "2.8.1"},
        "/api/system/stats": STATS,
        "/api/nodes": [NODE],
        "/api/users": {"users": [USER], "total": 1},
        "/api/users/u-1": USER,
        "/api/users/by-telegram-id/12345": [USER],
        "/api/hwid/devices/u-1": {
            "total": 1,
            "devices": [{"hwid": "h1", "platform": "iOS", "deviceModel": "iPhone"}],
        },
    }
    if path in routes:
        return httpx.Response(200, json={"response": routes[path]})
    if path.endswith(("/actions/restart", "/actions/enable", "/actions/disable")):
        return httpx.Response(204)
    return httpx.Response(404, json={"message": "not found"})


@pytest.fixture(autouse=True)
async def _db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    async with SessionLocal() as db:
        db.add(
            Admin(login=LOGIN, password_hash=hash_password(PASSWORD), is_owner=True)
        )
        await db.commit()
    yield


@pytest.fixture
def client(monkeypatch):
    # Клиент Remnawave ходит через поддельный транспорт вместо сети.
    from shared.remnawave import client as rw

    original = rw.RemnawaveClient.__init__

    def patched(self, base_url, token, **kwargs):
        original(self, base_url, token, **kwargs)
        self._client = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            transport=httpx.MockTransport(fake_remnawave),
        )

    monkeypatch.setattr(rw.RemnawaveClient, "__init__", patched)

    with TestClient(app) as c:
        c.post("/api/v1/auth/login", json={"login": LOGIN, "password": PASSWORD})
        yield c


def configure(client: TestClient) -> None:
    r = client.put(
        "/api/v1/settings/remnawave",
        json={"url": "https://panel.example.com", "token": "secret-token"},
    )
    assert r.status_code == 200, r.text


def test_overview_before_configuration(client: TestClient) -> None:
    body = client.get("/api/v1/dashboard/overview").json()
    assert body["configured"] is False


def test_settings_roundtrip_masks_token(client: TestClient) -> None:
    configure(client)
    body = client.get("/api/v1/settings/remnawave").json()
    assert body["configured"] is True
    assert body["url"] == "https://panel.example.com"
    # Токен наружу не отдаём ни при каких условиях.
    assert "secret-token" not in json.dumps(body)
    assert "•" in body["token_masked"]


def test_connection_check(client: TestClient) -> None:
    r = client.post(
        "/api/v1/settings/remnawave/check",
        json={"url": "https://panel.example.com", "token": "secret-token"},
    )
    assert r.json() == {"ok": True, "message": "Подключение работает", "version": "2.8.1"}


def test_overview_aggregates_stats(client: TestClient) -> None:
    configure(client)
    body = client.get("/api/v1/dashboard/overview").json()
    assert body["users_total"] == 3
    assert body["users_active"] == 2
    assert body["users_expired"] == 1
    assert body["online_now"] == 1
    assert body["nodes_total"] == 1
    assert body["nodes_online"] == 1
    assert body["traffic_lifetime_bytes"] == 999


def test_nodes_list(client: TestClient) -> None:
    configure(client)
    nodes = client.get("/api/v1/nodes").json()
    assert nodes[0]["name"] == "Amsterdam"
    assert nodes[0]["online"] is True
    assert nodes[0]["xray_version"] == "1.8.4"


def test_node_restart(client: TestClient) -> None:
    configure(client)
    assert client.post("/api/v1/nodes/n-1/restart").status_code == 204


def test_users_list_and_search(client: TestClient) -> None:
    configure(client)
    page = client.get("/api/v1/users").json()
    assert page["total"] == 1
    assert page["users"][0]["username"] == "client01"
    assert page["users"][0]["squads"] == [{"uuid": "s-1", "name": "Main"}]
    assert page["users"][0]["used_traffic_bytes"] == 500

    found = client.get("/api/v1/users", params={"search": "12345"}).json()
    assert found["total"] == 1

    missing = client.get("/api/v1/users", params={"search": "nobody"}).json()
    assert missing == {"users": [], "total": 0}


def test_extend_user_from_current_expiry(client: TestClient) -> None:
    configure(client)
    r = client.post("/api/v1/users/u-1/extend", json={"days": 30})
    assert r.status_code == 200
    expire = datetime.fromisoformat(r.json()["expire_at"])
    # Исходная дата 2027-01-01 плюс 30 дней.
    assert expire.date() == (datetime(2027, 1, 1, tzinfo=UTC) + timedelta(days=30)).date()


def test_devices(client: TestClient) -> None:
    configure(client)
    devices = client.get("/api/v1/users/u-1/devices").json()
    assert devices[0]["platform"] == "iOS"
    assert client.delete("/api/v1/users/u-1/devices/h1").status_code == 204


def test_requires_authentication() -> None:
    with TestClient(app) as anon:
        assert anon.get("/api/v1/nodes").status_code == 401
        assert anon.get("/api/v1/users").status_code == 401
        assert anon.get("/api/v1/settings/remnawave").status_code == 401


def test_status_counts(client: TestClient) -> None:
    configure(client)
    body = client.get("/api/v1/users/status-counts").json()
    assert body == {"total": 3, "active": 2, "expired": 1, "limited": 0, "disabled": 0}


def test_update_user_sends_only_changed_fields(client: TestClient) -> None:
    configure(client)
    r = client.patch(
        "/api/v1/users/u-1",
        json={"tag": "VIP", "hwid_device_limit": 5, "description": "постоянный клиент"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["tag"] == "VIP"
    assert body["hwid_device_limit"] == 5
    assert body["description"] == "постоянный клиент"


def test_update_user_rejects_empty_payload(client: TestClient) -> None:
    configure(client)
    assert client.patch("/api/v1/users/u-1", json={}).status_code == 422


def test_update_user_validates_status(client: TestClient) -> None:
    configure(client)
    assert client.patch("/api/v1/users/u-1", json={"status": "HACKED"}).status_code == 422


def test_update_user_squads_sends_uuids(client: TestClient) -> None:
    """В запрос сквады уходят UUID-строками (как требует спека 2.8.1),
    а в ответе разбираются как объекты."""
    configure(client)
    r = client.patch("/api/v1/users/u-1", json={"squad_uuids": ["s-9"]})
    assert r.status_code == 200
    assert r.json()["squads"] == [{"uuid": "s-9", "name": "Main"}]


def test_unexpected_remnawave_response_is_502(client: TestClient, monkeypatch) -> None:
    """Ответ не по схеме — это сбой внешнего сервиса (502), а не 500 панели."""
    from shared.remnawave import client as rw

    def broken(self, base_url, token, **kwargs):
        import httpx as _httpx

        self._client = _httpx.AsyncClient(
            base_url=base_url,
            transport=_httpx.MockTransport(
                lambda req: _httpx.Response(200, json={"response": {"garbage": True}})
            ),
        )

    monkeypatch.setattr(rw.RemnawaveClient, "__init__", broken)
    configure(client)
    assert client.get("/api/v1/users/u-1").status_code == 502
