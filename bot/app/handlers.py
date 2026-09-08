"""Хендлеры бота.

Один модуль на весь диалог: тексты вынесены в texts.py, клавиатуры в
keyboards.py, выдача подписок в services/subscriptions.py, применение
оплат — в services/payment_processor.py.
"""

import json
import logging
from dataclasses import dataclass
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
from sqlalchemy import func, select

from app import connection, keyboards, texts
from app.config import (
    Config,
    PlanView,
    TrafficPackageView,
    discounted_kopeks,
    format_rub,
)
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
from app.ui import safe_edit as _safe_edit
from app.ui import send_screen as _send_screen
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


async def screen(
    message: Message,
    config: Config,
    text: str,
    reply_markup=None,
    **kwargs: object,
) -> None:
    """Экран бота. Если в панели загружена картинка меню, показывается она
    с подписью, иначе обычный текст (см. app/ui.py)."""
    await _safe_edit(message, text, reply_markup, photo=config.menu_photo_path, **kwargs)


async def send_new_screen(
    message: Message,
    config: Config,
    text: str,
    reply_markup=None,
    **kwargs: object,
) -> None:
    await _send_screen(message, text, reply_markup, photo=config.menu_photo_path, **kwargs)


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


def _current_subscription(rows: list[BotSubscription]) -> BotSubscription | None:
    """Ключ с самым поздним сроком — тот, что показывается в шапке меню."""
    dated = [r for r in rows if r.expire_at is not None]
    if not dated:
        return rows[0] if rows else None
    return max(dated, key=lambda r: r.expire_at.replace(tzinfo=UTC) if r.expire_at.tzinfo is None else r.expire_at)


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
def _menu_text(config: Config, *, name: str, user: BotUser, plan_title: str | None) -> str:
    """Шапка меню: кто вы, что с подпиской и до какого числа.

    Дата берётся с ключа с самым поздним сроком (см. shared/sync.py), так
    что у владельца нескольких подписок здесь всегда актуальная."""
    if config.welcome_text:
        return t(config, config.welcome_text, brand=config.brand)

    lines = [f"<b>{name}</b>", ""]
    if subs.is_active(user):
        lines.append(f"Подписка: <b>активна</b>")
        lines.append(f"До: <b>{_date(user.expire_at)}</b> ({_left(user.expire_at)})")
        if plan_title:
            lines.append(f"Тариф: <b>{plan_title}</b>")
    elif user.expire_at is not None:
        lines.append("Подписка: <b>истекла</b>")
        lines.append(f"Закончилась: <b>{_date(user.expire_at)}</b>")
    else:
        lines.append("Подписка: <b>отсутствует</b>")
    lines += ["", "Выберите действие:"]
    return "\n".join(lines)


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
        rows = await subs.list_subscriptions(db, user)
        titles = await _plan_titles(db, rows)
        wallet_row = await wallet.get_or_create(db, user)
        balance = wallet_row.balance_kopeks
        current = _current_subscription(rows)
        plan_title = titles.get(current.id) if current else None
        text = _menu_text(
            config,
            name=target.from_user.full_name or target.from_user.first_name or "Профиль",
            user=user,
            plan_title=plan_title,
        )

    markup = keyboards.main_menu(
        config,
        trial_available=trial_available,
        has_subscription=bool(rows),
        balance_kopeks=balance,
    )

    if isinstance(target, CallbackQuery):
        await screen(message, config, text, markup)
        await target.answer()
    else:
        await send_new_screen(message, config, text, markup)


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

    # Нижняя клавиатура ставится отдельным сообщением: в одном сообщении
    # Telegram не разрешает и inline-кнопки, и клавиатуру под полем ввода.
    await message.answer(
        t(config, "{@rocket} <b>{brand}</b>", brand=config.brand),
        reply_markup=keyboards.reply_menu(),
    )
    await _show_menu(message, config)


@router.message(F.text == keyboards.BTN_MENU)
async def on_menu_button(message: Message, config: Config, state: FSMContext) -> None:
    """Кнопка «Меню» работает всегда, в том числе посреди ввода промокода
    или суммы — незавершённый диалог сбрасывается."""
    await state.clear()
    await _show_menu(message, config)


