"""Клиент 3x-ui и выдача подписок через него.

Поддельная 3x-ui хранит клиентов в памяти и отвечает в формате её API
({success, msg, obj}), включая ошибки с HTTP 200 и success=false.
"""

import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config import Config, PlanView
from app.services import subscriptions as subs
from shared.db.base import Base
from shared.db.models import EmojiMode
from shared.panel import make_client
from shared.remnawave import RemnawaveError
from shared.remnawave.models import UserStatus
from shared.xui import XuiClient, XuiError

BASE = "https://xui.example:2053/secret"


class FakeXui:
    def __init__(self) -> None:
        self.clients: dict[str, dict] = {}
        self.links: dict[str, set[int]] = {}
        self.traffic: dict[str, tuple[int, int]] = {}
        self.calls: list[tuple[str, dict]] = []
        self.next_id = 1
        self.token = "tok"
        self.links_for: dict[str, list[str]] = {}

    @staticmethod
    def ok(obj=None, msg: str = "") -> httpx.Response:
        return httpx.Response(200, json={"success": True, "msg": msg, "obj": obj})

    @staticmethod
    def fail(msg: str) -> httpx.Response:
        return httpx.Response(200, json={"success": False, "msg": msg, "obj": None})

    def payload(self, email: str) -> dict:
        up, down = self.traffic.get(email, (0, 0))
        return {
            "client": self.clients[email],
            "inboundIds": sorted(self.links[email]),
            "usedTraffic": up + down,
        }

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if request.headers.get("authorization") != f"Bearer {self.token}":
            return httpx.Response(302, headers={"location": "/secret/"})
        assert request.url.path.startswith("/secret/panel/api/"), request.url.path
        path = request.url.path.removeprefix("/secret")
        body = json.loads(request.content) if request.content else {}
        self.calls.append((f"{request.method} {path}", body))
        parts = path.split("/")

        if path == "/panel/api/server/status":
            return self.ok(
                {"cpu": 1.0, "mem": {"current": 1, "total": 2}, "xray": {"state": "running", "version": "v25.10.31"}, "uptime": 99}
            )
        if path == "/panel/api/setting/defaultSettings":
            return self.ok({"subEnable": True, "subURI": "https://sub.example:2096/sub"})
        if path == "/panel/api/inbounds/options":
            return self.ok(
                [
                    {"id": 1, "remark": "VLESS-443", "protocol": "vless"},
                    {"id": 2, "remark": "Trojan", "protocol": "trojan"},
                    {"id": 3, "remark": "WG-router", "protocol": "wireguard"},
                    {"id": 4, "remark": "AWG-router", "protocol": "amneziawg"},
                ]
            )
        if parts[4:5] == ["links"]:
            return self.ok(self.links_for.get(parts[5], []))
        if path == "/panel/api/nodes/list":
            return self.ok([])
        if path == "/panel/api/clients/onlines":
            return self.ok(["tg_1"] if "tg_1" in self.clients else [])
        if path == "/panel/api/clients/list":
            rows = []
            for email, rec in self.clients.items():
                up, down = self.traffic.get(email, (0, 0))
                rows.append({**rec, "inboundIds": sorted(self.links[email]), "traffic": {"up": up, "down": down, "lastOnline": 0}})
            return self.ok(rows)
        if path == "/panel/api/clients/add":
            client = body["client"]
            if client["email"] in self.clients:
                return self.fail("Something went wrong (email already in use: " + client["email"] + ")")
            rec = {
                **client,
                "id": self.next_id,
                "uuid": f"uuid-{self.next_id}",
                "subId": f"sub{self.next_id}",
                "password": "",
                "flow": "",
                "createdAt": 1_700_000_000_000,
            }
            self.next_id += 1
            self.clients[client["email"]] = rec
            self.links[client["email"]] = set(body["inboundIds"])
            return self.ok(None, "Client added")
        if parts[4] == "get" and parts[5] == "tgId":
            tg = int(parts[6])
            return self.ok([self.payload(e) for e, r in self.clients.items() if r.get("tgId") == tg])

        # /panel/api/clients/{email}/attach|detach
        if len(parts) > 5 and parts[5] in ("attach", "detach"):
            email = parts[4]
            if email not in self.clients:
                return self.fail("record not found")
            ids = set(body["inboundIds"])
            if parts[5] == "attach":
                self.links[email] |= ids
            else:
                self.links[email] -= ids
            return self.ok()
        # /panel/api/clients/{action}/{email}
        email = parts[-1]
        if email not in self.clients:
            return self.fail("Error getting (record not found)")
        if parts[4] == "get":
            return self.ok(self.payload(email))
        if parts[4] == "update":
            # Сервер заменяет запись — проверяем, что секреты не потерялись.
            assert body["id"] == self.clients[email]["uuid"]
            assert body["subId"] == self.clients[email]["subId"]
            self.clients[email] = {**self.clients[email], **{k: v for k, v in body.items() if k != "id"}}
            return self.ok(None, "Client updated")
        if parts[4] == "resetTraffic":
            self.traffic[email] = (0, 0)
            self.clients[email]["enable"] = True
            return self.ok()
        if parts[4] == "del":
            del self.clients[email]
            del self.links[email]
            return self.ok()
        raise AssertionError(f"неожиданный запрос {path}")


