"""Регрессионные тесты на исправления после аудита: гонка создания
пользователя, апгрейд триала при покупке, сводка по нескольким ключам,
скидка промокода, откат платежа-сироты, безопасный рендер текста."""

import asyncio
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app import texts
from app.config import Config, PlanView, discounted_kopeks
from app.services import payment_flow
from app.services import subscriptions as subs
from shared.db.base import Base
from shared.db.models import (
    BotSubscription,
    BotUser,
    EmojiMode,
    Payment,
    PaymentProvider,
    PaymentPurpose,
    Purchase,
)
from shared.sync import refresh_user_summary

REMOTE = {
    "id": 1,
    "username": "tg_777",
    "status": "ACTIVE",
    "trafficLimitBytes": 0,
    "trafficLimitStrategy": "NO_RESET",
    "subscriptionUrl": "https://sub.example/xyz",
    "activeInternalSquads": [],
}


@pytest.fixture
async def engine():
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()


@pytest.fixture
async def db(engine):
    async with async_sessionmaker(engine, expire_on_commit=False)() as s:
        yield s


@pytest.fixture
def remote(monkeypatch):
    """Поддельная Remnawave: запоминает вызовы, новым пользователям выдаёт
    новые id, PATCH возвращает тот же id, что пришёл."""
    calls: list[tuple[str, dict]] = []
    counter = {"id": 10}

    def handler(request: httpx.Request) -> httpx.Response:
        import json

        body = json.loads(request.content) if request.content else {}
        calls.append((f"{request.method} {request.url.path}", body))
        if request.method == "POST":
            counter["id"] += 1
            uid = counter["id"]
        else:
            uid = body.get("id", 1)
        return httpx.Response(
            200,
            json={
                "response": {
                    **REMOTE,
                    "id": uid,
                    "username": body.get("username", REMOTE["username"]),
                    "expireAt": body.get("expireAt"),
                }
            },
        )

    from shared.remnawave import client as rw

    original = rw.RemnawaveClient.__init__

    def patched(self, base_url, token, **kwargs):
        original(self, base_url, token, **kwargs)
        self._client = httpx.AsyncClient(
            base_url=base_url, transport=httpx.MockTransport(handler)
        )

    monkeypatch.setattr(rw.RemnawaveClient, "__init__", patched)
    return calls


PLAN = PlanView(
    id=1,
    title="Месяц",
    days=30,
    price_kopeks=39900,
    squad_uuids=["squad-main"],
    hwid_limit=3,
    traffic_limit_bytes=0,
    category_id=1,
    category_title="VPN",
)


@pytest.fixture
def config():
    return Config(
        token="1:x",
        enabled=True,
        emoji_mode=EmojiMode.PLAIN,
        premium_emoji={},
        brand="Test",
        welcome_text=None,
        support_url=None,
        channel_url=None,
        channel_id=None,
        require_channel_sub=False,
        trial_enabled=True,
        trial_days=3,
        trial_squad_uuids=["squad-trial"],
        trial_hwid_limit=2,
        plans=[PLAN],
        remnawave_url="https://rw.example",
        remnawave_token="t",
        platega_enabled=True,
        platega_merchant_id="m",
        platega_secret="s",
    )


# ── Гонка создания пользователя ────────────────────────────────────────
async def test_get_or_create_survives_concurrent_insert(engine) -> None:
    """Второй INSERT того же telegram_id упирается в уникальный индекс —
    раньше это был трейсбек и зависшая кнопка у пользователя."""
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as a, maker() as b:
        first = await subs.get_or_create_user(a, 777, username="a")
        await a.commit()
        # Вторая сессия «не видела» первого пользователя до своего select,
        # эмулируем: удаляем из identity map и вставляем напрямую.
        second = await subs.get_or_create_user(b, 777, username="b")
        await b.commit()
    assert first.id == second.id


async def test_get_or_create_integrity_error_falls_back_to_select(db, monkeypatch) -> None:
    from sqlalchemy.exc import IntegrityError

    existing = await subs.get_or_create_user(db, 777)
    await db.commit()

    # Первый select притворяется, что пользователя нет → INSERT → конфликт.
    real_scalar = db.scalar
    state = {"calls": 0}

    async def fake_scalar(stmt, *a, **kw):
        state["calls"] += 1
        if state["calls"] == 1:
            return None
        return await real_scalar(stmt, *a, **kw)

    monkeypatch.setattr(db, "scalar", fake_scalar)
    user = await subs.get_or_create_user(db, 777, username="retry")
    assert user.id == existing.id
    assert user.username == "retry"


