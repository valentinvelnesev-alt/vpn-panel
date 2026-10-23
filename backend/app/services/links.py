"""Ссылки, которые админ вводит в панели руками."""

import re

_USERNAME = re.compile(r"^[A-Za-z][A-Za-z0-9_]{3,31}$")


def normalize_support_link(raw: str | None) -> str | None:
    """«@name», «name», «t.me/name» и обычные ссылки → рабочая ссылка.

    Люди вписывают юзернейм как привыкли; кнопке в боте нужен адрес с
    https://. Не похоже ни на то, ни на другое — ValueError с подсказкой."""
    value = (raw or "").strip()
    if not value:
        return None
    if value.startswith(("https://", "http://", "tg://")):
        return value
    bare = value.removeprefix("@")
    for prefix in ("t.me/", "telegram.me/"):
        if bare.lower().startswith(prefix):
            return f"https://t.me/{bare[len(prefix):].lstrip('@')}"
    if _USERNAME.match(bare):
        return f"https://t.me/{bare}"
    raise ValueError(
        "Укажите @username (например @my_support) или ссылку, начинающуюся с https://"
    )