@pytest.fixture
def fake(monkeypatch):
    server = FakeXui()
    original = XuiClient.__init__

    def patched(self, base_url, token, **kwargs):
        original(self, base_url, token, **kwargs)
        self._client = httpx.AsyncClient(
            base_url=self._client.base_url,
            headers=self._client.headers,
            transport=httpx.MockTransport(server),
        )

    monkeypatch.setattr(XuiClient, "__init__", patched)
    monkeypatch.setenv("PANEL_TYPE", "3xui")
    return server


def test_factory_picks_panel_by_env(monkeypatch) -> None:
    monkeypatch.setenv("PANEL_TYPE", "3xui")
    assert isinstance(make_client(BASE, "t"), XuiClient)
    monkeypatch.delenv("PANEL_TYPE")
    assert not isinstance(make_client(BASE, "t"), XuiClient)


def test_base_url_keeps_web_base_path() -> None:
    client = XuiClient(BASE + "/panel/inbounds", "t")
    assert str(client._client.base_url).rstrip("/") == BASE


async def test_create_maps_fields_and_builds_subscription_url(fake) -> None:
    client = XuiClient(BASE, "tok")
    expire = datetime(2030, 1, 1, tzinfo=UTC)
    user = await client.create_user(
        username="tg_1",
        expire_at=expire,
        internal_squad_uuids=["1", "2"],
        telegram_id=1,
        hwid_device_limit=3,
        traffic_limit_bytes=10 * 1024**3,
    )
    sent = fake.calls[0][1]
    assert sent["inboundIds"] == [1, 2]
    assert sent["client"]["expiryTime"] == int(expire.timestamp() * 1000)
    assert sent["client"]["limitHwid"] == 3
    assert sent["client"]["tgId"] == 1

    assert user.ref == "tg_1"
    assert user.status is UserStatus.ACTIVE
    assert user.expire_at == expire
    assert user.subscription_url == "https://sub.example:2096/sub/sub1"
    assert [s.name for s in user.active_internal_squads] == ["VLESS-443", "Trojan"]


async def test_duplicate_email_is_reported_as_username_conflict(fake) -> None:
    client = XuiClient(BASE, "tok")
    kw = dict(username="tg_1", expire_at=datetime(2030, 1, 1, tzinfo=UTC), internal_squad_uuids=["1"])
    await client.create_user(**kw)
    with pytest.raises(XuiError) as exc:
        await client.create_user(**kw)
    assert exc.value.status_code == 409 and "username" in str(exc.value)


async def test_update_keeps_secrets_and_moves_inbounds(fake) -> None:
    client = XuiClient(BASE, "tok")
    await client.create_user(username="tg_1", expire_at=datetime(2030, 1, 1, tzinfo=UTC), internal_squad_uuids=["1"])
    new_expire = datetime(2031, 6, 1, tzinfo=UTC)
    user = await client.update_user(
        "tg_1", expireAt=new_expire.isoformat(), status="DISABLED", activeInternalSquads=["2"]
    )
    assert user.expire_at == new_expire
    assert user.status is UserStatus.DISABLED
    assert [s.uuid for s in user.active_internal_squads] == ["2"]


