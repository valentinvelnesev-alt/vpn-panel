"""Хендлеры бота.

Один модуль на весь диалог — он умещается в несколько сотен строк, потому
что тексты вынесены в texts.py, клавиатуры в keyboards.py, а выдача
подписок в services/subscriptions.py. Разрастётся — резать по этим швам.
"""

import html
import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime

from aiogram import Bot, Dispatcher, F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandStart, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import BaseStorage
from aiogram.types import (
    CallbackQuery,
    ChatMemberUpdated,
    LabeledPrice,
    Message,
    PreCheckoutQuery,
)
from sqlalchemy import func, select

from app import keyboards, texts
from app.config import Config, PlanView, plan_view
from app.services import payment_check, payment_flow, pricing, referral, stars
from app.services import promo as promo_service
from app.services import subscriptions as subs
from app.services import wallet
from app.states import UserStates
from shared.db.models import (
    BotSubscription,
    BotUser,
    Payment,
    PaymentProvider,
    PaymentPurpose,
    Purchase,
    WalletTxType,
)
from shared.db.models import Plan as PlanRow
from shared.db.session import session
from shared.remnawave import RemnawaveClient, RemnawaveError

log = logging.getLogger("bot.handlers")

router = Router()


def build_dispatcher(*, storage: BaseStorage, config: Config) -> Dispatcher:
    # router — модульный singleton (на нём висят все @router.* хендлеры).
    # aiogram не даёт повторно прикрепить Router к новому Dispatcher, если
    # он уже был прикреплён к старому (start → stop → start), поэтому явно
    # открепляем перед каждой пересборкой.
    from app import admin

    router._parent_router = None
    admin.router._parent_router = None
    dispatcher = Dispatcher(storage=storage)
    # Конфиг кладём в контекст: хендлеры получают его аргументом и не лезут
    # в глобальные переменные.
    dispatcher["config"] = config
    dispatcher.include_router(admin.router)
    dispatcher.include_router(router)
    return dispatcher


def t(config: Config, template: str, **values: object) -> str:
    return texts.render(
        template, config.emoji_mode, config.premium_emoji, **values
    )


def _left(expire_at: datetime | None) -> str:
    if expire_at is None:
        return "—"
    if expire_at.tzinfo is None:
        expire_at = expire_at.replace(tzinfo=UTC)
    days = (expire_at - datetime.now(UTC)).days
    if days < 0:
        return "истекла"
    if days == 0:
        return "меньше суток"
    return _plural(days, "день", "дня", "дней")


def _plural(n: int, one: str, few: str, many: str) -> str:
    if n % 10 == 1 and n % 100 != 11:
        return f"{n} {one}"
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return f"{n} {few}"
    return f"{n} {many}"


def _date(value: datetime | None) -> str:
    if value is None:
        return "—"
    return value.strftime("%d.%m.%Y")


# ── Проверка подписки на канал ────────────────────────────────────────
async def _channel_ok(bot: Bot, config: Config, telegram_id: int) -> bool:
    if not config.require_channel_sub or not config.channel_id:
        return True
    try:
        member = await bot.get_chat_member(config.channel_id, telegram_id)
    except Exception as exc:  # noqa: BLE001
        # Бот не админ канала или id неверный — не запираем людей из-за
        # чужой ошибки настройки.
        log.warning("Не удалось проверить подписку на канал: %s", exc)
        return True
    return member.status in {"member", "administrator", "creator"}


# ── Главное меню ──────────────────────────────────────────────────────
async def _show_menu(target: Message | CallbackQuery, config: Config) -> None:
    message = target if isinstance(target, Message) else target.message
    telegram_id = target.from_user.id

    async with session() as db:
        user = await subs.get_or_create_user(
            db,
            telegram_id,
            username=target.from_user.username,
            first_name=target.from_user.first_name,
            language_code=target.from_user.language_code,
        )
        has_subscription = await subs.has_subscription(db, user)
        # Триал — только тем, у кого ещё не было ни одного ключа: иначе он
        # перезаписал бы сквады и лимит устройств платной подписки триальными.
        trial_available = config.trial_enabled and not user.trial_used and not has_subscription

    text = t(config, config.welcome_text or texts.WELCOME_DEFAULT, brand=config.brand)
    markup = keyboards.main_menu(
        config, trial_available=trial_available, has_subscription=has_subscription
    )

    if isinstance(target, CallbackQuery):
        await _edit(message, text, markup)
        await target.answer()
    else:
        await message.answer(text, reply_markup=markup)


async def _edit(message: Message, text: str, markup=None) -> None:
    """Правит сообщение с меню. Под фото или счётом (Stars) текст не
    отредактировать — тогда просто присылаем новое сообщение."""
    if message.text is not None:
        try:
            await message.edit_text(text, reply_markup=markup)
            return
        except TelegramBadRequest as exc:
            if "message is not modified" in str(exc):
                return
    await message.answer(text, reply_markup=markup)