@router.message(F.text == keyboards.BTN_HELP)
async def on_help_button(message: Message, config: Config, state: FSMContext) -> None:
    await state.clear()
    if config.support_url:
        await message.answer(
            t(
                config,
                "{@info} <b>Поддержка</b>\n\nНапишите нам — поможем с подключением и оплатой.",
            ),
            reply_markup=keyboards.support_menu(config),
        )
        return
    await message.answer(
        t(config, "{@info} Контакт поддержки пока не указан в панели.")
    )


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

    markup = keyboards.back_to_menu(config)
    if isinstance(target, CallbackQuery):
        await screen(message, config, text, markup)
        await target.answer()
    else:
        await send_new_screen(message, config, text, markup)


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

    await screen(
        callback.message,
        config,
        t(
            config,
            texts.TRIAL_GRANTED,
            days=_plural(config.trial_days, "день", "дня", "дней"),
            until=_date(expire_at),
            url=url or "—",
        ),
        keyboards.back_to_menu(config),
    )
    await callback.answer()


# ── Тарифы ────────────────────────────────────────────────────────────
@router.callback_query(F.data == "plans")
async def cb_plans(callback: CallbackQuery, config: Config) -> None:
    if not config.plans:
        await screen(callback.message, config, t(config, texts.NO_PLANS), keyboards.back_to_menu(config))
        await callback.answer()
        return

    async with session() as db:
        discount = await _user_discount(db, callback.from_user.id)

    categories = keyboards.plan_categories(config)
    if categories:
        # Больше одной категории тарифов — сперва даём выбрать категорию,
        # чтобы длинный список не сваливался в одну простыню кнопок.
        await screen(
            callback.message,
            config,
            t(config, "{@card} <b>Выберите тариф</b>"),
            keyboards.categories_menu(config, discount_percent=discount),
        )
    else:
        await screen(
            callback.message,
            config,
            _plans_header(config, discount),
            keyboards.plans_menu(config, discount_percent=discount),
        )
    await callback.answer()


