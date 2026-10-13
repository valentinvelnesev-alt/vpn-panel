"""Клиент 3x-ui и выдача подписок через него.

Поддельная 3x-ui хранит клиентов в памяти и отвечает в формате её API
({success, msg, obj}), включая ошибки с HTTP 200 и success=false.
"""

import json
import re
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config import Config, PlanView
from app.services import subscriptions as subs
from shared.db.base import Base
from shared.db.models import BotSubscription, EmojiMode, Purchase
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
        self.sub_id_for_new_clients: str | None = None

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
                "uuid": str(uuid4()),
                # The upstream API defaults an omitted Sub ID to a UUID;
                # its web form sends a 16-character lowercase token instead.
                "subId": self.sub_id_for_new_clients or client.get("subId") or str(uuid4()),
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
    sent = next(body for path, body in fake.calls if path == "POST /panel/api/clients/add")
    assert sent["inboundIds"] == [1, 2]
    assert sent["client"]["expiryTime"] == int(expire.timestamp() * 1000)
    assert sent["client"]["limitHwid"] == 3
    assert sent["client"]["tgId"] == 1
    sub_id = sent["client"]["subId"]
    assert re.fullmatch(r"[a-z0-9]{16}", sub_id)
    assert fake.clients["tg_1"]["subId"] == sub_id
    assert sub_id != fake.clients["tg_1"]["uuid"]

    assert user.ref == "tg_1"
    assert user.status is UserStatus.ACTIVE
    assert user.expire_at == expire
    assert user.short_uuid == sub_id
    assert user.subscription_url == f"https://sub.example:2096/sub/{sub_id}"
    assert [s.name for s in user.active_internal_squads] == ["VLESS-443", "Trojan"]


async def test_successive_creates_have_distinct_subscription_ids(fake) -> None:
    client = XuiClient(BASE, "tok")
    first = await client.create_user(
        username="tg_1", expire_at=datetime(2030, 1, 1, tzinfo=UTC), internal_squad_uuids=["1"]
    )
    second = await client.create_user(
        username="tg_2", expire_at=datetime(2030, 1, 1, tzinfo=UTC), internal_squad_uuids=["1"]
    )
    assert re.fullmatch(r"[a-z0-9]{16}", second.short_uuid)
    assert first.short_uuid != second.short_uuid
    assert first.subscription_url != second.subscription_url


async def test_create_uses_subscription_id_returned_by_panel(fake) -> None:
    fake.sub_id_for_new_clients = "storedbythepanel"
    client = XuiClient(BASE, "tok")
    user = await client.create_user(
        username="tg_1", expire_at=datetime(2030, 1, 1, tzinfo=UTC), internal_squad_uuids=["1"]
    )
    assert user.short_uuid == fake.clients["tg_1"]["subId"] == "storedbythepanel"
    assert user.subscription_url == "https://sub.example:2096/sub/storedbythepanel"


async def test_existing_uuid_subscription_id_is_preserved_on_update(fake) -> None:
    client = XuiClient(BASE, "tok")
    # An existing user created by the API before the explicit Sub ID fix.
    await client._post(
        "/panel/api/clients/add",
        json={"client": {"email": "legacy", "enable": True}, "inboundIds": [1]},
    )
    before = await client.get_user("legacy")
    assert str(UUID(before.short_uuid)) == before.short_uuid
    updated = await client.update_user(
        "legacy", expireAt=datetime(2031, 1, 1, tzinfo=UTC).isoformat(), activeInternalSquads=["2"]
    )
    assert updated.short_uuid == before.short_uuid
    assert updated.subscription_url == before.subscription_url
    assert fake.clients["legacy"]["subId"] == before.short_uuid


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
    assert (await client.get_user("tg_2")).expire_at is None


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
    trial_sub_id = fake.clients["tg_777"]["subId"]
    trial_url = f"https://sub.example:2096/sub/{trial_sub_id}"
    assert re.fullmatch(r"[a-z0-9]{16}", trial_sub_id)
    assert user.subscription_url == trial_url
    assert fake.links["tg_777"] == {1}

    await subs.grant_plan(db, config, user, config.plans[0], source="manual")
    await db.commit()

    # Тот же клиент продлён, а не создан второй — ссылка не меняется.
    assert list(fake.clients) == ["tg_777"]
    assert fake.links["tg_777"] == {1, 2}
    expire = datetime.fromtimestamp(fake.clients["tg_777"]["expiryTime"] / 1000, tz=UTC)
    assert 32 <= (expire - datetime.now(UTC)).days <= 33
    assert fake.clients["tg_777"]["subId"] == trial_sub_id
    assert user.subscription_url == trial_url


