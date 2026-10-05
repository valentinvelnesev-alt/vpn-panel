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

from sqlalchemy import select

from app import config as config_module
from app import texts
from app.services import subscriptions as subs
from app.services import wallet
from app.services.notify import send
from shared.db.models import (
    BotSubscription,
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
        # FOR UPDATE: одну оплату одновременно могут применять событие из
        # шины, воркер догонки, опрос провайдера и кнопка «Проверить оплату».
        # Без блокировки двое читали applied_at IS NULL и оба выдавали ключ.
        payment = await db.scalar(
            select(Payment).where(Payment.id == payment_id).with_for_update()
        )
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
        if user is None:
            return
        # Без токена (бот выключен в панели) оплату всё равно применяем —
        # просто не сможем написать клиенту, send() это переживёт.

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
                # Тариф удалили, пока клиент платил. Деньги не теряем —
                # зачисляем на баланс, с него можно купить другой тариф.
                log.error("У платежа %s не найден тариф %s", payment_id, payment.plan_id)
                await wallet.credit(
                    db,
                    user,
                    payment.amount_kopeks,
                    WalletTxType.REFUND,
                    description=f"Тариф удалён, платёж #{payment.id}",
                )
                await send(
                    config.token,
                    user.telegram_id,
                    texts.render(
                        "{@warning} Тариф больше недоступен — {amount} ₽ зачислены на баланс",
                        config.emoji_mode,
                        config.premium_emoji,
                        amount=f"{payment.amount_kopeks / 100:.2f}",
                    ),
                )
                return
            plan = config_module.plan_view(plan_row)
            subscription = None
            if payment.subscription_id is not None:
                subscription = await db.get(BotSubscription, payment.subscription_id)
                if subscription is not None and subscription.user_id != user.id:
                    subscription = None
                if subscription is None:
                    # Ключ удалили, пока клиент платил, — не теряем оплату,
                    # а выдаём подписку так же, как при обычной покупке.
                    log.warning(
                        "У платежа %s не найден ключ %s", payment_id, payment.subscription_id
                    )
            if (
                subscription is None
                and not config.allow_multiple_subscriptions
                and await subs.has_subscription(db, user)
            ):
                # Счёт выставили до того, как у клиента появился ключ (или до
                # смены настройки) — всё равно продлеваем, а не плодим второй.
                subscription = await subs.renew_target(db, user)

            if subscription is not None:
                subscription = await subs.extend_subscription(
                    db, config, subscription, plan, amount_kopeks=payment.amount_kopeks
                )
                kind = "renewal"
            else:
                subscription = await subs.create_subscription(
                    db,
                    config,
                    user,
                    plan,
                    source=payment.provider,
                    amount_kopeks=payment.amount_kopeks,
                )
                kind = "purchase"
            until = subscription.expire_at
            text = texts.render(
                "{@check} Оплата получена, подписка {action} до {until}",
                config.emoji_mode,
                config.premium_emoji,
                action="продлена" if kind == "renewal" else "активна",
                until=until.strftime("%d.%m.%Y") if until else "—",
            )
            discount = 0
            if plan.price_kopeks and payment.amount_kopeks < plan.price_kopeks:
                discount = round(100 - payment.amount_kopeks * 100 / plan.price_kopeks)
            commissions = await subs.after_paid_purchase(
                db,
                config,
                user,
                payment.amount_kopeks,
                plan_title=plan.title,
                kind=kind,
                method=str(payment.provider),
                discount_percent=discount,
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
