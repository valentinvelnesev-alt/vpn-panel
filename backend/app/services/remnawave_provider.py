"""Выдаёт готовый клиент Remnawave по настройкам из БД.

Клиент кэшируется по (url, токен): пересоздавать httpx-соединения на каждый
запрос панели дорого, а настройки меняются редко. Смена настроек в UI
автоматически даёт новый клиент, потому что меняется ключ кэша.
"""

import asyncio

from fastapi import Depends, HTTPException, status
from typing import Annotated

from app.api.deps import DbSession
from app.services import settings_service as cfg
from shared.remnawave import RemnawaveClient

_clients: dict[tuple[str, str, bool], RemnawaveClient] = {}
_lock = asyncio.Lock()


async def get_client(db: DbSession) -> RemnawaveClient:
    values = await cfg.get_many(
        db, cfg.REMNAWAVE_URL, cfg.REMNAWAVE_TOKEN, cfg.REMNAWAVE_VERIFY_TLS
    )
    url = values[cfg.REMNAWAVE_URL]
    token = values[cfg.REMNAWAVE_TOKEN]

    if not url or not token:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Remnawave не подключена — укажите адрес и токен в настройках",
        )

    verify_tls = values[cfg.REMNAWAVE_VERIFY_TLS] != "false"
    key = (url, token, verify_tls)

    async with _lock:
        client = _clients.get(key)
        if client is None:
            # Настройки сменились — старые клиенты больше не нужны.
            for stale in _clients.values():
                await stale.aclose()
            _clients.clear()
            client = RemnawaveClient(url, token, verify_tls=verify_tls)
            _clients[key] = client
    return client


async def close_all() -> None:
    async with _lock:
        for client in _clients.values():
            await client.aclose()
        _clients.clear()


Remnawave = Annotated[RemnawaveClient, Depends(get_client)]
