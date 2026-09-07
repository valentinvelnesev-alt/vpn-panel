"""Клавиатуры бота.

Иконка кнопки задаётся полем `icon_custom_emoji_id` (HTML в тексте кнопки
Telegram не рендерит вообще), цвет — полем `style`, и допустимы ровно три
значения, см. app/icons.py. Ключи иконок берутся из карты премиум-эмодзи
в панели, поэтому владелец меняет оформление без правки кода; пока ключ
не задан, кнопка просто рисуется без иконки.
"""

from typing import Any

from aiogram.types import (
    CopyTextButton,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
)

from app.config import Config, discounted_kopeks, format_rub
from app.icons import icon_id, style_or_none

TOPUP_PRESETS_RUB = [100, 300, 500, 1000]

# Подписи кнопок нижней клавиатуры — они же условие в хендлерах.
BTN_MENU = "Меню"
BTN_HELP = "Помощь"


def reply_menu() -> ReplyKeyboardMarkup:
    """Постоянная клавиатура под полем ввода: «Меню» и «Помощь».

    Иконок и цветов у неё быть не может — это возможности только у inline-
    кнопок; здесь Telegram рисует обычный текст.
    """
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=BTN_MENU), KeyboardButton(text=BTN_HELP)]],
        resize_keyboard=True,
        is_persistent=True,
    )


def _btn(
    config: Config | None,
    text: str,
    *,
    callback_data: str | None = None,
    url: str | None = None,
    icon: str | None = None,
    style: str | None = None,
) -> InlineKeyboardButton:
    kwargs: dict[str, Any] = {"text": text}
    if callback_data:
        kwargs["callback_data"] = callback_data
    if url:
        kwargs["url"] = url
    emoji = icon_id(icon, config.premium_emoji if config else None)
    if emoji:
        kwargs["icon_custom_emoji_id"] = emoji
    allowed = style_or_none(style)
    if allowed:
        kwargs["style"] = allowed
    return InlineKeyboardButton(**kwargs)


def _markup(rows: list[list[InlineKeyboardButton]]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[r for r in rows if r])


# ── Главное меню ──────────────────────────────────────────────────────
def main_menu(
    config: Config,
    *,
    trial_available: bool,
    has_subscription: bool,
    balance_kopeks: int,
) -> InlineKeyboardMarkup:
    """Порядок как в исходном боте: покупка, подписки, подключение,
    кабинет, баланс, рефералка, промокод, канал и поддержка."""
    rows: list[list[InlineKeyboardButton]] = []
    if trial_available:
        rows.append(
            [_btn(config, "Попробовать бесплатно", callback_data="trial", icon="trial", style="success")]
        )

    rows.append([_btn(config, "Купить подписку", callback_data="plans", icon="buy")])
    if has_subscription:
        rows.append([_btn(config, "Подписка", callback_data="my_subscriptions", icon="subscription")])
        # «Подключиться» без единого ключа вело бы в тупик — прячем.
        rows.append([_btn(config, "Подключиться", callback_data="connect", icon="connect")])

    rows.append([_btn(config, "Личный кабинет", callback_data="profile", icon="profile")])
    rows.append(
        [
            _btn(
                config,
                f"Баланс: {format_rub(balance_kopeks)}",
                callback_data="wallet",
                icon="balance",
            )
        ]
    )

    if config.referral_visible:
        rows.append([_btn(config, "Рефералка", callback_data="referral", icon="referral", style="success")])

    rows.append([_btn(config, "Промокод", callback_data="promo", icon="promo")])

    last: list[InlineKeyboardButton] = []
    if config.channel_url:
        last.append(_btn(config, "Наш канал", url=config.channel_url, icon="channel"))
    if config.support_url:
        last.append(_btn(config, "Поддержка", url=config.support_url, icon="support"))
    rows.append(last)
    return _markup(rows)


def back_to_menu(config: Config | None = None) -> InlineKeyboardMarkup:
    return _markup([[_btn(config, "Назад", callback_data="menu", icon="back")]])


def cancel_to_menu(config: Config | None = None) -> InlineKeyboardMarkup:
    return _markup([[_btn(config, "Отмена", callback_data="menu", icon="cancel", style="danger")]])


def channel_gate(config: Config) -> InlineKeyboardMarkup:
    rows = []
    if config.channel_url:
        rows.append([_btn(config, "Подписаться", url=config.channel_url, icon="channel", style="primary")])
    rows.append([_btn(config, "Я подписался", callback_data="check_sub", icon="check", style="success")])
    return _markup(rows)


# ── Тарифы ────────────────────────────────────────────────────────────
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


def category_from_price(config: Config, category_id: int | None, discount_percent: int) -> int:
    """Минимальная цена в категории — для подписи «от N ₽»."""
    prices = [
        discounted_kopeks(p.price_kopeks, discount_percent)
        for p in config.plans
        if p.category_id == category_id
    ]
    return min(prices) if prices else 0


