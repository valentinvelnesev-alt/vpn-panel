"""Бот поддержки целиком: настоящие обработчики aiogram поверх подменённого Telegram."""

from datetime import UTC, datetime

import pytest
from aiogram import Bot, Dispatcher
from aiogram.client.session.base import BaseSession
from aiogram.methods import (
    CopyMessage,
    CreateForumTopic,
    GetChatMember,
    SendMessage,
    TelegramMethod,
)
from aiogram.types import (
    Chat,
    ChatMemberAdministrator,
    ChatMemberMember,
    ChatMemberUpdated,
    ForumTopic,
    Message,
    MessageId,
    Update,
    User,
)
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.support import service
from app.support.runner import router
from app.support.service import SupportContext
from shared.db.base import Base
from shared.db.models import BotConfig, SupportTicket

CHAT = -1001234567890
BOT = User(id=999, is_bot=True, first_name="Поддержка", username="my_support_bot")
CLIENT = User(id=42, is_bot=False, first_name="Иван", username="ivan")
OPERATOR = User(id=500, is_bot=False, first_name="Оператор")


class FakeTelegram(BaseSession):
    """Вместо сети — запись вызовов и готовые ответы."""

    def __init__(self) -> None:
        super().__init__()
        self.calls: list[TelegramMethod] = []

    async def close(self) -> None:
        pass

    async def stream_content(self, *args, **kwargs):  # pragma: no cover
        yield b""

    async def make_request(self, bot, method, timeout=None):
        self.calls.append(method)
        if isinstance(method, CreateForumTopic):
            return ForumTopic(message_thread_id=101, name=method.name, icon_color=0)
        if isinstance(method, CopyMessage):
            return MessageId(message_id=1)
        if isinstance(method, GetChatMember):
            if method.user_id == OPERATOR.id:
                return ChatMemberAdministrator.model_construct(
                    status="administrator", user=OPERATOR, can_manage_topics=True
                )
            return ChatMemberMember.model_construct(status="member", user=CLIENT)
        if isinstance(method, SendMessage):
            return Message(
                message_id=2, date=datetime.now(UTC), chat=Chat(id=method.chat_id, type="private")
            )
        return True

    def sent(self, kind: type) -> list:
        return [c for c in self.calls if isinstance(c, kind)]


