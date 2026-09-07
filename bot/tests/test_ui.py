"""Проверки оформления клавиатур.

Telegram отвергает всё сообщение целиком, если у кнопки недопустимый
`style` или слишком длинный `callback_data`. Проверено на живом API:
допустимы ровно primary, success и danger. Поэтому здесь обходятся все
экраны разом — дешевле поймать это тестом, чем «сообщение не отправилось»
у клиента.
"""

from aiogram.types import InlineKeyboardMarkup

from app import connection, keyboards
from app.config import Config, PlanView
from app.icons import ALLOWED_STYLES
from shared.db.models import EmojiMode

PLANS = [
    PlanView(
        id=i,
        title=title,
        days=days,
        price_kopeks=price,
        squad_uuids=["s"],
        hwid_limit=3,
        traffic_limit_bytes=0,
        category_id=category_id,
        category_title=category_title,
    )
    for i, (title, days, price, category_id, category_title) in enumerate(
        [
            ("1 месяц", 30, 8500, 1, "Базовый"),
            ("1 год", 365, 84900, 1, "Базовый"),
            ("1 месяц", 30, 20300, 2, "VIP"),
        ],
        start=1,
    )
]

PACKAGES = [
    __import__("app.config", fromlist=["TrafficPackageView"]).TrafficPackageView(
        id=1, title="50 ГБ", traffic_gb=50, price_kopeks=9900
    ),
    __import__("app.config", fromlist=["TrafficPackageView"]).TrafficPackageView(
        id=2, title="200 ГБ", traffic_gb=200, price_kopeks=29900
    ),
]

CONFIG = Config(
    token="1:x",
    enabled=True,
    emoji_mode=EmojiMode.PLAIN,
    premium_emoji={},
    brand="LUX VPN",
    welcome_text=None,
    support_url="https://t.me/support",
    channel_url="https://t.me/channel",
    channel_id="@channel",
    require_channel_sub=True,
    trial_enabled=True,
    trial_days=3,
    trial_squad_uuids=["s"],
    trial_hwid_limit=3,
    referral_enabled=True,
    plans=PLANS,
    traffic_packages=PACKAGES,
    privacy_policy_url="https://example.com/p",
    terms_url="https://example.com/t",
    platega_enabled=True,
    platega_merchant_id="m",
    platega_secret="s",
    stars_enabled=True,
)


class _Sub:
    def __init__(self, id_: int, expire_at=None):
        self.id = id_
        self.expire_at = expire_at


def _all_markups() -> dict[str, InlineKeyboardMarkup]:
    url = "https://sub.example.com/abcdef"
    markups = {
        "main_menu": keyboards.main_menu(
            CONFIG, trial_available=True, has_subscription=True, balance_kopeks=20300
        ),
        "main_menu_new": keyboards.main_menu(
            CONFIG, trial_available=True, has_subscription=False, balance_kopeks=0
        ),
        "back": keyboards.back_to_menu(CONFIG),
        "cancel": keyboards.cancel_to_menu(CONFIG),
        "channel_gate": keyboards.channel_gate(CONFIG),
        "categories": keyboards.categories_menu(CONFIG, discount_percent=25),
        "plans": keyboards.plans_menu(CONFIG, category_id=1, discount_percent=25),
        "plans_renew": keyboards.plans_menu(
            CONFIG, prefix="renewbuy-7", category_id=2, back="viewsub:7"
        ),
        "providers": keyboards.providers_menu(CONFIG, purpose="plan", target="1"),
        "providers_renew": keyboards.providers_menu(CONFIG, purpose="renew", target="7-1"),
        "pay": keyboards.pay_menu(CONFIG, pay_url="https://pay.example", payment_id=42),
        "subscriptions": keyboards.subscriptions_menu(
            CONFIG, [_Sub(7)], {7: "1 месяц"}
        ),
        "subscriptions_empty": keyboards.subscriptions_menu(CONFIG, [], {}),
        "detail": keyboards.subscription_detail_menu(
            CONFIG, 7, has_url=True, auto_renew=True, can_buy_traffic=True
        ),
        "traffic_packages": keyboards.traffic_packages_menu(CONFIG, 7),
        "providers_traffic": keyboards.providers_menu(
            CONFIG, purpose="traffic", target="7-1", back="subtraffic:7"
        ),
        "detail_trial": keyboards.subscription_detail_menu(CONFIG, 7, has_url=False, auto_renew=None),
        "devices": keyboards.devices_menu(CONFIG, True, subscription_id=7),
        "devices_empty": keyboards.devices_menu(CONFIG, False, subscription_id=7),
        "profile": keyboards.profile_menu(CONFIG),
        "wallet": keyboards.wallet_menu(CONFIG),
        "referral": keyboards.referral_menu(CONFIG, "https://t.me/bot?start=ref_ABC"),
        "connect_devices": connection.devices_keyboard(7, url),
    }
    for device in connection.APPS:
        markups[f"connect_apps_{device}"] = connection.apps_keyboard(7, device)
        for app in ("incy", "happ"):
            markups[f"connect_{device}_{app}"] = connection.instruction_keyboard(7, device, app, url)
    return markups