@router.message(CommandStart())
async def cmd_start(message: Message, config: Config, bot: Bot) -> None:
    if not await _channel_ok(bot, config, message.from_user.id):
        await message.answer(
            t(config, texts.CHANNEL_REQUIRED),
            reply_markup=keyboards.channel_gate(config),
        )
        return

    # Реферальная ссылка: t.me/bot?start=ref_ABC123 → payload "ref_ABC123".
    payload = (message.text or "").partition(" ")[2].strip()
    if payload.startswith("ref_") and config.referral_enabled:
        async with session() as db:
            user = await subs.get_or_create_user(
                db, message.from_user.id, username=message.from_user.username
            )
            await referral.attach_referrer(db, config, user, payload.removeprefix("ref_"))

    await _show_menu(message, config)


@router.callback_query(F.data == "menu")
async def cb_menu(callback: CallbackQuery, config: Config) -> None:
    await _show_menu(callback, config)


@router.callback_query(F.data == "check_sub")
async def cb_check_sub(callback: CallbackQuery, config: Config, bot: Bot) -> None:
    if await _channel_ok(bot, config, callback.from_user.id):
        await _show_menu(callback, config)
    else:
        await callback.answer("Подписка на канал не найдена", show_alert=True)


@router.message(Command("help"))
async def cmd_help(message: Message, config: Config) -> None:
    if not config.shows("help"):
        # Команду скрыли в панели — показываем обычное меню, а не справку.
        await _show_menu(message, config)
        return
    await message.answer(t(config, texts.HELP))


# ── Подписка ──────────────────────────────────────────────────────────
@router.message(Command("subscription"))
async def cmd_subscription(message: Message, config: Config) -> None:
    await _show_subscription(message, config)


@router.callback_query(F.data == "subscription")
async def cb_subscription(callback: CallbackQuery, config: Config) -> None:
    await _show_subscription(callback, config)


async def _show_subscription(target: Message | CallbackQuery, config: Config) -> None:
    message = target if isinstance(target, Message) else target.message

    async with session() as db:
        user = await subs.get_or_create_user(db, target.from_user.id)
        active = subs.is_active(user)
        expire_at, url = user.expire_at, user.subscription_url

    if active:
        text = t(
            config,
            texts.SUBSCRIPTION_ACTIVE,
            until=_date(expire_at),
            left=_left(expire_at),
            url=url or "—",
        )
    else:
        text = t(config, texts.SUBSCRIPTION_NONE)

    markup = keyboards.back_to_menu()
    if isinstance(target, CallbackQuery):
        await message.edit_text(text, reply_markup=markup)
        await target.answer()
    else:
        await message.answer(text, reply_markup=markup)


# ── Триал ─────────────────────────────────────────────────────────────
@router.callback_query(F.data == "trial")
async def cb_trial(callback: CallbackQuery, config: Config) -> None:
    if not config.trial_enabled:
        await callback.answer("Пробный период отключён", show_alert=True)
        return

    async with session() as db:
        user = await subs.get_or_create_user(db, callback.from_user.id)
        if user.trial_used:
            await callback.answer("Пробный период уже использован", show_alert=True)
            return
        if await subs.has_subscription(db, user):
            await callback.answer(
                "Пробный период доступен только до первой подписки", show_alert=True
            )
            return
        try:
            user = await subs.grant_trial(db, config, user)
        except RemnawaveError as exc:
            log.error("Не удалось выдать триал: %s", exc)
            await callback.answer(
                "Не удалось выдать доступ, попробуйте позже", show_alert=True
            )
            return
        expire_at, url = user.expire_at, user.subscription_url

    await callback.message.edit_text(
        t(
            config,
            texts.TRIAL_GRANTED,
            days=_plural(config.trial_days, "день", "дня", "дней"),
            until=_date(expire_at),
            url=url or "—",
        ),
        reply_markup=keyboards.back_to_menu(),
    )
    await callback.answer()


# ── Тарифы ────────────────────────────────────────────────────────────
async def _priced_plans(db, config: Config, user: BotUser):
    plans = await pricing.plans_for(db, config, user)
    return [(plan, pricing.price_for(config, plan, user)) for plan in plans]


def _plans_header(config: Config, user: BotUser) -> str:
    text = t(config, texts.PLANS_HEADER)
    percent = pricing.global_discount(config)
    if percent:
        until = (
            f" до {_date(config.discount_until)}" if config.discount_until is not None else ""
        )
        text += f"\n\n🔥 Скидка {percent}% на все тарифы{until}"
    if user.pending_discount_percent:
        text += f"\n🎟 По промокоду: −{user.pending_discount_percent}% на эту покупку"
    return text


async def _show_plans(
    callback: CallbackQuery,
    config: Config,
    *,
    prefix: str = "buy",
    back: str = "menu",
) -> None:
    async with session() as db:
        user = await subs.get_or_create_user(db, callback.from_user.id)
        priced = await _priced_plans(db, config, user)
        header = _plans_header(config, user)

    if not priced:
        await _edit(callback.message, t(config, texts.NO_PLANS), keyboards.back_to_menu())
    else:
        await _edit(
            callback.message,
            header,
            keyboards.plans_menu(config, priced, prefix=prefix, back=back),
        )
    await callback.answer()


