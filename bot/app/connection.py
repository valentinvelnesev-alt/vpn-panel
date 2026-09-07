"""Экран «Подключиться»: выбор устройства, приложение, инструкция.

Ссылка подписки одна и та же для всех устройств — меняются только шаги
установки. Экран открывается и из главного меню, и из карточки ключа,
поэтому конкретная подписка передаётся в callback_data: у пользователя
их может быть несколько, и «взять первую попавшуюся» было бы неверно.
"""

from html import escape
from typing import Any
from urllib.parse import quote, urlsplit

from aiogram.types import CopyTextButton, InlineKeyboardButton, InlineKeyboardMarkup

from app.icons import icon_id, style_or_none

# Схемы импорта подписки в приложениях.
DEEP_LINK_PREFIX = {"happ": "happ://add/", "incy": "incy://import/"}
# Страница-редиректор на домене подписок. Telegram не открывает happ:// и
# incy:// напрямую, поэтому ссылку заворачиваем в обычный https-адрес,
# который уже перебрасывает в приложение.
REDIRECT_PATH = "/miniapp/redirect.html"


def deep_link(app: str, subscription_url: str) -> str:
    return DEEP_LINK_PREFIX[app] + subscription_url


def redirect_url(app: str, subscription_url: str) -> str:
    """https://<домен подписок>/miniapp/redirect.html?url=<кодированная схема>.

    Домен берётся из самой ссылки подписки: страница-редиректор пускает
    только свой домен, поэтому чужой сюда подставить нельзя."""
    parts = urlsplit(subscription_url)
    base = f"{parts.scheme}://{parts.netloc}{REDIRECT_PATH}"
    return f"{base}?url={quote(deep_link(app, subscription_url), safe='')}"


def connect_url(app: str, subscription_url: str, *, via_redirect: bool) -> str:
    """Куда ведёт кнопка «Подключиться».

    Без редиректора отдаём саму ссылку подписки: она открывается страницей
    в браузере и работает всегда. Это же поведение остаётся запасным, пока
    страница на домене подписок не развёрнута."""
    if not via_redirect:
        return subscription_url
    return redirect_url(app, subscription_url)

APPS: dict[str, dict[str, Any]] = {
    "ios": {
        "title": "iPhone/iPad",
        "icon": "ios",
        "recommended": "incy",
        "incy": {"store": "https://apps.apple.com/app/id6756943388", "store_label": "App Store"},
        "happ": {
            "store": "https://apps.apple.com/us/app/happ-proxy-utility/id6504287215",
            "store_label": "App Store (Global)",
        },
    },
    "android": {
        "title": "Android",
        "icon": "android",
        "recommended": "incy",
        "incy": {
            "store": "https://play.google.com/store/apps/details?id=llc.itdev.incy",
            "store_label": "Открыть в Google Play",
            "apk": "https://github.com/INCY-DEV/incy-platforms/releases/latest/download/Incy.apk",
        },
        "happ": {
            "store": "https://play.google.com/store/apps/details?id=com.happproxy",
            "store_label": "Открыть в Google Play",
            "apk": "https://github.com/Happ-proxy/happ-android/releases/latest/download/Happ.apk",
        },
    },
    "windows": {
        "title": "Windows",
        "icon": "windows",
        "recommended": "happ",
        "incy": {
            "store": "https://github.com/INCY-DEV/incy-platforms/releases/latest/download/incy-windows-setup.exe",
            "store_label": "Скачать для Windows",
        },
        "happ": {
            "store": "https://github.com/Happ-proxy/happ-desktop/releases/latest/download/setup-Happ.x64.exe",
            "store_label": "Скачать для Windows",
        },
    },
    "macos": {
        "title": "macOS",
        "icon": "macos",
        "recommended": "happ",
        "incy": {
            "store": "https://apps.apple.com/app/id6756943388",
            "store_label": "App Store",
            "apk": "https://github.com/INCY-DEV/incy-platforms/releases/latest/download/incy-macos-intel.dmg",
            "apk_label": "Скачать .dmg",
        },
        "happ": {
            "store": "https://apps.apple.com/us/app/happ-proxy-utility/id6504287215",
            "store_label": "App Store (Global)",
            "apk": "https://github.com/Happ-proxy/happ-desktop/releases/latest/download/Happ.macOS.universal.dmg",
            "apk_label": "Скачать .dmg",
        },
    },
    "androidtv": {
        "title": "Android TV",
        "icon": "android",
        "recommended": "incy",
        "incy": {
            "store": "https://play.google.com/store/apps/details?id=llc.itdev.incy",
            "store_label": "Открыть в Google Play",
            "apk": "https://github.com/INCY-DEV/incy-platforms/releases/latest/download/Incy.apk",
        },
        "happ": {
            "store": "https://play.google.com/store/apps/details?id=com.happproxy",
            "store_label": "Открыть в Google Play",
            "apk": "https://github.com/Happ-proxy/happ-android/releases/latest/download/Happ.apk",
        },
    },
    "appletv": {
        "title": "Apple TV",
        "icon": "ios",
        "recommended": "incy",
        "incy": {"store": "https://apps.apple.com/app/id6756943388", "store_label": "App Store"},
        "happ": {
            "store": "https://apps.apple.com/us/app/happ-proxy-utility-for-tv/id6748297274",
            "store_label": "App Store",
        },
    },
}

APP_NAMES = {"incy": "INCY", "happ": "Happ"}


