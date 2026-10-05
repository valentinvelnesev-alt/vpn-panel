from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.config import Config, PlanView
from app.services.pricing import Price, button_price

TOPUP_PRESETS_RUB = [100, 300, 500, 1000]


# Кнопки, которые админ может скрыть в панели (Бот → Меню бота). Ключи
# хранятся в BotConfig.menu_hidden; подписи дублируются в панели.
MENU_BUTTONS: dict[str, str] = {
    "trial": "Попробовать бесплатно",
    "subscriptions": "Мои подписки",
    "profile": "Мой профиль",
    "promo": "Промокод",
    "referral": "Пригласить друга",
    "channel": "Наш канал",
    "support": "Поддержка",
    "balance": "Баланс и пополнение",
    "history": "История покупок",
    "help": "Команда /help",
}


def _rows(buttons: list[InlineKeyboardButton], per_row: int = 2) -> list[list[InlineKeyboardButton]]:
    return [buttons[i : i + per_row] for i in range(0, len(buttons), per_row)]


def main_menu(config: Config, *, trial_available: bool, has_subscription: bool = False):
    """Покупка/продление, ключи, профиль/промокод, приглашение, канал/поддержка.

    Нет ни одной подписки — «Купить подписку». Есть — «Продлить подписку»
    (продлевает существующий ключ, ссылка у клиента не меняется). Новый
    отдельный ключ — только через «Купить ещё одну», и только если в панели
    разрешено несколько подписок на пользователя.
    """
    B = InlineKeyboardButton
    rows: list[list[InlineKeyboardButton]] = []
    if trial_available and config.shows("trial"):
        rows.append([B(text="🎁 Попробовать бесплатно", callback_data="trial")])

    if has_subscription:
        rows.append([B(text="🔄 Продлить подписку", callback_data="renew")])
        if config.allow_multiple_subscriptions:
            rows.append([B(text="➕ Купить ещё одну", callback_data="plans_new")])
    else:
        rows.append([B(text="💳 Купить подписку", callback_data="plans")])

    if config.shows("subscriptions"):
        rows.append([B(text="🔑 Мои подписки", callback_data="my_subscriptions")])

    pair = []
    if config.shows("profile"):
        pair.append(B(text="👤 Мой профиль", callback_data="profile"))
    if config.shows("promo"):
        pair.append(B(text="🎟 Промокод", callback_data="promo"))
    rows += _rows(pair)

    if (config.referral_enabled or config.referral_commission_enabled) and config.shows(
        "referral"
    ):
        rows.append([B(text="🎁 Пригласить друга", callback_data="referral")])

    pair = []
    if config.channel_url and config.shows("channel"):
        pair.append(B(text="📢 Наш канал", url=config.channel_url))
    if config.support_url and config.shows("support"):
        pair.append(B(text="💬 Поддержка", url=config.support_url))
    rows += _rows(pair)

    return InlineKeyboardMarkup(inline_keyboard=rows)


def plan_categories(config: Config) -> list[tuple[int | None, str]]:
    """Различные категории среди активных тарифов, в порядке появления.

    None — «без категории»: тарифы, которым админ не назначил ни одной.
    Возвращается только если у тарифов реально больше одной категории —
    иначе бот показывает плоский список, как раньше (без лишней вкладки).
    """
    seen: dict[int | None, str] = {}
    for plan in config.plans:
        seen.setdefault(plan.category_id, plan.category_title or "Без категории")
    if len(seen) <= 1:
        return []
    return list(seen.items())


def _plan_button(plan: PlanView, price: Price, prefix: str) -> InlineKeyboardButton:
    title = f"⭐ {plan.title}" if plan.is_personal else plan.title
    return InlineKeyboardButton(
        text=f"{title} — {button_price(price)}", callback_data=f"{prefix}:{plan.id}"
    )


def plans_menu(
    config: Config,
    priced: list[tuple[PlanView, Price]],
    *,
    prefix: str = "buy",
    category_id: int | None = -1,
    back: str = "menu",
):
    """Список тарифов.

    category_id == -1 — верхний уровень: персональные тарифы клиента, затем
    либо вкладки категорий (если их больше одной), либо все общие тарифы.
    Иначе — общие тарифы выбранной категории (None — «без категории»).
    """
    rows: list[list[InlineKeyboardButton]] = []
    categories = plan_categories(config)
    if category_id == -1:
        rows += [[_plan_button(p, price, prefix)] for p, price in priced if p.is_personal]
        if categories:
            rows += [
                [
                    InlineKeyboardButton(
                        text=title,
                        callback_data=f"plancat:{prefix}:{cid if cid is not None else 0}",
                    )
                ]
                for cid, title in categories
            ]
        else:
            rows += [
                [_plan_button(p, price, prefix)] for p, price in priced if not p.is_personal
            ]
    else:
        rows += [
            [_plan_button(p, price, prefix)]
            for p, price in priced
            if not p.is_personal and p.category_id == category_id
        ]
    rows.append([InlineKeyboardButton(text="‹ Назад", callback_data=back)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def back_to_menu():
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="‹ Назад", callback_data="menu")]]
    )


