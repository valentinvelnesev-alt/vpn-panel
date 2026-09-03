"""Запуск, остановка и перезапуск бота по командам из панели.

Контейнер живёт всегда; сам бот — задача внутри него. Панель шлёт команду
через Redis, супервизор поднимает или гасит polling. Поэтому смена токена
и правка цен не требуют `docker compose restart`.
"""

import asyncio
import logging
import os
from datetime import UTC, datetime

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramUnauthorizedError
from aiogram.fsm.storage.redis import RedisStorage

from app import config as config_module
from shared import bus
from shared.db.models import BotConfig, BotState
from shared.db.session import session

log = logging.getLogger("bot.supervisor")

# Через сколько пробовать снова, если старт или polling упали не из-за
# токена (сеть, Telegram 5xx). Без этого бот после перезагрузки сервера с
# медленной сетью висел бы в «ошибке» до ручного нажатия в панели.
RETRY_DELAYS = (15, 30, 60, 120, 300)


class Supervisor:
    def __init__(self) -> None:
        self._task: asyncio.Task | None = None
        self._workers: list[asyncio.Task] = []
        self._bot: Bot | None = None
        self._dispatcher: Dispatcher | None = None
        self._storage: RedisStorage | None = None
        self._config: config_module.Config | None = None
        self._lock = asyncio.Lock()
        self._retry_task: asyncio.Task | None = None
        self._failures = 0

    # ── Состояние в БД ────────────────────────────────────────────────
    async def _set_state(
        self, state: BotState, message: str | None = None, **fields: object
    ) -> None:
        """Панель показывает это состояние на вкладке «Бот»."""
        async with session() as db:
            row = await db.get(BotConfig, 1)
            if row is None:
                return
            row.state = state
            row.state_message = message
            if state is BotState.RUNNING:
                row.started_at = datetime.now(UTC)
            for key, value in fields.items():
                setattr(row, key, value)

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    # ── Жизненный цикл ────────────────────────────────────────────────
    async def start(self) -> None:
        async with self._lock:
            await self._start_locked()

    async def _start_locked(self) -> None:
        self._cancel_retry()
        await self._stop_locked()

        async with session() as db:
            config = await config_module.load(db)

        if not config.can_run:
            log.info("Бот выключен или без токена — жду команды из панели")
            await self._set_state(BotState.STOPPED)
            return

        bot = Bot(
            token=config.token,
            default=DefaultBotProperties(parse_mode=ParseMode.HTML),
        )
        try:
            me = await bot.get_me()
        except TelegramUnauthorizedError:
            await bot.session.close()
            log.error("Telegram отверг токен")
            await self._set_state(
                BotState.ERROR,
                "Telegram отверг токен. Проверьте его в @BotFather и вставьте заново.",
            )
            return
        except Exception as exc:  # noqa: BLE001
            await bot.session.close()
            log.exception("Не удалось запустить бота")
            await self._set_state(
                BotState.ERROR, f"Не удалось запустить: {exc}. Повторю попытку автоматически."
            )
            self._schedule_retry()
            return

        from app.handlers import build_dispatcher

        storage = RedisStorage.from_url(os.environ.get("REDIS_URL", "redis://redis:6379/0"))
        dispatcher = build_dispatcher(storage=storage, config=config)

        from app.workers.auto_renewal import worker as auto_renewal_worker
        from app.workers.broadcast import worker as broadcast_worker
        from app.workers.expiry import worker as expiry_worker
        from app.workers.node_alerts import worker as node_alerts_worker
        from app.workers.payment_polling import worker as payment_polling_worker
        from app.workers.payments import worker as payments_worker

        self._bot = bot
        self._dispatcher = dispatcher
        self._storage = storage
        self._config = config
        self._failures = 0
        self._task = asyncio.create_task(self._run(bot, dispatcher))
        self._workers = [
            asyncio.create_task(expiry_worker(bot)),
            asyncio.create_task(payments_worker()),
            asyncio.create_task(payment_polling_worker()),
            asyncio.create_task(auto_renewal_worker()),
            asyncio.create_task(broadcast_worker(config.token)),
            asyncio.create_task(node_alerts_worker()),
        ]

        await self._set_state(
            BotState.RUNNING,
            None,
            bot_username=me.username,
            bot_name=me.full_name,
            bot_id=me.id,
        )
        log.info("Бот @%s запущен", me.username)

    async def _run(self, bot: Bot, dispatcher: Dispatcher) -> None:
        try:
            # Копим только нужные типы обновлений и пропускаем накопленные
            # за время простоя: старые нажатия кнопок уже неактуальны.
            await dispatcher.start_polling(
                bot,
                handle_signals=False,
                allowed_updates=["message", "callback_query", "my_chat_member"],
                drop_pending_updates=True,
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            log.exception("Polling упал")
            await self._set_state(
                BotState.ERROR, f"Бот остановился: {exc}. Повторю попытку автоматически."
            )
            self._schedule_retry()

    def _schedule_retry(self) -> None:
        if self._retry_task is not None and not self._retry_task.done():
            return
        delay = RETRY_DELAYS[min(self._failures, len(RETRY_DELAYS) - 1)]
        self._failures += 1

        async def _retry() -> None:
            await asyncio.sleep(delay)
            log.info("Повторная попытка запуска бота")
            await self.start()

        self._retry_task = asyncio.create_task(_retry())

    def _cancel_retry(self) -> None:
        if self._retry_task is not None and not self._retry_task.done():
            if self._retry_task is not asyncio.current_task():
                self._retry_task.cancel()
        self._retry_task = None

    async def stop(self) -> None:
        async with self._lock:
            self._cancel_retry()
            self._failures = 0
            await self._stop_locked()
            await self._set_state(BotState.STOPPED)

    async def _stop_locked(self) -> None:
        for task in self._workers:
            task.cancel()
        if self._workers:
            await asyncio.gather(*self._workers, return_exceptions=True)
            self._workers = []
        if self._dispatcher is not None:
            await self._dispatcher.stop_polling()
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):  # noqa: B014
                pass
            self._task = None
        if self._bot is not None:
            await self._bot.session.close()
            self._bot = None
        if self._storage is not None:
            try:
                await self._storage.close()
            except Exception:  # noqa: BLE001
                pass
            self._storage = None
        self._dispatcher = None
        self._config = None

    async def reload(self) -> None:
        """Тарифы, тексты или платёжки изменились — перечитываем конфиг.

        Если токен не сменился и бот работает, конфиг подменяется «на лету»
        в контексте диспетчера: хендлеры получают его аргументом на каждый
        апдейт, а воркеры перечитывают из БД сами. Полный перезапуск
        поллинга оставлен только для смены токена или включения после
        остановки — он ронял бы нажатия пользователей за время рестарта и
        ловил «Conflict: terminated by other getUpdates request».
        """
        async with self._lock:
            async with session() as db:
                config = await config_module.load(db)

            if (
                self.running
                and self._dispatcher is not None
                and self._config is not None
                and config.can_run
                and config.token == self._config.token
            ):
                self._dispatcher["config"] = config
                self._config = config
                log.info("Конфиг обновлён без перезапуска")
                return

            await self._start_locked()

    # ── Приём команд ──────────────────────────────────────────────────
    async def serve(self) -> None:
        await self.start()  # вдруг бот уже включён в панели с прошлого раза
        # Догоняем платежи, подтверждённые, пока контейнер был выключен —
        # их pub-sub уведомления пропали вместе с недоступным подписчиком.
        await self._catch_up_payments()

        async for message in bus.listen():
            command = message.get("command")
            log.info("Команда из панели: %s", command)
            try:
                if command == bus.CMD_START:
                    await self.start()
                elif command == bus.CMD_STOP:
                    await self.stop()
                elif command == bus.CMD_RELOAD:
                    await self.reload()
                elif command == bus.EVENT_PAYMENT_COMPLETED:
                    # Обрабатывается независимо от self._task: доступ клиента
                    # к VPN не должен ждать, пока админ снова включит бота.
                    from app.services.payment_processor import handle

                    await handle(message["payment_id"])
                elif command == bus.EVENT_BROADCAST_READY:
                    # Ускоряет реакцию — без этого воркер подхватит рассылку
                    # сам в течение CHECK_INTERVAL секунд.
                    if self._config and self._config.token:
                        from app.workers.broadcast import run_once

                        while await run_once(self._config.token):
                            pass
            except Exception:  # noqa: BLE001
                log.exception("Ошибка при обработке команды %s", command)

    async def _catch_up_payments(self) -> None:
        from app.workers.payments import run_once

        try:
            await run_once()
        except Exception:  # noqa: BLE001
            log.exception("Не удалось дообработать отложенные оплаты при старте")
