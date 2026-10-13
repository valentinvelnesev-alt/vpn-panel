"""Reconcile clients removed outside the bot every five minutes."""

import asyncio
import logging

from app.config import load
from app.services.subscriptions import reconcile_deleted_clients
from shared.db.session import session

log = logging.getLogger("bot.workers.panel_sync")


async def run_once() -> int:
    async with session() as db:
        return await reconcile_deleted_clients(db, await load(db))


async def worker() -> None:
    while True:
        try:
            removed = await run_once()
            if removed:
                log.info("Убрано удалённых в панели ключей: %s", removed)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("Синхронизация панели не выполнена; локальные ключи сохранены")
        await asyncio.sleep(300)
