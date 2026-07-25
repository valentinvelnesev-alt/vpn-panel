"""Автопродление подписки с баланса кошелька.

Раз в час: у кого включено автопродление, подписка истекает в ближайшие
24 часа и на балансе хватает денег на выбранный тариф — списываем и
продлеваем. Не хватает денег — просто пропускаем: обычные напоминания об
истечении (expiry.py) всё равно предупредят пользователя.
"""

import asyncio
import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app import config as config_module
from app import texts
from app.services import subscriptions as subs, wallet
from app.services.notify import send
from shared.db.models import BotUser, Plan, WalletTxType
from shared.db.session import session

log = logging.getLogger("bot.workers.auto_renewal")

CHECK_INTERVAL = 3600
WINDOW = timedelta(hours=24)


async def run_once() -> int:
    now = datetime.now(UTC)
    horizon = now + WINDOW
    renewed = 0

    async with session() as db:
        config = await config_module.load(db)
        if not config.token:
            return 0

        candidates = await db.scalars(
            select(BotUser).where(
                BotUser.auto_renew_enabled.is_(True),
                BotUser.auto_renew_plan_id.is_not(None),
                BotUser.expire_at.is_not(None),
                BotUser.expire_at <= horizon,
            )
        )

        for user in candidates:
            expire_at = user.expire_at
            if expire_at.tzinfo is None:
                expire_at = expire_at.replace(tzinfo=UTC)
            if expire_at > horizon:
                continue

            plan_row = await db.get(Plan, user.auto_renew_plan_id)
            if plan_row is None or not plan_row.is_active:
                continue
            plan = config_module.plan_view(plan_row)

            try:
                await wallet.debit(
                    db,
                    user,
                    plan.price_kopeks,
                    WalletTxType.AUTO_RENEWAL,
                    description=f"Автопродление: {plan.title}",
                )
            except wallet.InsufficientFunds:
                continue  # не хватает баланса — пропускаем без ошибки

            user = await subs.grant_plan(db, config, user, plan, source="auto_renewal")
            renewed += 1

            text = texts.render(
                "{@check} Подписка автоматически продлена: {title} до {until}",
                config.emoji_mode,
                config.premium_emoji,
                title=plan.title,
                until=user.expire_at.strftime("%d.%m.%Y") if user.expire_at else "—",
            )
            await send(config.token, user.telegram_id, text)
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