@pytest.fixture
async def env(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    import shared.db.session as shared_session

    monkeypatch.setattr(shared_session, "SessionLocal", maker)
    service._recent.clear()

    session = FakeTelegram()
    bot = Bot("123456:TESTTOKENTESTTOKENTESTTOKENTESTTOK", session=session)
    dispatcher = Dispatcher()
    dispatcher["ctx"] = SupportContext(chat_id=CHAT, bot_id=BOT.id, bot_username=BOT.username)
    router._parent_router = None
    dispatcher.include_router(router)
    yield bot, dispatcher, session, maker
    router._parent_router = None
    await engine.dispose()


def message(text, *, chat, user, thread=None, update_id=1) -> Update:
    return Update(
        update_id=update_id,
        message=Message(
            message_id=10,
            date=datetime.now(UTC),
            chat=chat,
            from_user=user,
            text=text,
            message_thread_id=thread,
            is_topic_message=True if thread else None,
        ),
    )


PRIVATE = Chat(id=CLIENT.id, type="private")
GROUP = Chat(id=CHAT, type="supergroup", title="Поддержка", is_forum=True)


async def test_client_message_creates_topic_and_client_gets_confirmation(env) -> None:
    bot, dispatcher, tg, _ = env
    await dispatcher.feed_update(bot, message("Не работает VPN", chat=PRIVATE, user=CLIENT))

    assert [t.name for t in tg.sent(CreateForumTopic)] == ["@ivan | ID 42"]
    copies = tg.sent(CopyMessage)
    assert [(c.chat_id, c.from_chat_id, c.message_thread_id) for c in copies] == [(CHAT, 42, 101)]
    to_client = [m for m in tg.sent(SendMessage) if m.chat_id == 42]
    assert len(to_client) == 1 and "Обращение создано" in to_client[0].text


async def test_start_greets_without_creating_a_topic(env) -> None:
    bot, dispatcher, tg, _ = env
    await dispatcher.feed_update(bot, message("/start", chat=PRIVATE, user=CLIENT))
    assert tg.sent(CreateForumTopic) == [] and tg.sent(CopyMessage) == []
    assert "оператор ответит" in tg.sent(SendMessage)[0].text


async def test_operator_reply_in_topic_reaches_client(env) -> None:
    bot, dispatcher, tg, _ = env
    await dispatcher.feed_update(bot, message("Помогите", chat=PRIVATE, user=CLIENT))
    tg.calls.clear()

    await dispatcher.feed_update(
        bot, message("Попробуйте перезапуск", chat=GROUP, user=OPERATOR, thread=101, update_id=2)
    )
    copies = tg.sent(CopyMessage)
    assert [(c.chat_id, c.from_chat_id) for c in copies] == [(42, CHAT)]


async def test_close_command_notifies_client(env) -> None:
    bot, dispatcher, tg, maker = env
    await dispatcher.feed_update(bot, message("Помогите", chat=PRIVATE, user=CLIENT))
    tg.calls.clear()
    await dispatcher.feed_update(
        bot, message("/close", chat=GROUP, user=OPERATOR, thread=101, update_id=2)
    )
    assert any("закрыто" in m.text and m.chat_id == 42 for m in tg.sent(SendMessage))
    async with maker() as db:
        from sqlalchemy import select

        assert (await db.scalar(select(SupportTicket.status))) == "closed"


async def test_messages_from_the_bot_itself_are_not_echoed(env) -> None:
    bot, dispatcher, tg, _ = env
    await dispatcher.feed_update(bot, message("Помогите", chat=PRIVATE, user=CLIENT))
    tg.calls.clear()
    await dispatcher.feed_update(
        bot, message("Новое обращение", chat=GROUP, user=BOT, thread=101, update_id=2)
    )
    assert tg.sent(CopyMessage) == []


async def test_id_command_reports_group_and_user_id(env) -> None:
    bot, dispatcher, tg, _ = env
    await dispatcher.feed_update(bot, message("/id", chat=GROUP, user=OPERATOR, update_id=3))
    text = tg.sent(SendMessage)[0].text
    assert str(CHAT) in text and str(OPERATOR.id) in text

    tg.calls.clear()
    await dispatcher.feed_update(bot, message("/id", chat=PRIVATE, user=CLIENT, update_id=4))
    text = tg.sent(SendMessage)[0].text
    assert "42" in text and "группы" not in text


async def test_adding_bot_to_group_announces_id_and_remembers_group(env) -> None:
    bot, dispatcher, tg, maker = env
    update = Update(
        update_id=5,
        my_chat_member=ChatMemberUpdated(
            chat=GROUP,
            from_user=OPERATOR,
            date=datetime.now(UTC),
            old_chat_member={"status": "left", "user": BOT.model_dump()},
            new_chat_member={"status": "member", "user": BOT.model_dump()},
        ),
    )
    async with maker() as db:
        db.add(BotConfig(id=1))
        await db.commit()

    await dispatcher.feed_update(bot, update)

    announce = tg.sent(SendMessage)[0]
    assert announce.chat_id == CHAT and str(CHAT) in announce.text
    async with maker() as db:
        row = await db.get(BotConfig, 1)
        assert (row.support_seen_chat_id, row.support_seen_chat_title) == (CHAT, "Поддержка")


async def test_messages_in_general_topic_are_ignored(env) -> None:
    bot, dispatcher, tg, _ = env
    await dispatcher.feed_update(bot, message("всем привет", chat=GROUP, user=OPERATOR))
    assert tg.calls == []
