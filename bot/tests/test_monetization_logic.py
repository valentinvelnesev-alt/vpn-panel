"""Кошелёк, промокоды, рефералка, платёжный флоу — логика бота."""

from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config import Config, PlanView
from app.services import payment_flow, promo as promo_service, referral, wallet
from app.services import subscriptions as subs
from shared.db.base import Base
from shared.db.models import (
    BotUser,
    EmojiMode,
    PaymentProvider,
    PaymentPurpose,
    PromoCode,
    WalletTxType,
)

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
    def handler(request: httpx.Request) -> httpx.Response:
        import json as _json

        body = _json.loads(request.content) if request.content else {}
        expire = body.get("expireAt")
        return httpx.Response(200, json={"response": {**REMOTE_USER, "expireAt": expire}})

    from shared.remnawave import client as rw

    original = rw.RemnawaveClient.__init__

    def patched(self, base_url, token, **kwargs):
        original(self, base_url, token, **kwargs)
        self._client = httpx.AsyncClient(
            base_url=base_url, transport=httpx.MockTransport(handler)
        )

    monkeypatch.setattr(rw.RemnawaveClient, "__init__", patched)

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
        referral_enabled=True,
        referral_reward_days=5,
        referral_bonus_days=2,
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
        platega_enabled=True,
        platega_merchant_id="m-1",
        platega_secret="sec",
        cryptobot_enabled=True,
        cryptobot_token="tok",
    )


# ── Кошелёк ───────────────────────────────────────────────────────────
async def test_wallet_credit_and_debit(db) -> None:
    user = await subs.get_or_create_user(db, 1)
    await db.flush()

    w = await wallet.credit(db, user, 10000, WalletTxType.TOPUP)
    assert w.balance_kopeks == 10000

    w = await wallet.debit(db, user, 4000, WalletTxType.PURCHASE)
    assert w.balance_kopeks == 6000


async def test_wallet_debit_insufficient(db) -> None:
    user = await subs.get_or_create_user(db, 1)
    await db.flush()
    with pytest.raises(wallet.InsufficientFunds):
        await wallet.debit(db, user, 100, WalletTxType.PURCHASE)


async def test_wallet_rejects_non_positive_amounts(db) -> None:
    user = await subs.get_or_create_user(db, 1)
    await db.flush()
    with pytest.raises(ValueError):
        await wallet.credit(db, user, 0, WalletTxType.TOPUP)
    with pytest.raises(ValueError):
        await wallet.debit(db, user, -5, WalletTxType.PURCHASE)


# ── Промокоды ─────────────────────────────────────────────────────────
async def test_promo_redeem_once(db) -> None:
    user = await subs.get_or_create_user(db, 1)
    promo = PromoCode(code="WELCOME", bonus_days=5)
    db.add(promo)
    await db.flush()

    found = await promo_service.find(db, "welcome")  # регистр не важен
    await promo_service.redeem(db, found, user)
    assert found.uses_count == 1

    with pytest.raises(promo_service.PromoError, match="уже использовали"):
        await promo_service.redeem(db, found, user)


async def test_promo_not_found(db) -> None:
    with pytest.raises(promo_service.PromoError, match="не найден"):
        await promo_service.find(db, "GHOST")


async def test_promo_expired(db) -> None:
    db.add(
        PromoCode(
            code="OLD", bonus_days=1, expires_at=datetime.now(UTC) - timedelta(days=1)
        )
    )
    await db.flush()
    with pytest.raises(promo_service.PromoError, match="истёк"):
        await promo_service.find(db, "OLD")


async def test_promo_exhausted(db) -> None:
    db.add(PromoCode(code="LIMITED", bonus_days=1, max_uses=1, uses_count=1))
    await db.flush()
    with pytest.raises(promo_service.PromoError, match="исчерпан"):
        await promo_service.find(db, "LIMITED")


async def test_promo_survives_activation_race_without_losing_earlier_changes(db) -> None:
    """redeem использует SAVEPOINT — конфликт не должен откатить весь db."""
    user = await subs.get_or_create_user(db, 1, username="ivan")
    promo = PromoCode(code="ONE", bonus_days=1)
    db.add(promo)
    await db.flush()

    await promo_service.redeem(db, promo, user)
    with pytest.raises(promo_service.PromoError):
        await promo_service.redeem(db, promo, user)

    # user всё ещё в сессии и не откачен несмотря на ошибку redeem.
    assert user.username == "ivan"
    assert user in db.new or user.id is not None


# ── Рефералы ──────────────────────────────────────────────────────────
async def test_referral_code_is_stable_and_unique(db) -> None:
    user = await subs.get_or_create_user(db, 1)
    await db.flush()
    code1 = await referral.ensure_code(db, user)
    code2 = await referral.ensure_code(db, user)
    assert code1 == code2
    assert len(code1) == 6


