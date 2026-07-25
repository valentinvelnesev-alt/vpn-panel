"""Контейнер бота.

Процесс живёт всегда и слушает команды панели. Сам бот включается, когда в
панели указан токен и нажато «Запустить» — поэтому смена бота не требует
доступа к серверу.
"""

import asyncio
import logging
import os
import signal

from app.supervisor import Supervisor

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
)
log = logging.getLogger("bot")


async def main() -> None:
    supervisor = Supervisor()
    stopping = asyncio.Event()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stopping.set)

    serve = asyncio.create_task(supervisor.serve())
    log.info("Служба бота запущена, слушаю команды панели")

    await stopping.wait()
    log.info("Останавливаюсь…")

    serve.cancel()
    try:
        await serve
    except asyncio.CancelledError:
        pass
    await supervisor.stop()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
