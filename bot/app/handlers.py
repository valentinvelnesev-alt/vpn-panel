"""Хендлеры бота.

Один модуль на весь диалог — он умещается в несколько сотен строк, потому
что тексты вынесены в texts.py, клавиатуры в keyboards.py, а выдача
подписок в services/subscriptions.py. Разрастётся — резать по этим швам.
"""

import json
import logging
from datetime import UTC, datetime

from aiogram import Bot, Dispatcher, F, Router
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
from app.config import Config
from app.services import payment_flow, promo as promo_service, referral, stars
from app.services import subscriptions as subs
from app.services import wallet
from app.states import UserStates
from shared.db.models import (
    BotSubscription,
    BotUser,
    PaymentProvider,
    PaymentPurpose,
    Purchase,
    WalletTxType,
)
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
        trial_available = config.trial_enabled and not user.trial_used

    text = t(config, config.welcome_text or texts.WELCOME_DEFAULT, brand=config.brand)
    markup = keyboards.main_menu(config, trial_available=trial_available)

    if isinstance(target, CallbackQuery):
        await message.edit_text(text, reply_markup=markup)
        await target.answer()
    else:
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
@router.callback_query(F.data == "plans")
async def cb_plans(callback: CallbackQuery, config: Config) -> None:
    if not config.plans:
        await callback.message.edit_text(
            t(config, texts.NO_PLANS), reply_markup=keyboards.back_to_menu()
        )
        await callback.answer()
        return

    categories = keyboards.plan_categories(config)
    if categories:
        # Больше одной категории тарифов — сперва даём выбрать категорию,
        # чтобы длинный список не сваливался в одну простыню кнопок.
        await callback.message.edit_text(
            "Выберите категорию тарифа:", reply_markup=keyboards.categories_menu(config)
        )
    else:
        await callback.message.edit_text(
            t(config, texts.PLANS_HEADER), reply_markup=keyboards.plans_menu(config)
        )
    await callback.answer()


@router.callback_query(F.data.startswith("plancat:"))
async def cb_plan_category(callback: CallbackQuery, config: Config) -> None:
    _, prefix, raw_category_id = callback.data.split(":", 2)
    category_id = None if raw_category_id == "0" else int(raw_category_id)

    await callback.message.edit_text(
        t(config, texts.PLANS_HEADER),
        reply_markup=keyboards.plans_menu(config, prefix=prefix, category_id=category_id),
    )
    await callback.answer()


def _any_provider_enabled(config: Config) -> bool:
    return (
        config.platega_enabled
        or config.rollypay_enabled
        or config.cryptobot_enabled
        or config.stars_enabled
    )


@router.callback_query(F.data.startswith("buy:"))
async def cb_buy(callback: CallbackQuery, config: Config) -> None:
    plan_id = int(callback.data.split(":", 1)[1])
    plan = next((p for p in config.plans if p.id == plan_id), None)
    if plan is None:
        await callback.answer("Тариф больше не доступен", show_alert=True)
        return

    if not _any_provider_enabled(config):
        await callback.answer(
            "Приём оплаты ещё не настроен в панели", show_alert=True
        )
        return

    price = f"{plan.price_rub:.0f} ₽".replace(".0", "")
    await callback.message.edit_text(
        t(config, "{@card} <b>{title}</b> — {price}\n\nСпособ оплаты:",
          title=plan.title, price=price),
        reply_markup=keyboards.providers_menu(config, purpose="plan", target=plan_id),
    )
    await callback.answer()


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
async def _resolve_pay_target(
    db, config: Config, user: BotUser, purpose: str, target: str
):
    """Разбирает `target` из callback_data в (plan, subscription|None, amount, описание).

    purpose == "plan"  → target = "{plan_id}" — покупка НОВОГО ключа.
    purpose == "renew" → target = "{subscription_id}-{plan_id}" — продление
    конкретного существующего ключа (проверяем, что он принадлежит user).
    purpose == "topup" → target = сумма в рублях, тариф/ключ не участвуют.
    """
    if purpose == "topup":
        return None, None, round(float(target) * 100), f"Пополнение баланса на {target} ₽"

    if purpose == "renew":
        sub_id_str, _, plan_id_str = target.partition("-")
        plan = next((p for p in config.plans if p.id == int(plan_id_str)), None)
        if plan is None:
            return None, None, 0, ""
        subscription = await db.get(BotSubscription, int(sub_id_str))
        if subscription is None or subscription.user_id != user.id:
            return None, None, 0, ""
        return plan, subscription, plan.price_kopeks, f"Продление «{plan.title}»"

    plan = next((p for p in config.plans if p.id == int(target)), None)
    if plan is None:
        return None, None, 0, ""
    return plan, None, plan.price_kopeks, f"Оплата тарифа «{plan.title}»"


