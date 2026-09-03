"""Автопродление ключа с баланса кошелька.

Раз в час: у ключей с включённым автопродлением, срок которых истекает в
ближайшие 24 часа (или истёк не больше суток назад), списываем цену тарифа
ключа и продлеваем. Не хватает денег — пропускаем: напоминания об истечении
(expiry.py) всё равно предупредят пользователя.

Каждый ключ — своя транзакция: сбой Remnawave у одного откатывает только
его списание, а не продления соседей, уже прошедшие в Remnawave.
"""

import asyncio
import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app import config as config_module
from app import texts
from app.services import subscriptions as subs, wallet
from app.services.notify import send
from shared.db.models import BotSubscription, BotUser, Plan, WalletTxType
from shared.db.session import session

log = logging.getLogger("bot.workers.auto_renewal")

CHECK_INTERVAL = 3600
WINDOW = timedelta(hours=24)
GRACE = timedelta(hours=24)


async def _renew_one(subscription_id: int) -> tuple[int, str] | None:
    """Возвращает (telegram_id, текст уведомления) при успехе."""
    now = datetime.now(UTC)
    async with session() as db:
        config = await config_module.load(db)
        if not config.token:
            return None
        subscription = await db.get(BotSubscription, subscription_id)
        if subscription is None or not subscription.auto_renew or subscription.plan_id is None:
            return None
        expire_at = subscription.expire_at
        if expire_at is None:
            return None
        if expire_at.tzinfo is None:
            expire_at = expire_at.replace(tzinfo=UTC)
        if not (now - GRACE <= expire_at <= now + WINDOW):
            return None

        plan_row = await db.get(Plan, subscription.plan_id)
        if plan_row is None or not plan_row.is_active:
            return None
        plan = await config_module.load_plan(db, subscription.plan_id)
        if plan is None:
            return None
        user = await db.get(BotUser, subscription.user_id)
        if user is None:
            return None

        try:
            await wallet.debit(
                db,
                user,
                plan.price_kopeks,
                WalletTxType.AUTO_RENEWAL,
                description=f"Автопродление: {plan.title}",
            )
        except wallet.InsufficientFunds:
            return None  # не хватает баланса — пропускаем без ошибки

        subscription = await subs.extend_subscription(
            db, config, subscription, plan, source="auto_renewal"
        )
        text = texts.render(
            "{@check} Подписка «{title}» автоматически продлена до {until}",
            config.emoji_mode,
            config.premium_emoji,
            title=plan.title,
            until=subscription.expire_at.strftime("%d.%m.%Y") if subscription.expire_at else "—",
        )
        return user.telegram_id, text, config.token


async def run_once() -> int:
    now = datetime.now(UTC)
    async with session() as db:
        ids = list(
            await db.scalars(
                select(BotSubscription.id).where(
                    BotSubscription.auto_renew.is_(True),
                    BotSubscription.plan_id.is_not(None),
                    BotSubscription.expire_at.is_not(None),
                    BotSubscription.expire_at <= now + WINDOW,
                    BotSubscription.expire_at >= now - GRACE,
                )
            )
        )

    renewed = 0
    for subscription_id in ids:
        try:
            result = await _renew_one(subscription_id)
        except Exception:  # noqa: BLE001
            log.exception("Сбой автопродления ключа #%s", subscription_id)
            continue
        if result is None:
            continue
        telegram_id, text, token = result
        renewed += 1
        await send(token, telegram_id, text)
        await asyncio.sleep(0.05)

    if renewed:
        log.info("Автопродлено подписок: %s", renewed)
    return renewed


async def worker() -> None:
    while True:
        try:
            await run_once()
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            log.exception("Сбой в воркере автопродления")
        await asyncio.sleep(CHECK_INTERVAL)
