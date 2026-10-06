"""Асинхронный клиент 3x-ui (API v3, /panel/api/*).

Клиент повторяет интерфейс RemnawaveClient и отдаёт те же модели (User,
Node, SystemStats, Device), поэтому бот и панель работают с ним, не зная,
какая панель стоит за адресом. Сопоставление понятий:

    Remnawave                       3x-ui
    username / uuid / id            email (уникален в панели)
    сквад (internal squad)          inbound; uuid сквада = str(id инбаунда)
    expireAt                        expiryTime, мс (0 — бессрочно)
    trafficLimitBytes               totalGB (это байты, несмотря на имя)
    hwidDeviceLimit                 limitHwid
    description                     comment
    status DISABLED                 enable = false
    subscriptionUrl                 subURI из настроек + subId клиента

Ответы 3x-ui завёрнуты в {"success", "msg", "obj"}, а ошибки приходят с
HTTP 200 и success=false — `_request` превращает их в XuiError.

Справочник API: https://docs.sanaei.dev/docs/reference/api/
"""

import logging
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import httpx

from shared.remnawave.client import RemnawaveError
from shared.remnawave.models import (
    Device,
    Node,
    NodeVersions,
    NodesStats,
    OnlineStats,
    Squad,
    SystemStats,
    User,
    UserPage,
    UsersStats,
    UserStatus,
    UserTraffic,
)

log = logging.getLogger("xui")

# Бессрочная подписка в 3x-ui — expiryTime=0. Модели и бот ждут дату,
# поэтому показываем её как очень далёкую.
_FAR_FUTURE = datetime(2099, 12, 31, tzinfo=UTC)

# Узел «сама панель»: в 3x-ui ноды необязательны, клиенты обслуживает
# Xray на том же сервере. Чтобы дашборд и алерты не были пустыми, он
# показывается отдельной нодой.
LOCAL_NODE = "local"


class XuiError(RemnawaveError):
    """Ошибка 3x-ui. Наследник RemnawaveError — весь существующий код ловит
    ошибки панели через него и не должен различать типы панелей."""


UserRef = int | str


def _ref(ref: UserRef) -> str:
    # stored_ref превращает чисто цифровую строку в int (так Remnawave
    # отличает id от uuid). У 3x-ui идентификатор всегда email-строка.
    return str(ref)


def _ms(value: int | None) -> datetime | None:
    if not value or value <= 0:
        return None
    return datetime.fromtimestamp(value / 1000, tz=UTC)


def _to_ms(value: datetime | str) -> int:
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return int(value.timestamp() * 1000)


# Поля ClientRecord, которые обратно принимает /clients/update. Сервер
# заменяет запись целиком, поэтому секреты протоколов (uuid, password, auth…)
# обязательно отправляем как были, иначе у клиента сменятся ключи.
_UPDATE_FIELDS = (
    "email",
    "security",
    "password",
    "flow",
    "auth",
    "privateKey",
    "publicKey",
    "preSharedKey",
    "forwardedPorts",
    "secret",
    "adTag",
    "limitIp",
    "limitHwid",
    "totalGB",
    "expiryTime",
    "enable",
    "tgId",
    "subId",
    "group",
    "comment",
    "reset",
    "resetDay",
    "resetWeekday",
    "resetMax",
    "trafficReset",
    "trafficResetDay",
)