def channel_gate(config: Config):
    builder = InlineKeyboardBuilder()
    if config.channel_url:
        builder.button(text="📢 Подписаться", url=config.channel_url)
    builder.button(text="✓ Я подписался", callback_data="check_sub")
    builder.adjust(1)
    return builder.as_markup()


def devices_menu(devices: list, has_devices: bool, *, subscription_id: int):
    builder = InlineKeyboardBuilder()
    if has_devices:
        builder.button(text="🗑 Сбросить все", callback_data=f"devicesreset:{subscription_id}")
    builder.button(text="‹ Назад", callback_data=f"viewsub:{subscription_id}")
    builder.adjust(1)
    return builder.as_markup()


def subscriptions_menu(subscriptions: list) -> InlineKeyboardMarkup:
    """«Мои подписки» — список ключей, как в исходном боте."""
    builder = InlineKeyboardBuilder()
    if not subscriptions:
        builder.button(text="У вас нет активных подписок", callback_data="noop")
    for sub in subscriptions:
        until = sub.expire_at.strftime("%d.%m.%Y") if sub.expire_at else "—"
        builder.button(text=f"🔑 {sub.username} — до {until}", callback_data=f"viewsub:{sub.id}")
    builder.button(text="‹ Назад", callback_data="menu")
    builder.adjust(1)
    return builder.as_markup()


def subscription_detail_menu(subscription_id: int, *, has_url: bool) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    if has_url:
        builder.button(text="🔗 Получить ссылку", callback_data=f"sublink:{subscription_id}")
    builder.button(text="📱 Устройства", callback_data=f"subdevices:{subscription_id}")
    builder.button(text="💳 Продлить", callback_data=f"renewsub:{subscription_id}")
    builder.button(text="‹ Назад", callback_data="my_subscriptions")
    builder.adjust(1)
    return builder.as_markup()


def profile_menu(config: Config, *, has_subscription: bool = False) -> InlineKeyboardMarkup:
    B = InlineKeyboardButton
    rows: list[list[InlineKeyboardButton]] = []
    if config.shows("balance"):
        rows.append([B(text="💰 Пополнить баланс", callback_data="wallet_topup")])
    if has_subscription:
        rows.append([B(text="🔄 Продлить подписку", callback_data="renew")])
    else:
        rows.append([B(text="💳 Купить подписку", callback_data="plans")])
    if config.shows("history"):
        rows.append([B(text="🧾 История покупок", callback_data="purchase_history")])

    legal = []
    if config.privacy_policy_url:
        legal.append(B(text="Политика конфиденциальности", url=config.privacy_policy_url))
    if config.terms_url:
        legal.append(B(text="Пользовательское соглашение", url=config.terms_url))
    if legal:
        rows.append(legal)

    rows.append([B(text="‹ Назад", callback_data="menu")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def wallet_menu():
    builder = InlineKeyboardBuilder()
    for amount in TOPUP_PRESETS_RUB:
        builder.button(text=f"+{amount} ₽", callback_data=f"topup:{amount}")
    builder.button(text="Другая сумма", callback_data="topup_custom")
    builder.button(text="‹ Назад", callback_data="menu")
    builder.adjust(2, 2, 1, 1)
    return builder.as_markup()


def providers_menu(config: Config, *, purpose: str, target: str, free: bool = False):
    """purpose: 'topup', 'plan' или 'renew'; target: сумма, id тарифа или
    «ключ-тариф». free — тариф стал бесплатным (скидка 100%): внешние
    кассы не принимают нулевые счета, остаётся только оформление сразу."""
    builder = InlineKeyboardBuilder()
    if free and purpose in ("plan", "renew"):
        builder.button(text="✅ Оформить бесплатно", callback_data=f"pay:{purpose}:wallet:{target}")
        builder.button(text="‹ Назад", callback_data="menu")
        builder.adjust(1)
        return builder.as_markup()
    if config.platega_enabled:
        builder.button(text="СБП / карта", callback_data=f"pay:{purpose}:platega:{target}")
    if config.rollypay_enabled:
        builder.button(text="СБП (резерв)", callback_data=f"pay:{purpose}:rollypay:{target}")
    if config.cryptobot_enabled:
        builder.button(
            text="Криптовалюта", callback_data=f"pay:{purpose}:cryptobot:{target}"
        )
    if config.stars_enabled:
        builder.button(text="Telegram Stars", callback_data=f"pay:{purpose}:stars:{target}")
    if purpose in ("plan", "renew"):
        builder.button(text="Из баланса", callback_data=f"pay:{purpose}:wallet:{target}")
    builder.button(text="‹ Назад", callback_data="menu")
    builder.adjust(1)
    return builder.as_markup()


def referral_menu():
    return back_to_menu()