@router.callback_query(F.data.startswith("pay:"))
async def cb_pay(callback: CallbackQuery, config: Config, bot: Bot) -> None:
    _, purpose, provider_name, target = callback.data.split(":", 3)

    async with session() as db:
        user = await subs.get_or_create_user(db, callback.from_user.id)
        plan, subscription, amount_kopeks, description = await _resolve_pay_target(
            db, config, user, purpose, target
        )
        if purpose != "topup" and plan is None:
            await callback.answer("Тариф или ключ недоступен", show_alert=True)
            return

        if provider_name == "wallet":
            if plan is None:
                await callback.answer(
                    "Из баланса можно оплатить только тариф", show_alert=True
                )
                return
            try:
                await wallet.debit(
                    db, user, amount_kopeks, WalletTxType.PURCHASE, description=description
                )
            except wallet.InsufficientFunds as exc:
                await callback.answer(str(exc), show_alert=True)
                return

            if subscription is not None:
                subscription = await subs.extend_subscription(db, config, subscription, plan)
                until = subscription.expire_at
            else:
                subscription = await subs.create_subscription(
                    db, config, user, plan, source="wallet"
                )
                until = subscription.expire_at

            reward = await subs.apply_referral_reward(db, config, user)
            commissions = await subs.after_paid_purchase(
                db, config, user, amount_kopeks, plan_title=plan.title
            )
            await callback.message.edit_text(
                t(
                    config,
                    "{@check} Оплачено с баланса. Подписка продлена до {until}",
                    until=_date(until),
                ),
                reply_markup=keyboards.back_to_menu(),
            )
            await callback.answer()
            if reward is not None:
                referrer, days = reward
                await bot.send_message(
                    referrer.telegram_id,
                    t(config, "{@gift} Ваш друг оплатил подписку — начислено {days} дн.", days=days),
                )
            for referrer, share in commissions:
                await bot.send_message(
                    referrer.telegram_id,
                    t(
                        config,
                        "{@gift} Начислена реферальная комиссия: {amount} ₽",
                        amount=f"{share / 100:.2f}",
                    ),
                )
            return

        if provider_name == "stars":
            amount_stars = stars.rub_to_stars(amount_kopeks / 100)
            payload = json.dumps({"purpose": purpose, "target": target})
            await callback.message.delete()
            await bot.send_invoice(
                chat_id=callback.from_user.id,
                title=description,
                description=description,
                payload=payload,
                currency="XTR",
                prices=[LabeledPrice(label=description, amount=amount_stars)],
            )
            await callback.answer()
            return

        try:
            provider = PaymentProvider(provider_name)
            payment, pay_url = await payment_flow.create_external_payment(
                db,
                config,
                user,
                purpose=PaymentPurpose.TOPUP if purpose == "topup" else PaymentPurpose.PLAN,
                amount_kopeks=amount_kopeks,
                provider=provider,
                plan_id=plan.id if plan else None,
                subscription_id=subscription.id if subscription else None,
                description=description,
            )
        except payment_flow.PaymentFlowError as exc:
            await callback.answer(str(exc), show_alert=True)
            return

    if pay_url:
        from aiogram.utils.keyboard import InlineKeyboardBuilder

        builder = InlineKeyboardBuilder()
        builder.button(text="Оплатить", url=pay_url)
        builder.button(text="‹ Назад", callback_data="menu")
        builder.adjust(1)
        await callback.message.edit_text(
            t(config, "{@card} Ссылка на оплату готова:"), reply_markup=builder.as_markup()
        )
    else:
        await callback.message.edit_text(
            t(config, "{@warning} Не удалось создать счёт, попробуйте позже"),
            reply_markup=keyboards.back_to_menu(),
        )
    await callback.answer()


