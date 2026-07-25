"""Асинхронный клиент Remnawave API 2.8.1.

Все ответы Remnawave завёрнуты в {"response": …} — метод `_get`/`_post`
разворачивает это один раз, чтобы вызывающий код не думал об обёртке.
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
        # Ответы обёрнуты в {"response": …}; у пустых 204 обёртки нет.
        return body.get("response", body) if isinstance(body, dict) else body

    @staticmethod
    def _parse(model: Any, data: Any) -> Any:
        """Разбирает ответ Remnawave, превращая расхождение со схемой в
        RemnawaveError.

        Иначе ValidationError улетает наружу как 500 «внутренняя ошибка
        панели», хотя виноват ответ внешнего сервиса, — а по 502 сразу
        понятно, где искать причину.
        """
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
        """Дёргается кнопкой «Проверить подключение» в настройках панели."""
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

    async def get_user(self, uuid: str) -> User:
        return self._parse(User, await self._get(f"/api/users/{uuid}"))

    async def get_users_by_telegram_id(self, telegram_id: int) -> list[User]:
        return self._parse(
            _users, await self._get(f"/api/users/by-telegram-id/{telegram_id}") or []
        )

    async def get_users_by_username(self, username: str) -> list[User]:
        # Эндпоинт отдаёт одного пользователя — приводим к списку, чтобы
        # поиск в панели обрабатывал все варианты одинаково.
        data = await self._get(f"/api/users/by-username/{username}")
        return self._parse(_users, [data] if isinstance(data, dict) else data or [])

    async def get_users_by_email(self, email: str) -> list[User]:
        return self._parse(_users, await self._get(f"/api/users/by-email/{email}") or [])

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
            "expireAt": expire_at.isoformat(),
            "activeInternalSquads": internal_squad_uuids,
            "trafficLimitBytes": traffic_limit_bytes,
            "status": "ACTIVE",
        }
        # Лимит устройств и сквады приходят из настроек тарифа в панели,
        # а не зашиты в коде, как было в старом боте.
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

    async def update_user(self, uuid: str, **fields: Any) -> User:
        """Частичное обновление. Ключи — как в API: expireAt, status и т. д."""
        payload = {"uuid": uuid, **fields}
        return self._parse(User, await self._patch("/api/users", json=payload))

    async def extend_expiration(self, uuid: str, new_expire_at: datetime) -> User:
        return await self.update_user(uuid, expireAt=new_expire_at.isoformat())

    async def set_status(self, uuid: str, status: str) -> User:
        return await self.update_user(uuid, status=status)

    # ── Массовые операции ─────────────────────────────────────────────
    async def bulk_extend_expiration(self, uuids: list[str], days: int) -> None:
        await self._post(
            "/api/users/bulk/extend-expiration-date",
            json={"uuids": uuids, "days": days},
        )

    async def bulk_reset_traffic(self, uuids: list[str]) -> None:
        await self._post("/api/users/bulk/reset-traffic", json={"uuids": uuids})

    async def bulk_update_squads(
        self, uuids: list[str], internal_squad_uuids: list[str]
    ) -> None:
        await self._post(
            "/api/users/bulk/update-squads",
            json={"uuids": uuids, "activeInternalSquads": internal_squad_uuids},
        )

    async def bulk_delete(self, uuids: list[str]) -> None:
        await self._post("/api/users/bulk/delete", json={"uuids": uuids})

    # ── Сквады ────────────────────────────────────────────────────────
    async def get_internal_squads(self) -> list[dict[str, Any]]:
        """Список сквадов для выпадающего списка в настройках тарифа."""
        data = await self._get("/api/internal-squads")
        if isinstance(data, dict):
            data = data.get("internalSquads", [])
        return data or []

    # ── Устройства (HWID) ─────────────────────────────────────────────
    async def get_devices(self, user_uuid: str) -> list[Device]:
        data = await self._get(f"/api/hwid/devices/{user_uuid}")
        items = data.get("devices", []) if isinstance(data, dict) else (data or [])
        return self._parse(_devices, items)

    async def delete_device(self, user_uuid: str, hwid: str) -> None:
        await self._post(
            "/api/hwid/devices/delete", json={"userUuid": user_uuid, "hwid": hwid}
        )

    async def delete_all_devices(self, user_uuid: str) -> None:
        await self._post("/api/hwid/devices/delete-all", json={"userUuid": user_uuid})


def _extract_error(response: httpx.Response) -> str:
    try:
        body = response.json()
    except ValueError:
        return response.text[:200] or "без описания"
    if isinstance(body, dict):
        return str(body.get("message") or body.get("error") or body)[:200]
    return str(body)[:200]
