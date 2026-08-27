"""Асинхронный клиент Remnawave API.

Все ответы Remnawave завёрнуты в {"response": …} — метод `_request`
разворачивает это один раз, чтобы вызывающий код не думал об обёртке.

Изменения API >= 2.9:
  - User.uuid убран, первичный ключ теперь User.id (int)
  - /api/users/{userId} принимает числовой id
  - /api/users/by-telegram-id удалён → используем /api/users/stream?telegramId=
  - /api/users/by-email удалён → используем /api/users/stream?email=
  - bulk-операции принимают userIds (list[int]) вместо uuids (list[str])
  - PATCH /api/users принимает id (int), не uuid
"""

import logging
from datetime import datetime
from typing import Any

import httpx
from pydantic import TypeAdapter, ValidationError

from .models import Device, Node, SystemStats, User, UserPage

log = logging.getLogger("remnawave")

_users = TypeAdapter(list[User])
_nodes = TypeAdapter(list[Node])
_devices = TypeAdapter(list[Device])


class RemnawaveError(Exception):
    """Ошибка обращения к Remnawave, пригодная для показа в панели."""

    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class RemnawaveClient:
    """Тонкая обёртка над httpx.

    Клиент живёт столько же, сколько процесс: соединения переиспользуются.
    """

    def __init__(
        self,
        base_url: str,
        token: str,
        *,
        timeout: float = 15.0,
        verify_tls: bool = True,
    ) -> None:
        self._client = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
            timeout=timeout,
            verify=verify_tls,
            follow_redirects=True,
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> "RemnawaveClient":
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.aclose()

    # ── Транспорт ─────────────────────────────────────────────────────
    async def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        try:
            response = await self._client.request(method, path, **kwargs)
        except httpx.TimeoutException as exc:
            raise RemnawaveError("Remnawave не ответила вовремя") from exc
        except httpx.HTTPError as exc:
            raise RemnawaveError(f"Не удалось связаться с Remnawave: {exc}") from exc

        if response.status_code == 401:
            raise RemnawaveError(
                "Remnawave отклонила токен — проверьте его в настройках", 401
            )
        if response.status_code == 404:
            raise RemnawaveError("Объект не найден в Remnawave", 404)
        if response.status_code >= 400:
            detail = _extract_error(response)
            raise RemnawaveError(
                f"Remnawave вернула ошибку {response.status_code}: {detail}",
                response.status_code,
            )

        if not response.content:
            return None
        body = response.json()
        return body.get("response", body) if isinstance(body, dict) else body

    @staticmethod
    def _parse(model: Any, data: Any) -> Any:
        try:
            return (
                model.validate_python(data)
                if isinstance(model, TypeAdapter)
                else model.model_validate(data)
            )
        except ValidationError as exc:
            log.error("Remnawave вернула неожиданный ответ: %s", exc)
            raise RemnawaveError(
                "Remnawave вернула ответ в неожиданном формате — "
                "возможно, версия панели несовместима"
            ) from exc

    async def _get(self, path: str, **kwargs: Any) -> Any:
        return await self._request("GET", path, **kwargs)

    async def _post(self, path: str, **kwargs: Any) -> Any:
        return await self._request("POST", path, **kwargs)

    async def _patch(self, path: str, **kwargs: Any) -> Any:
        return await self._request("PATCH", path, **kwargs)

    # ── Проверка подключения ──────────────────────────────────────────
    async def check_connection(self) -> dict[str, Any]:
        return await self._get("/api/system/metadata") or {}

    async def get_stats(self) -> SystemStats:
        return self._parse(SystemStats, await self._get("/api/system/stats"))

    # ── Ноды ──────────────────────────────────────────────────────────
    async def get_nodes(self) -> list[Node]:
        return self._parse(_nodes, await self._get("/api/nodes") or [])

    async def get_node(self, uuid: str) -> Node:
        return self._parse(Node, await self._get(f"/api/nodes/{uuid}"))

    async def enable_node(self, uuid: str) -> None:
        await self._post(f"/api/nodes/{uuid}/actions/enable")

    async def disable_node(self, uuid: str) -> None:
        await self._post(f"/api/nodes/{uuid}/actions/disable")

    async def restart_node(self, uuid: str) -> None:
        await self._post(f"/api/nodes/{uuid}/actions/restart")

    async def restart_all_nodes(self) -> None:
        await self._post("/api/nodes/actions/restart-all")

    # ── Пользователи ──────────────────────────────────────────────────
    async def get_users(self, *, start: int = 0, size: int = 50) -> UserPage:
        data = await self._get("/api/users", params={"start": start, "size": size})
        return self._parse(UserPage, data)

    async def get_user(self, user_id: int) -> User:
        return self._parse(User, await self._get(f"/api/users/{user_id}"))

    async def get_users_by_telegram_id(self, telegram_id: int) -> list[User]:
        # /api/users/by-telegram-id удалён в новом API — используем stream
        data = await self._get(
            "/api/users/stream", params={"telegramId": str(telegram_id), "size": 100}
        )
        users = data.get("users", []) if isinstance(data, dict) else (data or [])
        return self._parse(_users, users)

    async def get_users_by_username(self, username: str) -> list[User]:
        data = await self._get(f"/api/users/by-username/{username}")
        return self._parse(_users, [data] if isinstance(data, dict) else data or [])

    async def get_users_by_email(self, email: str) -> list[User]:
        # /api/users/by-email удалён в новом API — используем stream
        data = await self._get(
            "/api/users/stream", params={"email": email, "size": 100}
        )
        users = data.get("users", []) if isinstance(data, dict) else (data or [])
        return self._parse(_users, users)

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
        payload: dict[str, Any] = {
            "username": username,
            "expireAt": expire_at.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
            "activeInternalSquads": internal_squad_uuids,
            "trafficLimitBytes": traffic_limit_bytes,
            "status": "ACTIVE",
        }
        if telegram_id is not None:
            payload["telegramId"] = telegram_id
        if email:
            payload["email"] = email
        if hwid_device_limit is not None:
            payload["hwidDeviceLimit"] = hwid_device_limit
        if description:
            payload["description"] = description
        if tag:
            payload["tag"] = tag

        return self._parse(User, await self._post("/api/users", json=payload))

    async def update_user(self, user_id: int, **fields: Any) -> User:
        """Частичное обновление. user_id — целочисленный id пользователя."""
        payload = {"id": user_id, **fields}
        return self._parse(User, await self._patch("/api/users", json=payload))

    async def extend_expiration(self, user_id: int, new_expire_at: datetime) -> User:
        return await self.update_user(
            user_id, expireAt=new_expire_at.strftime("%Y-%m-%dT%H:%M:%S.000Z")
        )

    async def set_status(self, user_id: int, status: str) -> User:
        return await self.update_user(user_id, status=status)

    # ── Массовые операции ─────────────────────────────────────────────
    async def bulk_extend_expiration(self, user_ids: list[int], days: int) -> None:
        await self._post(
            "/api/users/bulk/extend-expiration-date",
            json={"userIds": user_ids, "extendDays": days},
        )

    async def bulk_reset_traffic(self, user_ids: list[int]) -> None:
        await self._post("/api/users/bulk/reset-traffic", json={"userIds": user_ids})

    async def bulk_update_squads(
        self, user_ids: list[int], internal_squad_uuids: list[str]
    ) -> None:
        await self._post(
            "/api/users/bulk/update-squads",
            json={"userIds": user_ids, "activeInternalSquads": internal_squad_uuids},
        )

    async def bulk_delete(self, user_ids: list[int]) -> None:
        await self._post("/api/users/bulk/delete", json={"userIds": user_ids})

    # ── Сквады ────────────────────────────────────────────────────────
    async def get_internal_squads(self) -> list[dict[str, Any]]:
        data = await self._get("/api/internal-squads")
        if isinstance(data, dict):
            data = data.get("internalSquads", [])
        return data or []

    # ── Устройства (HWID) ─────────────────────────────────────────────
    async def get_devices(self, user_id: int) -> list[Device]:
        data = await self._get(f"/api/hwid/devices/{user_id}")
        items = data.get("devices", []) if isinstance(data, dict) else (data or [])
        return self._parse(_devices, items)

    async def delete_device(self, user_id: int, hwid: str) -> None:
        await self._post(
            "/api/hwid/devices/delete", json={"userId": user_id, "hwid": hwid}
        )

    async def delete_all_devices(self, user_id: int) -> None:
        await self._post("/api/hwid/devices/delete-all", json={"userId": user_id})


def _extract_error(response: httpx.Response) -> str:
    try:
        body = response.json()
    except ValueError:
        return response.text[:200] or "без описания"
    if isinstance(body, dict):
        return str(body.get("message") or body.get("error") or body)[:200]
    return str(body)
