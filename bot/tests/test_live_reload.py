"""Подмена конфига на ходу (Supervisor.reload) действительно меняет то, что
видит клиент: настоящий /start через настоящий диспетчер."""

from dataclasses import replace
from datetime import UTC, datetime

import pytest
from aiogram import Bot
from aiogram.client.session.base import BaseSession
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.methods import SendMessage
from aiogram.types import Chat, Message, Update, User
from shared.db.base import Base
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app import handlers
from tests.test_audit_fixes import make_config


class Recorder(BaseSession):
    def __init__(self) -> None:
        super().__init__()
        self.sent: list[SendMessage] = []

    async def close(self) -> None:
        pass

    async def stream_content(self, *args, **kwargs):  # pragma: no cover
        yield b""

    async def make_request(self, bot, method, timeout=None):
        if isinstance(method, SendMessage):
            self.sent.append(method)
            return Message(
                message_id=len(self.sent),
                date=datetime.now(UTC),
                chat=Chat(id=method.chat_id, type="private"),
            )
        return True


@pytest.fixture
async def env(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    import shared.db.session as shared_session

    monkeypatch.setattr(
        shared_session, "SessionLocal", async_sessionmaker(engine, expire_on_commit=False)
    )
    yield
    await engine.dispose()


async def start(dispatcher, bot, update_id):
    me = User(id=7, is_bot=False, first_name="Иван", username="ivan")
    await dispatcher.feed_update(
        bot,
        Update(
            update_id=update_id,
            message=Message(
                message_id=update_id,
                date=datetime.now(UTC),
                chat=Chat(id=7, type="private"),
                from_user=me,
                text="/start",
            ),
        ),
    )


def menu_labels(recorder) -> list[str]:
    markup = recorder.sent[-1].reply_markup
    return [b.text for row in markup.inline_keyboard for b in row]


async def test_new_config_is_used_by_the_next_start(env) -> None:
    recorder = Recorder()
    bot = Bot("123456:TESTTOKENTESTTOKENTESTTOKENTESTTOK", session=recorder)
    config = make_config()
    dispatcher = handlers.build_dispatcher(storage=MemoryStorage(), config=config)

    await start(dispatcher, bot, 1)
    assert "Промокод" in menu_labels(recorder)

    # То, что делает Supervisor.reload(): подмена конфига в диспетчере.
    dispatcher["config"] = replace(config, menu_labels={"promo": "Код скидки"})
    await start(dispatcher, bot, 2)
    labels = menu_labels(recorder)
    assert "Код скидки" in labels and "Промокод" not in labels


async def test_config_equality_ignores_load_time() -> None:
    a = make_config()
    b = replace(a, loaded_at=datetime.now())
    assert a == b  # иначе сверка с базой считала бы изменением каждое чтение
    assert a != replace(a, menu_labels={"promo": "x"})


async def test_watcher_applies_changes_whose_command_was_lost(env, monkeypatch) -> None:
    """Команда панели потерялась (обрыв Redis), а настройки в базе новые —
    бот должен применить их сам, без перезапуска."""
    from app import config as config_module
    from app.supervisor import Supervisor

    supervisor = Supervisor()
    old = make_config()
    new = replace(old, menu_labels={"promo": "Код скидки"})
    supervisor._last_seen = old
    reloads: list[int] = []

    async def fake_load(_db):
        return new

    async def fake_reload():
        reloads.append(1)
        supervisor._last_seen = new

    monkeypatch.setattr(config_module, "load", fake_load)
    monkeypatch.setattr(supervisor, "reload", fake_reload)
    monkeypatch.setattr("app.supervisor.CONFIG_WATCH_INTERVAL", 0)

    import asyncio

    task = asyncio.create_task(supervisor.watch_config())
    for _ in range(50):
        await asyncio.sleep(0.01)
        if reloads:
            break
    await asyncio.sleep(0.05)  # ещё несколько проходов: повторно не применяет
    task.cancel()
    assert reloads == [1]
