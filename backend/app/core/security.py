import hashlib
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from app.core.config import settings

# Шифрование живёт в shared: бот расшифровывает тем же ключом свой токен.
from shared.crypto import decrypt, encrypt, mask  # noqa: F401  — реэкспорт

_hasher = PasswordHasher()

ALGORITHM = "HS256"
TokenType = Literal["access", "refresh"]


# ── Пароли ────────────────────────────────────────────────────────────
def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    try:
        _hasher.verify(password_hash, password)
    except (VerifyMismatchError, ValueError):
        return False
    return True


def needs_rehash(password_hash: str) -> bool:
    return _hasher.check_needs_rehash(password_hash)


# ── JWT ───────────────────────────────────────────────────────────────
def create_access_token(admin_id: int) -> str:
    now = datetime.now(UTC)
    payload = {
        "sub": str(admin_id),
        "type": "access",
        "iat": now,
        "exp": now + timedelta(minutes=settings.access_token_ttl_minutes),
    }
    return jwt.encode(payload, settings.secret_key, algorithm=ALGORITHM)


def decode_token(token: str, expected_type: TokenType = "access") -> dict[str, Any]:
    """Разбирает и валидирует JWT. Бросает jwt.InvalidTokenError при проблеме."""
    payload = jwt.decode(token, settings.secret_key, algorithms=[ALGORITHM])
    if payload.get("type") != expected_type:
        raise jwt.InvalidTokenError("неверный тип токена")
    return payload


def generate_refresh_token() -> tuple[str, str]:
    """Возвращает (сам токен для клиента, его SHA-256 для хранения в БД)."""
    token = secrets.token_urlsafe(48)
    return token, hash_refresh_token(token)


def hash_refresh_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()