async def test_attach_referrer_only_for_new_users(db, config) -> None:
    referrer = await subs.get_or_create_user(db, 1)
    await db.flush()
    code = await referral.ensure_code(db, referrer)

    newcomer = await subs.get_or_create_user(db, 2)
    await db.flush()
    result = await referral.attach_referrer(db, config, newcomer, code)
    assert result is not None
    assert newcomer.referred_by_id == referrer.id

    # Уже не новый (после триала) — повторно реферера не сменить.
    newcomer.trial_used = True
    other = await subs.get_or_create_user(db, 3)
    await db.flush()
    other_code = await referral.ensure_code(db, other)
    result2 = await referral.attach_referrer(db, config, newcomer, other_code)
    assert result2 is None
    assert newcomer.referred_by_id == referrer.id


async def test_attach_referrer_rejects_self(db, config) -> None:
    user = await subs.get_or_create_user(db, 1)
    await db.flush()
    code = await referral.ensure_code(db, user)
    result = await referral.attach_referrer(db, config, user, code)
    assert result is None


async def test_referral_reward_only_once(db, config) -> None:
    referrer = await subs.get_or_create_user(db, 1)
    newcomer = await subs.get_or_create_user(db, 2)
    await db.flush()
    newcomer.referred_by_id = referrer.id

    reward1 = await referral.reward_if_first_purchase(db, config, newcomer)
    assert reward1 is not None
    assert reward1.days == config.referral_reward_days
    assert newcomer.referral_reward_paid is True

    reward2 = await referral.reward_if_first_purchase(db, config, newcomer)
    assert reward2 is None


async def test_apply_referral_reward_grants_days_to_referrer(db, config) -> None:
    referrer = await subs.get_or_create_user(db, 1)
    newcomer = await subs.get_or_create_user(db, 2)
    await db.flush()
    newcomer.referred_by_id = referrer.id

    result = await subs.apply_referral_reward(db, config, newcomer)
    assert result is not None
    updated_referrer, days = result
    assert days == config.referral_reward_days
    assert updated_referrer.remnawave_uuid == "rw-1"  # аккаунт создан бонусом
    assert subs.is_active(updated_referrer)


async def test_trial_gets_referral_bonus_days(db, config) -> None:
    referrer = await subs.get_or_create_user(db, 1)
    newcomer = await subs.get_or_create_user(db, 2)
    await db.flush()
    newcomer.referred_by_id = referrer.id

    newcomer = await subs.grant_trial(db, config, newcomer)
    # trial_days=3 + referral_bonus_days=2 = 5 суток.
    left = (newcomer.expire_at - datetime.now(UTC)).days
    assert left in (4, 5)


async def test_trial_without_referral_has_no_bonus(db, config) -> None:
    user = await subs.get_or_create_user(db, 1)
    await db.flush()
    user = await subs.grant_trial(db, config, user)
    left = (user.expire_at - datetime.now(UTC)).days
    assert left in (2, 3)


# ── Бонусные дни не трогают сквады существующего клиента ───────────────
async def test_grant_bonus_days_preserves_existing_squads(db, config, monkeypatch) -> None:
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        import json as _json

        calls.append((request.method, request.url.path))
        body = _json.loads(request.content) if request.content else {}
        return httpx.Response(
            200, json={"response": {**REMOTE_USER, "expireAt": body.get("expireAt")}}
        )

    from shared.remnawave import client as rw

    original = rw.RemnawaveClient.__init__

    def patched(self, base_url, token, **kwargs):
        original(self, base_url, token, **kwargs)
        self._client = httpx.AsyncClient(
            base_url=base_url, transport=httpx.MockTransport(handler)
        )

    monkeypatch.setattr(rw.RemnawaveClient, "__init__", patched)

    user = await subs.get_or_create_user(db, 1)
    user.remnawave_uuid = "rw-1"
    user.expire_at = datetime.now(UTC) + timedelta(days=10)
    await db.flush()

    await subs.grant_bonus_days(db, config, user, 5, source="promo")

    method, path = calls[-1]
    # extend_expiration идёт через PATCH /api/users без полей squads —
    # значит existing сквады не перезаписываются.
    assert method == "PATCH"
    assert path == "/api/users"


# ── Платёжный флоу ────────────────────────────────────────────────────
async def test_create_external_payment_platega(db, config, monkeypatch) -> None:
    from shared.payments import platega as platega_module

    async def fake_create_transaction(self, **kwargs):
        assert kwargs["order_id"]
        return {"id": "px-1", "redirectUrl": "https://pay.example/px-1"}

    monkeypatch.setattr(
        platega_module.PlategaClient, "create_transaction", fake_create_transaction
    )

    user = await subs.get_or_create_user(db, 1)
    await db.flush()

    payment, pay_url = await payment_flow.create_external_payment(
        db,
        config,
        user,
        purpose=PaymentPurpose.TOPUP,
        amount_kopeks=10000,
        provider=PaymentProvider.PLATEGA,
        description="Пополнение",
    )
    assert payment.external_id == "px-1"
    assert pay_url == "https://pay.example/px-1"


async def test_create_external_payment_disabled_provider(db) -> None:
    user = await subs.get_or_create_user(db, 1)
    await db.flush()
    disabled_config = Config(
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
    with pytest.raises(payment_flow.PaymentFlowError):
        await payment_flow.create_external_payment(
            db,
            disabled_config,
            user,
            purpose=PaymentPurpose.TOPUP,
            amount_kopeks=1000,
            provider=PaymentProvider.PLATEGA,
            description="x",
        )
