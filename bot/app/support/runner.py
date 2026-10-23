"""Запуск бота поддержки внутри контейнера бота.

Отдельный токен и отдельный polling: сообщения клиентов поддержке никак не
пересекаются с ботом продаж. Жизненным циклом управляет Supervisor: включили
режим «через бота» в панели — запустили, поменяли токен или группу —
перезапустили, выключили — остановили.
"""

import asyncio
import html
import logging

from aiogram import Bot, Dispatcher, F, Router
from aiogram.exceptions import TelegramUnauthorizedError
from aiogram.filters import Command, CommandStart
from aiogram.types import ChatMemberUpdated, Message

from app import telegram_client
from app.support import service
from app.support.service import SupportContext
from shared.db.models import BotConfig
from shared.db.session import session

log = logging.getLogger("bot.support.runner")

RETRY_DELAYS = (15, 30, 60, 120, 300)
ALLOWED_UPDATES = ["message", "my_chat_member"]

router = Router()


# ── Узнать ID: группы и свой ──────────────────────────────────────────
@router.message(Command("id"))
async def cmd_id(message: Message) -> None:
    lines = []
    if message.chat.type != "private":
        lines.append(f"ID этой группы: <code>{message.chat.id}</code>")
    if message.from_user is not None:
        lines.append(f"Ваш ID: <code>{message.from_user.id}</code>")
    await message.answer("\n".join(lines))


