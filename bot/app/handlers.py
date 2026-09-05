"""Хендлеры бота.

Один модуль на весь диалог: тексты вынесены в texts.py, клавиатуры в
keyboards.py, выдача подписок в services/subscriptions.py, применение
оплат — в services/payment_processor.py.
"""

import json
import logging
from datetime import UTC, datetime
from uuid import uuid4

from aiogram import Bot, Dispatcher, F, Router
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.filters import Command, CommandStart, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import BaseStorage
from aiogram.types import (
    CallbackQuery,
    ChatMemberUpdated,
    ErrorEvent,
    LabeledPrice,
    Message,
    PreCheckoutQuery,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy import func, select

from app import keyboards, texts
from app.config import Config, PlanView, discounted_kopeks, format_rub
from app.services import (
    payment_check,
    payment_flow,
    payment_processor,
    promo as promo_service,
    referral,
    stars,
)
from app.services import subscriptions as subs
from app.services import wallet
from app.states import UserStates
from app.ui import safe_edit
from shared.db.models import (
    BotSubscription,
    BotUser,
    Payment,
    PaymentProvider,
    PaymentPurpose,
    PaymentStatus,
    Plan,
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
    # в глобальные переменные. Супервизор подменяет его при reload.
    dispatcher["config"] = config
    dispatcher.include_router(admin.router)
    dispatcher.include_router(router)
    dispatcher.errors.register(on_error)
    return dispatcher


async def on_error(event: ErrorEvent) -> bool:
    """Общий обработчик: не роняем апдейт трейсбеком на типовых ошибках
    Telegram, а пользователю снимаем «часики» с кнопки."""
    exc = event.exception
    update = event.update
    callback = update.callback_query
    message = update.message

    if isinstance(exc, TelegramForbiddenError):
        telegram_id = (callback or message).from_user.id if (callback or message) else None
        if telegram_id is not None:
            async with session() as db:
                user = await db.scalar(select(BotUser).where(BotUser.telegram_id == telegram_id))
                if user is not None:
                    user.has_stopped_bot = True
        return True

    if isinstance(exc, TelegramBadRequest) and "message is not modified" in str(exc):
        if callback is not None:
            try:
                await callback.answer()
            except TelegramBadRequest:
                pass
        return True

    log.exception("Ошибка в обработчике апдейта %s", update.update_id, exc_info=exc)
    if callback is not None:
        try:
            await callback.answer("Что-то пошло не так, попробуйте ещё раз", show_alert=True)
        except TelegramBadRequest:
            pass
    return True


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


async def _user_discount(db, telegram_id: int) -> int:
    user = await subs.get_or_create_user(db, telegram_id)
    return user.discount_percent or 0


async def _plan_titles(db, subscriptions: list[BotSubscription]) -> dict[int, str]:
    """id ключа → человекочитаемое имя.

    Имя берётся из снимка в самой подписке: тариф могли переименовать или
    удалить (тогда plan_id обнуляется), а ключ у клиента остаётся. «Пробный
    период» показывается только по явному признаку, иначе оплаченная
    подписка с удалённым тарифом выглядела бы пробной.
    """
    missing = {s.plan_id for s in subscriptions if s.plan_id and not s.title}
    titles: dict[int, str] = {}
    if missing:
        rows = await db.scalars(select(Plan).where(Plan.id.in_(missing)))
        titles = {p.id: p.title for p in rows}
    return {
        s.id: s.title
        or titles.get(s.plan_id)
        or ("Пробный период" if s.is_trial else "Подписка")
        for s in subscriptions
    }


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
        # Пробный период предлагается только тем, у кого ещё нет ключей.
        trial_available = await subs.trial_available(db, config, user)

    text = t(config, config.welcome_text or texts.WELCOME_DEFAULT, brand=config.brand)
    markup = keyboards.main_menu(config, trial_available=trial_available)

    if isinstance(target, CallbackQuery):
        await safe_edit(message, text, markup)
        await target.answer()
    else:
        await message.answer(text, reply_markup=markup)


@router.message(CommandStart())
async def cmd_start(message: Message, config: Config, bot: Bot, state: FSMContext) -> None:
    # Любой незавершённый ввод (промокод, сумма) сбрасывается — иначе
    # следующее сообщение пользователя ушло бы в старый сценарий.
    await state.clear()

    # Реферальная ссылка: t.me/bot?start=ref_ABC123 → payload "ref_ABC123".
    # Привязываем ДО проверки канала: после нажатия «Я подписался» payload
    # уже недоступен, и реферер терялся.
    payload = (message.text or "").partition(" ")[2].strip()
    if payload.startswith("ref_") and config.referral_visible:
        async with session() as db:
            user = await subs.get_or_create_user(
                db, message.from_user.id, username=message.from_user.username
            )
            await referral.attach_referrer(db, config, user, payload.removeprefix("ref_"))

    if not await _channel_ok(bot, config, message.from_user.id):
        await message.answer(
            t(config, texts.CHANNEL_REQUIRED),
            reply_markup=keyboards.channel_gate(config),
        )
        return

    await _show_menu(message, config)


@router.callback_query(F.data == "menu")
async def cb_menu(callback: CallbackQuery, config: Config, state: FSMContext) -> None:
    await state.clear()
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
        await safe_edit(message, text, markup)
        await target.answer()
    else:
        await message.answer(text, reply_markup=markup)


# ── Триал ─────────────────────────────────────────────────────────────
@router.callback_query(F.data == "trial")
async def cb_trial(callback: CallbackQuery, config: Config, bot: Bot) -> None:
    if not config.trial_enabled:
        await callback.answer("Пробный период отключён", show_alert=True)
        return
    if not await _channel_ok(bot, config, callback.from_user.id):
        await callback.answer("Сначала подпишитесь на канал", show_alert=True)
        return

    async with session() as db:
        user = await subs.get_or_create_user(db, callback.from_user.id)
        try:
            user = await subs.grant_trial(db, config, user)
        except subs.TrialUnavailable as exc:
            await callback.answer(str(exc), show_alert=True)
            return
        except RemnawaveError as exc:
            log.error("Не удалось выдать триал: %s", exc)
            await callback.answer(
                "Не удалось выдать доступ, попробуйте позже", show_alert=True
            )
            return
        expire_at, url = user.expire_at, user.subscription_url

    await safe_edit(
        callback.message,
        t(
            config,
            texts.TRIAL_GRANTED,
            days=_plural(config.trial_days, "день", "дня", "дней"),
            until=_date(expire_at),
            url=url or "—",
        ),
        keyboards.back_to_menu(),
    )
    await callback.answer()


# ── Тарифы ────────────────────────────────────────────────────────────
@router.callback_query(F.data == "plans")
async def cb_plans(callback: CallbackQuery, config: Config) -> None:
    if not config.plans:
        await safe_edit(callback.message, t(config, texts.NO_PLANS), keyboards.back_to_menu())
        await callback.answer()
        return

    async with session() as db:
        discount = await _user_discount(db, callback.from_user.id)

    categories = keyboards.plan_categories(config)
    if categories:
        # Больше одной категории тарифов — сперва даём выбрать категорию,
        # чтобы длинный список не сваливался в одну простыню кнопок.
        await safe_edit(
            callback.message, "Выберите категорию тарифа:", keyboards.categories_menu(config)
        )
    else:
        await safe_edit(
            callback.message,
            _plans_header(config, discount),
            keyboards.plans_menu(config, discount_percent=discount),
        )
    await callback.answer()


def _plans_header(config: Config, discount: int) -> str:
    text = t(config, texts.PLANS_HEADER)
    if discount:
        text += f"\n\nВаша скидка по промокоду: <b>{discount}%</b> — уже учтена в ценах."
    return text


@router.callback_query(F.data.startswith("plancat:"))
async def cb_plan_category(callback: CallbackQuery, config: Config) -> None:
    _, prefix, raw_category_id = callback.data.split(":", 2)
    category_id = None if raw_category_id == "0" else int(raw_category_id)

    async with session() as db:
        discount = await _user_discount(db, callback.from_user.id)

    back = "menu"
    if prefix.startswith("renewbuy-"):
        back = f"renewsub:{prefix.removeprefix('renewbuy-')}"
    await safe_edit(
        callback.message,
        _plans_header(config, discount),
        keyboards.plans_menu(
            config, prefix=prefix, category_id=category_id, discount_percent=discount, back=back
        ),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("buy:"))
async def cb_buy(callback: CallbackQuery, config: Config) -> None:
    plan = config.plan(int(callback.data.split(":", 1)[1]))
    if plan is None:
        await callback.answer("Тариф больше не доступен", show_alert=True)
        return

    if not config.any_payment_ready:
        await callback.answer(
            "Приём оплаты ещё не настроен в панели", show_alert=True
        )
        return

    async with session() as db:
        discount = await _user_discount(db, callback.from_user.id)

    await safe_edit(
        callback.message,
        _pay_header(config, plan, discount),
        keyboards.providers_menu(config, purpose="plan", target=str(plan.id)),
    )
    await callback.answer()


def _pay_header(config: Config, plan: PlanView, discount: int) -> str:
    price = discounted_kopeks(plan.price_kopeks, discount)
    line = format_rub(price)
    if price != plan.price_kopeks:
        line = f"{format_rub(price)} (скидка {discount}%, без скидки {format_rub(plan.price_kopeks)})"
    return t(
        config,
        "{@card} <b>{title}</b> — {price}\n{days}\n\nСпособ оплаты:",
        title=plan.full_title,
        price=line,
        days=_plural(plan.days, "день", "дня", "дней"),
    )


# ── Кошелёк ───────────────────────────────────────────────────────────
@router.callback_query(F.data == "wallet")
async def cb_wallet(callback: CallbackQuery, config: Config) -> None:
    async with session() as db:
        user = await subs.get_or_create_user(db, callback.from_user.id)
        w = await wallet.get_or_create(db, user)
        balance = w.balance_kopeks

    await safe_edit(
        callback.message,
        t(config, "{@card} Баланс: <b>{balance} ₽</b>", balance=f"{balance / 100:.2f}"),
        keyboards.wallet_menu(),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("topup:"))
async def cb_topup_preset(callback: CallbackQuery, config: Config) -> None:
    amount = callback.data.split(":", 1)[1]
    await _show_topup_providers(callback, config, amount)


@router.callback_query(F.data == "topup_custom")
async def cb_topup_custom(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(UserStates.entering_topup_amount)
    await safe_edit(
        callback.message,
        "Введите сумму пополнения в рублях (от 10 до 100000):",
        keyboards.back_to_menu(),
    )
    await callback.answer()


@router.message(StateFilter(UserStates.entering_topup_amount))
async def on_topup_amount(message: Message, state: FSMContext, config: Config) -> None:
    raw = (message.text or "").strip().replace(",", ".")
    try:
        amount = round(float(raw), 2)
    except ValueError:
        amount = -1
    if not (10 <= amount <= 100_000):
        await message.answer("Сумма должна быть от 10 до 100000 ₽. Попробуйте ещё раз:")
        return

    await state.clear()
    target = f"{amount:.2f}".rstrip("0").rstrip(".")
    await message.answer(
        f"Пополнение на {target} ₽. Способ оплаты:",
        reply_markup=keyboards.providers_menu(config, purpose="topup", target=target),
    )


async def _show_topup_providers(callback: CallbackQuery, config: Config, amount: str) -> None:
    await safe_edit(
        callback.message,
        f"Пополнение на {amount} ₽. Способ оплаты:",
        keyboards.providers_menu(config, purpose="topup", target=amount),
    )
    await callback.answer()


# ── Оплата ────────────────────────────────────────────────────────────
async def _resolve_pay_target(
    db, config: Config, user: BotUser, purpose: str, target: str
):
    """Разбирает `target` из callback_data в (plan, subscription|None, сумма, описание).

    purpose == "plan"  → target = "{plan_id}" — покупка НОВОГО ключа.
    purpose == "renew" → target = "{subscription_id}-{plan_id}" — продление
    конкретного существующего ключа (проверяем, что он принадлежит user).
    purpose == "topup" → target = сумма в рублях, тариф/ключ не участвуют.

    Сумма для тарифа — уже со скидкой пользователя (промокод).
    """
    if purpose == "topup":
        return None, None, round(float(target) * 100), f"Пополнение баланса на {target} ₽"

    if purpose == "renew":
        sub_id_str, _, plan_id_str = target.partition("-")
        plan = config.plan(int(plan_id_str))
        if plan is None:
            return None, None, 0, ""
        subscription = await db.get(BotSubscription, int(sub_id_str))
        if subscription is None or subscription.user_id != user.id:
            return None, None, 0, ""
        amount = discounted_kopeks(plan.price_kopeks, user.discount_percent)
        return plan, subscription, amount, f"Продление «{plan.full_title}»"

    plan = config.plan(int(target))
    if plan is None:
        return None, None, 0, ""
    amount = discounted_kopeks(plan.price_kopeks, user.discount_percent)
    return plan, None, amount, f"Оплата тарифа «{plan.full_title}»"


@router.callback_query(F.data.startswith("pay:"))
async def cb_pay(callback: CallbackQuery, config: Config, bot: Bot) -> None:
    _, purpose, provider_name, target = callback.data.split(":", 3)

    if provider_name == "wallet":
        await _pay_from_wallet(callback, config, purpose, target)
        return
    if provider_name == "stars":
        await _pay_with_stars(callback, config, bot, purpose, target)
        return

    try:
        provider = PaymentProvider(provider_name)
    except ValueError:
        await callback.answer("Неизвестный способ оплаты", show_alert=True)
        return

    async with session() as db:
        user = await subs.get_or_create_user(db, callback.from_user.id)
        plan, subscription, amount_kopeks, description = await _resolve_pay_target(
            db, config, user, purpose, target
        )
        if purpose != "topup" and plan is None:
            await callback.answer("Тариф или ключ недоступен", show_alert=True)
            return

        try:
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
        payment_id = payment.id

    builder = InlineKeyboardBuilder()
    builder.button(text="Оплатить", url=pay_url)
    builder.button(text="🔄 Проверить оплату", callback_data=f"checkpay:{payment_id}")
    builder.button(text="‹ Назад", callback_data="menu")
    builder.adjust(1)
    await safe_edit(
        callback.message,
        t(
            config,
            "{@card} <b>{description}</b>\nК оплате: <b>{amount}</b>\n\n"
            "Нажмите «Оплатить». После оплаты доступ выдаётся автоматически, "
            "если этого не произошло — нажмите «Проверить оплату».",
            description=description,
            amount=format_rub(amount_kopeks),
        ),
        builder.as_markup(),
    )
    await callback.answer()


async def _pay_from_wallet(
    callback: CallbackQuery, config: Config, purpose: str, target: str
) -> None:
    """Списание с баланса и выдача — одна транзакция (сбой Remnawave вернёт
    деньги). Реферальные начисления и уведомления — отдельно, после коммита."""
    async with session() as db:
        user = await subs.get_or_create_user(db, callback.from_user.id)
        plan, subscription, amount_kopeks, description = await _resolve_pay_target(
            db, config, user, purpose, target
        )
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

        try:
            if subscription is not None:
                subscription = await subs.extend_subscription(
                    db, config, subscription, plan, amount_kopeks=amount_kopeks
                )
            else:
                subscription = await subs.create_subscription(
                    db, config, user, plan, source="wallet", amount_kopeks=amount_kopeks
                )
        except RemnawaveError as exc:
            log.error("Оплата с баланса: Remnawave недоступна: %s", exc)
            # Исключение откатит списание вместе с сессией.
            await callback.answer(
                "Не удалось выдать доступ, деньги не списаны. Попробуйте позже.",
                show_alert=True,
            )
            raise
        user.discount_percent = 0
        until = subscription.expire_at
        user_id = user.id
        plan_title = plan.full_title

    await safe_edit(
        callback.message,
        t(
            config,
            "{@check} Оплачено с баланса. Подписка «{title}» действует до {until}",
            title=plan.title,
            until=_date(until),
        ),
        keyboards.back_to_menu(),
    )
    await callback.answer()
    await payment_processor.after_purchase_effects(config, user_id, amount_kopeks, plan_title)


async def _pay_with_stars(
    callback: CallbackQuery, config: Config, bot: Bot, purpose: str, target: str
) -> None:
    """Счёт в Stars регистрируется как Payment ДО выставления: если после
    оплаты выдача не удастся (Remnawave лежит), воркер догонки применит её
    позже — деньги, списанные Telegram, не потеряются."""
    if not config.stars_enabled:
        await callback.answer("Оплата Stars отключена", show_alert=True)
        return

    async with session() as db:
        user = await subs.get_or_create_user(db, callback.from_user.id)
        plan, subscription, amount_kopeks, description = await _resolve_pay_target(
            db, config, user, purpose, target
        )
        if purpose != "topup" and plan is None:
            await callback.answer("Тариф или ключ недоступен", show_alert=True)
            return
        payment = Payment(
            user_id=user.id,
            provider=PaymentProvider.STARS,
            external_id=f"stars-{uuid4()}",
            amount_kopeks=amount_kopeks,
            purpose=PaymentPurpose.TOPUP if purpose == "topup" else PaymentPurpose.PLAN,
            plan_id=plan.id if plan else None,
            subscription_id=subscription.id if subscription else None,
        )
        db.add(payment)
        await db.flush()
        payment_id = payment.id

    amount_stars = stars.rub_to_stars(amount_kopeks / 100)
    try:
        await callback.message.delete()
    except TelegramBadRequest:
        pass
    await bot.send_invoice(
        chat_id=callback.from_user.id,
        title=description[:32],
        description=f"{description} — {format_rub(amount_kopeks)}",
        payload=str(payment_id),
        currency="XTR",
        prices=[LabeledPrice(label=description[:32], amount=amount_stars)],
    )
    await callback.answer()


@router.callback_query(F.data.startswith("checkpay:"))
async def cb_check_payment(callback: CallbackQuery, config: Config) -> None:
    payment_id = int(callback.data.split(":", 1)[1])
    try:
        paid = await payment_check.check_and_apply(payment_id)
    except Exception:  # noqa: BLE001
        log.exception("Ручная проверка платежа %s", payment_id)
        await callback.answer("Не удалось проверить оплату, попробуйте позже", show_alert=True)
        return

    if paid:
        await callback.answer("Оплата подтверждена!", show_alert=True)
        await safe_edit(
            callback.message,
            t(config, "{@check} Оплата подтверждена, доступ выдан. Детали — в «Мои подписки»."),
            keyboards.back_to_menu(),
        )
    else:
        await callback.answer("Оплата пока не поступила, попробуйте чуть позже", show_alert=True)


@router.pre_checkout_query()
async def on_pre_checkout(pre_checkout_query: PreCheckoutQuery) -> None:
    await pre_checkout_query.answer(ok=True)


@router.message(F.successful_payment)
async def on_successful_payment(message: Message, config: Config) -> None:
    payload = message.successful_payment.invoice_payload
    charge_id = message.successful_payment.telegram_payment_charge_id

    async with session() as db:
        payment = None
        if payload.isdigit():
            payment = await db.get(Payment, int(payload))
        if payment is None:
            # Старый формат payload (JSON) — счёт выставлен до обновления.
            payment = await _legacy_stars_payment(db, config, message, payload)
        if payment is None:
            log.error("Stars: не удалось сопоставить оплату, payload=%r", payload)
            await message.answer(t(config, texts.ERROR_GENERIC))
            return
        if payment.status == PaymentStatus.PAID:
            return  # Telegram повторил апдейт
        payment.status = PaymentStatus.PAID
        payment.paid_at = datetime.now(UTC)
        payment.external_id = charge_id or payment.external_id
        payment.raw_payload = {
            "total_amount": message.successful_payment.total_amount,
            "currency": message.successful_payment.currency,
            "charge_id": charge_id,
        }
        payment_id = payment.id

    # Выдача и уведомления — тем же кодом, что и для внешних платежей.
    await payment_processor.handle(payment_id)


async def _legacy_stars_payment(db, config: Config, message: Message, payload: str):
    try:
        data = json.loads(payload)
    except ValueError:
        return None
    user = await subs.get_or_create_user(db, message.from_user.id)
    plan, subscription, amount_kopeks, _ = await _resolve_pay_target(
        db, config, user, data.get("purpose", "plan"), str(data.get("target", ""))
    )
    if data.get("purpose") == "topup":
        amount_kopeks = round(message.successful_payment.total_amount * stars.RUB_PER_STAR * 100)
    elif plan is None:
        return None
    payment = Payment(
        user_id=user.id,
        provider=PaymentProvider.STARS,
        external_id=f"stars-{uuid4()}",
        amount_kopeks=amount_kopeks,
        purpose=PaymentPurpose.TOPUP if data.get("purpose") == "topup" else PaymentPurpose.PLAN,
        plan_id=plan.id if plan else None,
        subscription_id=subscription.id if subscription else None,
    )
    db.add(payment)
    await db.flush()
    return payment


# ── Промокоды ─────────────────────────────────────────────────────────
@router.callback_query(F.data == "promo")
async def cb_promo(callback: CallbackQuery, state: FSMContext, config: Config) -> None:
    await state.set_state(UserStates.entering_promo_code)
    await safe_edit(callback.message, "Введите промокод:", keyboards.back_to_menu())
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

        bonus_days = code_row.bonus_days
        discount = code_row.discount_percent
        if discount > 0:
            # Скидка хранится на пользователе и применяется к следующей
            # оплате тарифа (см. _resolve_pay_target), после неё сгорает.
            user.discount_percent = max(user.discount_percent or 0, discount)
        if bonus_days > 0:
            try:
                user = await subs.grant_bonus_days(
                    db, config, user, bonus_days, source="promo"
                )
            except RemnawaveError as exc:
                log.error("Промокод: Remnawave недоступна: %s", exc)
                await message.answer(
                    t(config, "{@warning} Не удалось начислить дни, попробуйте позже")
                )
                raise

    text = t(config, "{@check} Промокод активирован!")
    if bonus_days > 0:
        text += f"\nНачислено дней: {bonus_days}"
    if discount > 0:
        text += f"\nСкидка {discount}% будет применена к следующей оплате тарифа."
    await message.answer(text, reply_markup=keyboards.back_to_menu())


# ── Реферальная программа ─────────────────────────────────────────────
@router.callback_query(F.data == "referral")
async def cb_referral(callback: CallbackQuery, config: Config, bot: Bot) -> None:
    if not config.referral_visible:
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

    lines = ["{@star} <b>Пригласите друзей</b>", ""]
    if config.referral_enabled:
        lines.append(
            f"За первую оплату каждого друга вам начислят {config.referral_reward_days} дн."
        )
        if config.referral_bonus_days:
            lines.append(f"Друг получит {config.referral_bonus_days} дн. бонусом к пробному периоду.")
    if config.referral_commission_enabled:
        lines.append(
            f"С каждой оплаты приглашённого — {config.referral_level1_percent}% на ваш баланс"
            + (
                f", с оплат друзей ваших друзей — {config.referral_level2_percent}%."
                if config.referral_level2_percent
                else "."
            )
        )
    lines += ["", "Ваша ссылка:", "<code>{link}</code>", "", "Приглашено: {invited}"]
    text = t(config, "\n".join(lines), link=link, invited=invited or 0)
    await safe_edit(callback.message, text, keyboards.referral_menu())
    await callback.answer()


# ── Мои подписки (несколько ключей на пользователя) ────────────────────
@router.callback_query(F.data == "my_subscriptions")
async def cb_my_subscriptions(callback: CallbackQuery, config: Config) -> None:
    async with session() as db:
        user = await subs.get_or_create_user(db, callback.from_user.id)
        rows = await subs.list_subscriptions(db, user)
        titles = await _plan_titles(db, rows)

    text = "🔑 <b>Мои подписки</b>" if rows else t(config, texts.SUBSCRIPTION_NONE)
    await safe_edit(callback.message, text, keyboards.subscriptions_menu(rows, titles))
    await callback.answer()


async def _get_own_subscription(db, telegram_id: int, subscription_id: int):
    user = await subs.get_or_create_user(db, telegram_id)
    subscription = await db.get(BotSubscription, subscription_id)
    if subscription is None or subscription.user_id != user.id:
        return None
    return subscription


async def _render_subscription(callback: CallbackQuery, config: Config, subscription_id: int) -> None:
    async with session() as db:
        subscription = await _get_own_subscription(db, callback.from_user.id, subscription_id)
        if subscription is None:
            await callback.answer("Ключ не найден", show_alert=True)
            return
        title = (await _plan_titles(db, [subscription]))[subscription.id]
        plan_row = await db.get(Plan, subscription.plan_id) if subscription.plan_id else None

    lines = [
        f"🔑 <b>{title}</b>",
        "",
        f"Действует до: <b>{_date(subscription.expire_at)}</b> ({_left(subscription.expire_at)})",
    ]
    if plan_row is not None:
        lines.append(f"Устройств: до {plan_row.hwid_limit}")
        lines.append(
            "Автопродление с баланса: <b>{}</b>".format(
                "включено" if subscription.auto_renew else "выключено"
            )
        )
    auto_renew = subscription.auto_renew if plan_row is not None else None
    await safe_edit(
        callback.message,
        "\n".join(lines),
        keyboards.subscription_detail_menu(
            subscription_id, has_url=bool(subscription.subscription_url), auto_renew=auto_renew
        ),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("viewsub:"))
async def cb_view_subscription(callback: CallbackQuery, config: Config) -> None:
    await _render_subscription(callback, config, int(callback.data.split(":", 1)[1]))


@router.callback_query(F.data.startswith("subautorenew:"))
async def cb_toggle_auto_renew(callback: CallbackQuery, config: Config) -> None:
    subscription_id = int(callback.data.split(":", 1)[1])
    async with session() as db:
        subscription = await _get_own_subscription(db, callback.from_user.id, subscription_id)
        if subscription is None:
            await callback.answer("Ключ не найден", show_alert=True)
            return
        if subscription.plan_id is None:
            await callback.answer("У этого ключа нет тарифа для автопродления", show_alert=True)
            return
        subscription.auto_renew = not subscription.auto_renew
        enabled = subscription.auto_renew

    await callback.answer(
        "Автопродление включено: за сутки до окончания списывается цена тарифа с баланса"
        if enabled
        else "Автопродление выключено",
        show_alert=enabled,
    )
    await _render_subscription(callback, config, subscription_id)


@router.callback_query(F.data.startswith("sublink:"))
async def cb_subscription_link(callback: CallbackQuery, config: Config) -> None:
    subscription_id = int(callback.data.split(":", 1)[1])
    async with session() as db:
        subscription = await _get_own_subscription(db, callback.from_user.id, subscription_id)

    if subscription is None or not subscription.subscription_url:
        await callback.answer("Ссылка недоступна", show_alert=True)
        return

    await safe_edit(
        callback.message,
        f"🔗 Ссылка для подключения:\n\n<code>{subscription.subscription_url}</code>",
        keyboards.subscription_detail_menu(
            subscription_id,
            has_url=True,
            auto_renew=subscription.auto_renew if subscription.plan_id else None,
        ),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("renewsub:"))
async def cb_renew_subscription(callback: CallbackQuery, config: Config) -> None:
    subscription_id = int(callback.data.split(":", 1)[1])
    async with session() as db:
        subscription = await _get_own_subscription(db, callback.from_user.id, subscription_id)
        discount = await _user_discount(db, callback.from_user.id)
    if subscription is None:
        await callback.answer("Ключ не найден", show_alert=True)
        return

    if not config.plans:
        await safe_edit(callback.message, t(config, texts.NO_PLANS), keyboards.back_to_menu())
        await callback.answer()
        return

    prefix = f"renewbuy-{subscription_id}"
    back = f"viewsub:{subscription_id}"
    categories = keyboards.plan_categories(config)
    current_plan = config.plan(subscription.plan_id) if subscription.plan_id else None
    if categories and current_plan is not None and any(
        p.category_id == current_plan.category_id for p in config.plans
    ):
        # Продление — в рамках категории ключа: иначе продление тарифа «DE»
        # тарифом «VPN» молча переключило бы сквады, и клиент потерял бы
        # доступ к тому, за что платил.
        await safe_edit(
            callback.message,
            _plans_header(config, discount),
            keyboards.plans_menu(
                config,
                prefix=prefix,
                category_id=current_plan.category_id,
                discount_percent=discount,
                back=back,
            ),
        )
    elif categories:
        await safe_edit(
            callback.message,
            "Выберите категорию тарифа:",
            keyboards.categories_menu(config, prefix=prefix, back=back),
        )
    else:
        await safe_edit(
            callback.message,
            _plans_header(config, discount),
            keyboards.plans_menu(config, prefix=prefix, discount_percent=discount, back=back),
        )
    await callback.answer()


@router.callback_query(F.data.startswith("renewbuy-"))
async def cb_renew_pick_plan(callback: CallbackQuery, config: Config) -> None:
    prefix, plan_id_str = callback.data.split(":", 1)
    subscription_id = int(prefix.removeprefix("renewbuy-"))
    plan = config.plan(int(plan_id_str))
    if plan is None:
        await callback.answer("Тариф больше не доступен", show_alert=True)
        return
    if not config.any_payment_ready:
        await callback.answer("Приём оплаты ещё не настроен в панели", show_alert=True)
        return

    async with session() as db:
        discount = await _user_discount(db, callback.from_user.id)

    target = f"{subscription_id}-{plan.id}"
    await safe_edit(
        callback.message,
        _pay_header(config, plan, discount),
        keyboards.providers_menu(config, purpose="renew", target=target),
    )
    await callback.answer()


async def _render_devices(callback: CallbackQuery, config: Config, subscription_id: int) -> None:
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

    await safe_edit(
        callback.message,
        text,
        keyboards.devices_menu(devices, bool(devices), subscription_id=subscription_id),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("subdevices:"))
async def cb_subscription_devices(callback: CallbackQuery, config: Config) -> None:
    await _render_devices(callback, config, int(callback.data.split(":", 1)[1]))


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
    await _render_devices(callback, config, subscription_id)


# ── Профиль ───────────────────────────────────────────────────────────
@router.callback_query(F.data == "profile")
async def cb_profile(callback: CallbackQuery, config: Config) -> None:
    async with session() as db:
        user = await subs.get_or_create_user(db, callback.from_user.id)
        w = await wallet.get_or_create(db, user)
        balance = w.balance_kopeks
        discount = user.discount_percent or 0

    text = (
        f"👤 <b>Ваш профиль</b>\n\n"
        f"ID: <code>{callback.from_user.id}</code>\n"
        f"💰 Баланс: <b>{balance / 100:.2f} ₽</b>"
    )
    if discount:
        text += f"\n🎟 Скидка на следующую оплату: <b>{discount}%</b>"
    await safe_edit(callback.message, text, keyboards.profile_menu(config))
    await callback.answer()


@router.callback_query(F.data == "wallet_topup")
async def cb_wallet_topup(callback: CallbackQuery, config: Config) -> None:
    await safe_edit(
        callback.message,
        t(config, "{@card} Выберите сумму пополнения:"),
        keyboards.wallet_menu(),
    )
    await callback.answer()


_SOURCE_LABEL = {
    "trial": "пробный период",
    "promo": "промокод",
    "referral_reward": "бонус за друга",
    "wallet": "с баланса",
    "renewal": "продление",
    "auto_renewal": "автопродление",
    "stars": "Telegram Stars",
    "platega": "СБП / карта",
    "rollypay": "СБП",
    "cryptobot": "криптовалюта",
    "manual": "выдано администратором",
}


@router.callback_query(F.data == "purchase_history")
async def cb_purchase_history(callback: CallbackQuery, config: Config) -> None:
    async with session() as db:
        user = await subs.get_or_create_user(db, callback.from_user.id)
        rows = list(
            await db.scalars(
                select(Purchase)
                .where(Purchase.user_id == user.id)
                .order_by(Purchase.created_at.desc())
                .limit(15)
            )
        )
        plan_ids = {p.plan_id for p in rows if p.plan_id and not p.plan_title}
        titles = {}
        if plan_ids:
            plans = await db.scalars(select(Plan).where(Plan.id.in_(plan_ids)))
            titles = {p.id: p.title for p in plans}

    if not rows:
        text = "🧾 <b>История покупок</b>\n\nПока пусто."
    else:
        lines = []
        for p in rows:
            what = p.plan_title or (titles.get(p.plan_id) if p.plan_id else None)
            what = what or f"+{_plural(p.days, 'день', 'дня', 'дней')}"
            how = _SOURCE_LABEL.get(p.source, p.source)
            amount = f" — {format_rub(p.amount_kopeks)}" if p.amount_kopeks else ""
            lines.append(f"• {p.created_at.strftime('%d.%m.%Y')} · {what} ({how}){amount}")
        text = "🧾 <b>История покупок</b>\n\n" + "\n".join(lines)

    await safe_edit(callback.message, text, keyboards.back_to_menu())
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