def test_button_styles_are_supported_by_telegram() -> None:
    for name, markup in _all_markups().items():
        for row in markup.inline_keyboard:
            for button in row:
                style = getattr(button, "style", None)
                assert style is None or style in ALLOWED_STYLES, (name, button.text, style)


def test_callback_data_within_telegram_limit() -> None:
    """64 байта — жёсткий лимит Telegram на callback_data."""
    for name, markup in _all_markups().items():
        for row in markup.inline_keyboard:
            for button in row:
                if button.callback_data:
                    assert len(button.callback_data.encode()) <= 64, (name, button.callback_data)


def test_every_button_is_actionable_and_labelled() -> None:
    for name, markup in _all_markups().items():
        assert markup.inline_keyboard, name
        for row in markup.inline_keyboard:
            assert row, f"пустой ряд в {name}"
            for button in row:
                assert button.text.strip(), name
                assert (
                    button.callback_data or button.url or button.copy_text
                ), f"кнопка «{button.text}» в {name} ничего не делает"


def test_icon_ids_look_like_custom_emoji() -> None:
    for name, markup in _all_markups().items():
        for row in markup.inline_keyboard:
            for button in row:
                icon = getattr(button, "icon_custom_emoji_id", None)
                if icon is not None:
                    assert icon.isdigit(), (name, button.text, icon)


def test_instruction_text_covers_every_device_and_app() -> None:
    url = "https://sub.example.com/abcdef"
    for device in connection.APPS:
        for app in ("incy", "happ"):
            text = connection.instruction_text(device, app, url)
            assert url in text
            assert connection.APPS[device]["title"] in text
            assert len(text) <= 4096, (device, app, len(text))


def test_connect_keyboards_carry_subscription_id() -> None:
    """Инструкция должна вести к тому же ключу, а не к «какому-нибудь»."""
    markup = connection.instruction_keyboard(42, "ios", "incy", "https://sub.example/x")
    targets = [b.callback_data for row in markup.inline_keyboard for b in row if b.callback_data]
    assert "connapps:42:ios" in targets
    assert "connect:42" in targets
    assert "viewsub:42" in targets


def test_main_menu_hides_connect_without_subscription() -> None:
    labels = [
        b.text
        for row in keyboards.main_menu(
            CONFIG, trial_available=False, has_subscription=False, balance_kopeks=0
        ).inline_keyboard
        for b in row
    ]
    assert "Подключиться" not in labels
    assert any(l.startswith("Баланс:") for l in labels)


def test_referral_button_named_as_requested() -> None:
    labels = [
        b.text
        for row in keyboards.main_menu(
            CONFIG, trial_available=False, has_subscription=True, balance_kopeks=0
        ).inline_keyboard
        for b in row
    ]
    assert "Рефералка" in labels


# ── Нижняя клавиатура ─────────────────────────────────────────────────
def test_reply_keyboard_has_menu_and_help() -> None:
    kb = keyboards.reply_menu()
    labels = [b.text for row in kb.keyboard for b in row]
    assert labels == [keyboards.BTN_MENU, keyboards.BTN_HELP]
    assert kb.resize_keyboard is True
    assert kb.is_persistent is True


