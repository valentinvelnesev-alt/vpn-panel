"""Промокоды: бонусные дни или скидка на покупку.

Активация записывается отдельной строкой с уникальным индексом
(promo_code_id, user_id) — повторно применить тот же код нельзя, даже если
два запроса пришли одновременно.
"""

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from shared.db.models import BotUser, PromoCode, PromoCodeActivation


class PromoError(Exception):
    """Причина отказа — показывается пользователю как есть."""


async def find(db: AsyncSession, code: str) -> PromoCode:
    promo = await db.scalar(
        select(PromoCode).where(PromoCode.code == code.strip().upper())
    )
    if promo is None:
        raise PromoError("Промокод не найден")
    if not promo.is_active:
        raise PromoError("Промокод больше не активен")
    if promo.expires_at is not None:
        expires = promo.expires_at
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=UTC)
        if expires <= datetime.now(UTC):
            raise PromoError("Срок действия промокода истёк")
    if promo.max_uses is not None and promo.uses_count >= promo.max_uses:
        raise PromoError("Промокод исчерпан")
    return promo


async def redeem(db: AsyncSession, promo: PromoCode, user: BotUser) -> None:
    """Отмечает использование. Бросает PromoError при повторной попытке.

    Идёт через SAVEPOINT (begin_nested): при конфликте откатывается только
    эта попытка активации, а не вся транзакция — иначе откатились бы и
    более ранние изменения в той же сессии (например, только что созданный
    BotUser).
    """
    try:
        async with db.begin_nested():
            db.add(
                PromoCodeActivation(
                    promo_code_id=promo.id,
                    user_id=user.id,
                    created_at=datetime.now(UTC),
                )
            )
            await db.flush()
    except IntegrityError as exc:
        raise PromoError("Вы уже использовали этот промокод") from exc
    promo.uses_count += 1