# ── Триал → покупка ───────────────────────────────────────────────────
async def test_purchase_upgrades_trial_key_instead_of_second_account(db, config, remote) -> None:
    user = await subs.get_or_create_user(db, 777)
    user = await subs.grant_trial(db, config, user)
    await db.commit()
    trial_remote_id = int(user.remnawave_uuid)

    sub = await subs.create_subscription(db, config, user, PLAN, source="platega")
    await db.commit()

    # Тот же аккаунт Remnawave, PATCH, а не второй POST /api/users.
    assert sub.remnawave_id == trial_remote_id
    method, body = remote[-1]
    assert method == "PATCH /api/users"
    assert body["activeInternalSquads"] == ["squad-main"]
    assert body["hwidDeviceLimit"] == 3
    # Срок — полный срок тарифа от сегодня, а не +30 к триалу.
    expire = datetime.fromisoformat(body["expireAt"])
    assert 29 <= (expire - datetime.now(UTC)).days <= 30

    rows = list(await db.scalars(select(BotSubscription).where(BotSubscription.user_id == user.id)))
    assert len(rows) == 1
    assert rows[0].plan_id == PLAN.id
    # Сводка пользователя теперь по платному ключу.
    assert 29 <= (user.expire_at.replace(tzinfo=UTC) - datetime.now(UTC)).days <= 30


async def test_second_purchase_creates_separate_key(db, config, remote) -> None:
    user = await subs.get_or_create_user(db, 777)
    first = await subs.create_subscription(db, config, user, PLAN, source="wallet")
    second = await subs.create_subscription(db, config, user, PLAN, source="wallet")
    await db.commit()
    assert first.remnawave_id != second.remnawave_id
    assert remote[-1][0] == "POST /api/users"


async def test_user_summary_follows_latest_key(db, config, remote) -> None:
    """BotUser.expire_at — по ключу с самой поздней датой, а не по первому."""
    user = await subs.get_or_create_user(db, 777)
    await subs.grant_trial(db, config, user)  # 3 дня
    await db.commit()
    trial_expire = user.expire_at

    # Пользователь ещё не «апгрейдится» (триал + уже купленный ключ), чтобы
    # проверить сводку по двум ключам: добавляем платный ключ вручную.
    db.add(
        BotSubscription(
            user_id=user.id,
            remnawave_id=555,
            username="tg_777_abc",
            subscription_url="https://sub.example/paid",
            expire_at=datetime.now(UTC) + timedelta(days=60),
            plan_id=None,
        )
    )
    await db.flush()
    await refresh_user_summary(db, user)
    assert user.remnawave_uuid == "555"
    assert user.subscription_url == "https://sub.example/paid"
    assert user.expire_at != trial_expire
    assert subs.is_active(user)


# ── Скидка промокода ──────────────────────────────────────────────────
def test_discounted_kopeks() -> None:
    assert discounted_kopeks(39900, 0) == 39900
    assert discounted_kopeks(39900, 25) == 29925
    assert discounted_kopeks(39900, 100) == 100  # не ниже 1 ₽
    assert discounted_kopeks(39900, 150) == 100


async def test_extend_records_actual_paid_amount(db, config, remote) -> None:
    user = await subs.get_or_create_user(db, 777)
    sub = await subs.create_subscription(db, config, user, PLAN, source="wallet", amount_kopeks=29925)
    await db.commit()
    purchase = await db.scalar(select(Purchase).where(Purchase.subscription_id == sub.id))
    assert purchase.amount_kopeks == 29925


# ── Платёж-сирота ─────────────────────────────────────────────────────
async def test_external_payment_not_created_when_provider_unconfigured(db) -> None:
    user = await subs.get_or_create_user(db, 1)
    await db.flush()
    cfg = Config(
        token="1:x", enabled=True, emoji_mode=EmojiMode.PLAIN, premium_emoji={}, brand="T",
        welcome_text=None, support_url=None, channel_url=None, channel_id=None,
        require_channel_sub=False, trial_enabled=True, trial_days=3, trial_squad_uuids=[],
        trial_hwid_limit=2,
        platega_enabled=True,  # включена, но реквизитов нет — как на сервере
    )
    with pytest.raises(payment_flow.PaymentFlowError):
        await payment_flow.create_external_payment(
            db, cfg, user, purpose=PaymentPurpose.PLAN, amount_kopeks=39900,
            provider=PaymentProvider.PLATEGA, plan_id=1, description="x",
        )
    await db.commit()
    assert (await db.scalar(select(Payment))) is None
    assert not cfg.platega_ready and not cfg.any_payment_ready


async def test_external_payment_rolled_back_when_provider_fails(db, config, monkeypatch) -> None:
    from shared.payments import platega as platega_module

    async def broken(self, **kwargs):
        raise platega_module.PlategaError("Platega вернула 500")

    monkeypatch.setattr(platega_module.PlategaClient, "create_transaction", broken)
    user = await subs.get_or_create_user(db, 1)
    await db.flush()
    with pytest.raises(payment_flow.PaymentFlowError):
        await payment_flow.create_external_payment(
            db, config, user, purpose=PaymentPurpose.PLAN, amount_kopeks=39900,
            provider=PaymentProvider.PLATEGA, plan_id=1, description="x",
        )
    await db.commit()
    # Пользователь, созданный в той же сессии до платежа, остался.
    assert (await db.scalar(select(BotUser))) is not None
    assert (await db.scalar(select(Payment))) is None