@router.my_chat_member(F.chat.type.in_({"group", "supergroup"}))
async def on_added_to_group(event: ChatMemberUpdated, bot: Bot) -> None:
    """Бота добавили в группу — сразу пишем туда её ID и запоминаем группу:
    панель предложит её одной кнопкой, искать ID руками не придётся."""
    if event.new_chat_member.status not in {"member", "administrator", "restricted"}:
        return
    async with session() as db:
        row = await db.get(BotConfig, 1)
        if row is not None:
            row.support_seen_chat_id = event.chat.id
            row.support_seen_chat_title = (event.chat.title or "")[:255] or None
    try:
        await bot.send_message(
            event.chat.id,
            "👋 Я бот поддержки.\n"
            f"ID этой группы: <code>{event.chat.id}</code>\n\n"
            "В панели откройте Бот → Поддержка — группа уже предложена там одной кнопкой. "
            "Не забудьте сделать меня администратором с правом «Управление темами» "
            "и включить в группе «Темы».",
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("Не удалось написать в группу %s: %s", event.chat.id, exc)


# ── Клиент пишет боту ─────────────────────────────────────────────────
@router.message(CommandStart(), F.chat.type == "private")
async def cmd_start(message: Message, ctx: SupportContext) -> None:
    await message.answer(html.escape(ctx.welcome) if ctx.welcome else service.DEFAULT_WELCOME)


@router.message(F.chat.type == "private")
async def from_client(message: Message, bot: Bot, ctx: SupportContext) -> None:
    user = message.from_user
    if user is None or user.is_bot:
        return
    if message.content_type.value not in service.RELAYED_TYPES:
        await message.answer("Такой тип сообщений передать не получится — отправьте текст, фото или файл.")
        return
    result = await service.relay_from_client(
        bot,
        ctx,
        user_id=user.id,
        username=user.username,
        full_name=user.full_name,
        message_id=message.message_id,
    )
    if result == "new":
        await message.answer("✅ Обращение создано. Оператор ответит прямо здесь.")
    elif result == "blocked":
        await message.answer("Доступ ограничен.")
    elif result == "flood":
        await message.answer("Слишком много сообщений подряд — подождите минуту.")
    elif result == "error":
        await message.answer("Не получилось передать сообщение оператору. Попробуйте ещё раз чуть позже.")


# ── Оператор отвечает в теме ──────────────────────────────────────────
@router.message(F.chat.type.in_({"group", "supergroup"}), F.is_topic_message)
async def from_operator(message: Message, bot: Bot, ctx: SupportContext) -> None:
    if message.chat.id != ctx.chat_id or message.message_thread_id is None:
        return
    anonymous = bool(message.sender_chat and message.sender_chat.id == message.chat.id)
    sender = message.from_user
    if not anonymous and (sender is None or sender.is_bot):
        return  # сообщения самого бота и других ботов в клиента не уходят
    if message.content_type.value not in service.RELAYED_TYPES:
        return  # служебное: создана/закрыта тема, кто-то вошёл
    await service.relay_from_operator(
        bot,
        ctx,
        thread_id=message.message_thread_id,
        message_id=message.message_id,
        text=message.text or message.caption or "",
        operator_id=None if sender is None else sender.id,
        anonymous=anonymous,
    )


async def _set_state(state: str, message: str | None = None) -> None:
    try:
        async with session() as db:
            row = await db.get(BotConfig, 1)
            if row is not None:
                row.support_state = state
                row.support_state_message = message
    except Exception:  # noqa: BLE001
        log.exception("Не удалось записать состояние поддержки")


class SupportRunner:
    def __init__(self) -> None:
        self._task: asyncio.Task | None = None
        self._bot: Bot | None = None
        self._dispatcher: Dispatcher | None = None
        self._ctx: SupportContext | None = None
        self._signature: tuple | None = None

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    async def apply(self, config) -> None:
        """Приводит запущенное в соответствие с настройками из панели."""
        wanted = (
            config.support_mode == "bot"
            and bool(config.support_token)
            and bool(config.support_chat_id)
        )
        if not wanted:
            if self._task is not None:
                await self.stop()
                await _set_state("stopped", None)
            return

        signature = (config.support_token, config.support_chat_id)
        if self.running and signature == self._signature and self._ctx is not None:
            # Токен и группа те же — подхватываем остальное без перезапуска.
            self._ctx.admin_ids = set(config.support_admin_ids)
            self._ctx.welcome = config.support_welcome
            return

        await self.stop()
        telegram_client.set_proxy(config.proxy)
        bot = telegram_client.make_bot(config.support_token)
        try:
            me = await bot.get_me()
        except TelegramUnauthorizedError:
            await bot.session.close()
            await _set_state("error", "Telegram отверг токен бота поддержки — вставьте его заново.")
            return
        except Exception as exc:  # noqa: BLE001
            await bot.session.close()
            log.warning("Бот поддержки не запустился: %s", exc)
            await _set_state("error", f"Не удалось запустить: {exc}")
            return

        ctx = SupportContext(
            chat_id=config.support_chat_id,
            bot_id=me.id,
            bot_username=me.username,
            admin_ids=set(config.support_admin_ids),
            welcome=config.support_welcome,
        )
        dispatcher = Dispatcher()
        dispatcher["ctx"] = ctx
        router._parent_router = None  # aiogram не даёт прикрепить Router дважды
        dispatcher.include_router(router)

        self._bot, self._dispatcher, self._ctx, self._signature = bot, dispatcher, ctx, signature
        self._task = asyncio.create_task(self._run(bot, dispatcher))
        await _set_state("running", None)
        log.info("Бот поддержки @%s запущен", me.username)

    async def _run(self, bot: Bot, dispatcher: Dispatcher) -> None:
        failures = 0
        while True:
            try:
                await bot.delete_webhook(drop_pending_updates=False)
                await dispatcher.start_polling(
                    bot, handle_signals=False, allowed_updates=ALLOWED_UPDATES
                )
                return
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                log.exception("Polling бота поддержки упал")
                await _set_state("error", f"Бот поддержки остановился: {exc}. Повторю попытку.")
                await asyncio.sleep(RETRY_DELAYS[min(failures, len(RETRY_DELAYS) - 1)])
                failures += 1
                await _set_state("running", None)

    async def stop(self) -> None:
        if self._dispatcher is not None:
            try:
                await self._dispatcher.stop_polling()
            except Exception:  # noqa: BLE001
                pass
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):  # noqa: B014
                pass
        if self._bot is not None:
            await self._bot.session.close()
        self._task = self._bot = self._dispatcher = self._ctx = self._signature = None