async def test_trial_upgrade_and_additional_purchase_keep_independent_urls(db, fake) -> None:
    config = _config()
    user = await subs.get_or_create_user(db, 777)
    await subs.grant_trial(db, config, user)
    trial_url = user.subscription_url
    trial_sub_id = fake.clients["tg_777"]["subId"]

    upgraded = await subs.create_subscription(db, config, user, config.plans[0], source="manual")
    assert upgraded.username == "tg_777"
    assert upgraded.subscription_url == trial_url
    assert fake.clients["tg_777"]["subId"] == trial_sub_id

    additional = await subs.create_subscription(db, config, user, config.plans[0], source="manual")
    additional_sub_id = fake.clients[additional.username]["subId"]
    assert re.fullmatch(r"[a-z0-9]{16}", additional_sub_id)
    assert additional_sub_id != trial_sub_id
    assert additional.subscription_url == f"https://sub.example:2096/sub/{additional_sub_id}"
    assert upgraded.subscription_url == trial_url
    assert len(fake.clients) == 2


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
    assert mixed.subscription_url == f"https://sub.example:2096/sub/{fake.clients['tg_5']['subId']}"


async def test_deleted_inbound_is_skipped_without_mutating_empty_tariff(fake):
    client = XuiClient(BASE, 'tok')
    user = await client.create_user(username='check', expire_at=datetime(2030, 1, 1, tzinfo=UTC), internal_squad_uuids=['1', '999'])
    assert fake.links['check'] == {1}
    await client.update_user(user.ref, activeInternalSquads=['2', '999'])
    assert fake.links['check'] == {2}
    original_expiry = fake.clients['check']['expiryTime']
    with pytest.raises(XuiError, match='не осталось'):
        await client.update_user(user.ref, activeInternalSquads=['999'], expireAt='2035-01-01T00:00:00+00:00')
    assert fake.clients['check']['expiryTime'] == original_expiry
    assert fake.links['check'] == {2}
    with pytest.raises(XuiError, match='не осталось'):
        await client.create_user(username='empty', expire_at=datetime(2030, 1, 1, tzinfo=UTC), internal_squad_uuids=['999'])
    assert 'empty' not in fake.clients
    await client.aclose()


async def test_deleted_client_keeps_history_and_trial(db, fake):
    config = _config()
    user = await subs.get_or_create_user(db, 887)
    await subs.grant_trial(db, config, user)
    await db.commit()
    purchases = list(await db.scalars(select(Purchase)))
    assert purchases
    del fake.clients['tg_887']
    assert await subs.reconcile_deleted_clients(db, config, user) == 1
    await db.commit()
    assert list(await db.scalars(select(BotSubscription))) == []
    assert user.subscription_url is None and user.remnawave_uuid is None
    assert user.trial_used
    assert not await subs.trial_available(db, config, user)
    kept = list(await db.scalars(select(Purchase)))
    assert [p.id for p in kept] == [p.id for p in purchases]
    assert all(p.subscription_id is None for p in kept)


async def test_failure_and_malformed_snapshot_keep_local_clients(db, fake, monkeypatch):
    config = _config()
    user = await subs.get_or_create_user(db, 888)
    await subs.grant_trial(db, config, user)
    await db.commit()
    original_url = user.subscription_url
    fake.token = 'different'
    with pytest.raises(XuiError):
        await subs.reconcile_deleted_clients(db, config, user)
    fake.token = 'tok'
    original = XuiClient._get
    async def malformed(self, path, **kwargs):
        return None if path == '/panel/api/clients/list' else await original(self, path, **kwargs)
    monkeypatch.setattr(XuiClient, '_get', malformed)
    with pytest.raises(XuiError, match='некорректный'):
        await subs.reconcile_deleted_clients(db, config, user)
    assert user.subscription_url == original_url
    assert len(list(await db.scalars(select(BotSubscription)))) == 1


