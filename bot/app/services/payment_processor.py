"""Обработка подтверждённой оплаты: зачисление и уведомление клиента.

Вызывается по событию `payment_completed` из шины (см. shared/bus.py),
воркером догонки (workers/payments.py), опросом провайдера
(payment_check.py) и успешной оплатой Stars. Здесь мы уже не проверяем
платёж повторно — только применяем его последствия.

Порядок важен. Выдача доступа (Remnawave + строки БД + applied_at) идёт
одной транзакцией и коммитится сразу. Всё, что после — сообщение клиенту,
комиссия рефереру, бонусные дни, уведомление о продаже — выполняется
отдельно и не может откатить выдачу. Раньше всё было в одной транзакции:
сбой на начислении рефереру откатывал БД, но не созданного пользователя
Remnawave, а воркер через минуту создавал ещё одного.
"""

import logging
from dataclasses import dataclass
from datetime import UTC, datetime

from app import config as config_module
from app import texts
from app.config import Config
from app.services import subscriptions as subs
from app.services import wallet
from app.services.notify import send
from shared.db.models import (
    BotSubscription,
    BotUser,
    Payment,
    PaymentPurpose,
    PaymentStatus,
    WalletTxType,
)
from shared.db.session import session

log = logging.getLogger("bot.payment_processor")


@dataclass(slots=True)
class Applied:
    config: Config
    user_id: int
    telegram_id: int
    text: str
    amount_kopeks: int
    plan_title: str | None  # None — пополнение баланса


async def handle(payment_id: int) -> None:
    applied = await _apply(payment_id)
    if applied is None:
        return

    if applied.config.token:
        await send(applied.config.token, applied.telegram_id, applied.text)

    if applied.plan_title is not None:
        await after_purchase_effects(
            applied.config, applied.user_id, applied.amount_kopeks, applied.plan_title
        )


async def _apply(payment_id: int) -> Applied | None:
    async with session() as db:
        payment = await db.get(Payment, payment_id)
        if payment is None:
            log.warning("payment_completed для несуществующего платежа %s", payment_id)
            return None
        if payment.status != PaymentStatus.PAID or payment.applied_at is not None:
            # Либо ещё не подтверждён, либо уже применён — pub/sub может
            # доставить сообщение больше одного раза.
            return None

        user = await db.get(BotUser, payment.user_id)
        config = await config_module.load(db)
        if user is None:
            log.error("У платежа %s нет пользователя %s", payment_id, payment.user_id)
            return None
        if not config.token:
            # Бот без токена — не помечаем применённым, воркер вернётся позже.
            log.warning("Платёж %s ждёт: у бота нет токена", payment_id)
            return None

        if payment.purpose == PaymentPurpose.TRAFFIC:
            package = await config_module.load_traffic_package(db, payment.traffic_package_id)
            subscription = (
                await db.get(BotSubscription, payment.subscription_id)
                if payment.subscription_id
                else None
            )
            if package is None or subscription is None or subscription.user_id != user.id:
                log.error("У платежа %s не найден пакет трафика или ключ", payment_id)
                return None
            await subs.add_traffic(
                db,
                config,
                subscription,
                package,
                source=str(payment.provider),
                amount_kopeks=payment.amount_kopeks,
            )
            text = texts.render(
                "{@check} Оплата получена, добавлено {gb} ГБ трафика",
                config.emoji_mode,
                config.premium_emoji,
                gb=package.traffic_gb,
            )
            plan_title = f"+{package.traffic_gb} ГБ трафика"
        elif payment.purpose == PaymentPurpose.TOPUP:
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
            plan_title = None
        else:
            plan = await config_module.load_plan(db, payment.plan_id)
            if plan is None:
                log.error("У платежа %s не найден тариф %s", payment_id, payment.plan_id)
                return None
            if payment.subscription_id is not None:
                subscription = await db.get(BotSubscription, payment.subscription_id)
                if subscription is None or subscription.user_id != user.id:
                    log.error(
                        "У платежа %s не найден ключ %s", payment_id, payment.subscription_id
                    )
                    return None
                subscription = await subs.extend_subscription(
                    db, config, subscription, plan, amount_kopeks=payment.amount_kopeks
                )
            else:
                subscription = await subs.create_subscription(
                    db,
                    config,
                    user,
                    plan,
                    source=str(payment.provider),
                    amount_kopeks=payment.amount_kopeks,
                )
            until = subscription.expire_at
            # Скидка из промокода — на одну покупку.
            user.discount_percent = 0
            text = texts.render(
                "{@check} Оплата получена, подписка «{title}» действует до {until}",
                config.emoji_mode,
                config.premium_emoji,
                title=plan.title,
                until=until.strftime("%d.%m.%Y") if until else "—",
            )
            plan_title = plan.full_title

        payment.applied_at = datetime.now(UTC)
        return Applied(
            config=config,
            user_id=user.id,
            telegram_id=user.telegram_id,
            text=text,
            amount_kopeks=payment.amount_kopeks,
            plan_title=plan_title,
        )


async def after_purchase_effects(
    config: Config, user_id: int, amount_kopeks: int, plan_title: str
) -> None:
    """Реферальная комиссия, уведомление о продаже, бонусные дни рефереру.
    Каждый шаг в своей транзакции и со своим try/except: их сбой не должен
    ни откатить выдачу, ни помешать остальным шагам."""
    notices: list[tuple[int, str]] = []

    try:
        async with session() as db:
            user = await db.get(BotUser, user_id)
            if user is None:
                return
            commissions = await subs.after_paid_purchase(
                db, config, user, amount_kopeks, plan_title=plan_title
            )
            for referrer, share in commissions:
                notices.append(
                    (
                        referrer.telegram_id,
                        texts.render(
                            "{@gift} Начислена реферальная комиссия: {amount} ₽",
                            config.emoji_mode,
                            config.premium_emoji,
                            amount=f"{share / 100:.2f}",
                        ),
                    )
                )
    except Exception:  # noqa: BLE001
        log.exception("Не удалось начислить реферальную комиссию (user_id=%s)", user_id)

    try:
        async with session() as db:
            user = await db.get(BotUser, user_id)
            if user is not None:
                reward = await subs.apply_referral_reward(db, config, user)
                if reward is not None:
                    referrer, days = reward
                    notices.append(
                        (
                            referrer.telegram_id,
                            texts.render(
                                "{@gift} Ваш друг оплатил подписку — начислено {days} дн.",
                                config.emoji_mode,
                                config.premium_emoji,
                                days=days,
                            ),
                        )
                    )
    except Exception:  # noqa: BLE001
        log.exception("Не удалось начислить бонусные дни рефереру (user_id=%s)", user_id)

    if config.token:
        for chat_id, text in notices:
            await send(config.token, chat_id, text)