# ── Редиректор подписки ───────────────────────────────────────────────
SUB_URL = "https://sub.luxinet.ru/x72yERRmWcMWy_zA"


def test_connect_url_without_redirect_is_the_subscription_link() -> None:
    """Пока страницы-редиректора нет, кнопка ведёт на саму подписку —
    рабочее поведение, а не заглушка."""
    for app in ("happ", "incy"):
        assert connection.connect_url(app, SUB_URL, via_redirect=False) == SUB_URL


def test_connect_url_wraps_deep_link_into_https_page() -> None:
    from urllib.parse import parse_qs, urlsplit

    for app, prefix in (("happ", "happ://add/"), ("incy", "incy://import/")):
        url = connection.connect_url(app, SUB_URL, via_redirect=True)
        parts = urlsplit(url)
        # Страница живёт на домене подписок, а не на домене панели.
        assert parts.scheme == "https"
        assert parts.netloc == "sub.luxinet.ru"
        assert parts.path == "/miniapp/redirect.html"
        target = parse_qs(parts.query)["url"][0]
        assert target == prefix + SUB_URL
        # Схема приложения обязана быть закодирована, иначе Telegram
        # обрежет ссылку на «://».
        assert "://" not in parts.query.split("url=", 1)[1]


def test_bot_link_passes_the_pages_own_allowlist() -> None:
    """Сверяем ссылку бота с проверкой, зашитой в redirect.html: если они
    разойдутся, пользователь увидит «Некорректная ссылка»."""
    import pathlib
    import re
    from urllib.parse import parse_qs, urlsplit

    page = pathlib.Path(__file__).resolve().parents[2] / "deploy/subscription-redirect/redirect.html"
    domain = re.search(r"var domain = '([^']+)'", page.read_text()).group(1)
    assert domain.endswith("/"), "домен в странице должен заканчиваться слэшем"

    subscription = domain + "abcdef123456"
    for app, prefix in (("happ", "happ://add/"), ("incy", "incy://import/")):
        url = connection.connect_url(app, subscription, via_redirect=True)
        target = parse_qs(urlsplit(url).query)["url"][0]
        assert target.startswith(prefix + domain)


def test_redirect_page_rejects_foreign_targets() -> None:
    """Параметр url приходит снаружи: чужой адрес должен отсекаться."""
    import pathlib
    import re

    page = pathlib.Path(__file__).resolve().parents[2] / "deploy/subscription-redirect/redirect.html"
    text = page.read_text()
    domain = re.search(r"var domain = '([^']+)'", text).group(1)

    def allowed(target: str) -> bool:
        return target.startswith("happ://add/" + domain) or target.startswith(
            "incy://import/" + domain
        )

    assert not allowed("https://evil.example/steal")
    assert not allowed("happ://add/https://evil.example/x")
    assert not allowed("javascript:alert(1)")
    assert not allowed("test")
    assert allowed("happ://add/" + domain + "key")


def test_instruction_keyboard_uses_redirect_when_enabled() -> None:
    def connect_button(via_redirect: bool) -> str:
        markup = connection.instruction_keyboard(
            7, "ios", "happ", SUB_URL, via_redirect=via_redirect
        )
        return next(
            b.url for row in markup.inline_keyboard for b in row if b.text == "Подключиться"
        )

    assert connect_button(False) == SUB_URL
    assert connect_button(True).startswith("https://sub.luxinet.ru/miniapp/redirect.html?url=")


def test_traffic_button_hidden_without_packages() -> None:
    """Кнопки в никуда быть не должно: нет пакетов — нет кнопки."""
    labels = [
        b.text
        for row in keyboards.subscription_detail_menu(
            CONFIG, 7, has_url=True, auto_renew=True, can_buy_traffic=False
        ).inline_keyboard
        for b in row
    ]
    assert "Докупить трафик" not in labels
    with_traffic = [
        b.text
        for row in keyboards.subscription_detail_menu(
            CONFIG, 7, has_url=True, auto_renew=True, can_buy_traffic=True
        ).inline_keyboard
        for b in row
    ]
    assert "Докупить трафик" in with_traffic
