"""Скидки, персональные тарифы, «Купить»/«Продлить», скрываемые кнопки меню."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta

from app import handlers, keyboards
from app.config import PlanView
from app.services import pricing
from app.services import subscriptions as subs
from shared.db.models import BotSubscription, Plan
from tests.test_monetization_logic import config, db  # noqa: F401  — фикстуры

PLAN = PlanView(
    id=1, title="Месяц", days=30, price_kopeks=20000, squad_uuids=[], hwid_limit=3,
    traffic_limit_bytes=0,
)
PERSONAL = replace(PLAN, id=2, title="VIP", price_kopeks=10000, owner_user_id=1)


def _callbacks(markup) -> list[str]:
    return [b.callback_data for row in markup.inline_keyboard for b in row if b.callback_data]


# ── Цены ──────────────────────────────────────────────────────────────
def test_global_discount_applies_until_date(config) -> None:
    cfg = replace(config, discount_percent=25, discount_until=datetime.now(UTC) + timedelta(days=1))
    price = pricing.price_for(cfg, PLAN, None)
    assert (price.base_kopeks, price.final_kopeks, price.percent) == (20000, 15000, 25)

    expired = replace(cfg, discount_until=datetime.now(UTC) - timedelta(minutes=1))
    assert pricing.price_for(expired, PLAN, None).final_kopeks == 20000


def test_global_discount_skips_personal_plans(config) -> None:
    cfg = replace(config, discount_percent=50)
    assert pricing.price_for(cfg, PERSONAL, None).final_kopeks == 10000


async def test_promo_discount_does_not_stack_with_global(db, config) -> None:
    user = await subs.get_or_create_user(db, 1)
    user.pending_discount_percent = 10
    cfg = replace(config, discount_percent=20)
    # Берётся большая скидка, а не 30%.
    assert pricing.price_for(cfg, PLAN, user).percent == 20
    user.pending_discount_percent = 40
    assert pricing.price_for(cfg, PLAN, user).final_kopeks == 12000
    # Промокод действует и на персональный тариф.
    assert pricing.price_for(cfg, PERSONAL, user).final_kopeks == 6000


def test_button_shows_old_and_new_price(config) -> None:
    cfg = replace(config, discount_percent=25)
    text = pricing.button_price(pricing.price_for(cfg, PLAN, None))
    assert text.endswith("150 ₽") and "̶" in text
    assert pricing.text_price(pricing.price_for(cfg, PLAN, None)) == "<s>200 ₽</s> 150 ₽ (−25%)"


# ── Персональные тарифы ───────────────────────────────────────────────
async def test_personal_plan_visible_only_to_owner(db, config) -> None:
    config = replace(config, plans=[replace(PLAN, id=100)])
    owner = await subs.get_or_create_user(db, 1)
    other = await subs.get_or_create_user(db, 2)
    await db.flush()
    row = Plan(title="VIP", days=90, price_kopeks=5000, owner_user_id=owner.id)
    db.add(row)
    await db.flush()

    assert [p.id for p in await pricing.plans_for(db, config, owner)][0] == row.id
    assert row.id not in [p.id for p in await pricing.plans_for(db, config, other)]
    assert await pricing.find_plan(db, config, other, row.id) is None
    found = await pricing.find_plan(db, config, owner, row.id)
    assert found is not None and found.is_personal


# ── Главное меню ──────────────────────────────────────────────────────
def test_menu_buy_without_subscription(config) -> None:
    callbacks = _callbacks(keyboards.main_menu(config, trial_available=False))
    assert "plans" in callbacks and "renew" not in callbacks


def test_menu_renew_with_subscription(config) -> None:
    callbacks = _callbacks(
        keyboards.main_menu(config, trial_available=False, has_subscription=True)
    )
    assert "renew" in callbacks
    assert "plans" not in callbacks and "plans_new" not in callbacks

    multi = replace(config, allow_multiple_subscriptions=True)
    callbacks = _callbacks(keyboards.main_menu(multi, trial_available=False, has_subscription=True))
    assert "plans_new" in callbacks


def test_menu_buttons_can_be_hidden(config) -> None:
    cfg = replace(
        config,
        support_url="https://t.me/support",
        menu_hidden=frozenset({"support", "promo", "profile"}),
    )
    markup = keyboards.main_menu(cfg, trial_available=True)
    texts = [b.text for row in markup.inline_keyboard for b in row]
    assert not any("Поддержка" in t or "Промокод" in t or "профиль" in t for t in texts)
    assert all(row for row in markup.inline_keyboard)  # без пустых рядов


# ── Покупка при уже существующей подписке ─────────────────────────────
async def _user_with_key(db, config):
    user = await subs.get_or_create_user(db, 1)
    await db.flush()
    await subs.create_subscription(db, config, user, config.plans[0], source="test")
    await db.flush()
    return user


async def test_buy_turns_into_renewal_when_single_subscription(db, config) -> None:
    user = await _user_with_key(db, config)
    pay = await handlers._resolve_pay_target(db, config, user, "plan", str(config.plans[0].id))
    assert pay is not None
    assert pay.subscription is not None  # продление, а не второй ключ
    assert pay.description.startswith("Продление")


async def test_buy_creates_new_key_when_multiple_allowed(db, config) -> None:
    cfg = replace(config, allow_multiple_subscriptions=True)
    user = await _user_with_key(db, cfg)
    pay = await handlers._resolve_pay_target(db, cfg, user, "plan", str(cfg.plans[0].id))
    assert pay is not None and pay.subscription is None


async def test_renew_rejects_foreign_key(db, config) -> None:
    owner = await _user_with_key(db, config)
    stranger = await subs.get_or_create_user(db, 2)
    await db.flush()
    key = (await subs.list_subscriptions(db, owner))[0]
    pay = await handlers._resolve_pay_target(
        db, config, stranger, "renew", f"{key.id}-{config.plans[0].id}"
    )
    assert pay is None


async def test_pay_target_uses_discounted_amount(db, config) -> None:
    cfg = replace(config, discount_percent=50)
    user = await subs.get_or_create_user(db, 1)
    await db.flush()
    pay = await handlers._resolve_pay_target(db, cfg, user, "plan", str(cfg.plans[0].id))
    assert pay.amount_kopeks == cfg.plans[0].price_kopeks // 2
    assert pay.discount_percent == 50


async def test_renew_target_prefers_main_key(db, config) -> None:
    cfg = replace(config, allow_multiple_subscriptions=True)
    user = await _user_with_key(db, cfg)
    await subs.create_subscription(db, cfg, user, cfg.plans[0], source="test")
    await db.flush()
    target = await subs.renew_target(db, user)
    assert isinstance(target, BotSubscription)
    assert str(target.remnawave_id) == user.remnawave_uuid


# ── После оплаты ──────────────────────────────────────────────────────
async def test_after_purchase_consumes_promo_discount_and_notifies(
    db, config, monkeypatch
) -> None:
    sent = []

    async def fake_sales(token, chat_id, text):
        sent.append((chat_id, text))
        return True

    monkeypatch.setattr(subs, "notify_sales", fake_sales)
    cfg = replace(config, purchase_notify_chat_id=-1001234567890)
    user = await subs.get_or_create_user(db, 1, username="<evil>")
    user.pending_discount_percent = 30
    await db.flush()

    await subs.after_paid_purchase(
        db, cfg, user, 14000, plan_title="Месяц", kind="renewal", method="wallet",
        discount_percent=30,
    )
    assert user.pending_discount_percent == 0
    chat_id, text = sent[0]
    assert chat_id == -1001234567890
    assert "Продление" in text and "140.00 ₽" in text and "скидка 30%" in text
    assert "<evil>" not in text  # имя экранировано для HTML


async def test_auto_renewal_keeps_promo_discount(db, config, monkeypatch) -> None:
    monkeypatch.setattr(subs, "notify_sales", lambda *a: None)
    user = await subs.get_or_create_user(db, 1)
    user.pending_discount_percent = 30
    await db.flush()
    await subs.after_paid_purchase(db, config, user, 100, consume_discount=False)
    assert user.pending_discount_percent == 30


# ── Сквозной сценарий: оплата с баланса через хендлер ─────────────────
async def test_wallet_payment_renews_with_discount(config, monkeypatch) -> None:
    from unittest.mock import AsyncMock, MagicMock

    from app.services import wallet
    from shared.db.base import Base
    from shared.db.models import BotUser, Wallet
    from shared.db.session import SessionLocal, engine
    from sqlalchemy import select

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)

    monkeypatch.setattr(subs, "notify_sales", AsyncMock())
    cfg = replace(config, discount_percent=25)
    async with SessionLocal() as db:
        user = await subs.get_or_create_user(db, 4242)
        await db.flush()
        await subs.create_subscription(db, cfg, user, cfg.plans[0], source="test")
        await wallet.credit(db, user, 50000, wallet.WalletTxType.TOPUP)
        user.pending_discount_percent = 10
        await db.commit()
        user_id = user.id

    callback = MagicMock()
    callback.from_user.id = 4242
    callback.answer = AsyncMock()
    callback.message.text = "меню"
    callback.message.edit_text = AsyncMock()
    callback.data = f"pay:plan:wallet:{cfg.plans[0].id}"
    await handlers.cb_pay(callback, cfg, MagicMock())

    async with SessionLocal() as db:
        user = await db.get(BotUser, user_id)
        keys = await subs.list_subscriptions(db, user)
        balance = await db.scalar(
            select(Wallet.balance_kopeks).where(Wallet.user_id == user_id)
        )
    price = cfg.plans[0].price_kopeks * 75 // 100  # общая 25% больше промокода 10%
    assert len(keys) == 1  # продлили, а не завели второй ключ
    assert balance == 50000 - price
    assert user.pending_discount_percent == 0
    text = callback.message.edit_text.await_args.args[0]
    assert "продлена" in text
