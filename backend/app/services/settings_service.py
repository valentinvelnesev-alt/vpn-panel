"""Хранилище настроек панели: ключ-значение в БД с шифрованием секретов."""

from typing import Final

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import decrypt, encrypt
from shared.db.models import Setting

# Ключи настроек. Секретные шифруются и наружу отдаются маскированными.
REMNAWAVE_URL: Final = "remnawave_url"
REMNAWAVE_TOKEN: Final = "remnawave_token"
REMNAWAVE_VERIFY_TLS: Final = "remnawave_verify_tls"
BRAND_NAME: Final = "brand_name"
BRAND_LOGO_URL: Final = "brand_logo_url"
HIDE_POWERED_BY: Final = "hide_powered_by"

PLATEGA_ENABLED: Final = "payment_platega_enabled"
PLATEGA_MERCHANT_ID: Final = "payment_platega_merchant_id"
PLATEGA_SECRET: Final = "payment_platega_secret"

ROLLYPAY_ENABLED: Final = "payment_rollypay_enabled"
ROLLYPAY_API_KEY: Final = "payment_rollypay_api_key"

CRYPTOBOT_ENABLED: Final = "payment_cryptobot_enabled"
CRYPTOBOT_TOKEN: Final = "payment_cryptobot_token"

STARS_ENABLED: Final = "payment_stars_enabled"

SECRET_KEYS: Final = frozenset(
    {REMNAWAVE_TOKEN, PLATEGA_SECRET, ROLLYPAY_API_KEY, CRYPTOBOT_TOKEN}
)


async def get(db: AsyncSession, key: str) -> str | None:
    """Возвращает значение, расшифровывая секреты."""
    row = await db.get(Setting, key)
    if row is None or row.value is None:
        return None
    return decrypt(row.value) if row.is_secret else row.value


async def get_many(db: AsyncSession, *keys: str) -> dict[str, str | None]:
    rows = await db.scalars(select(Setting).where(Setting.key.in_(keys)))
    found = {
        row.key: (decrypt(row.value) if row.is_secret and row.value else row.value)
        for row in rows
    }
    return {key: found.get(key) for key in keys}


async def set_(db: AsyncSession, key: str, value: str | None) -> None:
    is_secret = key in SECRET_KEYS
    stored = encrypt(value) if (is_secret and value) else value
    # UPSERT: настройка может ещё не существовать, а гонки двух вкладок
    # панели не должны падать на конфликте первичного ключа.
    await db.execute(
        pg_insert(Setting)
        .values(key=key, value=stored, is_secret=is_secret)
        .on_conflict_do_update(
            index_elements=[Setting.key],
            set_={"value": stored, "is_secret": is_secret},
        )
    )


async def is_configured(db: AsyncSession) -> bool:
    """Настроено ли подключение к Remnawave — от этого зависит весь дашборд."""
    values = await get_many(db, REMNAWAVE_URL, REMNAWAVE_TOKEN)
    return bool(values[REMNAWAVE_URL] and values[REMNAWAVE_TOKEN])