# ── Рендер текста ─────────────────────────────────────────────────────
def test_render_tolerates_braces_in_admin_text() -> None:
    out = texts.render("{@shield} {brand} — цены {от 99 ₽}", EmojiMode.PLAIN, brand="X")
    assert out == "🛡 X — цены {от 99 ₽}"


# ── Автопродление ─────────────────────────────────────────────────────
async def test_auto_renewal_debits_and_extends_per_key(db, engine, config, remote, monkeypatch) -> None:
    from app import config as config_module
    from app.services import wallet
    from app.workers import auto_renewal
    from shared.db.models import Plan, PlanCategory, WalletTxType
    import shared.db.session as shared_session

    maker = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(shared_session, "SessionLocal", maker)
    monkeypatch.setattr(auto_renewal, "session", shared_session.session)

    async def fake_load(_db):
        return config

    monkeypatch.setattr(config_module, "load", fake_load)
    sent: list[tuple[int, str]] = []

    async def fake_send(token, chat_id, text, **kw):
        sent.append((chat_id, text))
        return True

    monkeypatch.setattr(auto_renewal, "send", fake_send)

    db.add(PlanCategory(id=1, title="VPN"))
    db.add(Plan(id=1, title="Месяц", days=30, price_kopeks=39900, squad_uuids=["squad-main"], hwid_limit=3, category_id=1))
    user = await subs.get_or_create_user(db, 777)
    await wallet.credit(db, user, 50000, WalletTxType.TOPUP)
    sub = await subs.create_subscription(db, config, user, PLAN, source="wallet")
    sub.expire_at = datetime.now(UTC) + timedelta(hours=5)
    sub.auto_renew = True
    await db.commit()

    renewed = await auto_renewal.run_once()
    assert renewed == 1
    assert sent and sent[0][0] == 777

    async with maker() as s:
        refreshed = await s.get(BotSubscription, sub.id)
        w = await wallet.get_or_create(s, await s.get(BotUser, user.id))
        assert w.balance_kopeks == 50000 - 39900
        assert (refreshed.expire_at.replace(tzinfo=UTC) - datetime.now(UTC)).days >= 29

    # Второй проход: срок уже далеко — ничего не списывается.
    assert await auto_renewal.run_once() == 0


# ── Применение оплаты ─────────────────────────────────────────────────
async def test_payment_processor_applies_plan_with_category(db, engine, config, remote, monkeypatch) -> None:
    """Сквозной путь: PAID-платёж → выдача ключа → applied_at → сообщение.
    Тариф с категорией — раньше здесь падал ленивый доступ к category."""
    from app import config as config_module
    from app.services import payment_processor
    from shared.db.models import PaymentStatus, Plan, PlanCategory
    import shared.db.session as shared_session

    maker = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(shared_session, "SessionLocal", maker)
    monkeypatch.setattr(payment_processor, "session", shared_session.session)

    real_load = config_module.load

    async def fake_load(_db):
        return config

    monkeypatch.setattr(config_module, "load", fake_load)
    sent: list[tuple[int, str]] = []

    async def fake_send(token, chat_id, text, **kw):
        sent.append((chat_id, text))
        return True

    monkeypatch.setattr(payment_processor, "send", fake_send)

    db.add(PlanCategory(id=1, title="VPN"))
    db.add(Plan(id=1, title="Месяц", days=30, price_kopeks=39900, squad_uuids=["squad-main"], hwid_limit=3, category_id=1))
    user = await subs.get_or_create_user(db, 777)
    user.discount_percent = 25
    payment = Payment(
        user_id=user.id,
        provider=PaymentProvider.STARS,
        external_id="stars-1",
        amount_kopeks=29925,
        purpose=PaymentPurpose.PLAN,
        plan_id=1,
        status=PaymentStatus.PAID,
        paid_at=datetime.now(UTC),
    )
    db.add(payment)
    await db.commit()

    await payment_processor.handle(payment.id)
    # Повтор (pub/sub доставил дважды) — ничего не делает.
    await payment_processor.handle(payment.id)

    async with maker() as s:
        p = await s.get(Payment, payment.id)
        assert p.applied_at is not None
        u = await s.get(BotUser, user.id)
        assert u.discount_percent == 0
        assert subs.is_active(u)
        sub = await s.scalar(select(BotSubscription).where(BotSubscription.user_id == user.id))
        assert sub is not None and sub.plan_id == 1
        purchase = await s.scalar(select(Purchase).where(Purchase.subscription_id == sub.id))
        assert purchase.amount_kopeks == 29925
    assert len(sent) == 1 and sent[0][0] == 777 and "Месяц" in sent[0][1]
    assert remote[-1][0] == "POST /api/users"
    monkeypatch.setattr(config_module, "load", real_load)
