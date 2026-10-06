"""Общая скидка, персональные тарифы, «Купить»/«Продлить», скрываемые кнопки,
уведомления о продажах."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

from sqlalchemy import select

from app import handlers, keyboards
from app.services import pricing
from app.services import subscriptions as subs
from shared.db.models import Plan, Wallet
from tests.test_fixes import PLAN, config, db, engine, remote  # noqa: F401  — фикстуры

PERSONAL = replace(PLAN, id=50, title="VIP", price_kopeks=10000, owner_user_id=1)


def _buttons(markup):
    return [b for row in markup.inline_keyboard for b in row]


def _callbacks(markup) -> list[str]:
    return [b.callback_data for b in _buttons(markup) if b.callback_data]


# ── Скидки ────────────────────────────────────────────────────────────
def test_global_discount_until_date(config) -> None:
    cfg = replace(config, discount_percent=25, discount_until=datetime.now(UTC) + timedelta(days=1))
    assert cfg.global_discount() == 25
    assert cfg.plan_discount(PLAN) == 25
    expired = replace(cfg, discount_until=datetime.now(UTC) - timedelta(minutes=1))
    assert expired.global_discount() == 0


def test_personal_plan_ignores_global_discount(config) -> None:
    cfg = replace(config, discount_percent=50)
    assert cfg.plan_discount(PERSONAL) == 0
    # Промокод действует и на персональный тариф.
    assert cfg.plan_discount(PERSONAL, 10) == 10


def test_discounts_do_not_stack(config) -> None:
    cfg = replace(config, discount_percent=20)
    assert cfg.plan_discount(PLAN, 10) == 20
    assert cfg.plan_discount(PLAN, 40) == 40


def test_plan_button_shows_price_in_words(config) -> None:
    cfg = replace(config, discount_percent=15, plans=[replace(PLAN, price_kopeks=19900)])
    button = _buttons(keyboards.plans_menu(cfg))[0]
    # Без кривых комбинируемых символов и копеек: «169 ₽ вместо 199 ₽».
    assert button.text == "Месяц • 169 ₽ вместо 199 ₽"


# ── Персональные тарифы ───────────────────────────────────────────────
async def test_personal_plan_visible_only_to_owner(db, config) -> None:
    owner = await subs.get_or_create_user(db, 1)
    other = await subs.get_or_create_user(db, 2)
    await db.flush()
    row = Plan(title="VIP", days=90, price_kopeks=5000, owner_user_id=owner.id)
    db.add(row)
    await db.flush()
    cfg = replace(config, plans=[replace(PLAN, id=row.id + 100)])

    assert [p.id for p in await pricing.personal_plans(db, owner)] == [row.id]
    assert await pricing.personal_plans(db, other) == []
    assert await pricing.find_plan(db, cfg, other, row.id) is None
    found = await pricing.find_plan(db, cfg, owner, row.id)
    assert found is not None and found.is_personal


def test_personal_plans_on_top_of_list(config) -> None:
    markup = keyboards.plans_menu(config, personal=[PERSONAL])
    first = _buttons(markup)[0]
    assert first.text.startswith("⭐ VIP") and first.callback_data == "buy:50"


# ── Главное меню ──────────────────────────────────────────────────────
def _menu(config, has_subscription: bool = False):
    return keyboards.main_menu(
        config, trial_available=False, has_subscription=has_subscription, balance_kopeks=0
    )


def test_menu_buy_without_subscription(config) -> None:
    callbacks = _callbacks(_menu(config))
    assert "plans" in callbacks and "renew" not in callbacks


def test_menu_renew_with_subscription(config) -> None:
    callbacks = _callbacks(_menu(config, has_subscription=True))
    assert "renew" in callbacks
    assert "plans" not in callbacks and "plans_new" not in callbacks
    multi = replace(config, allow_multiple_subscriptions=True)
    assert "plans_new" in _callbacks(_menu(multi, has_subscription=True))


def test_menu_buttons_can_be_hidden(config) -> None:
    cfg = replace(
        config,
        support_url="https://t.me/support",
        menu_hidden=frozenset({"support", "promo", "profile", "balance"}),
    )
    texts = [b.text for b in _buttons(_menu(cfg))]
    assert not any(t.startswith(("Поддержка", "Промокод", "Личный", "Баланс")) for t in texts)
    assert all(row for row in _menu(cfg).inline_keyboard)  # без пустых рядов


def test_help_button_can_be_hidden(config) -> None:
    shown = [b.text for row in keyboards.reply_menu(config).keyboard for b in row]
    assert shown == ["Меню", "Помощь"]
    hidden = replace(config, menu_hidden=frozenset({"help"}))
    assert [b.text for row in keyboards.reply_menu(hidden).keyboard for b in row] == ["Меню"]


# ── Покупка при уже существующей подписке ─────────────────────────────
async def _user_with_key(db, config):
    user = await subs.get_or_create_user(db, 777)
    await db.flush()
    await subs.create_subscription(db, config, user, PLAN, source="test")
    await db.flush()
    return user


async def test_buy_turns_into_renewal_with_single_subscription(db, config, remote) -> None:
    user = await _user_with_key(db, config)
    pay = await handlers._resolve_pay_target(db, config, user, "plan", str(PLAN.id))
    assert pay.ok and pay.subscription is not None  # продление, а не второй ключ
    assert pay.description.startswith("Продление")


async def test_buy_creates_new_key_when_multiple_allowed(db, config, remote) -> None:
    cfg = replace(config, allow_multiple_subscriptions=True)
    user = await _user_with_key(db, cfg)
    pay = await handlers._resolve_pay_target(db, cfg, user, "plan", str(PLAN.id))
    assert pay.ok and pay.subscription is None


async def test_pay_target_uses_global_discount(db, config) -> None:
    cfg = replace(config, discount_percent=25)
    user = await subs.get_or_create_user(db, 1)
    await db.flush()
    pay = await handlers._resolve_pay_target(db, cfg, user, "plan", str(PLAN.id))
    assert pay.amount_kopeks == 29900


# ── Уведомления о продажах ────────────────────────────────────────────
async def test_sale_notification_kinds_and_escaping(db, config, monkeypatch) -> None:
    sent = []

    async def fake_sales(token, chat_id, text):
        sent.append((chat_id, text))
        return True

    monkeypatch.setattr(subs, "notify_sales", fake_sales)
    cfg = replace(config, purchase_notify_chat_id=-1001234567890)
    user = await subs.get_or_create_user(db, 1, username="<evil>")
    await db.flush()

    await subs.after_paid_purchase(
        db, cfg, user, 14000, plan_title="Месяц", kind="renewal", method="wallet"
    )
    chat_id, text = sent[0]
    assert chat_id == -1001234567890
    assert "Продление" in text and "140.00 ₽" in text and "баланс" in text
    assert "<evil>" not in text


# ── Сквозной сценарий: оплата с баланса через хендлер ─────────────────
async def test_wallet_payment_renews_with_discount(config, remote, monkeypatch) -> None:
    from app.services import wallet
    from shared.db.base import Base
    from shared.db.models import BotUser
    from shared.db.session import SessionLocal
    from shared.db.session import engine as shared_engine

    async with shared_engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)

    monkeypatch.setattr(subs, "notify_sales", AsyncMock())
    cfg = replace(config, discount_percent=25)
    async with SessionLocal() as db:
        user = await subs.get_or_create_user(db, 4242)
        await db.flush()
        await subs.create_subscription(db, cfg, user, PLAN, source="test")
        await wallet.credit(db, user, 50000, wallet.WalletTxType.TOPUP)
        await db.commit()
        user_id = user.id

    callback = MagicMock()
    callback.from_user.id = 4242
    callback.answer = AsyncMock()
    callback.message.photo = None
    callback.message.edit_text = AsyncMock()
    callback.data = f"pay:plan:wallet:{PLAN.id}"
    await handlers.cb_pay(callback, cfg, MagicMock())

    async with SessionLocal() as db:
        user = await db.get(BotUser, user_id)
        keys = await subs.list_subscriptions(db, user)
        balance = await db.scalar(select(Wallet.balance_kopeks).where(Wallet.user_id == user_id))
    assert len(keys) == 1  # продлили, а не завели второй ключ
    assert balance == 50000 - 29900
    assert "Оплачено с баланса" in callback.message.edit_text.await_args.args[0]


# ── Раскладка, цвета, кнопка меню, QR (1.6.1) ─────────────────────────
def test_menu_columns(config) -> None:
    cfg = replace(config, menu_columns=2, support_url="https://t.me/s", channel_url="https://t.me/c")
    rows = _menu(cfg, has_subscription=True).inline_keyboard
    assert all(len(row) <= 2 for row in rows)
    assert max(len(row) for row in rows) == 2
    three = replace(cfg, menu_columns=3)
    assert max(len(row) for row in _menu(three, has_subscription=True).inline_keyboard) == 3


def test_menu_button_colors(config) -> None:
    cfg = replace(config, menu_styles={"buy": "danger", "trial": "none"})
    markup = keyboards.main_menu(
        cfg, trial_available=True, has_subscription=False, balance_kopeks=0
    )
    by_data = {b.callback_data: b for b in _buttons(markup)}
    assert by_data["plans"].style == "danger"
    assert by_data["trial"].style is None  # «none» снимает цвет по умолчанию
    assert by_data["promo"].style is None


def test_commands_mode_removes_reply_keyboard(config) -> None:
    from aiogram.types import ReplyKeyboardMarkup, ReplyKeyboardRemove

    assert isinstance(keyboards.bottom_keyboard(config), ReplyKeyboardMarkup)
    assert isinstance(
        keyboards.bottom_keyboard(replace(config, menu_mode="commands")), ReplyKeyboardRemove
    )


def test_qr_png() -> None:
    assert handlers.qr_png("https://sub.example/abc").startswith(b"\x89PNG")
