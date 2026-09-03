"""Сводка по подписке пользователя бота — общая для бота и панели.

У пользователя может быть несколько ключей (`bot_subscriptions`), а меню,
напоминания, сегменты рассылок и статистика смотрят на одно поле
`BotUser.expire_at`. Раньше оно застревало на первом выданном ключе
(обычно триале): платящий клиент через три дня получал «подписка
закончилась» и попадал в сегмент «истёкшие». Здесь единственное правило:
сводка = ключ с самой поздней датой окончания.

Панель, меняя пользователя в Remnawave (выдать дни, статус), обязана
дёрнуть `sync_from_remote` — иначе бот продолжит показывать старую дату.
"""

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from shared.db.models import BotSubscription, BotUser


def _aware(value: datetime | None) -> datetime | None:
    if value is not None and value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value


async def refresh_user_summary(db: AsyncSession, user: BotUser) -> None:
    """Пересчитывает BotUser.expire_at / subscription_url / remnawave_uuid
    по ключам пользователя. Ключ без даты окончания не считается."""
    rows = list(
        await db.scalars(
            select(BotSubscription).where(BotSubscription.user_id == user.id)
        )
    )
    dated = [r for r in rows if r.expire_at is not None]
    if not dated:
        return
    best = max(dated, key=lambda r: _aware(r.expire_at))
    user.remnawave_uuid = str(best.remnawave_id)
    user.subscription_url = best.subscription_url
    user.expire_at = best.expire_at


async def sync_from_remote(
    db: AsyncSession,
    *,
    remnawave_id: int,
    expire_at: datetime | None,
    subscription_url: str | None,
) -> bool:
    """Обновляет локальный ключ по данным Remnawave. Возвращает, был ли
    такой ключ вообще известен боту (ручные пользователи панели — нет)."""
    row = await db.scalar(
        select(BotSubscription).where(BotSubscription.remnawave_id == remnawave_id)
    )
    if row is None:
        return False
    row.expire_at = expire_at
    if subscription_url:
        row.subscription_url = subscription_url
    user = await db.get(BotUser, row.user_id)
    if user is not None:
        await refresh_user_summary(db, user)
    return True