async def _start_renew(callback: CallbackQuery, config: Config, subscription_id: int) -> None:
    await _show_plans(
        callback,
        config,
        prefix=f"renewbuy-{subscription_id}",
        back=f"viewsub:{subscription_id}",
    )


@router.callback_query(F.data == "plans")
async def cb_plans(callback: CallbackQuery, config: Config) -> None:
    """«Купить подписку». Если ключ уже есть, а несколько подписок в панели
    не разрешены, — ведём в продление: иначе покупка заведёт клиенту второй
    ключ, и он будет платить за две подписки вместо продления одной."""
    async with session() as db:
        user = await subs.get_or_create_user(db, callback.from_user.id)
        has_subscription = await subs.has_subscription(db, user)
    if has_subscription:
        await cb_renew(callback, config)
        return
    await _show_plans(callback, config)


@router.callback_query(F.data == "plans_new")
async def cb_plans_new(callback: CallbackQuery, config: Config) -> None:
    """«Купить ещё одну» — отдельный новый ключ (если это разрешено)."""
    if not config.allow_multiple_subscriptions:
        await cb_renew(callback, config)
        return
    await _show_plans(callback, config)


@router.callback_query(F.data == "renew")
async def cb_renew(callback: CallbackQuery, config: Config) -> None:
    """«Продлить подписку»: один ключ — сразу к тарифам продления, несколько —
    список ключей, где у каждого своя кнопка «Продлить»."""
    async with session() as db:
        user = await subs.get_or_create_user(db, callback.from_user.id)
        rows = await subs.list_subscriptions(db, user)
    if not rows:
        await _show_plans(callback, config)
    elif len(rows) == 1:
        await _start_renew(callback, config, rows[0].id)
    else:
        await _edit(
            callback.message,
            "🔑 <b>Какую подписку продлить?</b>",
            keyboards.subscriptions_menu(rows),
        )
        await callback.answer()


@router.callback_query(F.data.startswith("plancat:"))
async def cb_plan_category(callback: CallbackQuery, config: Config) -> None:
    _, prefix, raw_category_id = callback.data.split(":", 2)
    category_id = None if raw_category_id == "0" else int(raw_category_id)
    back_to_top = "plans" if prefix == "buy" else (
        f"renewsub:{prefix.removeprefix('renewbuy-')}"
    )

    async with session() as db:
        user = await subs.get_or_create_user(db, callback.from_user.id)
        priced = await _priced_plans(db, config, user)
        header = _plans_header(config, user)

    await _edit(
        callback.message,
        header,
        keyboards.plans_menu(
            config, priced, prefix=prefix, category_id=category_id, back=back_to_top
        ),
    )
    await callback.answer()


def _any_provider_enabled(config: Config) -> bool:
    return (
        config.platega_enabled
        or config.rollypay_enabled
        or config.cryptobot_enabled
        or config.stars_enabled
    )


async def _show_checkout(
    callback: CallbackQuery, config: Config, plan_id: int, *, purpose: str, target_prefix: str
) -> None:
    async with session() as db:
        user = await subs.get_or_create_user(db, callback.from_user.id)
        plan = await pricing.find_plan(db, config, user, plan_id)
        price = pricing.price_for(config, plan, user) if plan else None
    if plan is None or price is None:
        await callback.answer("Тариф больше не доступен", show_alert=True)
        return

    free = price.final_kopeks == 0
    if not free and not _any_provider_enabled(config):
        # Из баланса можно платить и без внешних касс — но если их нет совсем,
        # пополнить баланс тоже нечем, поэтому честно говорим об этом.
        await callback.answer("Приём оплаты ещё не настроен в панели", show_alert=True)
        return

    await _edit(
        callback.message,
        t(
            config,
            "{@card} <b>{title}</b> — {price}\n\nСпособ оплаты:",
            title=html.escape(plan.title),
            price=pricing.text_price(price),
        ),
        keyboards.providers_menu(
            config, purpose=purpose, target=f"{target_prefix}{plan.id}", free=free
        ),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("buy:"))
async def cb_buy(callback: CallbackQuery, config: Config) -> None:
    plan_id = int(callback.data.split(":", 1)[1])
    await _show_checkout(callback, config, plan_id, purpose="plan", target_prefix="")


# ── Кошелёк ───────────────────────────────────────────────────────────
@router.callback_query(F.data == "wallet")
async def cb_wallet(callback: CallbackQuery, config: Config) -> None:
    async with session() as db:
        user = await subs.get_or_create_user(db, callback.from_user.id)
        w = await wallet.get_or_create(db, user)
        balance = w.balance_kopeks

    await callback.message.edit_text(
        t(config, "{@card} Баланс: <b>{balance} ₽</b>", balance=f"{balance / 100:.2f}"),
        reply_markup=keyboards.wallet_menu(),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("topup:"))
async def cb_topup_preset(callback: CallbackQuery, config: Config) -> None:
    amount = callback.data.split(":", 1)[1]
    await _show_topup_providers(callback, config, amount)


