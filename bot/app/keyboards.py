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
    ReplyKeyboardRemove,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
)

from app.config import Config, PlanView, discounted_kopeks, format_rub
from app.icons import icon_id, style_or_none

TOPUP_PRESETS_RUB = [100, 300, 500, 1000]

# Подписи кнопок нижней клавиатуры — они же условие в хендлерах.
BTN_MENU = "Меню"
BTN_HELP = "Помощь"

# Кнопки, которые админ может скрыть в панели (Бот → Меню бота). Ключи
# хранятся в BotConfig.menu_hidden; подписи продублированы в панели.
MENU_BUTTONS: dict[str, str] = {
    "trial": "Попробовать бесплатно",
    "subscriptions": "Подписка",
    "connect": "Подключиться",
    "profile": "Личный кабинет",
    "balance": "Баланс",
    "referral": "Рефералка",
    "promo": "Промокод",
    "channel": "Наш канал",
    "support": "Поддержка",
    "help": "Помощь (кнопка под полем ввода)",
    "history": "История покупок",
}


def bottom_keyboard(config: Config) -> ReplyKeyboardMarkup | ReplyKeyboardRemove:
    """Что поставить под полем ввода. В режиме «кнопка меню слева» прежнюю
    клавиатуру нужно явно убрать: иначе она так и висит у клиентов."""
    if config.menu_mode == "commands":
        return ReplyKeyboardRemove()
    return reply_menu(config)


def reply_menu(config: Config | None = None) -> ReplyKeyboardMarkup:
    """Постоянная клавиатура под полем ввода: «Меню» и «Помощь».

    Иконок и цветов у неё быть не может — это возможности только у inline-
    кнопок; здесь Telegram рисует обычный текст.
    """
    row = [KeyboardButton(text=BTN_MENU)]
    if config is None or config.shows("help"):
        row.append(KeyboardButton(text=BTN_HELP))
    return ReplyKeyboardMarkup(keyboard=[row], resize_keyboard=True, is_persistent=True)


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
    # Премиум-иконки на кнопках — только если в панели включён режим
    # премиум-эмодзи: без Telegram Premium у владельца бота Telegram их
    # не покажет, а в обычном режиме кнопки должны быть без них.
    emoji = icon_id(icon, config.premium_emoji) if config is not None and config.premium else None
    if emoji:
        kwargs["icon_custom_emoji_id"] = emoji
    allowed = style_or_none(style)
    if allowed:
        kwargs["style"] = allowed
    return InlineKeyboardButton(**kwargs)


def _markup(rows: list[list[InlineKeyboardButton]]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[r for r in rows if r])


# ── Главное меню ──────────────────────────────────────────────────────
# Цвет кнопок по умолчанию; админ переопределяет в панели (menu_styles),
# "none" там — «без цвета».
DEFAULT_STYLES: dict[str, str] = {"trial": "success", "referral": "success"}


def menu_style(config: Config, key: str) -> str | None:
    chosen = config.menu_styles.get(key)
    if chosen is None:
        return DEFAULT_STYLES.get(key)
    return None if chosen == "none" else chosen


def main_menu(
    config: Config,
    *,
    trial_available: bool,
    has_subscription: bool,
    balance_kopeks: int,
) -> InlineKeyboardMarkup:
    """Порядок как в исходном боте: покупка, подписки, подключение,
    кабинет, баланс, рефералка, промокод, канал и поддержка. Сколько
    кнопок в ряд (1–3) и их цвет задаются в панели.

    Нет ни одного ключа — «Купить подписку». Есть — «Продлить подписку»:
    раньше «Купить» при живой подписке заводил второй ключ вместо продления.
    Отдельный новый ключ — «Купить ещё одну», если это разрешено в панели.
    """
    items: list[tuple[str, str, dict]] = []  # (ключ, подпись, параметры кнопки)
    if trial_available and config.shows("trial"):
        items.append(("trial", "Попробовать бесплатно", {"callback_data": "trial", "icon": "trial"}))

    if has_subscription:
        items.append(("buy", "Продлить подписку", {"callback_data": "renew", "icon": "clock"}))
        if config.allow_multiple_subscriptions:
            items.append(("buy_more", "Купить ещё одну", {"callback_data": "plans_new", "icon": "buy"}))
        if config.shows("subscriptions"):
            items.append(
                ("subscriptions", "Подписка", {"callback_data": "my_subscriptions", "icon": "subscription"})
            )
        # «Подключиться» без единого ключа вело бы в тупик — прячем.
        if config.shows("connect"):
            items.append(("connect", "Подключиться", {"callback_data": "connect", "icon": "connect"}))
    else:
        items.append(("buy", "Купить подписку", {"callback_data": "plans", "icon": "buy"}))

    if config.shows("profile"):
        items.append(("profile", "Личный кабинет", {"callback_data": "profile", "icon": "profile"}))
    if config.shows("balance"):
        items.append(
            ("balance", f"Баланс: {format_rub(balance_kopeks)}", {"callback_data": "wallet", "icon": "balance"})
        )
    if config.referral_visible and config.shows("referral"):
        items.append(("referral", "Рефералка", {"callback_data": "referral", "icon": "referral"}))
    if config.shows("promo"):
        items.append(("promo", "Промокод", {"callback_data": "promo", "icon": "promo"}))
    if config.channel_url and config.shows("channel"):
        items.append(("channel", "Наш канал", {"url": config.channel_url, "icon": "channel"}))
    if config.support_url and config.shows("support"):
        items.append(("support", "Поддержка", {"url": config.support_url, "icon": "support"}))

    buttons = [
        _btn(config, text, style=menu_style(config, key), **params) for key, text, params in items
    ]
    if config.menu_columns == 1:
        # Канал и поддержка — короткие, рядом друг с другом, как раньше.
        tail = [b for (key, _, _), b in zip(items, buttons, strict=True) if key in ("channel", "support")]
        head = [b for (key, _, _), b in zip(items, buttons, strict=True) if key not in ("channel", "support")]
        return _markup([[b] for b in head] + [tail])
    step = config.menu_columns
    return _markup([buttons[i : i + step] for i in range(0, len(buttons), step)])


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
        discounted_kopeks(p.price_kopeks, config.plan_discount(p, discount_percent))
        for p in config.plans
        if p.category_id == category_id
    ]
    return min(prices) if prices else 0


