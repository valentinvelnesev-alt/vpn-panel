"""Воркер рассылок: захват наступившей рассылки, отправка, живой прогресс."""

from datetime import UTC, datetime, timedelta

import pytest
from aiogram import Bot
from aiogram.exceptions import TelegramForbiddenError

from app.workers import broadcast as broadcast_worker
from shared.db.models import BotUser, Broadcast, BroadcastStatus


@pytest.fixture(autouse=True)
def _patch_session(monkeypatch, db_engine):
    """Подменяет SessionLocal бота на движок из фикстуры теста."""
    from sqlalchemy.ext.asyncio import async_sessionmaker

    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    monkeypatch.setattr(broadcast_worker, "SessionLocal", factory)

    import shared.db.session as shared_session

    monkeypatch.setattr(shared_session, "SessionLocal", factory)


@pytest.fixture
async def db_engine():
    from shared.db.base import Base
    from sqlalchemy.ext.asyncio import create_async_engine

    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()


@pytest.fixture
async def db(db_engine):
    from sqlalchemy.ext.asyncio import async_sessionmaker

    async with async_sessionmaker(db_engine, expire_on_commit=False)() as s:
        yield s


async def _make_broadcast(db, *, scheduled_at, segment="all", **extra) -> int:
    b = Broadcast(
        text="Привет!",
        segment=segment,
        scheduled_at=scheduled_at,
        created_at=datetime.now(UTC),
        **extra,
    )
    db.add(b)
    await db.commit()
    await db.refresh(b)
    return b.id


async def test_claim_picks_due_broadcast_only(db) -> None:
    now = datetime.now(UTC)
    future_id = await _make_broadcast(db, scheduled_at=now + timedelta(hours=1))
    due_id = await _make_broadcast(db, scheduled_at=now - timedelta(minutes=1))

    claimed = await broadcast_worker._claim_due_broadcast()
    assert claimed == due_id

    # Будущая рассылка не тронута, наступившая помечена как sending.
    await db.refresh(await db.get(Broadcast, future_id))
    due = await db.get(Broadcast, due_id)
    await db.refresh(due)
    assert due.status == BroadcastStatus.SENDING
    assert due.started_at is not None


async def test_claim_returns_none_when_nothing_due(db) -> None:
    await _make_broadcast(db, scheduled_at=datetime.now(UTC) + timedelta(hours=1))
    assert await broadcast_worker._claim_due_broadcast() is None


async def test_send_updates_progress_and_completes(db, monkeypatch) -> None:
    for tg_id in (1, 2, 3):
        db.add(BotUser(telegram_id=tg_id))
    await db.commit()

    broadcast_id = await _make_broadcast(db, scheduled_at=datetime.now(UTC))

    sent_to: list[int] = []

    async def fake_send_message(self, chat_id, text, **kwargs):
        if chat_id == 2:
            raise TelegramForbiddenError(method=None, message="bot was blocked")
        sent_to.append(chat_id)

    async def fake_close(self):
        return None

    monkeypatch.setattr(Bot, "send_message", fake_send_message)
    monkeypatch.setattr("aiogram.client.session.aiohttp.AiohttpSession.close", fake_close)

    await broadcast_worker._send(broadcast_id, "1:fake-token")

    updated = await db.get(Broadcast, broadcast_id)
    await db.refresh(updated)
    assert updated.status == BroadcastStatus.COMPLETED
    assert updated.total_recipients == 3
    assert updated.sent_count == 2
    assert updated.failed_count == 1
    assert sorted(sent_to) == [1, 3]

    blocked = (
        await db.execute(
            __import__("sqlalchemy").select(BotUser).where(BotUser.telegram_id == 2)
        )
    ).scalar_one()
    assert blocked.has_stopped_bot is True


async def test_send_with_buttons_and_photo(db, monkeypatch) -> None:
    db.add(BotUser(telegram_id=1))
    await db.commit()

    broadcast_id = await _make_broadcast(
        db,
        scheduled_at=datetime.now(UTC),
        photo_url="https://example.com/pic.jpg",
        buttons=[{"text": "Купить", "url": "https://example.com"}],
    )

    calls = []

    async def fake_send_photo(self, chat_id, photo, caption=None, reply_markup=None, **kw):
        calls.append((chat_id, photo, caption, reply_markup))

    async def fake_close(self):
        return None

    monkeypatch.setattr(Bot, "send_photo", fake_send_photo)
    monkeypatch.setattr("aiogram.client.session.aiohttp.AiohttpSession.close", fake_close)

    await broadcast_worker._send(broadcast_id, "1:fake-token")

    assert len(calls) == 1
    chat_id, photo, caption, markup = calls[0]
    assert photo == "https://example.com/pic.jpg"
    assert caption == "Привет!"
    assert markup.inline_keyboard[0][0].text == "Купить"


async def test_run_once_skips_without_token() -> None:
    assert await broadcast_worker.run_once(None) is False
