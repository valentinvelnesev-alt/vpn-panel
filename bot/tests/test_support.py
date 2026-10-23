"""Поддержка через бота: обращения, темы, ответы операторов, права."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.methods import CopyMessage
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.support import service
from app.support.service import SupportContext
from shared.db.base import Base
from shared.db.models import BotUser, SupportTicket, Wallet

CHAT = -1001234567890


@pytest.fixture
async def engine(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    import shared.db.session as shared_session

    monkeypatch.setattr(
        shared_session, "SessionLocal", async_sessionmaker(engine, expire_on_commit=False)
    )
    service._recent.clear()
    yield engine
    await engine.dispose()


@pytest.fixture
async def db(engine):
    async with async_sessionmaker(engine, expire_on_commit=False)() as s:
        yield s


class FakeBot:
    """Записывает вызовы и умеет «ломаться» по команде теста."""

    def __init__(self, *, admins: tuple[int, ...] = ()) -> None:
        self.calls: list[tuple[str, dict]] = []
        self.next_thread = 100
        self.admins = admins
        self.fail_copy_to_chat: Exception | None = None
        self.fail_copy_to_user: Exception | None = None

    async def create_forum_topic(self, chat_id, name):
        self.calls.append(("create_topic", {"chat_id": chat_id, "name": name}))
        self.next_thread += 1
        return SimpleNamespace(message_thread_id=self.next_thread)

    async def send_message(self, chat_id, text, **kwargs):
        self.calls.append(("send", {"chat_id": chat_id, "text": text, **kwargs}))

    async def copy_message(self, chat_id, from_chat_id, message_id, **kwargs):
        self.calls.append(
            ("copy", {"chat_id": chat_id, "from": from_chat_id, "id": message_id, **kwargs})
        )
        error = self.fail_copy_to_chat if chat_id == CHAT else self.fail_copy_to_user
        if error is not None:
            if chat_id == CHAT:
                self.fail_copy_to_chat = None  # ломаем один раз
            raise error

    async def close_forum_topic(self, chat_id, message_thread_id):
        self.calls.append(("close_topic", {"chat_id": chat_id, "thread": message_thread_id}))

    async def get_chat_member(self, chat_id, user_id):
        status = "administrator" if user_id in self.admins else "member"
        return SimpleNamespace(status=status)

    def of(self, kind: str) -> list[dict]:
        return [payload for name, payload in self.calls if name == kind]


def ctx(**kwargs) -> SupportContext:
    return SupportContext(chat_id=CHAT, bot_id=999, **kwargs)


def bad_request(text: str) -> TelegramBadRequest:
    return TelegramBadRequest(method=CopyMessage(chat_id=1, from_chat_id=1, message_id=1), message=text)


def forbidden() -> TelegramForbiddenError:
    return TelegramForbiddenError(
        method=CopyMessage(chat_id=1, from_chat_id=1, message_id=1), message="bot was blocked"
    )


async def client_says(bot, user_id=42, username="ivan", message_id=7, **kw):
    return await service.relay_from_client(
        bot, ctx(**kw), user_id=user_id, username=username, full_name="Иван", message_id=message_id
    )


# ── Клиент → тема ─────────────────────────────────────────────────────
async def test_first_message_creates_topic_with_card_and_copies_message(db) -> None:
    bot = FakeBot()
    assert await client_says(bot) == "new"

    assert bot.of("create_topic") == [{"chat_id": CHAT, "name": "@ivan | ID 42"}]
    card = bot.of("send")[0]
    assert card["message_thread_id"] == 101 and "Новое обращение" in card["text"]
    assert "<code>42</code>" in card["text"] and "В боте продаж не найден" in card["text"]
    assert bot.of("copy") == [
        {"chat_id": CHAT, "from": 42, "id": 7, "message_thread_id": 101}
    ]
    tickets = list(await db.scalars(select(SupportTicket)))
    assert [(t.user_telegram_id, t.thread_id, t.status) for t in tickets] == [(42, 101, "open")]


async def test_next_messages_go_to_same_topic(db) -> None:
    bot = FakeBot()
    await client_says(bot, message_id=1)
    assert await client_says(bot, message_id=2) == "ok"
    assert len(bot.of("create_topic")) == 1
    assert [c["message_thread_id"] for c in bot.of("copy")] == [101, 101]


async def test_card_shows_subscription_and_balance(db) -> None:
    user = BotUser(telegram_id=42, expire_at=datetime.now(UTC) + timedelta(days=10))
    db.add(user)
    await db.flush()
    db.add(Wallet(user_id=user.id, balance_kopeks=15050))
    await db.commit()

    bot = FakeBot()
    await client_says(bot)
    card = bot.of("send")[0]["text"]
    assert "Подписка: до " in card and "Баланс: 150.50 ₽" in card


async def test_deleted_topic_is_replaced_not_lost(db) -> None:
    bot = FakeBot()
    await client_says(bot, message_id=1)
    # Оператор удалил тему руками: следующая пересылка падает.
    bot.fail_copy_to_chat = bad_request("Bad Request: message thread not found")
    assert await client_says(bot, message_id=2) == "new"  # клиенту скажем про новое обращение

    assert len(bot.of("create_topic")) == 2  # завели новую тему
    assert bot.of("copy")[-1]["message_thread_id"] == 102
    statuses = sorted(t.status for t in await db.scalars(select(SupportTicket)))
    assert statuses == ["closed", "open"]


async def test_blocked_client_is_not_relayed(db) -> None:
    db.add(BotUser(telegram_id=42, is_blocked=True))
    await db.commit()
    bot = FakeBot()
    assert await client_says(bot) == "blocked"
    assert bot.calls == []


async def test_flood_is_limited(db) -> None:
    bot = FakeBot()
    results = [await client_says(bot, message_id=i) for i in range(service.FLOOD_LIMIT + 3)]
    assert results.count("flood") == 3
    assert "flood" not in results[: service.FLOOD_LIMIT]


async def test_topic_name_is_trimmed() -> None:
    assert service.topic_name(5, None, "Имя") == "Имя | ID 5"
    assert len(service.topic_name(5, "x" * 300, "")) == service.TOPIC_NAME_LIMIT


# ── Оператор → клиент ─────────────────────────────────────────────────
async def open_ticket(bot):
    await client_says(bot)
    bot.calls.clear()


async def operator_says(bot, text="Здравствуйте", operator_id=500, thread=101, **kw):
    return await service.relay_from_operator(
        bot, ctx(**kw), thread_id=thread, message_id=55, text=text, operator_id=operator_id, **{}
    )


async def test_group_admin_reply_goes_to_client(db) -> None:
    bot = FakeBot(admins=(500,))
    await open_ticket(bot)
    assert await operator_says(bot) == "ok"
    assert bot.of("copy") == [{"chat_id": 42, "from": CHAT, "id": 55}]


async def test_listed_operator_can_reply_without_being_group_admin(db) -> None:
    bot = FakeBot()
    await open_ticket(bot)
    assert await operator_says(bot, admin_ids={500}) == "ok"


async def test_stranger_in_group_is_ignored(db) -> None:
    bot = FakeBot()
    await open_ticket(bot)
    assert await operator_says(bot, operator_id=777) == "forbidden"
    assert bot.of("copy") == []


async def test_anonymous_admin_can_reply(db) -> None:
    bot = FakeBot()
    await open_ticket(bot)
    result = await service.relay_from_operator(
        bot, ctx(), thread_id=101, message_id=55, text="Привет", operator_id=None, anonymous=True
    )
    assert result == "ok"


async def test_unknown_topic_is_ignored(db) -> None:
    bot = FakeBot(admins=(500,))
    await open_ticket(bot)
    assert await operator_says(bot, thread=555) == "ignored"
    assert bot.of("copy") == []


async def test_close_closes_ticket_topic_and_notifies_client(db) -> None:
    bot = FakeBot(admins=(500,))
    await open_ticket(bot)
    assert await operator_says(bot, text="/close") == "closed"

    assert bot.of("close_topic") == [{"chat_id": CHAT, "thread": 101}]
    texts = {c["chat_id"]: c["text"] for c in bot.of("send")}
    assert "закрыто" in texts[42] and "закрыто" in texts[CHAT]
    assert (await db.scalar(select(SupportTicket.status))) == "closed"
    # Клиент написал снова — заведено новое обращение, а не залито в закрытое.
    service._recent.clear()
    assert await client_says(bot) == "new"
    assert len(bot.of("create_topic")) == 1
    # И в закрытую тему больше не отвечаем.
    assert await operator_says(bot, thread=101) == "ignored"


async def test_close_with_bot_suffix_works(db) -> None:
    bot = FakeBot(admins=(500,))
    await open_ticket(bot)
    assert await operator_says(bot, text="/close@my_support_bot") == "closed"


async def test_blocked_support_bot_is_reported_in_topic(db) -> None:
    bot = FakeBot(admins=(500,))
    await open_ticket(bot)
    bot.fail_copy_to_user = forbidden()
    assert await operator_says(bot) == "error"
    assert "заблокировал" in bot.of("send")[-1]["text"]


# ── Запуск и остановка ────────────────────────────────────────────────
def _config(**kwargs):
    base = dict(
        support_mode="bot", support_token="1:tok", support_chat_id=CHAT,
        support_admin_ids=[5], support_welcome=None, proxy=None,
    )
    base.update(kwargs)
    return SimpleNamespace(**base)


async def test_runner_stays_off_in_link_mode_or_without_setup(db) -> None:
    from app.support.runner import SupportRunner

    runner = SupportRunner()
    for config in (_config(support_mode="link"), _config(support_token=None), _config(support_chat_id=None)):
        await runner.apply(config)
        assert not runner.running


async def test_runner_reports_rejected_token(db, monkeypatch) -> None:
    from aiogram.exceptions import TelegramUnauthorizedError

    from app.support import runner as runner_module

    class RejectingBot:
        class session:  # noqa: N801
            @staticmethod
            async def close() -> None:
                pass

        async def get_me(self):
            raise TelegramUnauthorizedError(
                method=CopyMessage(chat_id=1, from_chat_id=1, message_id=1), message="Unauthorized"
            )

    states: list[tuple[str, str | None]] = []

    async def record(state, message=None):
        states.append((state, message))

    monkeypatch.setattr(runner_module.telegram_client, "make_bot", lambda token: RejectingBot())
    monkeypatch.setattr(runner_module, "_set_state", record)
    runner = runner_module.SupportRunner()
    await runner.apply(_config())
    assert not runner.running
    assert states[0][0] == "error" and "токен" in states[0][1]


async def test_welcome_text_is_escaped_and_default_exists() -> None:
    assert "оператор ответит" in service.DEFAULT_WELCOME