@router.callback_query(F.data == "topup_custom")
async def cb_topup_custom(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(UserStates.entering_topup_amount)
    await callback.message.edit_text(
        "Введите сумму пополнения в рублях (от 10 до 100000):",
        reply_markup=keyboards.back_to_menu(),
    )
    await callback.answer()


@router.message(StateFilter(UserStates.entering_topup_amount))
async def on_topup_amount(message: Message, state: FSMContext, config: Config) -> None:
    raw = (message.text or "").strip().replace(",", ".")
    try:
        amount = float(raw)
    except ValueError:
        amount = -1
    if not (10 <= amount <= 100_000):
        await message.answer("Сумма должна быть от 10 до 100000 ₽. Попробуйте ещё раз:")
        return

    await state.clear()
    await message.answer(
        "Способ оплаты:",
        reply_markup=keyboards.providers_menu(
            config, purpose="topup", target=f"{amount:g}"
        ),
    )


async def _show_topup_providers(callback: CallbackQuery, config: Config, amount: str) -> None:
    await callback.message.edit_text(
        f"Пополнение на {amount} ₽. Способ оплаты:",
        reply_markup=keyboards.providers_menu(config, purpose="topup", target=amount),
    )
    await callback.answer()


# ── Оплата ────────────────────────────────────────────────────────────
@dataclass(slots=True)
class PayTarget:
    plan: PlanView | None
    subscription: BotSubscription | None
    amount_kopeks: int
    description: str
    discount_percent: int = 0


async def _resolve_pay_target(
    db, config: Config, user: BotUser, purpose: str, target: str
) -> PayTarget | None:
    """Разбирает `target` из callback_data / payload Stars.

    purpose == "plan"  → target = "{plan_id}" — покупка. Если у клиента уже
    есть ключ, а несколько подписок не разрешены, покупка превращается в
    продление этого ключа — даже если кнопку нажали в старом сообщении.
    purpose == "renew" → target = "{subscription_id}-{plan_id}" — продление
    конкретного существующего ключа (проверяем, что он принадлежит user).
    purpose == "topup" → target = сумма в рублях, тариф/ключ не участвуют.
    None — тариф или ключ недоступны.
    """
    if purpose == "topup":
        amount = round(float(target) * 100)
        if not 1000 <= amount <= 10_000_000:
            return None
        return PayTarget(None, None, amount, f"Пополнение баланса на {target} ₽")

    subscription: BotSubscription | None = None
    if purpose == "renew":
        sub_id_str, _, plan_id_str = target.partition("-")
        subscription = await db.get(BotSubscription, int(sub_id_str))
        if subscription is None or subscription.user_id != user.id:
            return None
    else:
        plan_id_str = target
        if not config.allow_multiple_subscriptions and await subs.has_subscription(db, user):
            subscription = await subs.renew_target(db, user)

    plan = await pricing.find_plan(db, config, user, int(plan_id_str))
    if plan is None:
        return None
    price = pricing.price_for(config, plan, user)
    verb = "Продление" if subscription is not None else "Оплата тарифа"
    return PayTarget(
        plan, subscription, price.final_kopeks, f"{verb} «{plan.title}»", price.percent
    )


async def _apply_plan(
    db, config: Config, user: BotUser, pay: PayTarget, *, source: str
) -> tuple[datetime | None, str]:
    """Выдаёт оплаченный тариф: продлевает ключ или заводит новый.
    Возвращает (действует до, вид продажи для уведомления)."""
    if pay.subscription is not None:
        subscription = await subs.extend_subscription(
            db, config, pay.subscription, pay.plan, amount_kopeks=pay.amount_kopeks
        )
        return subscription.expire_at, "renewal"
    subscription = await subs.create_subscription(
        db, config, user, pay.plan, source=source, amount_kopeks=pay.amount_kopeks
    )
    return subscription.expire_at, "purchase"


def _paid_text(config: Config, kind: str, until: datetime | None, *, prefix: str) -> str:
    action = "продлена" if kind == "renewal" else "активна"
    return t(
        config,
        "{@check} {prefix}Подписка {action} до {until}",
        prefix=prefix,
        action=action,
        until=_date(until),
    )


async def _notify_referrers(bot: Bot, config: Config, reward, commissions) -> None:
    if reward is not None:
        referrer, days = reward
        await _safe_send(
            bot,
            referrer.telegram_id,
            t(config, "{@gift} Ваш друг оплатил подписку — начислено {days} дн.", days=days),
        )
    for referrer, share in commissions:
        await _safe_send(
            bot,
            referrer.telegram_id,
            t(
                config,
                "{@gift} Начислена реферальная комиссия: {amount} ₽",
                amount=f"{share / 100:.2f}",
            ),
        )


async def _safe_send(bot: Bot, chat_id: int, text: str) -> None:
    # Реферер мог заблокировать бота — это не повод показывать покупателю ошибку.
    try:
        await bot.send_message(chat_id, text)
    except Exception as exc:  # noqa: BLE001
        log.warning("Не удалось уведомить %s: %s", chat_id, exc)


async def _pay_from_wallet(
    callback: CallbackQuery, config: Config, bot: Bot, purpose: str, target: str
) -> None:
    async with session() as db:
        user = await subs.get_or_create_user(db, callback.from_user.id)
        pay = await _resolve_pay_target(db, config, user, purpose, target)
        if pay is None or pay.plan is None:
            await callback.answer("Из баланса можно оплатить только тариф", show_alert=True)
            return
        try:
            if pay.amount_kopeks > 0:
                await wallet.debit(
                    db,
                    user,
                    pay.amount_kopeks,
                    WalletTxType.PURCHASE,
                    description=pay.description,
                )
            until, kind = await _apply_plan(db, config, user, pay, source="wallet")
        except wallet.InsufficientFunds as exc:
            await callback.answer(str(exc), show_alert=True)
            return
        except RemnawaveError as exc:
            # Откатываем и списание: деньги не должны уйти без подписки.
            await db.rollback()
            log.error("Не удалось выдать тариф с баланса: %s", exc)
            await callback.answer(
                "Не удалось выдать подписку, деньги не списаны. Попробуйте позже.",
                show_alert=True,
            )
            return

        reward = await subs.apply_referral_reward(db, config, user)
        commissions = await subs.after_paid_purchase(
            db,
            config,
            user,
            pay.amount_kopeks,
            plan_title=pay.plan.title,
            kind=kind,
            method="wallet",
            discount_percent=pay.discount_percent,
        )

    # Отвечаем после выхода из session() — транзакция уже зафиксирована.
    prefix = "Оплачено с баланса. " if pay.amount_kopeks > 0 else ""
    await _edit(
        callback.message, _paid_text(config, kind, until, prefix=prefix), keyboards.back_to_menu()
    )
    await callback.answer()
    await _notify_referrers(bot, config, reward, commissions)


@router.callback_query(F.data.startswith("pay:"))
async def cb_pay(callback: CallbackQuery, config: Config, bot: Bot) -> None:
    _, purpose, provider_name, target = callback.data.split(":", 3)

    if provider_name == "wallet":
        await _pay_from_wallet(callback, config, bot, purpose, target)
        return

    async with session() as db:
        user = await subs.get_or_create_user(db, callback.from_user.id)
        pay = await _resolve_pay_target(db, config, user, purpose, target)
        if pay is None:
            await callback.answer("Тариф или ключ недоступен", show_alert=True)
            return

        if provider_name == "stars":
            amount_stars = stars.rub_to_stars(pay.amount_kopeks / 100)
            # Сумму кладём в payload: при оплате тариф выдаётся по цене,
            # которую клиент видел в счёте, даже если скидка уже закончилась.
            payload = json.dumps({"purpose": purpose, "target": target, "amount": pay.amount_kopeks})
            await bot.send_invoice(
                chat_id=callback.from_user.id,
                title=pay.description[:32],
                description=pay.description,
                payload=payload,
                currency="XTR",
                prices=[LabeledPrice(label=pay.description[:32], amount=amount_stars)],
            )
            try:
                await callback.message.delete()
            except TelegramBadRequest:
                pass
            await callback.answer()
            return

        try:
            provider = PaymentProvider(provider_name)
            payment, pay_url = await payment_flow.create_external_payment(
                db,
                config,
                user,
                purpose=PaymentPurpose.TOPUP if purpose == "topup" else PaymentPurpose.PLAN,
                amount_kopeks=pay.amount_kopeks,
                provider=provider,
                plan_id=pay.plan.id if pay.plan else None,
                subscription_id=pay.subscription.id if pay.subscription else None,
                description=pay.description,
            )
        except payment_flow.PaymentFlowError as exc:
            await callback.answer(str(exc), show_alert=True)
            return
        except Exception:  # noqa: BLE001
            log.exception("Не удалось создать счёт %s", provider_name)
            await db.rollback()
            await callback.answer(
                "Платёжная система не ответила, попробуйте другой способ", show_alert=True
            )
            return

    if pay_url:
        from aiogram.utils.keyboard import InlineKeyboardBuilder

        builder = InlineKeyboardBuilder()
        builder.button(text="Оплатить", url=pay_url)
        builder.button(text="🔄 Проверить оплату", callback_data=f"checkpay:{payment.id}")
        builder.button(text="‹ Назад", callback_data="menu")
        builder.adjust(1)
        await _edit(
            callback.message, t(config, "{@card} Ссылка на оплату готова:"), builder.as_markup()
        )
    else:
        await _edit(
            callback.message,
            t(config, "{@warning} Не удалось создать счёт, попробуйте позже"),
            keyboards.back_to_menu(),
        )
    await callback.answer()


@router.callback_query(F.data.startswith("checkpay:"))
async def cb_check_payment(callback: CallbackQuery, config: Config) -> None:
    payment_id = int(callback.data.split(":", 1)[1])
    async with session() as db:
        user = await subs.get_or_create_user(db, callback.from_user.id)
        payment = await db.get(Payment, payment_id)
        if payment is None or payment.user_id != user.id:
            await callback.answer("Платёж не найден", show_alert=True)
            return
    try:
        paid = await payment_check.check_and_apply(payment_id)
    except Exception:  # noqa: BLE001
        await callback.answer("Не удалось проверить оплату, попробуйте позже", show_alert=True)
        return

    if paid:
        await callback.answer("Оплата подтверждена!", show_alert=True)
        await _edit(
            callback.message,
            t(config, "{@check} Оплата подтверждена"),
            keyboards.back_to_menu(),
        )
    else:
        await callback.answer("Оплата пока не поступила, попробуйте чуть позже", show_alert=True)


@router.pre_checkout_query()
async def on_pre_checkout(pre_checkout_query: PreCheckoutQuery) -> None:
    try:
        payload = json.loads(pre_checkout_query.invoice_payload)
        ok = payload.get("purpose") in ("topup", "plan", "renew")
    except (ValueError, AttributeError):
        ok = False
    if ok:
        await pre_checkout_query.answer(ok=True)
    else:
        await pre_checkout_query.answer(ok=False, error_message="Счёт устарел, создайте новый")


async def _plan_for_paid_invoice(db, config: Config, user: BotUser, plan_id: int):
    """Тариф для уже ОПЛАЧЕННОГО счёта Stars: даже если его успели выключить
    в панели, клиент должен получить то, за что заплатил."""
    plan = await pricing.find_plan(db, config, user, plan_id)
    if plan is not None:
        return plan
    row = await db.get(PlanRow, plan_id)
    if row is None or row.owner_user_id not in (None, user.id):
        return None
    return plan_view(row)


@router.message(F.successful_payment)
async def on_successful_payment(message: Message, config: Config) -> None:
    payload = json.loads(message.successful_payment.invoice_payload)
    amount_rub = message.successful_payment.total_amount * stars.RUB_PER_STAR

    async with session() as db:
        user = await subs.get_or_create_user(db, message.from_user.id)

        if payload["purpose"] == "topup":
            await wallet.credit(
                db,
                user,
                round(amount_rub * 100),
                WalletTxType.TOPUP,
                description="Telegram Stars",
            )
            await message.answer(
                t(config, "{@check} Баланс пополнен на {amount} ₽", amount=f"{amount_rub:.2f}")
            )
            return

        pay = await _resolve_pay_target(db, config, user, payload["purpose"], payload["target"])
        if pay is None or pay.plan is None:
            # Тариф выключили после выставления счёта — ищем его напрямую.
            plan_id = int(payload["target"].rpartition("-")[2])
            plan = await _plan_for_paid_invoice(db, config, user, plan_id)
            if plan is None:
                log.error("Stars: оплачен недоступный тариф %s, tg=%s", plan_id, user.telegram_id)
                # Деньги не теряем — зачисляем на баланс.
                await wallet.credit(
                    db, user, round(amount_rub * 100), WalletTxType.REFUND,
                    description="Stars: тариф недоступен",
                )
                await message.answer(t(config, texts.ERROR_GENERIC) + "\nСумма зачислена на баланс.")
                return
            pay = PayTarget(plan, None, plan.price_kopeks, plan.title)
            if not config.allow_multiple_subscriptions and await subs.has_subscription(db, user):
                pay.subscription = await subs.renew_target(db, user)
        if "amount" in payload:
            pay.amount_kopeks = int(payload["amount"])

        until, kind = await _apply_plan(db, config, user, pay, source="stars")
        reward = await subs.apply_referral_reward(db, config, user)
        commissions = await subs.after_paid_purchase(
            db,
            config,
            user,
            pay.amount_kopeks,
            plan_title=pay.plan.title,
            kind=kind,
            method="stars",
            discount_percent=pay.discount_percent,
        )

    await message.answer(_paid_text(config, kind, until, prefix="Оплата получена. "))
    await _notify_referrers(message.bot, config, reward, commissions)


# ── Промокоды ─────────────────────────────────────────────────────────
@router.callback_query(F.data == "promo")
async def cb_promo(callback: CallbackQuery, state: FSMContext, config: Config) -> None:
    await state.set_state(UserStates.entering_promo_code)
    await _edit(callback.message, "Введите промокод:", keyboards.back_to_menu())
    await callback.answer()


@router.message(StateFilter(UserStates.entering_promo_code))
async def on_promo_code(message: Message, state: FSMContext, config: Config) -> None:
    await state.clear()
    code = (message.text or "").strip()

    async with session() as db:
        user = await subs.get_or_create_user(db, message.from_user.id)
        try:
            code_row = await promo_service.find(db, code)
            await promo_service.redeem(db, code_row, user)
        except promo_service.PromoError as exc:
            await message.answer(t(config, "{@cross} {reason}", reason=html.escape(str(exc))))
            return

        if code_row.discount_percent > 0:
            # Раньше скидка только обещалась в тексте, но нигде не
            # применялась. Теперь запоминаем её до следующей оплаты тарифа.
            user.pending_discount_percent = max(
                user.pending_discount_percent or 0, code_row.discount_percent
            )

        try:
            if code_row.bonus_days > 0:
                user = await subs.grant_bonus_days(
                    db, config, user, code_row.bonus_days, source="promo"
                )
        except RemnawaveError as exc:
            await db.rollback()
            log.error("Не удалось начислить дни по промокоду: %s", exc)
            await message.answer(t(config, texts.ERROR_GENERIC))
            return

    text = t(config, "{@check} Промокод активирован!")
    if code_row.bonus_days > 0:
        text += f"\nНачислено дней: {code_row.bonus_days}"
    if code_row.discount_percent > 0:
        text += f"\nСкидка {code_row.discount_percent}% применится к следующей оплате тарифа."
    await message.answer(text, reply_markup=keyboards.back_to_menu())


# ── Реферальная программа ─────────────────────────────────────────────
@router.callback_query(F.data == "referral")
async def cb_referral(callback: CallbackQuery, config: Config, bot: Bot) -> None:
    if not config.referral_enabled:
        await callback.answer("Реферальная программа отключена", show_alert=True)
        return

    async with session() as db:
        user = await subs.get_or_create_user(db, callback.from_user.id)
        code = await referral.ensure_code(db, user)
        invited = await db.scalar(
            select(func.count())
            .select_from(BotUser)
            .where(BotUser.referred_by_id == user.id)
        )

    me = await bot.get_me()
    link = f"https://t.me/{me.username}?start=ref_{code}"
    text = t(
        config,
        "{@star} <b>Пригласите друзей</b>\n\n"
        "За каждого друга, который оплатит подписку, вам начислят {reward} дн.\n"
        "Друг получит {bonus} дн. бонусом к триалу.\n\n"
        "Ваша ссылка:\n<code>{link}</code>\n\n"
        "Приглашено: {invited}",
        reward=config.referral_reward_days,
        bonus=config.referral_bonus_days,
        link=link,
        invited=invited or 0,
    )
    await callback.message.edit_text(text, reply_markup=keyboards.referral_menu())
    await callback.answer()


# ── Устройства ────────────────────────────────────────────────────────
# ── Мои подписки (несколько ключей на пользователя) ────────────────────
@router.callback_query(F.data == "my_subscriptions")
async def cb_my_subscriptions(callback: CallbackQuery, config: Config) -> None:
    async with session() as db:
        user = await subs.get_or_create_user(db, callback.from_user.id)
        rows = await subs.list_subscriptions(db, user)

    text = "🔑 <b>Мои подписки</b>" if rows else t(config, texts.SUBSCRIPTION_NONE)
    await callback.message.edit_text(text, reply_markup=keyboards.subscriptions_menu(rows))
    await callback.answer()


async def _get_own_subscription(db, telegram_id: int, subscription_id: int):
    user = await subs.get_or_create_user(db, telegram_id)
    subscription = await db.get(BotSubscription, subscription_id)
    if subscription is None or subscription.user_id != user.id:
        return None
    return subscription


@router.callback_query(F.data.startswith("viewsub:"))
async def cb_view_subscription(callback: CallbackQuery, config: Config) -> None:
    subscription_id = int(callback.data.split(":", 1)[1])
    async with session() as db:
        subscription = await _get_own_subscription(db, callback.from_user.id, subscription_id)

    if subscription is None:
        await callback.answer("Ключ не найден", show_alert=True)
        return

    text = (
        f"🔑 <b>{subscription.username}</b>\n\n"
        f"Действует до: <b>{_date(subscription.expire_at)}</b> "
        f"({_left(subscription.expire_at)})"
    )
    await callback.message.edit_text(
        text,
        reply_markup=keyboards.subscription_detail_menu(
            subscription_id, has_url=bool(subscription.subscription_url)
        ),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("sublink:"))
async def cb_subscription_link(callback: CallbackQuery, config: Config) -> None:
    subscription_id = int(callback.data.split(":", 1)[1])
    async with session() as db:
        subscription = await _get_own_subscription(db, callback.from_user.id, subscription_id)

    if subscription is None or not subscription.subscription_url:
        await callback.answer("Ссылка недоступна", show_alert=True)
        return

    await callback.message.edit_text(
        f"🔗 Ссылка для подключения:\n\n<code>{subscription.subscription_url}</code>",
        reply_markup=keyboards.subscription_detail_menu(subscription_id, has_url=True),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("renewsub:"))
async def cb_renew_subscription(callback: CallbackQuery, config: Config) -> None:
    subscription_id = int(callback.data.split(":", 1)[1])
    async with session() as db:
        subscription = await _get_own_subscription(db, callback.from_user.id, subscription_id)
    if subscription is None:
        await callback.answer("Ключ не найден", show_alert=True)
        return
    await _start_renew(callback, config, subscription_id)


@router.callback_query(F.data.startswith("renewbuy-"))
async def cb_renew_pick_plan(callback: CallbackQuery, config: Config) -> None:
    prefix, plan_id_str = callback.data.split(":", 1)
    subscription_id = int(prefix.removeprefix("renewbuy-"))
    await _show_checkout(
        callback,
        config,
        int(plan_id_str),
        purpose="renew",
        target_prefix=f"{subscription_id}-",
    )


@router.callback_query(F.data.startswith("subdevices:"))
async def cb_subscription_devices(callback: CallbackQuery, config: Config) -> None:
    subscription_id = int(callback.data.split(":", 1)[1])
    async with session() as db:
        subscription = await _get_own_subscription(db, callback.from_user.id, subscription_id)
    if subscription is None:
        await callback.answer("Ключ не найден", show_alert=True)
        return

    try:
        client = subs.client_for(config)
        try:
            devices = await client.get_devices(subscription.remnawave_id)
        finally:
            await client.aclose()
    except RemnawaveError:
        await callback.answer("Не удалось получить список", show_alert=True)
        return

    if devices:
        lines = [
            f"• {d.device_model or d.platform or 'устройство'}"
            f"{f' ({d.platform})' if d.device_model and d.platform else ''}"
            for d in devices
        ]
        text = t(config, texts.DEVICES_HEADER) + "\n".join(lines)
    else:
        text = t(config, texts.DEVICES_EMPTY)

    await callback.message.edit_text(
        text,
        reply_markup=keyboards.devices_menu(devices, bool(devices), subscription_id=subscription_id),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("devicesreset:"))
async def cb_subscription_devices_reset(callback: CallbackQuery, config: Config) -> None:
    subscription_id = int(callback.data.split(":", 1)[1])
    async with session() as db:
        subscription = await _get_own_subscription(db, callback.from_user.id, subscription_id)
    if subscription is None:
        await callback.answer("Ключ не найден", show_alert=True)
        return

    try:
        client = subs.client_for(config)
        try:
            await client.delete_all_devices(subscription.remnawave_id)
        finally:
            await client.aclose()
    except RemnawaveError:
        await callback.answer("Не удалось сбросить", show_alert=True)
        return

    await callback.answer("Устройства отвязаны", show_alert=True)
    await cb_subscription_devices(callback, config)


# ── Профиль ───────────────────────────────────────────────────────────
@router.callback_query(F.data == "profile")
async def cb_profile(callback: CallbackQuery, config: Config) -> None:
    async with session() as db:
        user = await subs.get_or_create_user(db, callback.from_user.id)
        w = await wallet.get_or_create(db, user)
        balance = w.balance_kopeks
        has_subscription = await subs.has_subscription(db, user)
        discount = user.pending_discount_percent

    text = f"👤 <b>Ваш профиль</b>\n\nID: <code>{callback.from_user.id}</code>"
    if config.shows("balance"):
        text += f"\n💰 Баланс: <b>{balance / 100:.2f} ₽</b>"
    if discount:
        text += f"\n🎟 Скидка по промокоду: <b>{discount}%</b> на следующую оплату"
    await _edit(
        callback.message,
        text,
        keyboards.profile_menu(config, has_subscription=has_subscription),
    )
    await callback.answer()


@router.callback_query(F.data == "wallet_topup")
async def cb_wallet_topup(callback: CallbackQuery, config: Config) -> None:
    await _edit(
        callback.message, t(config, "{@card} Выберите сумму пополнения:"), keyboards.wallet_menu()
    )
    await callback.answer()


_SOURCE_LABEL = {
    "trial": "пробный период",
    "renewal": "продление",
    "auto_renewal": "автопродление",
    "wallet": "оплата с баланса",
    "stars": "Telegram Stars",
    "platega": "СБП / карта",
    "rollypay": "СБП",
    "cryptobot": "криптовалюта",
    "promo": "промокод",
    "referral_reward": "бонус за друга",
    "admin": "выдано администратором",
}


@router.callback_query(F.data == "purchase_history")
async def cb_purchase_history(callback: CallbackQuery, config: Config) -> None:
    async with session() as db:
        user = await subs.get_or_create_user(db, callback.from_user.id)
        rows = await db.scalars(
            select(Purchase)
            .where(Purchase.user_id == user.id)
            .order_by(Purchase.created_at.desc())
            .limit(15)
        )
        rows = list(rows)

    if not rows:
        text = "🧾 <b>История покупок</b>\n\nПока пусто."
    else:
        lines = [
            f"• {p.created_at.strftime('%d.%m.%Y')} — "
            f"{_SOURCE_LABEL.get(p.source, p.source)} — {p.days} дн."
            + (f" — {p.amount_kopeks / 100:.2f} ₽" if p.amount_kopeks else "")
            for p in rows
        ]
        text = "🧾 <b>История покупок</b>\n\n" + "\n".join(lines)

    await _edit(callback.message, text, keyboards.back_to_menu())
    await callback.answer()


# ── Блокировка бота пользователем ─────────────────────────────────────
@router.my_chat_member()
async def on_block(event: ChatMemberUpdated) -> None:
    """Отмечаем тех, кто заблокировал бота, чтобы не тратить на них рассылку."""
    if event.chat.type != "private":
        return
    blocked = event.new_chat_member.status == "kicked"
    async with session() as db:
        user = await subs.get_or_create_user(db, event.from_user.id)
        user.has_stopped_bot = blocked


__all__ = ["build_dispatcher", "router", "RemnawaveClient"]