@router.pre_checkout_query()
async def on_pre_checkout(pre_checkout_query: PreCheckoutQuery) -> None:
    await pre_checkout_query.answer(ok=True)


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

        plan, subscription, amount_kopeks, _ = await _resolve_pay_target(
            db, config, user, payload["purpose"], payload["target"]
        )
        if plan is None:
            await message.answer(t(config, texts.ERROR_GENERIC))
            return

        if subscription is not None:
            subscription = await subs.extend_subscription(db, config, subscription, plan)
            until = subscription.expire_at
        else:
            subscription = await subs.create_subscription(db, config, user, plan, source="stars")
            until = subscription.expire_at

        reward = await subs.apply_referral_reward(db, config, user)
        commissions = await subs.after_paid_purchase(
            db, config, user, amount_kopeks, plan_title=plan.title
        )

    await message.answer(
        t(config, "{@check} Оплата получена, подписка продлена до {until}", until=_date(until))
    )
    if reward is not None:
        referrer, days = reward
        await message.bot.send_message(
            referrer.telegram_id,
            t(config, "{@gift} Ваш друг оплатил подписку — начислено {days} дн.", days=days),
        )
    for referrer, share in commissions:
        await message.bot.send_message(
            referrer.telegram_id,
            t(config, "{@gift} Начислена реферальная комиссия: {amount} ₽", amount=f"{share / 100:.2f}"),
        )
    for referrer, share in commissions:
        await message.bot.send_message(
            referrer.telegram_id,
            t(config, "{@gift} Начислена реферальная комиссия: {amount} ₽", amount=f"{share / 100:.2f}"),
        )


# ── Промокоды ─────────────────────────────────────────────────────────
@router.callback_query(F.data == "promo")
async def cb_promo(callback: CallbackQuery, state: FSMContext, config: Config) -> None:
    await state.set_state(UserStates.entering_promo_code)
    await callback.message.edit_text(
        "Введите промокод:", reply_markup=keyboards.back_to_menu()
    )
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
            await message.answer(t(config, "{@cross} {reason}", reason=str(exc)))
            return

        if code_row.bonus_days > 0:
            user = await subs.grant_bonus_days(
                db, config, user, code_row.bonus_days, source="promo"
            )

    text = t(config, "{@check} Промокод активирован!")
    if code_row.bonus_days > 0:
        text += f"\nНачислено дней: {code_row.bonus_days}"
    if code_row.discount_percent > 0:
        text += f"\nСкидка {code_row.discount_percent}% учтётся при следующей оплате тарифа."
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

    if not config.plans:
        await callback.message.edit_text(
            t(config, texts.NO_PLANS), reply_markup=keyboards.back_to_menu()
        )
        await callback.answer()
        return

    prefix = f"renewbuy-{subscription_id}"
    categories = keyboards.plan_categories(config)
    if categories:
        await callback.message.edit_text(
            "Выберите категорию тарифа:", reply_markup=keyboards.categories_menu(config, prefix=prefix)
        )
    else:
        await callback.message.edit_text(
            t(config, texts.PLANS_HEADER), reply_markup=keyboards.plans_menu(config, prefix=prefix)
        )
    await callback.answer()


@router.callback_query(F.data.startswith("renewbuy-"))
async def cb_renew_pick_plan(callback: CallbackQuery, config: Config) -> None:
    prefix, plan_id_str = callback.data.split(":", 1)
    subscription_id = int(prefix.removeprefix("renewbuy-"))
    plan = next((p for p in config.plans if p.id == int(plan_id_str)), None)
    if plan is None:
        await callback.answer("Тариф больше не доступен", show_alert=True)
        return
    if not _any_provider_enabled(config):
        await callback.answer("Приём оплаты ещё не настроен в панели", show_alert=True)
        return

    target = f"{subscription_id}-{plan.id}"
    price = f"{plan.price_rub:.0f} ₽".replace(".0", "")
    await callback.message.edit_text(
        t(config, "{@card} <b>{title}</b> — {price}\n\nСпособ оплаты:", title=plan.title, price=price),
        reply_markup=keyboards.providers_menu(config, purpose="renew", target=target),
    )
    await callback.answer()


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

    text = (
        f"👤 <b>Ваш профиль</b>\n\n"
        f"ID: <code>{callback.from_user.id}</code>\n"
        f"💰 Баланс: <b>{balance / 100:.2f} ₽</b>"
    )
    await callback.message.edit_text(text, reply_markup=keyboards.profile_menu())
    await callback.answer()


@router.callback_query(F.data == "wallet_topup")
async def cb_wallet_topup(callback: CallbackQuery, config: Config) -> None:
    await callback.message.edit_text(
        t(config, "{@card} Выберите сумму пополнения:"), reply_markup=keyboards.wallet_menu()
    )
    await callback.answer()


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
            f"• {p.created_at.strftime('%d.%m.%Y')} — {p.source} — {p.amount_kopeks / 100:.2f} ₽"
            for p in rows
        ]
        text = "🧾 <b>История покупок</b>\n\n" + "\n".join(lines)

    await callback.message.edit_text(text, reply_markup=keyboards.back_to_menu())
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
