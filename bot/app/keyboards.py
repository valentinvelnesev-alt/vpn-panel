from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.config import Config

TOPUP_PRESETS_RUB = [100, 300, 500, 1000]


def main_menu(config: Config, *, trial_available: bool):
    """Структура и порядок кнопок — как в исходном боте: покупка/список
    ключей, профиль/промокод, приглашение друга, канал/поддержка. Каждая
    покупка тарифа заводит отдельный ключ (см. «Мои подписки»), поэтому
    здесь нет разделения «купить» / «продлить» — это внутри карточки ключа.
    """
    builder = InlineKeyboardBuilder()
    if trial_available:
        builder.button(text="🎁 Попробовать бесплатно", callback_data="trial")

    builder.button(text="💳 Купить подписку", callback_data="plans")
    builder.button(text="🔑 Мои подписки", callback_data="my_subscriptions")

    builder.button(text="👤 Мой профиль", callback_data="profile")
    builder.button(text="🎟 Промокод", callback_data="promo")

    if config.referral_enabled or config.referral_commission_enabled:
        builder.button(text="🎁 Пригласить друга", callback_data="referral")

    channel_support_row = 0
    if config.channel_url:
        builder.button(text="📢 Наш канал", url=config.channel_url)
        channel_support_row += 1
    if config.support_url:
        builder.button(text="💬 Поддержка", url=config.support_url)
        channel_support_row += 1

    rows = [1] if trial_available else []
    rows += [2, 2]
    if config.referral_enabled or config.referral_commission_enabled:
        rows.append(1)
    if channel_support_row:
        rows.append(channel_support_row)
    builder.adjust(*rows)
    return builder.as_markup()


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


def categories_menu(config: Config, *, prefix: str = "buy"):
    builder = InlineKeyboardBuilder()
    for category_id, title in plan_categories(config):
        builder.button(
            text=title, callback_data=f"plancat:{prefix}:{category_id if category_id is not None else 0}"
        )
    builder.button(text="‹ Назад", callback_data="menu")
    builder.adjust(1)
    return builder.as_markup()


def plans_menu(config: Config, *, prefix: str = "buy", category_id: int | None = -1):
    """category_id: -1 — без фильтра (все тарифы, старое поведение),
    None — только тарифы без категории, иначе — тарифы этой категории."""
    builder = InlineKeyboardBuilder()
    for plan in config.plans:
        if category_id != -1 and plan.category_id != category_id:
            continue
        price = f"{plan.price_rub:.0f} ₽".replace(".0", "")
        builder.button(text=f"{plan.title} — {price}", callback_data=f"{prefix}:{plan.id}")
    builder.button(
        text="‹ Назад",
        callback_data="plans" if plan_categories(config) else "menu",
    )
    builder.adjust(1)
    return builder.as_markup()


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


def profile_menu(config: Config) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="💰 Пополнить баланс", callback_data="wallet_topup")
    builder.button(text="💳 Купить подписку", callback_data="plans")
    builder.button(text="🧾 История покупок", callback_data="purchase_history")

    legal_row = 0
    if config.privacy_policy_url:
        builder.button(text="Политика конфиденциальности", url=config.privacy_policy_url)
        legal_row += 1
    if config.terms_url:
        builder.button(text="Пользовательское соглашение", url=config.terms_url)
        legal_row += 1

    builder.button(text="‹ Назад", callback_data="menu")

    rows = [1, 1, 1]
    if legal_row:
        rows.append(legal_row)
    rows.append(1)
    builder.adjust(*rows)
    return builder.as_markup()


def wallet_menu():
    builder = InlineKeyboardBuilder()
    for amount in TOPUP_PRESETS_RUB:
        builder.button(text=f"+{amount} ₽", callback_data=f"topup:{amount}")
    builder.button(text="Другая сумма", callback_data="topup_custom")
    builder.button(text="‹ Назад", callback_data="menu")
    builder.adjust(2, 2, 1, 1)
    return builder.as_markup()


def providers_menu(config: Config, *, purpose: str, target: str):
    """purpose: 'topup' или 'plan'; target: сумма или id тарифа."""
    builder = InlineKeyboardBuilder()
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
