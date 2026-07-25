"""Команды от панели к боту через Redis pub/sub.

Панель и бот — разные контейнеры, поэтому «Запустить», «Остановить» и смена
токена передаются сообщением, а не перезапуском процесса: пользователь
нажимает кнопку в UI и бот реагирует за секунду.

Канал один, сообщения — JSON вида {"command": "...", ...}.
"""

import json
import logging
import os
from collections.abc import AsyncIterator
from typing import Any

import redis.asyncio as redis

log = logging.getLogger("bus")

CHANNEL = "bot:commands"

CMD_START = "start"
CMD_STOP = "stop"
CMD_RELOAD = "reload"  # изменились тексты, тарифы или режим эмодзи

# Не команда жизненного цикла бота, а уведомление о событии: бэкенд
# подтвердил оплату у провайдера и просит бота её применить. Обрабатывается
# независимо от того, запущен ли сейчас polling (см. Supervisor.serve).
EVENT_PAYMENT_COMPLETED = "payment_completed"

# Панель создала рассылку с отправкой «сейчас» — ускоряет реакцию бота;
# без этого сообщения рассылка всё равно начнётся по расписанию воркера.
EVENT_BROADCAST_READY = "broadcast_ready"


def _client() -> redis.Redis:
    url = os.environ.get("REDIS_URL", "redis://redis:6379/0")
    return redis.from_url(url, decode_responses=True)


async def publish(command: str, **payload: Any) -> None:
    """Шлёт команду боту. Молчаливо переживает недоступность Redis:
    настройки уже сохранены в БД и подхватятся при следующем старте."""
    client = _client()
    try:
        await client.publish(CHANNEL, json.dumps({"command": command, **payload}))
    except Exception as exc:  # noqa: BLE001
        log.warning("Не удалось отправить команду %s боту: %s", command, exc)
    finally:
        await client.aclose()


async def listen() -> AsyncIterator[dict[str, Any]]:
    """Бесконечно отдаёт приходящие команды."""
    client = _client()
    pubsub = client.pubsub()
    await pubsub.subscribe(CHANNEL)
    try:
        async for message in pubsub.listen():
            if message.get("type") != "message":
                continue
            try:
                yield json.loads(message["data"])
            except (ValueError, TypeError):
                log.warning("Неразбираемая команда: %r", message.get("data"))
    finally:
        await pubsub.aclose()
        await client.aclose()
