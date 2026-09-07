"""Экран «Подключиться»: выбор устройства, приложение, инструкция.

Ссылка подписки одна и та же для всех устройств — меняются только шаги
установки. Экран открывается и из главного меню, и из карточки ключа,
поэтому конкретная подписка передаётся в callback_data: у пользователя
их может быть несколько, и «взять первую попавшуюся» было бы неверно.
"""

from html import escape
from typing import Any

from aiogram.types import CopyTextButton, InlineKeyboardButton, InlineKeyboardMarkup

from app.icons import icon_id, style_or_none

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
    subscription_id: int, device: str, app: str, url: str, overrides=None
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
        [b("Подключиться", url=url, icon="connect", style="success")],
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
