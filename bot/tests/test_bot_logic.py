"""Логика бота: рендер эмодзи, выдача и продление подписок, напоминания."""

from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app import texts
from app.config import Config, PlanView
from app.services import subscriptions as subs
from shared.db.base import Base
from shared.db.models import BotUser, EmojiMode, ExpiryNotification, Purchase

REMOTE_USER = {
    "uuid": "rw-1",
    "username": "tg_777",
    "status": "ACTIVE",
    "trafficLimitBytes": 0,
    "trafficLimitStrategy": "NO_RESET",
    "subscriptionUrl": "https://sub.example/xyz",
    "activeInternalSquads": [],
}


@pytest.fixture
async def db():
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with async_sessionmaker(engine, expire_on_commit=False)() as s:
        yield s
    await engine.dispose()


@pytest.fixture
def config(monkeypatch):
    """Config с поддельной Remnawave: ответы в формате её API."""
    calls: list[tuple[str, dict]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        import json

        body = json.loads(request.content) if request.content else {}
        calls.append((f"{request.method} {request.url.path}", body))
        expire = body.get("expireAt")
        return httpx.Response(
            200, json={"response": {**REMOTE_USER, "expireAt": expire}}
        )

    from shared.remnawave import client as rw

    original = rw.RemnawaveClient.__init__

    def patched(self, base_url, token, **kwargs):
        original(self, base_url, token, **kwargs)
        self._client = httpx.AsyncClient(
            base_url=base_url, transport=httpx.MockTransport(handler)
        )

    monkeypatch.setattr(rw.RemnawaveClient, "__init__", patched)

    cfg = Config(
        token="1:x",
        enabled=True,
        emoji_mode=EmojiMode.PLAIN,
        premium_emoji={},
        brand="Test VPN",
        welcome_text=None,
        support_url=None,
        channel_url=None,
        channel_id=None,
        require_channel_sub=False,
        trial_enabled=True,
        trial_days=3,
        trial_squad_uuids=["squad-trial"],
        trial_hwid_limit=2,
        plans=[
            PlanView(
                id=1,
                title="Месяц",
                days=30,
                price_kopeks=19900,
                squad_uuids=["squad-main"],
                hwid_limit=5,
                traffic_limit_bytes=0,
            )
        ],
        remnawave_url="https://rw.example",
        remnawave_token="t",
    )
    # Config — frozen-датакласс, поэтому список вызовов отдаём рядом.
    return cfg, calls


# ── Эмодзи ────────────────────────────────────────────────────────────
def test_plain_mode_uses_unicode() -> None:
    out = texts.render("{@shield} Привет", EmojiMode.PLAIN)
    assert out == "🛡 Привет"


def test_premium_mode_wraps_configured_ids() -> None:
    out = texts.render(
        "{@shield} Привет", EmojiMode.PREMIUM, {"shield": "5237699328843200968"}
    )
    assert out == '<tg-emoji emoji-id="5237699328843200968">🛡</tg-emoji> Привет'


def test_premium_mode_without_map_falls_back_to_plain() -> None:
    """Карта пуста — рисуем обычные символы, а не битый тег."""
    assert texts.render("{@shield} Привет", EmojiMode.PREMIUM, {}) == "🛡 Привет"


def test_premium_mode_falls_back_per_icon() -> None:
    out = texts.render("{@shield}{@clock}", EmojiMode.PREMIUM, {"shield": "1"})
    assert out == '<tg-emoji emoji-id="1">🛡</tg-emoji>⏳'


def test_render_substitutes_values() -> None:
    out = texts.render("{@check} до {until}", EmojiMode.PLAIN, until="01.01.2027")
    assert out == "✅ до 01.01.2027"


# ── Пользователи ──────────────────────────────────────────────────────
async def test_get_or_create_is_idempotent(db) -> None:
    first = await subs.get_or_create_user(db, 777, username="ivan")
    await db.commit()
    second = await subs.get_or_create_user(db, 777, username="ivan_new")

    assert first.id == second.id
    # Профиль обновляется при каждом обращении.
    assert second.username == "ivan_new"


async def test_returning_user_unmarked_as_blocked(db) -> None:
    user = await subs.get_or_create_user(db, 777)
    user.has_stopped_bot = True
    await db.commit()

    again = await subs.get_or_create_user(db, 777)
    assert again.has_stopped_bot is False


# ── Выдача доступа ────────────────────────────────────────────────────
async def test_trial_creates_remote_user_and_marks_used(db, config) -> None:
    config, calls = config
    user = await subs.get_or_create_user(db, 777)
    user = await subs.grant_trial(db, config, user)
    await db.commit()

    assert user.trial_used is True
    assert user.remnawave_uuid == "rw-1"
    assert user.subscription_url == "https://sub.example/xyz"
    assert subs.is_active(user)

    method, body = calls[0]
    assert method == "POST /api/users"
    # Сквады и лимит устройств берутся из настроек, а не захардкожены.
    assert body["activeInternalSquads"] == ["squad-trial"]
    assert body["hwidDeviceLimit"] == 2
    assert body["telegramId"] == 777


async def test_purchase_extends_existing_subscription(db, config) -> None:
    """Продление считается от даты окончания, а не от «сегодня»."""
    config, calls = config
    user = await subs.get_or_create_user(db, 777)
    user.remnawave_uuid = "rw-1"
    user.expire_at = datetime.now(UTC) + timedelta(days=10)
    await db.flush()

    await subs.grant_plan(db, config, user, config.plans[0], source="manual")

    method, body = calls[-1]
    # Существующий пользователь обновляется, а не создаётся заново —
    # ссылка подписки у клиента остаётся прежней.
    assert method == "PATCH /api/users"
    expire = datetime.fromisoformat(body["expireAt"])
    assert 39 <= (expire - datetime.now(UTC)).days <= 40


async def test_expired_subscription_restarts_from_now(db, config) -> None:
    config, calls = config
    user = await subs.get_or_create_user(db, 777)
    user.remnawave_uuid = "rw-1"
    user.expire_at = datetime.now(UTC) - timedelta(days=100)
    await db.flush()

    await subs.grant_plan(db, config, user, config.plans[0], source="manual")

    _, body = calls[-1]
    expire = datetime.fromisoformat(body["expireAt"])
    assert 29 <= (expire - datetime.now(UTC)).days <= 30


async def test_purchase_is_recorded(db, config) -> None:
    config, _ = config
    user = await subs.get_or_create_user(db, 777)
    await subs.grant_plan(db, config, user, config.plans[0], source="payment")
    await db.commit()

    purchase = await db.get(Purchase, 1)
    assert purchase.days == 30
    assert purchase.amount_kopeks == 19900
    assert purchase.source == "payment"


def test_is_active_handles_naive_datetimes() -> None:
    """SQLite отдаёт даты без зоны — сравнение не должно падать."""
    user = BotUser(telegram_id=1)
    user.expire_at = (datetime.now(UTC) + timedelta(days=1)).replace(tzinfo=None)
    assert subs.is_active(user) is True

    user.expire_at = (datetime.now(UTC) - timedelta(days=1)).replace(tzinfo=None)
    assert subs.is_active(user) is False

    user.expire_at = None
    assert subs.is_active(user) is False


# ── Напоминания ───────────────────────────────────────────────────────
def test_expiry_windows() -> None:
    from app.workers.expiry import _window_for

    now = datetime.now(UTC)
    assert _window_for(now + timedelta(days=10), now) is None
    assert _window_for(now + timedelta(days=2), now) == "3d"
    assert _window_for(now + timedelta(hours=12), now) == "1d"
    assert _window_for(now - timedelta(days=1), now) == "expired"


async def test_expiry_notification_is_unique(db) -> None:
    """Повторная отметка того же окна не проходит — дублей не будет."""
    from sqlalchemy.exc import IntegrityError

    user = await subs.get_or_create_user(db, 777)
    await db.flush()
    expire = datetime.now(UTC) + timedelta(days=1)

    db.add(
        ExpiryNotification(
            user_id=user.id, window="1d", expire_at=expire, sent_at=datetime.now(UTC)
        )
    )
    await db.flush()

    db.add(
        ExpiryNotification(
            user_id=user.id, window="1d", expire_at=expire, sent_at=datetime.now(UTC)
        )
    )
    with pytest.raises(IntegrityError):
        await db.flush()
