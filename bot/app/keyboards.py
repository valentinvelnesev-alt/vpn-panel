from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.config import Config

TOPUP_PRESETS_RUB = [100, 300, 500, 1000]


def main_menu(
    config: Config,
    *,
    has_subscription: bool,
    trial_available: bool,
    wallet_enabled: bool = True,
):
    builder = InlineKeyboardBuilder()
    builder.button(
        text="💳 Продлить" if has_subscription else "💳 Купить доступ",
        callback_data="plans",
    )
    if trial_available:
        builder.button(text="🎁 Попробовать бесплатно", callback_data="trial")
    builder.button(text="🔑 Моя подписка", callback_data="subscription")
    if wallet_enabled:
        builder.button(text="👛 Баланс", callback_data="wallet")
    builder.button(text="📱 Устройства", callback_data="devices")
    if config.referral_enabled:
        builder.button(text="👥 Пригласить друга", callback_data="referral")
    builder.button(text="🎟 Промокод", callback_data="promo")
    if config.support_url:
        builder.button(text="💬 Поддержка", url=config.support_url)
    builder.adjust(1)
    return builder.as_markup()


def plans_menu(config: Config, *, prefix: str = "buy"):
    builder = InlineKeyboardBuilder()
    for plan in config.plans:
        price = f"{plan.price_rub:.0f} ₽".replace(".0", "")
        builder.button(text=f"{plan.title} — {price}", callback_data=f"{prefix}:{plan.id}")
    builder.button(text="‹ Назад", callback_data="menu")
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


def devices_menu(devices: list, has_devices: bool):
    builder = InlineKeyboardBuilder()
    if has_devices:
        builder.button(text="🗑 Сбросить все", callback_data="devices_reset")
    builder.button(text="‹ Назад", callback_data="menu")
    builder.adjust(1)
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
    if config.cryptobot_enabled:
        builder.button(
            text="Криптовалюта", callback_data=f"pay:{purpose}:cryptobot:{target}"
        )
    if config.stars_enabled:
        builder.button(text="Telegram Stars", callback_data=f"pay:{purpose}:stars:{target}")
    if purpose == "plan":
        builder.button(text="Из баланса", callback_data=f"pay:{purpose}:wallet:{target}")
    builder.button(text="‹ Назад", callback_data="menu")
    builder.adjust(1)
    return builder.as_markup()


def referral_menu():
    return back_to_menu()
