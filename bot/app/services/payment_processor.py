"""Обработка подтверждённой оплаты: зачисление и уведомление клиента.

Вызывается по событию `payment_completed` из шины (см. shared/bus.py),
которое публикует бэкенд после того, как сам подтвердил статус у
провайдера. Здесь мы уже не проверяем платёж повторно — только применяем
его последствия: пополняем кошелёк или продлеваем подписку.

Работает независимо от того, запущен ли сейчас polling: подтверждение
может прийти, пока админ ненадолго остановил бота в панели.
"""

import logging
from datetime import UTC, datetime

from app import config as config_module
from app import texts
from app.services import subscriptions as subs
from app.services import wallet
from app.services.notify import send
from shared.db.models import (
    BotUser,
    Payment,
    PaymentPurpose,
    PaymentStatus,
    Plan,
    WalletTxType,
)
from shared.db.session import session

log = logging.getLogger("bot.payment_processor")


async def handle(payment_id: int) -> None:
    async with session() as db:
        payment = await db.get(Payment, payment_id)
        if payment is None:
            log.warning("payment_completed для несуществующего платежа %s", payment_id)
            return
        if payment.status != PaymentStatus.PAID or payment.applied_at is not None:
            # Либо ещё не подтверждён, либо уже применён — pub/sub может
            # доставить сообщение больше одного раза.
            return

        payment.applied_at = datetime.now(UTC)

        user = await db.get(BotUser, payment.user_id)
        config = await config_module.load(db)
        if user is None or not config.token:
            return

        if payment.purpose == PaymentPurpose.TOPUP:
            await wallet.credit(
                db,
                user,
                payment.amount_kopeks,
                WalletTxType.TOPUP,
                description=f"{payment.provider} #{payment.external_id}",
            )
            text = texts.render(
                "{@check} Баланс пополнен на {amount} ₽",
                config.emoji_mode,
                config.premium_emoji,
                amount=f"{payment.amount_kopeks / 100:.2f}",
            )
        else:
            plan_row = await db.get(Plan, payment.plan_id) if payment.plan_id else None
            if plan_row is None:
                log.error("У платежа %s не найден тариф %s", payment_id, payment.plan_id)
                return
            plan = config_module.plan_view(plan_row)
            user = await subs.grant_plan(db, config, user, plan, source=payment.provider)
            text = texts.render(
                "{@check} Оплата получена, подписка продлена до {until}",
                config.emoji_mode,
                config.premium_emoji,
                until=user.expire_at.strftime("%d.%m.%Y") if user.expire_at else "—",
            )
            commissions = await subs.after_paid_purchase(
                db, config, user, payment.amount_kopeks, plan_title=plan.title
            )
            for referrer, share in commissions:
                await send(
                    config.token,
                    referrer.telegram_id,
                    texts.render(
                        "{@gift} Начислена реферальная комиссия: {amount} ₽",
                        config.emoji_mode,
                        config.premium_emoji,
                        amount=f"{share / 100:.2f}",
                    ),
                )

        await send(config.token, user.telegram_id, text)

        # Награда рефереру — только за оплату тарифа, не за пополнение
        # баланса самого по себе (см. комментарий у reward_if_first_purchase).
        if payment.purpose != PaymentPurpose.TOPUP:
            reward = await subs.apply_referral_reward(db, config, user)
            if reward is not None:
                referrer, days = reward
                referral_text = texts.render(
                    "{@gift} Ваш друг оплатил подписку — начислено {days} дн.",
                    config.emoji_mode,
                    config.premium_emoji,
                    days=days,
                )
                await send(config.token, referrer.telegram_id, referral_text)