async def test_status_reflects_expiry_and_traffic(fake) -> None:
    client = XuiClient(BASE, "tok")
    await client.create_user(
        username="tg_1",
        expire_at=datetime.now(UTC) - timedelta(days=1),
        internal_squad_uuids=["1"],
    )
    fake.clients["tg_1"]["enable"] = False  # 3x-ui сама выключает истёкших
    assert (await client.get_user("tg_1")).status is UserStatus.EXPIRED

    await client.create_user(
        username="tg_2", expire_at=datetime(2030, 1, 1, tzinfo=UTC),
        internal_squad_uuids=["1"], traffic_limit_bytes=100,
    )
    fake.traffic["tg_2"] = (60, 50)
    assert (await client.get_user("tg_2")).status is UserStatus.LIMITED

    fake.clients["tg_2"]["expiryTime"] = 0
    fake.traffic["tg_2"] = (0, 0)
    assert (await client.get_user("tg_2")).expire_at.year == 2099


async def test_missing_client_and_bad_token(fake) -> None:
    client = XuiClient(BASE, "tok")
    assert await client.get_users_by_username("nobody") == []
    with pytest.raises(RemnawaveError) as exc:
        await client.get_user("nobody")
    assert exc.value.status_code == 404

    bad = XuiClient(BASE, "wrong")
    with pytest.raises(XuiError) as exc:
        await bad.check_connection()
    assert exc.value.status_code == 401


async def test_stats_squads_and_nodes(fake) -> None:
    client = XuiClient(BASE, "tok")
    await client.create_user(username="tg_1", expire_at=datetime(2030, 1, 1, tzinfo=UTC), internal_squad_uuids=["1"])
    stats = await client.get_stats()
    assert stats.users.total_users == 1
    assert stats.users.status_counts["ACTIVE"] == 1
    assert stats.online_stats.online_now == 1

    assert await client.get_internal_squads() == [
        {"uuid": "1", "name": "VLESS-443"},
        {"uuid": "2", "name": "Trojan"},
        {"uuid": "3", "name": "WG-router"},
        {"uuid": "4", "name": "AWG-router"},
    ]
    nodes = await client.get_nodes()
    assert nodes[0].uuid == "local" and nodes[0].is_online
    assert (await client.check_connection())["version"] == "3x-ui, Xray v25.10.31"

    page = await client.get_users(start=0, size=10)
    assert page.total == 1 and page.users[0].username == "tg_1"


# ── Сквозной сценарий бота ────────────────────────────────────────────
@pytest.fixture
async def db():
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with async_sessionmaker(engine, expire_on_commit=False)() as s:
        yield s
    await engine.dispose()


def _config() -> Config:
    return Config(
        token="1:x",
        enabled=True,
        emoji_mode=EmojiMode.PLAIN,
        premium_emoji={},
        brand="Test VPN",
        welcome_text=None,
        support_url=None,
        channel_url=None,
        channel_id=None,
        require_channel_sub=False,
        trial_enabled=True,
        trial_days=3,
        trial_squad_uuids=["1"],
        trial_hwid_limit=2,
        plans=[
            PlanView(
                id=1, title="Месяц", days=30, price_kopeks=19900,
                squad_uuids=["1", "2"], hwid_limit=5, traffic_limit_bytes=0,
            )
        ],
        remnawave_url=BASE,
        remnawave_token="tok",
    )


async def test_trial_then_purchase_through_3xui(db, fake) -> None:
    config = _config()
    user = await subs.get_or_create_user(db, 777)
    user = await subs.grant_trial(db, config, user)
    await db.commit()

    assert user.remnawave_uuid == "tg_777"
    assert user.subscription_url == "https://sub.example:2096/sub/sub1"
    assert fake.links["tg_777"] == {1}

    await subs.grant_plan(db, config, user, config.plans[0], source="manual")
    await db.commit()

    # Тот же клиент продлён, а не создан второй — ссылка не меняется.
    assert list(fake.clients) == ["tg_777"]
    assert fake.links["tg_777"] == {1, 2}
    expire = datetime.fromtimestamp(fake.clients["tg_777"]["expiryTime"] / 1000, tz=UTC)
    assert 32 <= (expire - datetime.now(UTC)).days <= 33
    assert user.subscription_url == "https://sub.example:2096/sub/sub1"


