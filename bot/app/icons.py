"""Иконки на кнопках.

У Telegram два разных механизма, и путать их нельзя:

* в тексте сообщения — тег ``<tg-emoji emoji-id="…">символ</tg-emoji>``
  (см. texts.py). Символ-заглушка обязан совпадать с эмодзи из набора,
  иначе Telegram отвергает всё сообщение целиком;
* на кнопке — поле ``icon_custom_emoji_id``. HTML в тексте кнопки не
  работает вообще, поэтому только так.

Проверено на живом API: `icon_custom_emoji_id`, `copy_text` и стили
кнопок принимаются, но допустимых стилей ровно три — primary, success и
danger. Любое другое значение («secondary» в том числе) Telegram
отклоняет вместе со всем сообщением, поэтому STYLES — белый список.
"""

from typing import Final

# Стили кнопок, которые Telegram реально принимает.
STYLE_PRIMARY: Final = "primary"
STYLE_SUCCESS: Final = "success"
STYLE_DANGER: Final = "danger"
ALLOWED_STYLES: Final = frozenset({STYLE_PRIMARY, STYLE_SUCCESS, STYLE_DANGER})

# id премиум-эмодзи по смысловому ключу. Здесь только те, что реально
# известны (пришли вместе с модулем инструкций подключения). Остальные
# ключи владелец панели может добавить сам — до тех пор кнопка просто
# рисуется без иконки, ничего не ломается.
DEFAULT_ICONS: Final[dict[str, str]] = {
    "ios": "5416091637296177387",
    "android": "5415662007422590516",
    "windows": "5416021118228145148",
    "macos": "5415719109012797365",
    "download": "5258336354642697821",
    "connect": "5258336354642697821",
    "copy": "5258477770735885832",
    "other": "5258389041006518073",
    "devices": "5226513232549664618",
    "back": "5258236805890710909",
    "recommended": "5260416304224936047",
}


def icon_id(key: str | None, overrides: dict[str, str] | None = None) -> str | None:
    """id иконки для кнопки. Карта из панели важнее встроенной."""
    if not key:
        return None
    if overrides:
        found = overrides.get(key)
        if found:
            return found
    return DEFAULT_ICONS.get(key)


def style_or_none(style: str | None) -> str | None:
    """Отдаёт стиль только из белого списка — чужое значение уронило бы
    отправку сообщения, а не просто «покрасило бы иначе»."""
    return style if style in ALLOWED_STYLES else None