def _button(
    text: str,
    *,
    callback_data: str | None = None,
    url: str | None = None,
    icon: str | None = None,
    style: str | None = None,
    overrides: dict[str, str] | None = None,
) -> InlineKeyboardButton:
    kwargs: dict[str, Any] = {"text": text}
    if callback_data:
        kwargs["callback_data"] = callback_data
    if url:
        kwargs["url"] = url
    emoji = icon_id(icon, overrides)
    if emoji:
        kwargs["icon_custom_emoji_id"] = emoji
    allowed = style_or_none(style)
    if allowed:
        kwargs["style"] = allowed
    return InlineKeyboardButton(**kwargs)


def devices_keyboard(subscription_id: int, url: str, overrides=None) -> InlineKeyboardMarkup:
    def b(text, **kw):
        return _button(text, overrides=overrides, **kw)

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                b("iPhone/iPad", callback_data=f"conn:{subscription_id}:ios", icon="ios"),
                b("Android", callback_data=f"conn:{subscription_id}:android", icon="android"),
            ],
            [
                b("Windows", callback_data=f"conn:{subscription_id}:windows", icon="windows"),
                b("macOS", callback_data=f"conn:{subscription_id}:macos", icon="macos"),
            ],
            [
                b("Android TV", callback_data=f"conn:{subscription_id}:androidtv", icon="android"),
                b("Apple TV", callback_data=f"conn:{subscription_id}:appletv", icon="ios"),
            ],
            [
                InlineKeyboardButton(
                    text="Скопировать ссылку подписки",
                    copy_text=CopyTextButton(text=url),
                    icon_custom_emoji_id=icon_id("copy", overrides),
                )
            ],
            [b("Назад", callback_data=f"viewsub:{subscription_id}", icon="back")],
        ]
    )


def instruction_text(device: str, app: str, url: str) -> str:
    config = APPS[device]
    title = config["title"]
    name = APP_NAMES[app]
    link = f"<b>Ссылка подписки:</b>\n<code>{escape(url)}</code>"

    if app == "happ" and device == "ios":
        return (
            f"<b>{name} · {title}</b>\n\n{link}\n\n"
            "<b>1. Установка</b>\n"
            "В российском регионе App Store приложения Happ нет. Если у вас российский "
            "регион, сначала смените регион App Store, затем установите приложение по "
            "кнопке ниже. На запрос добавить VPN-конфигурацию нажмите «Разрешить» и "
            "введите пароль от устройства.\n\n"
            "<b>2. Добавление подписки</b>\n"
            "Нажмите «Подключиться» — приложение откроется и подписка добавится сама.\n\n"
            "<b>3. Подключение</b>\n"
            "Выберите сервер и нажмите большую кнопку включения."
        )

    if device in {"androidtv", "appletv"}:
        return (
            f"<b>{name} · {title}</b>\n\n{link}\n\n"
            "<b>1. Установка</b>\n"
            "Установите приложение из магазина по кнопке ниже. Если магазин недоступен, "
            "поставьте APK-файл напрямую.\n\n"
            "<b>2. Добавление подписки</b>\n"
            "На телевизоре удобнее скопировать ссылку подписки и вставить её в приложение "
            "вручную — кнопка «Скопировать подписку» ниже.\n\n"
            "<b>3. Подключение</b>\n"
            "Выберите сервер и включите VPN."
        )

    return (
        f"<b>{name} · {title}</b>\n\n{link}\n\n"
        "<b>1. Установка</b>\n"
        "Установите приложение по кнопке ниже.\n\n"
        "<b>2. Добавление подписки</b>\n"
        "Нажмите «Подключиться» — приложение откроется и подписка добавится сама.\n\n"
        "<b>3. Подключение</b>\n"
        "В приложении нажмите большую кнопку включения. Готово.\n\n"
        "Если подписка не добавилась сама — нажмите «Скопировать подписку» и вставьте "
        "ссылку в приложение вручную."
    )


def instruction_keyboard(
    subscription_id: int,
    device: str,
    app: str,
    url: str,
    overrides=None,
    *,
    via_redirect: bool = False,
) -> InlineKeyboardMarkup:
    config = APPS[device][app]

    def b(text, **kw):
        return _button(text, overrides=overrides, **kw)

    rows = [[b(config["store_label"], url=config["store"], icon="download", style="primary")]]
    if config.get("apk"):
        rows.append(
            [b(config.get("apk_label", "Скачать APK"), url=config["apk"], icon="download", style="primary")]
        )
    rows += [
        [
            b(
                "Подключиться",
                url=connect_url(app, url, via_redirect=via_redirect),
                icon="connect",
                style="success",
            )
        ],
        [
            InlineKeyboardButton(
                text="Скопировать подписку",
                copy_text=CopyTextButton(text=url),
                icon_custom_emoji_id=icon_id("copy", overrides),
            )
        ],
        [b("Другое приложение", callback_data=f"connapps:{subscription_id}:{device}", icon="other")],
        [b("Другое устройство", callback_data=f"connect:{subscription_id}", icon="devices")],
        [b("К подписке", callback_data=f"viewsub:{subscription_id}", icon="back")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def apps_keyboard(subscription_id: int, device: str, overrides=None) -> InlineKeyboardMarkup:
    recommended = APPS[device]["recommended"]

    def b(text, **kw):
        return _button(text, overrides=overrides, **kw)

    rows = [
        [
            b(
                f"{APP_NAMES[key]} — рекомендуем" if key == recommended else APP_NAMES[key],
                callback_data=f"connapp:{subscription_id}:{device}:{key}",
                icon="recommended" if key == recommended else None,
            )
        ]
        for key in ("incy", "happ")
    ]
    rows += [
        [b("Другое устройство", callback_data=f"connect:{subscription_id}", icon="devices")],
        [b("К подписке", callback_data=f"viewsub:{subscription_id}", icon="back")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)
