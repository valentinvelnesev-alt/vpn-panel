"""Сегменты получателей рассылки.

Общий модуль: панель считает по нему предварительный размер сегмента,
бот — реальный список получателей при отправке. Одна реализация исключает
расхождение между «увидели ~120» и «отправили 150».
"""

from datetime import UTC, datetime

from sqlalchemy import exists, select
from sqlalchemy.sql import Select

from shared.db.models import BotUser, BroadcastSegment, Purchase


def recipients_query(segment: BroadcastSegment) -> Select:
    """select(BotUser), отфильтрованный по сегменту.

    Заблокировавшие бота и забаненные исключаются всегда — рассылка на них
    не долетит и только тратит время воркера.
    """
    query = select(BotUser).where(
        BotUser.has_stopped_bot.is_(False), BotUser.is_blocked.is_(False)
    )
    now = datetime.now(UTC)

    if segment is BroadcastSegment.ACTIVE:
        return query.where(BotUser.expire_at.is_not(None), BotUser.expire_at > now)

    if segment is BroadcastSegment.EXPIRED:
        return query.where(BotUser.expire_at.is_not(None), BotUser.expire_at <= now)

    if segment is BroadcastSegment.NO_PURCHASE:
        # Триал не считается покупкой — цель сегмента как раз в том, чтобы
        # подтолкнуть попробовавших триал к первой оплате.
        paid_exists = exists(
            select(Purchase.id).where(
                Purchase.user_id == BotUser.id, Purchase.source != "trial"
            )
        )
        return query.where(~paid_exists)

    return query  # BroadcastSegment.ALL
