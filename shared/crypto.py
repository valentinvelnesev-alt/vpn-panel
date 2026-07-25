"""Шифрование секретов в БД. Общее для панели и бота.

Панель шифрует токены при сохранении, бот расшифровывает свой токен на
старте, поэтому ключ ENCRYPTION_KEY должен быть одинаковым в обоих
контейнерах — он и берётся из общего .env.
"""

import os
from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken


@lru_cache
def _fernet() -> Fernet:
    key = os.environ.get("ENCRYPTION_KEY", "")
    if not key:
        raise RuntimeError("не задан ENCRYPTION_KEY")
    return Fernet(key.encode())


def encrypt(value: str) -> str:
    return _fernet().encrypt(value.encode()).decode()


def decrypt(value: str) -> str:
    try:
        return _fernet().decrypt(value.encode()).decode()
    except InvalidToken as exc:
        raise ValueError(
            "не удалось расшифровать значение — вероятно, сменился ENCRYPTION_KEY"
        ) from exc


def mask(value: str, visible: int = 4) -> str:
    """Маскирует секрет для показа в UI: «1234••••••••abcd»."""
    if len(value) <= visible * 2:
        return "•" * len(value)
    return f"{value[:visible]}{'•' * 8}{value[-visible:]}"