def _category_button(config: Config, category_id, title: str, prefix: str, discount: int):
    cheapest = category_from_price(config, category_id, discount)
    label = f"{title} • от {format_rub(cheapest)}" if cheapest else title
    return _btn(
        config,
        label,
        callback_data=f"plancat:{prefix}:{category_id if category_id is not None else 0}",
        icon="plan",
    )


def categories_menu(
    config: Config,
    *,
    prefix: str = "buy",
    back: str = "menu",
    discount_percent: int = 0,
    personal: list[PlanView] | None = None,
) -> InlineKeyboardMarkup:
    """Экран выбора категории. Персональные тарифы клиента — сверху,
    отдельно от вкладок: они не принадлежат ни одной категории."""
    rows = [
        [_plan_button(config, plan, prefix, discount_percent)] for plan in personal or []
    ]
    for category_id, title in plan_categories(config):
        rows.append([_category_button(config, category_id, title, prefix, discount_percent)])
    rows.append([_btn(config, "Отмена", callback_data=back, icon="cancel", style="danger")])
    return _markup(rows)


def plan_price_label(plan, discount_percent: int = 0) -> str:
    """«399 ₽» или «299 ₽ вместо 399 ₽». Зачёркивание Telegram в кнопках
    не рисует (а комбинируемые символы выглядят криво), поэтому — словами."""
    price = discounted_kopeks(plan.price_kopeks, discount_percent)
    if price == plan.price_kopeks:
        return format_rub(price)
    return f"{format_rub(price)} вместо {format_rub(plan.price_kopeks)}"


def _plan_button(config: Config, plan: PlanView, prefix: str, user_discount: int):
    discount = config.plan_discount(plan, user_discount)
    title = f"⭐ {plan.title}" if plan.is_personal else plan.title
    return _btn(
        config,
        f"{title} • {plan_price_label(plan, discount)}",
        callback_data=f"{prefix}:{plan.id}",
        icon="star" if plan.is_personal else "period",
    )


def plans_menu(
    config: Config,
    *,
    prefix: str = "buy",
    category_id: int | None = -1,
    discount_percent: int = 0,
    back: str = "menu",
    with_promo: bool = True,
    personal: list[PlanView] | None = None,
) -> InlineKeyboardMarkup:
    """category_id: -1 — без фильтра (все тарифы, старое поведение),
    None — только тарифы без категории, иначе — тарифы этой категории.

    discount_percent — скидка клиента по промокоду; общая скидка из панели
    учитывается для каждого тарифа отдельно (см. Config.plan_discount)."""
    rows = []
    if category_id == -1:
        rows += [[_plan_button(config, plan, prefix, discount_percent)] for plan in personal or []]
    for plan in config.plans:
        if category_id != -1 and plan.category_id != category_id:
            continue
        rows.append([_plan_button(config, plan, prefix, discount_percent)])

    if with_promo and not discount_percent and config.shows("promo"):
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
        rows.append([_btn(config, "СБП / карта", callback_data=f"pay:{purpose}:platega:{target}", icon="sbp")])
    if config.rollypay_ready:
        rows.append([_btn(config, "СБП (резерв)", callback_data=f"pay:{purpose}:rollypay:{target}", icon="sbp")])
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
            [_btn(config, "Оплатить", url=pay_url, icon="sbp", style="success")],
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
        rows.append(
            [
                _btn(config, "Подключиться", callback_data=f"connect:{subscription_id}", icon="connect"),
                _btn(config, "QR-код", callback_data=f"qr:{subscription_id}", icon="link"),
            ]
        )
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
def profile_menu(config: Config, *, has_subscription: bool = False) -> InlineKeyboardMarkup:
    rows = []
    if config.shows("balance"):
        rows.append([_btn(config, "Пополнить баланс", callback_data="wallet_topup", icon="balance")])
    if has_subscription:
        rows.append([_btn(config, "Продлить подписку", callback_data="renew", icon="clock")])
    else:
        rows.append([_btn(config, "Купить подписку", callback_data="plans", icon="buy")])
    if config.shows("history"):
        rows.append([_btn(config, "История покупок", callback_data="purchase_history", icon="history")])
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
        _btn(config, f"+{amount} ₽", callback_data=f"topup:{amount}")
        for amount in TOPUP_PRESETS_RUB
    ]
    return _markup(
        [
            presets[:2],
            presets[2:],
            [_btn(config, "Другая сумма", callback_data="topup_custom")],
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
                    icon_custom_emoji_id=(
                        icon_id("copy", config.premium_emoji) if config.premium else None
                    ),
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
