"""Алерты о падении нод Remnawave (Pro).

Раз в минуту сверяем список нод; если нода была на связи и перестала —
шлём сообщение в чат из настроек. Состояние храним в памяти процесса —
рестарт бота максимум даст один лишний/пропущенный алерт, не критично.
"""

import asyncio
import logging

from app import config as config_module
from app.services.notify import send
from app.services.subscriptions import client_for
from shared.db.session import session
from shared.remnawave import RemnawaveError

log = logging.getLogger("bot.workers.node_alerts")

CHECK_INTERVAL = 60

_last_online: dict[str, bool] = {}


async def run_once() -> None:
    async with session() as db:
        config = await config_module.load(db)

    if not config.token:
        return

    from shared.db.models import BotConfig

    async with session() as db:
        row = await db.get(BotConfig, 1)
        if row is None or not row.node_alerts_enabled or not row.node_alerts_chat_id:
            return
        chat_id = row.node_alerts_chat_id

    try:
        client = client_for(config)
        try:
            nodes = await client.get_nodes()
        finally:
            await client.aclose()
    except RemnawaveError as exc:
        log.warning("Не удалось проверить ноды: %s", exc)
        return

    for node in nodes:
        online = node.is_online
        was_online = _last_online.get(node.uuid)
        _last_online[node.uuid] = online
        if was_online is True and online is False:
            await send(
                config.token,
                chat_id,
                f"⚠️ Нода <b>{node.name}</b> ({node.address}) недоступна.",
            )
        elif was_online is False and online is True:
            await send(
                config.token,
                chat_id,
                f"✅ Нода <b>{node.name}</b> ({node.address}) снова на связи.",
            )


async def worker() -> None:
    while True:
        try:
            await run_once()
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            log.exception("Сбой в воркере алертов нод")
        await asyncio.sleep(CHECK_INTERVAL)
