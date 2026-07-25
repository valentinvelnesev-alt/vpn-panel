"""Кэш в Redis для тяжёлых ответов Remnawave.

Дашборд опрашивается часто, а /api/system/stats и /api/nodes считаются на
стороне Remnawave не бесплатно. Короткий TTL держит цифры свежими, но
снимает нагрузку при нескольких открытых вкладках панели.
"""

import json
import logging
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

import redis.asyncio as redis

from app.core.config import settings

log = logging.getLogger("cache")

_redis: redis.Redis | None = None
T = TypeVar("T")


def get_redis() -> redis.Redis:
    global _redis
    if _redis is None:
        _redis = redis.from_url(settings.redis_url, decode_responses=True)
    return _redis


async def cached_json(
    key: str,
    ttl: int,
    producer: Callable[[], Awaitable[Any]],
) -> Any:
    """Отдаёт значение из кэша либо вычисляет и кладёт его туда.

    Недоступность Redis не должна ронять панель — при ошибке просто идём
    в Remnawave напрямую.
    """
    client = get_redis()
    try:
        hit = await client.get(key)
        if hit is not None:
            return json.loads(hit)
    except Exception as exc:  # noqa: BLE001
        log.warning("Redis недоступен при чтении %s: %s", key, exc)

    value = await producer()

    try:
        await client.set(key, json.dumps(value, default=str), ex=ttl)
    except Exception as exc:  # noqa: BLE001
        log.warning("Redis недоступен при записи %s: %s", key, exc)

    return value


async def invalidate(*keys: str) -> None:
    """Сбрасывает кэш после действий, меняющих состояние (рестарт ноды и т. п.)."""
    try:
        await get_redis().delete(*keys)
    except Exception as exc:  # noqa: BLE001
        log.warning("Не удалось сбросить кэш %s: %s", keys, exc)


async def close() -> None:
    global _redis
    if _redis is not None:
        await _redis.aclose()
        _redis = None