class XuiClient:
    """Тонкая обёртка над httpx с авторизацией по API-токену 3x-ui
    (Настройки → Безопасность → API Token)."""

    def __init__(
        self,
        base_url: str,
        token: str,
        *,
        timeout: float = 15.0,
        verify_tls: bool = True,
    ) -> None:
        # Путь в адресе — это webBasePath панели (…:2053/AbCdEf/), без него
        # API недоступен, поэтому сохраняем его. Хвост /panel… отрезаем:
        # часто вставляют адрес открытой страницы панели.
        parts = urlsplit(base_url)
        path = parts.path.rstrip("/")
        idx = path.lower().find("/panel")
        if idx != -1:
            path = path[:idx]
        clean = urlunsplit((parts.scheme, parts.netloc, path, "", ""))

        self._client = httpx.AsyncClient(
            base_url=clean,
            headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
            timeout=timeout,
            verify=verify_tls,
            follow_redirects=False,
        )
        self._sub_base: str | None = None
        self._sub_loaded = False
        self._squad_names: dict[int, str] | None = None

    async def aclose(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> "XuiClient":
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.aclose()

    # ── Транспорт ─────────────────────────────────────────────────────
    async def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        try:
            response = await self._client.request(method, path, **kwargs)
        except httpx.TimeoutException as exc:
            raise XuiError("3x-ui не ответила вовремя") from exc
        except httpx.HTTPError as exc:
            raise XuiError(f"Не удалось связаться с 3x-ui: {exc}") from exc

        # Без токена (или с неверным) 3x-ui отправляет на страницу входа
        # либо отвечает 401/404 — для API это одна и та же беда.
        if response.status_code in (401, 403) or response.is_redirect:
            raise XuiError("3x-ui отклонила токен — проверьте его в настройках", 401)
        if response.status_code == 404:
            raise XuiError(
                "3x-ui: API не найден по этому адресу — проверьте, что адрес "
                "включает секретный путь панели (webBasePath) и версия 3x-ui не ниже 3.0",
                404,
            )
        if response.status_code >= 400:
            raise XuiError(
                f"3x-ui вернула ошибку {response.status_code}: {response.text[:200]}",
                response.status_code,
            )

        try:
            body = response.json()
        except ValueError as exc:
            raise XuiError(
                "3x-ui вернула не JSON — вероятно, это страница входа: "
                "проверьте адрес и токен"
            ) from exc
        if not isinstance(body, dict) or "success" not in body:
            return body
        if not body.get("success"):
            msg = str(body.get("msg") or "без описания")
            low = msg.lower()
            code = 400
            if "not found" in low:
                code = 404
            elif "already in use" in low or "exist" in low:
                code = 409
            raise XuiError(f"3x-ui: {msg[:300]}", code)
        return body.get("obj")

    async def _get(self, path: str, **kwargs: Any) -> Any:
        return await self._request("GET", path, **kwargs)

    async def _post(self, path: str, **kwargs: Any) -> Any:
        return await self._request("POST", path, **kwargs)

    # ── Проверка подключения ──────────────────────────────────────────
    async def check_connection(self) -> dict[str, Any]:
        status = await self._get("/panel/api/server/status") or {}
        xray = status.get("xray") or {}
        return {"version": f"3x-ui, Xray {xray.get('version')}" if xray.get("version") else "3x-ui"}

    # ── Подписка ──────────────────────────────────────────────────────
    async def _subscription_base(self) -> str | None:
        """Адрес подписки (subURI) один на панель — берём из настроек один
        раз за жизнь клиента. Подписка выключена — ссылки нет."""
        if self._sub_loaded:
            return self._sub_base
        try:
            data = await self._post("/panel/api/setting/defaultSettings") or {}
        except XuiError as exc:
            log.warning("3x-ui: не удалось прочитать адрес подписки: %s", exc)
            return None
        base = data.get("subURI") if data.get("subEnable", True) else None
        if base and not base.endswith("/"):
            base += "/"
        self._sub_base = base or None
        self._sub_loaded = True
        return self._sub_base

    async def _inbound_names(self) -> dict[int, str]:
        if self._squad_names is None:
            try:
                items = await self._get("/panel/api/inbounds/options") or []
            except XuiError:
                return {}
            self._squad_names = {
                int(i["id"]): i.get("remark") or i.get("tag") or f"inbound {i['id']}"
                for i in items
            }
        return self._squad_names

    # ── Преобразование клиента 3x-ui в User ──────────────────────────
    async def _to_user(
        self,
        rec: dict[str, Any],
        inbound_ids: list[int],
        *,
        used: int = 0,
        last_online: int | None = None,
    ) -> User:
        now = datetime.now(UTC)
        expiry = rec.get("expiryTime") or 0
        total = rec.get("totalGB") or 0
        expire_at = _ms(expiry) if expiry > 0 else _FAR_FUTURE

        if not rec.get("enable", True):
            # 3x-ui сама выключает клиента по сроку и трафику — уточняем
            # причину, чтобы бот не путал «истёк» с «заблокирован».
            if expiry > 0 and expire_at and expire_at <= now:
                status = UserStatus.EXPIRED
            elif total > 0 and used >= total:
                status = UserStatus.LIMITED
            else:
                status = UserStatus.DISABLED
        elif expiry > 0 and expire_at and expire_at <= now:
            status = UserStatus.EXPIRED
        elif total > 0 and used >= total:
            status = UserStatus.LIMITED
        else:
            status = UserStatus.ACTIVE

        names = await self._inbound_names()
        sub_base = await self._subscription_base()
        sub_id = rec.get("subId")
        tg_id = rec.get("tgId") or None

        return User(
            id=None,
            # email — единственный устойчивый ключ клиента 3x-ui. Кладём его
            # в uuid: User.ref отдаёт его первым, и он сохраняется в БД бота.
            uuid=rec["email"],
            shortUuid=sub_id,
            username=rec["email"],
            status=status,
            expireAt=expire_at,
            telegramId=tg_id,
            description=rec.get("comment") or None,
            tag=rec.get("group") or None,
            hwidDeviceLimit=rec.get("limitHwid") or None,
            trafficLimitBytes=total,
            subscriptionUrl=(sub_base + sub_id) if sub_base and sub_id else None,
            createdAt=_ms(rec.get("createdAt")),
            activeInternalSquads=[
                Squad(uuid=str(i), name=names.get(i, f"inbound {i}")) for i in inbound_ids
            ],
            userTraffic=UserTraffic(usedTrafficBytes=used, onlineAt=_ms(last_online)),
        )

    async def _from_payload(self, payload: dict[str, Any]) -> User:
        """Ответ /clients/get: {client, inboundIds, usedTraffic, …}."""
        return await self._to_user(
            payload.get("client") or {},
            list(payload.get("inboundIds") or []),
            used=int(payload.get("usedTraffic") or 0),
        )

    async def _from_list_row(self, row: dict[str, Any]) -> User:
        """Строка /clients/list: поля клиента + inboundIds + traffic."""
        traffic = row.get("traffic") or {}
        return await self._to_user(
            row,
            list(row.get("inboundIds") or []),
            used=int(traffic.get("up") or 0) + int(traffic.get("down") or 0),
            last_online=traffic.get("lastOnline"),
        )

    async def _payload(self, ref: UserRef) -> dict[str, Any]:
        return await self._get(f"/panel/api/clients/get/{_ref(ref)}") or {}

    # ── Пользователи ──────────────────────────────────────────────────
    async def _all_rows(self) -> list[dict[str, Any]]:
        rows = await self._get("/panel/api/clients/list") or []
        return sorted(rows, key=lambda r: r.get("id") or 0)

    async def get_users(self, *, start: int = 0, size: int = 50) -> UserPage:
        rows = await self._all_rows()
        users = [await self._from_list_row(r) for r in rows[start : start + size]]
        return UserPage(users=users, total=len(rows))

    async def get_user(self, ref: UserRef) -> User:
        return await self._from_payload(await self._payload(ref))

    async def get_users_by_telegram_id(self, telegram_id: int) -> list[User]:
        items = await self._get(f"/panel/api/clients/get/tgId/{telegram_id}") or []
        return [await self._from_payload(p) for p in items]

    async def get_users_by_username(self, username: str) -> list[User]:
        try:
            return [await self.get_user(username)]
        except XuiError as exc:
            if exc.status_code in (400, 404):
                return []
            raise

    async def get_users_by_email(self, email: str) -> list[User]:
        # Отдельного поля e-mail у клиента 3x-ui нет — «email» там имя.
        return await self.get_users_by_username(email)

    async def create_user(
        self,
        *,
        username: str,
        expire_at: datetime,
        internal_squad_uuids: list[str],
        telegram_id: int | None = None,
        email: str | None = None,
        hwid_device_limit: int | None = None,
        traffic_limit_bytes: int = 0,
        description: str | None = None,
        tag: str | None = None,
    ) -> User:
        inbound_ids = [int(s) for s in internal_squad_uuids]
        if not inbound_ids:
            raise XuiError(
                "3x-ui: у тарифа не выбран ни один инбаунд — укажите их в настройках тарифа",
                400,
            )
        client: dict[str, Any] = {
            "email": username,
            "totalGB": traffic_limit_bytes,
            "expiryTime": _to_ms(expire_at),
            "tgId": telegram_id or 0,
            "limitIp": 0,
            "limitHwid": hwid_device_limit or 0,
            "enable": True,
            "comment": description or "",
        }
        if tag:
            client["group"] = tag
        try:
            await self._post(
                "/panel/api/clients/add",
                json={"client": client, "inboundIds": inbound_ids},
            )
        except XuiError as exc:
            # _create_or_adopt ловит конфликт по слову «username».
            if exc.status_code == 409:
                raise XuiError(f"username {username} уже занят в 3x-ui", 409) from exc
            raise
        return await self.get_user(username)

    async def update_user(self, ref: UserRef, **fields: Any) -> User:
        """Частичное обновление поверх полной записи: 3x-ui принимает только
        целую запись, поэтому читаем, меняем и отправляем обратно."""
        email = _ref(ref)
        payload = await self._payload(email)
        rec = payload.get("client") or {}
        body: dict[str, Any] = {k: rec[k] for k in _UPDATE_FIELDS if k in rec}
        body["id"] = rec.get("uuid") or ""

        if "expireAt" in fields and fields["expireAt"]:
            body["expiryTime"] = _to_ms(fields["expireAt"])
        if "status" in fields and fields["status"]:
            body["enable"] = str(fields["status"]).upper() != UserStatus.DISABLED
        if "trafficLimitBytes" in fields and fields["trafficLimitBytes"] is not None:
            body["totalGB"] = int(fields["trafficLimitBytes"])
        if "hwidDeviceLimit" in fields:
            body["limitHwid"] = int(fields["hwidDeviceLimit"] or 0)
        if "telegramId" in fields:
            body["tgId"] = int(fields["telegramId"] or 0)
        if "description" in fields:
            body["comment"] = fields["description"] or ""
        if "tag" in fields:
            body["group"] = fields["tag"] or ""
        # trafficLimitStrategy и email у 3x-ui аналогов не имеют — пропускаем.

        await self._post(f"/panel/api/clients/update/{email}", json=body)

        squads = fields.get("activeInternalSquads")
        if squads is not None:
            await self._set_inbounds(email, set(payload.get("inboundIds") or []), squads)
        return await self.get_user(email)

    async def _set_inbounds(self, email: str, current: set[int], squads: list[str]) -> None:
        wanted = {int(s) for s in squads}
        if not wanted:
            # Пустой список отвязал бы клиента от всего — ключ перестал бы
            # работать без всякого предупреждения. Оставляем как есть.
            return
        if add := sorted(wanted - current):
            await self._post(f"/panel/api/clients/{email}/attach", json={"inboundIds": add})
        if remove := sorted(current - wanted):
            await self._post(f"/panel/api/clients/{email}/detach", json={"inboundIds": remove})

    async def extend_expiration(self, ref: UserRef, new_expire_at: datetime) -> User:
        return await self.update_user(ref, expireAt=new_expire_at, status=UserStatus.ACTIVE)

    async def set_status(self, ref: UserRef, status: str) -> User:
        return await self.update_user(ref, status=status)

    async def reset_traffic(self, ref: UserRef) -> None:
        await self._post(f"/panel/api/clients/resetTraffic/{_ref(ref)}")

    # ── Массовые операции ─────────────────────────────────────────────
    async def bulk_extend_expiration(self, refs: list[UserRef], days: int) -> None:
        await self._post(
            "/panel/api/clients/bulkAdjust",
            json={"emails": [_ref(r) for r in refs], "addDays": days},
        )

    async def bulk_reset_traffic(self, refs: list[UserRef]) -> None:
        await self._post(
            "/panel/api/clients/bulkResetTraffic", json={"emails": [_ref(r) for r in refs]}
        )

    async def bulk_update_squads(
        self, refs: list[UserRef], internal_squad_uuids: list[str]
    ) -> None:
        for ref in refs:
            payload = await self._payload(ref)
            await self._set_inbounds(
                _ref(ref), set(payload.get("inboundIds") or []), internal_squad_uuids
            )

    async def delete_user(self, ref: UserRef) -> None:
        await self._post(f"/panel/api/clients/del/{_ref(ref)}")

    async def bulk_delete(self, refs: list[UserRef]) -> None:
        await self._post(
            "/panel/api/clients/bulkDel",
            json={"emails": [_ref(r) for r in refs], "keepTraffic": False},
        )

    # ── «Сквады» = инбаунды ───────────────────────────────────────────
    async def get_internal_squads(self) -> list[dict[str, Any]]:
        self._squad_names = None
        names = await self._inbound_names()
        return [{"uuid": str(i), "name": n} for i, n in sorted(names.items())]

    # ── Устройства (HWID) ─────────────────────────────────────────────
    async def get_devices(self, ref: UserRef) -> list[Device]:
        items = await self._post(f"/panel/api/clients/hwids/{_ref(ref)}") or []
        return [
            Device(
                hwid=str(d.get("id")),
                platform=d.get("deviceOs") or None,
                osVersion=d.get("osVersion") or None,
                deviceModel=d.get("deviceModel") or None,
                userAgent=d.get("userAgent") or None,
                createdAt=_ms(d.get("firstSeen")),
                updatedAt=_ms(d.get("lastSeen")),
            )
            for d in items
        ]

    async def delete_device(self, ref: UserRef, hwid: str) -> None:
        # hwid здесь — id записи устройства в 3x-ui (см. get_devices).
        await self._request("DELETE", f"/panel/api/clients/hwids/{_ref(ref)}/{hwid}")

    async def delete_all_devices(self, ref: UserRef) -> None:
        await self._request("DELETE", f"/panel/api/clients/hwids/{_ref(ref)}")

    # ── Ноды ──────────────────────────────────────────────────────────
    async def _local_node(self) -> Node:
        status = await self._get("/panel/api/server/status") or {}
        xray = status.get("xray") or {}
        net = status.get("netTraffic") or status.get("netIO") or {}
        try:
            online = len(await self._post("/panel/api/clients/onlines") or [])
        except XuiError:
            online = 0
        host = urlsplit(str(self._client.base_url)).hostname or "localhost"
        return Node(
            uuid=LOCAL_NODE,
            name="3x-ui",
            address=host,
            isConnected=xray.get("state") == "running",
            isDisabled=False,
            lastStatusMessage=xray.get("errorMsg") or None,
            trafficUsedBytes=int(net.get("sent") or net.get("up") or 0)
            + int(net.get("recv") or net.get("down") or 0),
            usersOnline=online,
            xrayUptime=float(status.get("appStats", {}).get("uptime") or status.get("uptime") or 0),
            versions=NodeVersions(xray=xray.get("version")),
        )

    async def get_nodes(self) -> list[Node]:
        nodes = [await self._local_node()]
        try:
            remote = await self._get("/panel/api/nodes/list") or []
        except XuiError as exc:
            log.debug("3x-ui: список нод недоступен: %s", exc)
            remote = []
        for i, n in enumerate(remote, start=1):
            nodes.append(
                Node(
                    uuid=str(n.get("id")),
                    name=n.get("name") or n.get("address") or f"node {n.get('id')}",
                    address=n.get("address") or "",
                    port=n.get("port"),
                    isConnected=n.get("status") == "online",
                    isDisabled=not n.get("enable", True),
                    lastStatusMessage=n.get("lastError") or n.get("xrayError") or None,
                    lastStatusChange=datetime.fromtimestamp(n["lastHeartbeat"], tz=UTC)
                    if n.get("lastHeartbeat")
                    else None,
                    trafficUsedBytes=int(n.get("netUp") or 0) + int(n.get("netDown") or 0),
                    usersOnline=int(n.get("onlineCount") or 0),
                    xrayUptime=float(n.get("uptimeSecs") or 0),
                    viewPosition=i,
                    versions=NodeVersions(xray=n.get("xrayVersion"), node=n.get("panelVersion")),
                )
            )
        return nodes

    async def get_node(self, uuid: str) -> Node:
        for node in await self.get_nodes():
            if node.uuid == uuid:
                return node
        raise XuiError("Нода не найдена в 3x-ui", 404)

    async def _set_node_enabled(self, uuid: str, enable: bool) -> None:
        if uuid == LOCAL_NODE:
            raise XuiError("Основной сервер 3x-ui нельзя выключить из бота", 400)
        await self._post(f"/panel/api/nodes/setEnable/{uuid}", json={"enable": enable})

    async def enable_node(self, uuid: str) -> None:
        await self._set_node_enabled(uuid, True)

    async def disable_node(self, uuid: str) -> None:
        await self._set_node_enabled(uuid, False)

    async def restart_node(self, uuid: str) -> None:
        if uuid != LOCAL_NODE:
            raise XuiError(
                "3x-ui не умеет перезапускать отдельную ноду по API — "
                "перезапустите Xray в панели самой ноды",
                400,
            )
        await self._post("/panel/api/server/restartXrayService")

    async def restart_all_nodes(self) -> None:
        await self._post("/panel/api/server/restartXrayService")

    # ── Системная статистика ──────────────────────────────────────────
    async def get_stats(self) -> SystemStats:
        rows = await self._all_rows()
        users = [await self._from_list_row(r) for r in rows]
        counts: dict[str, int] = {s.value: 0 for s in UserStatus}
        for u in users:
            counts[u.status.value] += 1

        try:
            online_now = len(await self._post("/panel/api/clients/onlines") or [])
        except XuiError:
            online_now = 0
        day_ago = datetime.now(UTC).timestamp() - 86400
        last_day = sum(1 for u in users if u.online_at and u.online_at.timestamp() >= day_ago)
        lifetime = sum(u.used_traffic_bytes for u in users)

        status = await self._get("/panel/api/server/status") or {}
        mem = status.get("mem") or {}
        return SystemStats(
            users=UsersStats(totalUsers=len(users), statusCounts=counts),
            onlineStats=OnlineStats(
                onlineNow=online_now,
                lastDay=last_day,
                neverOnline=sum(1 for u in users if not u.online_at),
            ),
            nodes=NodesStats(totalOnline=online_now, totalBytesLifetime=lifetime),
            uptime=float(status.get("uptime") or 0),
            memory={"total": mem.get("total", 0), "used": mem.get("current", 0)},
        )
