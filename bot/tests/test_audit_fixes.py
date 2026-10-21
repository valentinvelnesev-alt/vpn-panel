"""Регрессии по аудиту: воркер догонки оплат, рефералка «только комиссия»,
лимит промокода, Stars pre-checkout, бан клиента."""

from datetime import UTC, datetime

import pytest
from aiogram.types import CallbackQuery, Chat, Message, PreCheckoutQuery, User
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app import handlers
from app.config import Config
from app.services import payment_processor
from app.services import promo as promo_service
from app.services import referral
from app.services import subscriptions as subs
from app.workers import payments as payments_worker
from shared.db.base import Base
from shared.db.models import (
    BotUser,
    EmojiMode,
    Payment,
    PaymentProvider,
    PaymentPurpose,
    PaymentStatus,
    PromoCode,
    PromoCodeActivation,
)


@pytest.fixture
async def engine(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    import shared.db.session as shared_session

    monkeypatch.setattr(
        shared_session, "SessionLocal", async_sessionmaker(engine, expire_on_commit=False)
    )
    yield engine
    await engine.dispose()


@pytest.fixture
async def db(engine):
    async with async_sessionmaker(engine, expire_on_commit=False)() as s:
        yield s


def make_config(**overrides) -> Config:
    values = dict(
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
        trial_squad_uuids=[],
        trial_hwid_limit=2,
    )
    values.update(overrides)
    return Config(**values)


# ── Воркер догонки оплат ──────────────────────────────────────────────
async def test_payment_worker_continues_after_failure(db, monkeypatch) -> None:
    user = BotUser(telegram_id=1)
    db.add(user)
    await db.flush()
    for n in range(3):
        db.add(
            Payment(
                user_id=user.id,
                provider=PaymentProvider.PLATEGA,
                external_id=f"tx-{n}",
                amount_kopeks=100,
                purpose=PaymentPurpose.TOPUP,
                status=PaymentStatus.PAID,
            )
        )
    await db.commit()

    handled: list[int] = []

    async def flaky_handle(payment_id: int) -> None:
        handled.append(payment_id)
        if len(handled) == 1:
            raise RuntimeError("Remnawave лежит")

    monkeypatch.setattr(payment_processor, "handle", flaky_handle)
    assert await payments_worker.run_once() == 3
    # Сбой на первом платеже не оставил без обработки остальные.
    assert len(handled) == 3


# ── Рефералка ─────────────────────────────────────────────────────────
async def test_commission_only_program_attaches_referrer(db) -> None:
    config = make_config(referral_enabled=False, referral_commission_enabled=True)
    referrer = await subs.get_or_create_user(db, 1)
    await db.flush()
    code = await referral.ensure_code(db, referrer)
    newcomer = await subs.get_or_create_user(db, 2)
    await db.flush()

    assert await referral.attach_referrer(db, config, newcomer, code) is not None
    assert newcomer.referred_by_id == referrer.id

    awarded = await referral.award_commission(db, config, newcomer, 10000)
    assert [(r.id, share) for r, share in awarded] == [(referrer.id, 2500)]


async def test_no_program_no_attach(db) -> None:
    config = make_config(referral_enabled=False, referral_commission_enabled=False)
    referrer = await subs.get_or_create_user(db, 1)
    await db.flush()
    code = await referral.ensure_code(db, referrer)
    newcomer = await subs.get_or_create_user(db, 2)
    await db.flush()
    assert await referral.attach_referrer(db, config, newcomer, code) is None


# ── Промокоды ─────────────────────────────────────────────────────────
async def test_promo_limit_holds_under_race(db) -> None:
    promo = PromoCode(code="ONE", bonus_days=1, max_uses=1)
    db.add(promo)
    second = await subs.get_or_create_user(db, 2)
    await db.flush()

    found = await promo_service.find(db, "one")
    # Пока второй проверял лимит, первый успел активировать код.
    await db.execute(
        update(PromoCode)
        .where(PromoCode.id == promo.id)
        .values(uses_count=1)
        .execution_options(synchronize_session=False)
    )
    with pytest.raises(promo_service.PromoError, match="исчерпан"):
        await promo_service.redeem(db, found, second)
    await db.refresh(promo)
    assert promo.uses_count == 1
    # Активация откатилась вместе с SAVEPOINT — код второму не засчитан.
    assert await db.scalar(select(func.count()).select_from(PromoCodeActivation)) == 0


async def test_promo_redeem_counts_use(db) -> None:
    promo = PromoCode(code="MANY", bonus_days=1, max_uses=5)
    db.add(promo)
    user = await subs.get_or_create_user(db, 1)
    await db.flush()
    await promo_service.redeem(db, promo, user)
    assert promo.uses_count == 1
    with pytest.raises(promo_service.PromoError, match="уже использовали"):
        await promo_service.redeem(db, promo, user)
    assert promo.uses_count == 1


# ── Stars ─────────────────────────────────────────────────────────────
class FakePreCheckout:
    def __init__(self, payload: str) -> None:
        self.invoice_payload = payload
        self.answers: list[tuple[bool, str | None]] = []

    async def answer(self, ok: bool, error_message: str | None = None) -> None:
        self.answers.append((ok, error_message))


async def _stars_payment(db, status_: PaymentStatus) -> int:
    user = BotUser(telegram_id=10)
    db.add(user)
    await db.flush()
    payment = Payment(
        user_id=user.id,
        provider=PaymentProvider.STARS,
        external_id=f"stars-{status_}",
        amount_kopeks=19900,
        purpose=PaymentPurpose.TOPUP,
        status=status_,
    )
    db.add(payment)
    await db.commit()
    return payment.id


async def test_pre_checkout_accepts_pending_invoice(db) -> None:
    query = FakePreCheckout(str(await _stars_payment(db, PaymentStatus.PENDING)))
    await handlers.on_pre_checkout(query)
    assert query.answers == [(True, None)]


async def test_pre_checkout_rejects_paid_or_missing_invoice(db) -> None:
    paid = FakePreCheckout(str(await _stars_payment(db, PaymentStatus.PAID)))
    await handlers.on_pre_checkout(paid)
    assert paid.answers[0][0] is False

    missing = FakePreCheckout("999999")
    await handlers.on_pre_checkout(missing)
    assert missing.answers[0][0] is False


# ── Бан клиента ───────────────────────────────────────────────────────
FROM = User(id=77, is_bot=False, first_name="Spam")


def _callback() -> CallbackQuery:
    return CallbackQuery(id="1", from_user=FROM, chat_instance="c", data="trial")


def _message(**extra) -> Message:
    return Message(
        message_id=1,
        date=datetime.now(UTC),
        chat=Chat(id=77, type="private"),
        from_user=FROM,
        text="/start",
        **extra,
    )


@pytest.fixture
def answers(monkeypatch) -> list[str]:
    sent: list[str] = []

    async def fake_answer(self, text=None, *args, **kwargs):
        sent.append(text or kwargs.get("error_message"))

    for cls in (CallbackQuery, Message, PreCheckoutQuery):
        monkeypatch.setattr(cls, "answer", fake_answer)
    return sent


async def _run(event, config: Config) -> bool:
    called = []

    async def handler(event, data):
        called.append(event)

    await handlers.block_banned(handler, event, {"config": config})
    return bool(called)


async def test_banned_user_is_stopped(db, answers) -> None:
    db.add(BotUser(telegram_id=77, is_blocked=True))
    await db.commit()

    assert await _run(_callback(), make_config()) is False
    assert await _run(_message(), make_config()) is False
    assert answers == [handlers.BANNED_TEXT, handlers.BANNED_TEXT]


async def test_regular_and_admin_users_pass(db, answers) -> None:
    assert await _run(_callback(), make_config()) is True  # ещё не в базе

    db.add(BotUser(telegram_id=77, is_blocked=True))
    await db.commit()
    assert await _run(_callback(), make_config(admin_telegram_ids=[77])) is True
    assert answers == []


# ── Улучшения: имя ключа по оплате, курс Stars, истёкшие счета ────────
def test_paid_key_username_is_stable_per_payment() -> None:
    assert subs._new_key_username(5, 42) == subs._new_key_username(5, 42) == "tg_5_p42"
    # Без оплаты (баланс, админ) — случайный суффикс, как раньше.
    assert subs._new_key_username(5) != subs._new_key_username(5)


def test_stars_rate_from_panel() -> None:
    from app.services import stars

    assert stars.rub_to_stars(199) == 111  # курс по умолчанию 1.8
    assert stars.rub_to_stars(199, 1.0) == 199
    assert stars.rub_to_stars(0.5, 2.0) == 1  # меньше звезды не бывает


async def test_stale_pending_payments_expire(db) -> None:
    from datetime import timedelta

    from app.workers import payment_polling

    user = BotUser(telegram_id=3)
    db.add(user)
    await db.flush()
    old = Payment(
        user_id=user.id,
        provider=PaymentProvider.PLATEGA,
        external_id="old",
        amount_kopeks=100,
        purpose=PaymentPurpose.TOPUP,
        created_at=datetime.now(UTC) - timedelta(days=2),
    )
    fresh = Payment(
        user_id=user.id,
        provider=PaymentProvider.PLATEGA,
        external_id="fresh",
        amount_kopeks=100,
        purpose=PaymentPurpose.TOPUP,
    )
    db.add_all([old, fresh])
    await db.commit()

    assert await payment_polling.expire_stale() == 1
    await db.refresh(old)
    await db.refresh(fresh)
    assert old.status == PaymentStatus.EXPIRED
    assert fresh.status == PaymentStatus.PENDING


async def test_late_payment_on_expired_invoice_still_applies(db, monkeypatch) -> None:
    from app.services import payment_check

    user = BotUser(telegram_id=4)
    db.add(user)
    await db.flush()
    payment = Payment(
        user_id=user.id,
        provider=PaymentProvider.PLATEGA,
        external_id="late",
        amount_kopeks=100,
        purpose=PaymentPurpose.TOPUP,
        status=PaymentStatus.EXPIRED,
    )
    db.add(payment)
    await db.commit()

    async def fake_load(_db):
        return make_config()

    async def paid(config, p) -> bool:
        return True

    handled: list[int] = []

    async def fake_handle(payment_id: int) -> None:
        handled.append(payment_id)

    monkeypatch.setattr("app.config.load", fake_load)
    monkeypatch.setattr(payment_check, "_is_paid_at_provider", paid)
    monkeypatch.setattr(payment_processor, "handle", fake_handle)

    assert await payment_check.check_and_apply(payment.id) is True
    await db.refresh(payment)
    assert payment.status == PaymentStatus.PAID
    assert handled == [payment.id]


# ── cisPay ────────────────────────────────────────────────────────────
async def test_cispay_invoice_created_with_customer_and_return_link(db, monkeypatch) -> None:
    from app.services import payment_flow
    from shared.payments import cispay

    sent: dict = {}

    async def fake_create(self, **kwargs):
        sent.update(kwargs)
        return {"id": "cis-tx-9", "payment_url": "https://pay.cispay.app/cis-tx-9", "status": "PENDING"}

    monkeypatch.setattr(cispay.CisPayClient, "create_payment", fake_create)
    config = make_config(
        cispay_enabled=True, cispay_shop_id="shop", cispay_api_key="key", bot_username="deal_bot"
    )
    assert config.cispay_ready and config.any_payment_ready
    user = await subs.get_or_create_user(db, 4242)
    await db.flush()

    payment, url = await payment_flow.create_external_payment(
        db,
        config,
        user,
        purpose=PaymentPurpose.TOPUP,
        amount_kopeks=15049,
        provider=PaymentProvider.CISPAY,
        description="Пополнение баланса",
    )
    assert payment.external_id == "cis-tx-9" and url.endswith("cis-tx-9")
    assert sent["amount_kopeks"] == 15049
    assert sent["customer_id"] == "4242"
    assert sent["order_id"] == f"vpn-{payment.id}"
    assert sent["return_url"] == "https://t.me/deal_bot"


def test_cispay_webhook_signature() -> None:
    import hashlib
    import hmac

    from shared.payments.cispay import CisPayClient, verify_signature

    body = b'{"id":"x","status":"PAID"}'
    good = hmac.new(b"key", body, hashlib.sha256).hexdigest()
    assert verify_signature("key", body, good)
    assert not verify_signature("key", body, "0" * 64)
    assert not verify_signature("key", body + b" ", good)
    assert CisPayClient.is_paid({"status": "PAID"}) and not CisPayClient.is_paid({"status": "PENDING"})