async def test_rename_and_unlimited_sync_preserve_subscription(db, fake):
    config = _config()
    user = await subs.get_or_create_user(db, 890)
    await subs.grant_trial(db, config, user)
    await db.commit()
    before = await db.scalar(select(BotSubscription))
    before_id = before.id
    rec = fake.clients.pop('tg_890')
    rec['email'] = 'Renamed'
    rec['expiryTime'] = 0
    fake.clients['Renamed'] = rec
    fake.links['Renamed'] = fake.links.pop('tg_890')
    assert await subs.reconcile_deleted_clients(db, config, user) == 0
    await db.commit()
    after = await db.scalar(select(BotSubscription))
    assert after.id == before_id and after.username == 'Renamed'
    assert after.remote_ref == 'Renamed' and after.expire_at is None
    assert subs.is_active(user) and user.expire_at is None
    assert user.trial_used
    purchases = list(await db.scalars(select(Purchase)))
    assert all(p.subscription_id == before_id for p in purchases)


async def test_json_base_is_used_and_unlimited_renewal_stays_unlimited(db, fake, monkeypatch):
    original = XuiClient._post
    async def json_defaults(self, path, **kwargs):
        if path == '/panel/api/setting/defaultSettings':
            return {'subEnable': True, 'subURI': 'https://sub.example/sub/', 'subJsonURI': 'https://sub.example/json/'}
        return await original(self, path, **kwargs)
    monkeypatch.setattr(XuiClient, '_post', json_defaults)
    config = _config()
    user = await subs.get_or_create_user(db, 891)
    await subs.grant_trial(db, config, user)
    row = await db.scalar(select(BotSubscription))
    assert row.subscription_url.startswith('https://sub.example/json/')
    fake.clients['tg_891']['expiryTime'] = 0
    await subs.reconcile_deleted_clients(db, config, user)
    await subs.extend_subscription(db, config, row, config.plans[0], source='admin', amount_kopeks=0)
    assert row.expire_at is None and fake.clients['tg_891']['expiryTime'] == 0


async def test_nodes_accept_millisecond_heartbeat(fake, monkeypatch):
    original = XuiClient._get
    async def with_node(self, path, **kwargs):
        if path == '/panel/api/nodes/list':
            return [{'id': 1, 'name': 'node', 'lastHeartbeat': 1791700000000}]
        return await original(self, path, **kwargs)
    monkeypatch.setattr(XuiClient, '_get', with_node)
    c = XuiClient(BASE, 'tok')
    assert (await c.get_nodes())[1].last_status_change.year == 2026
    await c.aclose()


async def test_purchase_after_importing_unlimited_key_gets_real_term(db, fake):
    config = _config()
    c = XuiClient(BASE, 'tok')
    await c.create_user(username='manual', expire_at=datetime(2030, 1, 1, tzinfo=UTC), internal_squad_uuids=['1'], telegram_id=892)
    fake.clients['manual']['expiryTime'] = 0
    user = await subs.get_or_create_user(db, 892)
    purchased = await subs.create_subscription(db, config, user, config.plans[0], source='admin', amount_kopeks=0)
    assert 29 <= (purchased.expire_at - datetime.now(UTC)).days <= 30
    assert fake.clients['manual']['expiryTime'] == 0
    assert purchased.username != 'manual'
    await c.aclose()


async def test_global_sync_reimports_existing_renamed_client(db, fake):
    config = _config()
    user = await subs.get_or_create_user(db, 893)
    await subs.grant_trial(db, config, user)
    await db.commit()
    row = await db.scalar(select(BotSubscription))
    from shared.sync import forget_remote_key
    await forget_remote_key(db, remnawave_id=None, remnawave_uuid=row.remnawave_uuid)
    await db.commit()
    await subs.reconcile_deleted_clients(db, config)
    rows = list(await db.scalars(select(BotSubscription)))
    assert len(rows) == 1 and rows[0].user_id == user.id
    assert user.trial_used