def categories_menu(
    config: Config, *, prefix: str = "buy", back: str = "menu", discount_percent: int = 0
) -> InlineKeyboardMarkup:
    rows = []
    for category_id, title in plan_categories(config):
        cheapest = category_from_price(config, category_id, discount_percent)
        label = f"{title} • от {format_rub(cheapest)}" if cheapest else title
        rows.append(
            [
                _btn(
                    config,
                    label,
                    callback_data=f"plancat:{prefix}:{category_id if category_id is not None else 0}",
                    icon="plan",
                )
            ]
        )
    rows.append([_btn(config, "Отмена", callback_data=back, icon="cancel", style="danger")])
    return _markup(rows)


def plan_price_label(plan, discount_percent: int = 0) -> str:
    """«399 ₽» или «299 ₽ (-25%)» — зачёркивание Telegram в кнопках не
    рисует, поэтому показываем итоговую цену и процент."""
    price = discounted_kopeks(plan.price_kopeks, discount_percent)
    if price == plan.price_kopeks:
        return format_rub(price)
    return f"{format_rub(price)} (-{discount_percent}%)"


def plans_menu(
    config: Config,
    *,
    prefix: str = "buy",
    category_id: int | None = -1,
    discount_percent: int = 0,
    back: str = "menu",
    with_promo: bool = True,
) -> InlineKeyboardMarkup:
    """category_id: -1 — без фильтра (все тарифы, старое поведение),
    None — только тарифы без категории, иначе — тарифы этой категории."""
    rows = []
    for plan in config.plans:
        if category_id != -1 and plan.category_id != category_id:
            continue
        rows.append(
            [
                _btn(
                    config,
                    f"{plan.title} • {plan_price_label(plan, discount_percent)}",
                    callback_data=f"{prefix}:{plan.id}",
                    icon="period",
                )
            ]
        )

    if with_promo and not discount_percent:
        rows.append(
            [_btn(config, "Применить промокод", callback_data="promo", icon="promo", style="primary")]
        )

    has_categories = bool(plan_categories(config))
    back_target = ("plans" if prefix == "buy" else back) if has_categories else back
    tail = [_btn(config, "Назад", callback_data=back_target, icon="back")]
    if back_target != "menu":
        tail.append(_btn(config, "Отмена", callback_data="menu", icon="cancel", style="danger"))
    rows.append(tail)
    return _markup(rows)


def providers_menu(config: Config, *, purpose: str, target: str, back: str = "menu") -> InlineKeyboardMarkup:
    """purpose: 'topup', 'plan' или 'renew'; target: сумма или id тарифа.

    Показываем только провайдеров с заполненными реквизитами — кнопка
    «включённого» провайдера без ключей вела в тупик."""
    rows = []
    if config.platega_ready:
        rows.append([_btn(config, "СБП / карта", callback_data=f"pay:{purpose}:platega:{target}", icon="card")])
    if config.rollypay_ready:
        rows.append([_btn(config, "СБП (резерв)", callback_data=f"pay:{purpose}:rollypay:{target}", icon="card")])
    if config.cryptobot_ready:
        rows.append([_btn(config, "Криптовалюта", callback_data=f"pay:{purpose}:cryptobot:{target}", icon="crypto")])
    if config.stars_enabled:
        rows.append([_btn(config, "Telegram Stars", callback_data=f"pay:{purpose}:stars:{target}", icon="star")])
    if purpose in ("plan", "renew"):
        rows.append([_btn(config, "Оплатить с баланса", callback_data=f"pay:{purpose}:wallet:{target}", icon="balance")])
    rows.append(
        [
            _btn(config, "Назад", callback_data=back, icon="back"),
            _btn(config, "Отмена", callback_data="menu", icon="cancel", style="danger"),
        ]
    )
    return _markup(rows)


def pay_menu(config: Config, *, pay_url: str, payment_id: int) -> InlineKeyboardMarkup:
    return _markup(
        [
            [_btn(config, "Оплатить", url=pay_url, icon="card", style="success")],
            [_btn(config, "Проверить оплату", callback_data=f"checkpay:{payment_id}", icon="refresh")],
            [_btn(config, "Отмена", callback_data="menu", icon="cancel", style="danger")],
        ]
    )


# ── Подписки ──────────────────────────────────────────────────────────
def subscriptions_menu(
    config: Config, subscriptions: list, titles: dict[int, str]
) -> InlineKeyboardMarkup:
    """«Мои подписки» — список ключей. Показываем название тарифа, а не
    техническое имя аккаунта Remnawave вида tg_123_ab12cd."""
    rows = []
    for sub in subscriptions:
        until = sub.expire_at.strftime("%d.%m.%Y") if sub.expire_at else "—"
        rows.append(
            [
                _btn(
                    config,
                    f"{titles.get(sub.id, 'Подписка')} | до {until}",
                    callback_data=f"viewsub:{sub.id}",
                    icon="subscription",
                )
            ]
        )
    if not rows:
        rows.append([_btn(config, "Купить подписку", callback_data="plans", icon="buy", style="primary")])
    rows.append([_btn(config, "Назад", callback_data="menu", icon="back")])
    return _markup(rows)