def _plans_header(config: Config, discount: int, category_title: str | None = None) -> str:
    if category_title:
        text = t(config, "{@card} <b>{title}</b>\n\nВыберите период подписки:", title=category_title)
    else:
        text = t(config, texts.PLANS_HEADER)
    if discount:
        text += f"\n\nСкидка по промокоду <b>{discount}%</b> уже учтена в ценах."
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
    title = next(
        (p.category_title for p in config.plans if p.category_id == category_id), None
    )
    await screen(
        callback.message,
        config,
        _plans_header(config, discount, title),
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

    await screen(
        callback.message,
        config,
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

    await screen(
        callback.message,
        config,
        t(
            config,
            "{@wallet} <b>Баланс</b>\n\nДоступно: <b>{amount}</b>\n\n"
            "С баланса можно оплатить подписку и включить автоплатёж.\n"
            "Выберите сумму пополнения:",
            amount=format_rub(balance),
        ),
        keyboards.wallet_menu(config),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("topup:"))
async def cb_topup_preset(callback: CallbackQuery, config: Config) -> None:
    amount = callback.data.split(":", 1)[1]
    await _show_topup_providers(callback, config, amount)


@router.callback_query(F.data == "topup_custom")
async def cb_topup_custom(callback: CallbackQuery, config: Config, state: FSMContext) -> None:
    await state.set_state(UserStates.entering_topup_amount)
    await screen(
        callback.message,
        config,
        "Введите сумму пополнения в рублях (от 10 до 100000):",
        keyboards.back_to_menu(config),
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
    await send_new_screen(
        message,
        config,
        f"Пополнение на {target} ₽. Способ оплаты:",
        keyboards.providers_menu(config, purpose="topup", target=target),
    )


async def _show_topup_providers(callback: CallbackQuery, config: Config, amount: str) -> None:
    await screen(
        callback.message,
        config,
        f"Пополнение на {amount} ₽. Способ оплаты:",
        keyboards.providers_menu(config, purpose="topup", target=amount),
    )
    await callback.answer()


# ── Оплата ────────────────────────────────────────────────────────────
@dataclass(slots=True)
class PayTarget:
    """Что именно оплачивают. Ровно одно из plan/package заполнено, кроме
    пополнения баланса, где не заполнено ничего."""

    amount_kopeks: int
    description: str
    plan: PlanView | None = None
    package: TrafficPackageView | None = None
    subscription: BotSubscription | None = None

    @property
    def ok(self) -> bool:
        return self.amount_kopeks > 0


EMPTY_TARGET = PayTarget(amount_kopeks=0, description="")


async def _resolve_pay_target(
    db, config: Config, user: BotUser, purpose: str, target: str
) -> PayTarget:
    """Разбирает `target` из callback_data.

    purpose == "plan"    → "{plan_id}" — покупка НОВОГО ключа.
    purpose == "renew"   → "{subscription_id}-{plan_id}" — продление
    конкретного ключа (проверяем, что он принадлежит пользователю).
    purpose == "traffic" → "{subscription_id}-{package_id}" — докупка трафика.
    purpose == "topup"   → сумма в рублях.

    Цена тарифа — со скидкой пользователя; на пакеты трафика скидка по
    промокоду не распространяется, она обещана «на оплату тарифа».
    """
    if purpose == "topup":
        return PayTarget(
            amount_kopeks=round(float(target) * 100),
            description=f"Пополнение баланса на {target} ₽",
        )

    async def own_subscription(raw_id: str) -> BotSubscription | None:
        subscription = await db.get(BotSubscription, int(raw_id))
        if subscription is None or subscription.user_id != user.id:
            return None
        return subscription

    if purpose == "renew":
        sub_id_str, _, plan_id_str = target.partition("-")
        plan = config.plan(int(plan_id_str))
        subscription = await own_subscription(sub_id_str)
        if plan is None or subscription is None:
            return EMPTY_TARGET
        return PayTarget(
            amount_kopeks=discounted_kopeks(plan.price_kopeks, user.discount_percent),
            description=f"Продление «{plan.full_title}»",
            plan=plan,
            subscription=subscription,
        )

    if purpose == "traffic":
        sub_id_str, _, package_id_str = target.partition("-")
        package = config.traffic_package(int(package_id_str))
        subscription = await own_subscription(sub_id_str)
        if package is None or subscription is None:
            return EMPTY_TARGET
        return PayTarget(
            amount_kopeks=package.price_kopeks,
            description=f"Докупка трафика: +{package.traffic_gb} ГБ",
            package=package,
            subscription=subscription,
        )

    plan = config.plan(int(target))
    if plan is None:
        return EMPTY_TARGET
    return PayTarget(
        amount_kopeks=discounted_kopeks(plan.price_kopeks, user.discount_percent),
        description=f"Оплата тарифа «{plan.full_title}»",
        plan=plan,
    )


def _purpose_of(purpose: str) -> PaymentPurpose:
    if purpose == "topup":
        return PaymentPurpose.TOPUP
    if purpose == "traffic":
        return PaymentPurpose.TRAFFIC
    return PaymentPurpose.PLAN


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
        pay = await _resolve_pay_target(db, config, user, purpose, target)
        if not pay.ok:
            await callback.answer("Тариф или ключ недоступен", show_alert=True)
            return
        amount_kopeks, description = pay.amount_kopeks, pay.description

        try:
            payment, pay_url = await payment_flow.create_external_payment(
                db,
                config,
                user,
                purpose=_purpose_of(purpose),
                amount_kopeks=amount_kopeks,
                provider=provider,
                plan_id=pay.plan.id if pay.plan else None,
                subscription_id=pay.subscription.id if pay.subscription else None,
                traffic_package_id=pay.package.id if pay.package else None,
                description=description,
            )
        except payment_flow.PaymentFlowError as exc:
            await callback.answer(str(exc), show_alert=True)
            return
        payment_id = payment.id

    await screen(
        callback.message,
        config,
        t(
            config,
            "{@card} <b>{description}</b>\nК оплате: <b>{amount}</b>\n\n"
            "Нажмите «Оплатить». После оплаты доступ выдаётся автоматически, "
            "если этого не произошло — нажмите «Проверить оплату».",
            description=description,
            amount=format_rub(amount_kopeks),
        ),
        keyboards.pay_menu(config, pay_url=pay_url, payment_id=payment_id),
    )
    await callback.answer()


async def _pay_from_wallet(
    callback: CallbackQuery, config: Config, purpose: str, target: str
) -> None:
    """Списание с баланса и выдача — одна транзакция (сбой Remnawave вернёт
    деньги). Реферальные начисления и уведомления — отдельно, после коммита."""
    async with session() as db:
        user = await subs.get_or_create_user(db, callback.from_user.id)
        pay = await _resolve_pay_target(db, config, user, purpose, target)
        if not pay.ok or (pay.plan is None and pay.package is None):
            await callback.answer(
                "Из баланса можно оплатить тариф или трафик", show_alert=True
            )
            return
        amount_kopeks = pay.amount_kopeks
        try:
            await wallet.debit(
                db, user, amount_kopeks, WalletTxType.PURCHASE, description=pay.description
            )
        except wallet.InsufficientFunds as exc:
            await callback.answer(str(exc), show_alert=True)
            return

        try:
            if pay.package is not None:
                await subs.add_traffic(
                    db,
                    config,
                    pay.subscription,
                    pay.package,
                    source="wallet",
                    amount_kopeks=amount_kopeks,
                )
                done = t(
                    config,
                    "{@check} Оплачено с баланса. Добавлено {gb} ГБ трафика.",
                    gb=pay.package.traffic_gb,
                )
                label = f"+{pay.package.traffic_gb} ГБ трафика"
            elif pay.subscription is not None:
                subscription = await subs.extend_subscription(
                    db, config, pay.subscription, pay.plan, amount_kopeks=amount_kopeks
                )
                done = t(
                    config,
                    "{@check} Оплачено с баланса. Подписка «{title}» действует до {until}",
                    title=pay.plan.title,
                    until=_date(subscription.expire_at),
                )
                label = pay.plan.full_title
            else:
                subscription = await subs.create_subscription(
                    db, config, user, pay.plan, source="wallet", amount_kopeks=amount_kopeks
                )
                done = t(
                    config,
                    "{@check} Оплачено с баланса. Подписка «{title}» действует до {until}",
                    title=pay.plan.title,
                    until=_date(subscription.expire_at),
                )
                label = pay.plan.full_title
        except subs.TrafficUnavailable as exc:
            await callback.answer(str(exc), show_alert=True)
            raise  # откатывает списание вместе с сессией
        except RemnawaveError as exc:
            log.error("Оплата с баланса: Remnawave недоступна: %s", exc)
            # Исключение откатит списание вместе с сессией.
            await callback.answer(
                "Не удалось выдать доступ, деньги не списаны. Попробуйте позже.",
                show_alert=True,
            )
            raise
        if pay.plan is not None:
            user.discount_percent = 0
        user_id = user.id

    await screen(callback.message, config, done, keyboards.back_to_menu(config))
    await callback.answer()
    await payment_processor.after_purchase_effects(config, user_id, amount_kopeks, label)


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
        pay = await _resolve_pay_target(db, config, user, purpose, target)
        if not pay.ok:
            await callback.answer("Тариф или ключ недоступен", show_alert=True)
            return
        amount_kopeks, description = pay.amount_kopeks, pay.description
        payment = Payment(
            user_id=user.id,
            provider=PaymentProvider.STARS,
            external_id=f"stars-{uuid4()}",
            amount_kopeks=amount_kopeks,
            purpose=_purpose_of(purpose),
            plan_id=pay.plan.id if pay.plan else None,
            subscription_id=pay.subscription.id if pay.subscription else None,
            traffic_package_id=pay.package.id if pay.package else None,
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
        await screen(
            callback.message,
            config,
            t(config, "{@check} Оплата подтверждена, доступ выдан. Детали — в «Мои подписки»."),
            keyboards.back_to_menu(config),
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
    purpose = data.get("purpose", "plan")
    pay = await _resolve_pay_target(db, config, user, purpose, str(data.get("target", "")))
    amount_kopeks = pay.amount_kopeks
    if purpose == "topup":
        amount_kopeks = round(message.successful_payment.total_amount * stars.RUB_PER_STAR * 100)
    elif not pay.ok:
        return None
    payment = Payment(
        user_id=user.id,
        provider=PaymentProvider.STARS,
        external_id=f"stars-{uuid4()}",
        amount_kopeks=amount_kopeks,
        purpose=_purpose_of(purpose),
        plan_id=pay.plan.id if pay.plan else None,
        subscription_id=pay.subscription.id if pay.subscription else None,
        traffic_package_id=pay.package.id if pay.package else None,
    )
    db.add(payment)
    await db.flush()
    return payment


# ── Промокоды ─────────────────────────────────────────────────────────
@router.callback_query(F.data == "promo")
async def cb_promo(callback: CallbackQuery, state: FSMContext, config: Config) -> None:
    await state.set_state(UserStates.entering_promo_code)
    await screen(
        callback.message,
        config,
        t(config, "{@gift} <b>Промокод</b>\n\nВведите код одним сообщением:"),
        keyboards.cancel_to_menu(config),
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
    await send_new_screen(message, config, text, keyboards.back_to_menu(config))


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
    await screen(callback.message, config, text, keyboards.referral_menu(config, link))
    await callback.answer()


# ── Мои подписки (несколько ключей на пользователя) ────────────────────
@router.callback_query(F.data == "my_subscriptions")
async def cb_my_subscriptions(callback: CallbackQuery, config: Config) -> None:
    async with session() as db:
        user = await subs.get_or_create_user(db, callback.from_user.id)
        rows = await subs.list_subscriptions(db, user)
        titles = await _plan_titles(db, rows)

    text = (
        t(config, "{@key} <b>Ваши подписки ({count})</b>\n\nВыберите нужную:", count=len(rows))
        if rows
        else t(config, texts.SUBSCRIPTION_NONE)
    )
    await screen(callback.message, config, text, keyboards.subscriptions_menu(config, rows, titles))
    await callback.answer()


async def _get_own_subscription(db, telegram_id: int, subscription_id: int):
    user = await subs.get_or_create_user(db, telegram_id)
    subscription = await db.get(BotSubscription, subscription_id)
    if subscription is None or subscription.user_id != user.id:
        return None
    return subscription


def _gb(value: int) -> str:
    return f"{value / 1024 ** 3:.2f} ГБ"


async def _remote_usage(config: Config, ref: int | str) -> tuple[int, int, int, int] | None:
    """(использовано, лимит трафика, устройств подключено, лимит устройств).

    Данные живут в Remnawave, а не у нас: локально мы храним только срок и
    ссылку. Недоступность панели не должна ломать экран, поэтому None."""
    try:
        client = subs.client_for(config)
        try:
            remote = await client.get_user(ref)
            devices = await client.get_devices(ref)
        finally:
            await client.aclose()
    except RemnawaveError as exc:
        log.warning("Не удалось получить данные ключа %s: %s", ref, exc)
        return None
    return (
        remote.used_traffic_bytes,
        remote.traffic_limit_bytes,
        len(devices),
        remote.hwid_device_limit or 0,
    )


async def _render_subscription(callback: CallbackQuery, config: Config, subscription_id: int) -> None:
    async with session() as db:
        subscription = await _get_own_subscription(db, callback.from_user.id, subscription_id)
        if subscription is None:
            await callback.answer("Ключ не найден", show_alert=True)
            return
        title = (await _plan_titles(db, [subscription]))[subscription.id]
        plan_row = await db.get(Plan, subscription.plan_id) if subscription.plan_id else None
        remote_ref = subscription.remote_ref
        url = subscription.subscription_url
        expire_at = subscription.expire_at
        auto_renew_on = subscription.auto_renew

    lines = [
        t(config, "{@key} <b>Детали подписки</b>"),
        "",
        f"Тариф: <b>{title}</b>",
        f"Истекает: <b>{_date(expire_at)}</b> ({_left(expire_at)})",
    ]

    usage = await _remote_usage(config, remote_ref)
    if usage is not None:
        used, limit, devices_used, devices_limit = usage
        traffic = f"{_gb(used)} / " + (_gb(limit) if limit else "∞ (безлимит)")
        lines.append(f"Трафик: <b>{traffic}</b>")
        if devices_limit:
            lines.append(f"Устройства: <b>{devices_used} / {devices_limit}</b>")
        else:
            lines.append(f"Устройства: <b>{devices_used}</b>")

    if url:
        lines += [
            "",
            t(config, "{@link} <b>Ссылка для подключения</b>"),
            f"<code>{url}</code>",
            "",
            "Нажмите «Подключиться» — покажу, что делать на вашем устройстве.",
        ]

    auto_renew = auto_renew_on if plan_row is not None else None
    # Докупка трафика имеет смысл только там, где лимит вообще есть:
    # к безлимиту прибавлять нечего.
    can_buy_traffic = bool(
        config.traffic_packages and usage is not None and usage[1] > 0
    )
    await screen(
        callback.message,
        config,
        "\n".join(lines),
        keyboards.subscription_detail_menu(
            config,
            subscription_id,
            has_url=bool(url),
            auto_renew=auto_renew,
            can_buy_traffic=can_buy_traffic,
        ),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("subtraffic:"))
async def cb_traffic_packages(callback: CallbackQuery, config: Config) -> None:
    subscription_id = int(callback.data.split(":", 1)[1])
    async with session() as db:
        subscription = await _get_own_subscription(db, callback.from_user.id, subscription_id)
    if subscription is None:
        await callback.answer("Ключ не найден", show_alert=True)
        return
    if not config.traffic_packages:
        await callback.answer("Пакеты трафика пока не настроены", show_alert=True)
        return

    await screen(
        callback.message,
        config,
        t(
            config,
            "{@traffic} <b>Докупить трафик</b>\n\n"
            "Пакет добавляется к текущему лимиту подписки и не сгорает при продлении.\n\n"
            "Выберите объём:",
        ),
        keyboards.traffic_packages_menu(config, subscription_id),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("pt:"))
async def cb_traffic_pick(callback: CallbackQuery, config: Config) -> None:
    _, raw_id, raw_package = callback.data.split(":", 2)
    package = config.traffic_package(int(raw_package))
    if package is None:
        await callback.answer("Пакет больше не доступен", show_alert=True)
        return
    if not config.any_payment_ready:
        await callback.answer("Приём оплаты ещё не настроен в панели", show_alert=True)
        return

    await screen(
        callback.message,
        config,
        t(
            config,
            "{@traffic} <b>+{gb} ГБ трафика</b>\n\nК оплате: <b>{price}</b>\n\nСпособ оплаты:",
            gb=package.traffic_gb,
            price=format_rub(package.price_kopeks),
        ),
        keyboards.providers_menu(
            config,
            purpose="traffic",
            target=f"{raw_id}-{package.id}",
            back=f"subtraffic:{raw_id}",
        ),
    )
    await callback.answer()


# ── Подключение ───────────────────────────────────────────────────────
async def _connect_target(db, telegram_id: int, subscription_id: int | None):
    """Ключ, к которому относится инструкция. Из главного меню конкретный
    ключ не выбран — берём с самым поздним сроком."""
    user = await subs.get_or_create_user(db, telegram_id)
    if subscription_id is not None:
        subscription = await db.get(BotSubscription, subscription_id)
        if subscription is None or subscription.user_id != user.id:
            return None
        return subscription
    return _current_subscription(await subs.list_subscriptions(db, user))


@router.callback_query(F.data == "connect")
@router.callback_query(F.data.startswith("connect:"))
async def cb_connect(callback: CallbackQuery, config: Config) -> None:
    raw = callback.data.partition(":")[2]
    async with session() as db:
        subscription = await _connect_target(db, callback.from_user.id, int(raw) if raw else None)
        url = subscription.subscription_url if subscription else None
        subscription_id = subscription.id if subscription else None

    if not url:
        await callback.answer("Активная подписка не найдена", show_alert=True)
        return

    await screen(
        callback.message,
        config,
        t(config, "{@link} <b>Подключение</b>")
        + f"\n\n<b>Ссылка подписки:</b>\n<code>{url}</code>\n\nВыберите устройство:",
        connection.devices_keyboard(subscription_id, url, config.premium_emoji),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("conn:"))
async def cb_connect_device(callback: CallbackQuery, config: Config) -> None:
    _, raw_id, device = callback.data.split(":", 2)
    if device not in connection.APPS:
        await callback.answer()
        return
    await _render_instruction(callback, config, int(raw_id), device, connection.APPS[device]["recommended"])


@router.callback_query(F.data.startswith("connapp:"))
async def cb_connect_app(callback: CallbackQuery, config: Config) -> None:
    _, raw_id, device, app = callback.data.split(":", 3)
    if device not in connection.APPS or app not in connection.APP_NAMES:
        await callback.answer()
        return
    await _render_instruction(callback, config, int(raw_id), device, app)


@router.callback_query(F.data.startswith("connapps:"))
async def cb_connect_apps(callback: CallbackQuery, config: Config) -> None:
    _, raw_id, device = callback.data.split(":", 2)
    if device not in connection.APPS:
        await callback.answer()
        return
    await screen(
        callback.message,
        config,
        f"<b>Приложения для {connection.APPS[device]['title']}</b>\n\nВыберите приложение:",
        connection.apps_keyboard(int(raw_id), device, config.premium_emoji),
    )
    await callback.answer()


async def _render_instruction(
    callback: CallbackQuery, config: Config, subscription_id: int, device: str, app: str
) -> None:
    async with session() as db:
        subscription = await _connect_target(db, callback.from_user.id, subscription_id)
        url = subscription.subscription_url if subscription else None

    if not url:
        await callback.answer("Активная подписка не найдена", show_alert=True)
        return

    await screen(
        callback.message,
        config,
        connection.instruction_text(device, app, url),
        connection.instruction_keyboard(
            subscription_id,
            device,
            app,
            url,
            config.premium_emoji,
            via_redirect=config.subscription_redirect,
        ),
        disable_web_page_preview=True,
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
        await screen(callback.message, config, t(config, texts.NO_PLANS), keyboards.back_to_menu(config))
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
        await screen(
            callback.message,
            config,
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
        await screen(
            callback.message,
            config,
            t(config, "{@card} <b>Выберите тариф</b>"),
            keyboards.categories_menu(config, prefix=prefix, back=back, discount_percent=discount),
        )
    else:
        await screen(
            callback.message,
            config,
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
    await screen(
        callback.message,
        config,
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
            devices = await client.get_devices(subscription.remote_ref)
        finally:
            await client.aclose()
    except RemnawaveError as exc:
        # 400/404 — ключа в Remnawave нет: он удалён или заведён на другой
        # панели. Это не сбой связи, и «попробуйте позже» тут вводит в
        # заблуждение, поэтому говорим прямо.
        log.warning("Устройства ключа #%s: %s", subscription_id, exc)
        if exc.status_code in (400, 404):
            await callback.answer(
                "Этот ключ не найден на сервере VPN. Напишите в поддержку — его нужно перевыпустить.",
                show_alert=True,
            )
        else:
            await callback.answer(
                "Сервер VPN сейчас не отвечает, попробуйте позже", show_alert=True
            )
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

    await screen(
        callback.message,
        config,
        text,
        keyboards.devices_menu(config, bool(devices), subscription_id=subscription_id),
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
            await client.delete_all_devices(subscription.remote_ref)
        finally:
            await client.aclose()
    except RemnawaveError as exc:
        log.warning("Сброс устройств ключа #%s: %s", subscription_id, exc)
        await callback.answer("Не удалось сбросить устройства", show_alert=True)
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

    lines = [
        t(config, "{@user} <b>Личный кабинет</b>"),
        "",
        f"ID профиля: <code>{callback.from_user.id}</code>",
        "",
        t(config, "{@wallet} Доступно: <b>{amount}</b>", amount=format_rub(balance)),
    ]
    if discount:
        lines.append(t(config, "{@gift} Скидка на следующую оплату: <b>{p}%</b>", p=discount))
    text = "\n".join(lines)
    await screen(callback.message, config, text, keyboards.profile_menu(config))
    await callback.answer()


@router.callback_query(F.data == "wallet_topup")
async def cb_wallet_topup(callback: CallbackQuery, config: Config) -> None:
    await screen(
        callback.message,
        config,
        t(config, "{@card} Выберите сумму пополнения:"),
        keyboards.wallet_menu(config),
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

    await screen(callback.message, config, text, keyboards.back_to_menu(config))
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