async def test_existing_client_is_adopted_on_start(db, fake) -> None:
    """Клиент с этим tgId уже есть в 3x-ui — бот подхватывает его по /start."""
    client = XuiClient(BASE, "tok")
    await client.create_user(
        username="old_user", expire_at=datetime(2030, 1, 1, tzinfo=UTC),
        internal_squad_uuids=["1"], telegram_id=555,
    )
    user = await subs.get_or_create_user(db, 555)
    await subs.link_from_remnawave(db, _config(), user)
    await db.commit()
    assert user.remnawave_uuid == "old_user"


# ── Роутеры: WireGuard и AmneziaWG отдаются файлом .conf ──────────────
AWG_CONF = (
    "[Interface]\nPrivateKey = privAWG=\nAddress = 10.8.0.2/32\nJc = 4\n\n"
    "# router\n[Peer]\nPublicKey = pubAWG=\nAllowedIPs = 0.0.0.0/0, ::/0\n"
    "Endpoint = 1.2.3.4:51821"
)


async def _router_user(fake, inbounds: list[str], links: list[str]) -> XuiClient:
    client = XuiClient(BASE, "tok")
    await client.create_user(
        username="tg_5", expire_at=datetime(2030, 1, 1, tzinfo=UTC), internal_squad_uuids=inbounds
    )
    fake.links_for["tg_5"] = links
    return client


async def test_awg_link_is_decoded_to_conf(fake) -> None:
    import base64

    link = "vpn://" + base64.urlsafe_b64encode(AWG_CONF.encode()).decode().rstrip("=")
    client = await _router_user(fake, ["4"], [link])
    [file] = await client.get_config_files("tg_5")
    assert file.protocol == "amneziawg"
    assert file.filename == "awg_tg_5.conf"
    assert file.text == AWG_CONF


async def test_wireguard_link_is_built_into_conf(fake) -> None:
    link = (
        "wireguard://yAnz5TF%2BlXXJte14tji3zlMNq%2BhdFOCfPk8Y7WJq%2FEg%3D@1.2.3.4:51820"
        "?publickey=pubWG%3D&address=10.0.0.2%2F32%2Cfd00%3A%3A2%2F128&mtu=1420"
        "&dns=1.1.1.1&presharedkey=psk%3D&keepalive=25#router"
    )
    client = await _router_user(fake, ["3"], [link, "vless://ignored@x:1"])
    [file] = await client.get_config_files("tg_5")
    assert file.protocol == "wireguard" and file.filename == "wg_tg_5.conf"
    assert file.text == (
        "[Interface]\n"
        "PrivateKey = yAnz5TF+lXXJte14tji3zlMNq+hdFOCfPk8Y7WJq/Eg=\n"
        "Address = 10.0.0.2/32, fd00::2/128\n"
        "DNS = 1.1.1.1\n"
        "MTU = 1420\n\n"
        "[Peer]\n"
        "PublicKey = pubWG=\n"
        "PresharedKey = psk=\n"
        "AllowedIPs = 0.0.0.0/0, ::/0\n"
        "Endpoint = 1.2.3.4:51820\n"
        "PersistentKeepalive = 25\n"
    )


async def test_wireguard_link_with_newline_injection_is_dropped(fake) -> None:
    link = "wireguard://key@1.2.3.4:51820?publickey=p&dns=1.1.1.1%0APostUp%3Drm"
    client = await _router_user(fake, ["3"], [link])
    assert await client.get_config_files("tg_5") == []


async def test_router_only_client_has_no_subscription_url(fake) -> None:
    client = await _router_user(fake, ["3", "4"], [])
    assert (await client.get_user("tg_5")).subscription_url is None

    mixed = await client.update_user("tg_5", activeInternalSquads=["1", "3"])
    assert mixed.subscription_url == "https://sub.example:2096/sub/sub1"