def traffic_packages_menu(config: Config, subscription_id: int) -> InlineKeyboardMarkup:
    rows = [
        [
            _btn(
                config,
                f"{package.title} • {format_rub(package.price_kopeks)}",
                callback_data=f"pt:{subscription_id}:{package.id}",
                icon="traffic",
            )
        ]
        for package in config.traffic_packages
    ]
    rows.append([_btn(config, "Назад", callback_data=f"viewsub:{subscription_id}", icon="back")])
    return _markup(rows)


def subscription_detail_menu(
    config: Config,
    subscription_id: int,
    *,
    has_url: bool,
    auto_renew: bool | None = None,
    can_buy_traffic: bool = False,
) -> InlineKeyboardMarkup:
    """auto_renew=None — у ключа нет тарифа (пробный/бонусный), продлевать
    автоматически нечем, переключатель не показываем."""
    rows = [[_btn(config, "Продлить", callback_data=f"renewsub:{subscription_id}", icon="clock")]]
    if has_url:
        rows.append([_btn(config, "Подключиться", callback_data=f"connect:{subscription_id}", icon="connect")])
    if auto_renew is not None:
        rows.append(
            [
                _btn(
                    config,
                    f"Автоплатёж: {'Вкл' if auto_renew else 'Выкл'}",
                    callback_data=f"subautorenew:{subscription_id}",
                    icon="autorenew",
                )
            ]
        )
    if can_buy_traffic:
        rows.append(
            [_btn(config, "Докупить трафик", callback_data=f"subtraffic:{subscription_id}", icon="traffic")]
        )
    rows.append([_btn(config, "Устройства", callback_data=f"subdevices:{subscription_id}", icon="devices")])
    rows.append([_btn(config, "Назад", callback_data="my_subscriptions", icon="back")])
    return _markup(rows)


def devices_menu(config: Config, has_devices: bool, *, subscription_id: int) -> InlineKeyboardMarkup:
    rows = []
    if has_devices:
        rows.append(
            [
                _btn(
                    config,
                    "Отвязать все устройства",
                    callback_data=f"devicesreset:{subscription_id}",
                    icon="trash",
                    style="danger",
                )
            ]
        )
    rows.append([_btn(config, "Назад", callback_data=f"viewsub:{subscription_id}", icon="back")])
    return _markup(rows)


# ── Профиль и кошелёк ─────────────────────────────────────────────────
def profile_menu(config: Config) -> InlineKeyboardMarkup:
    rows = [
        [_btn(config, "Пополнить баланс", callback_data="wallet_topup", icon="balance")],
        [_btn(config, "Купить подписку", callback_data="plans", icon="buy")],
        [_btn(config, "История покупок", callback_data="purchase_history", icon="history")],
    ]
    legal = []
    if config.privacy_policy_url:
        legal.append(_btn(config, "Политика конфиденциальности", url=config.privacy_policy_url, icon="doc"))
    if config.terms_url:
        legal.append(_btn(config, "Пользовательское соглашение", url=config.terms_url, icon="doc"))
    if legal:
        rows.append(legal)
    rows.append([_btn(config, "Назад", callback_data="menu", icon="back")])
    return _markup(rows)


def wallet_menu(config: Config) -> InlineKeyboardMarkup:
    presets = [
        _btn(config, f"+{amount} ₽", callback_data=f"topup:{amount}", icon="balance")
        for amount in TOPUP_PRESETS_RUB
    ]
    return _markup(
        [
            presets[:2],
            presets[2:],
            [_btn(config, "Другая сумма", callback_data="topup_custom", icon="edit")],
            [_btn(config, "Назад", callback_data="menu", icon="back")],
        ]
    )


def support_menu(config: Config) -> InlineKeyboardMarkup:
    return _markup(
        [
            [_btn(config, "Написать в поддержку", url=config.support_url, icon="support", style="primary")],
            [_btn(config, "Назад", callback_data="menu", icon="back")],
        ]
    )


def referral_menu(config: Config, link: str) -> InlineKeyboardMarkup:
    return _markup(
        [
            [
                InlineKeyboardButton(
                    text="Скопировать ссылку",
                    copy_text=CopyTextButton(text=link),
                    icon_custom_emoji_id=icon_id("copy", config.premium_emoji),
                )
            ],
            [
                _btn(
                    config,
                    "Пригласить друга",
                    url=f"https://t.me/share/url?url={link}",
                    icon="referral",
                    style="success",
                )
            ],
            [_btn(config, "Назад", callback_data="menu", icon="back")],
        ]
    )
