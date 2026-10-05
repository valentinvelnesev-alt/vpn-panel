"""Цена тарифа для конкретного пользователя и список доступных ему тарифов.

Цена складывается из трёх источников:
- базовая цена тарифа (персональный тариф — уже своя цена для клиента);
- общая скидка из панели — только на обычные тарифы и только до её даты;
- скидка из промокода — на следующую оплату любого тарифа.
Скидки не суммируются: берётся большая, чтобы промокод на 10% при общей
акции 20% не превращался в 30%.

Итоговая сумма фиксируется в момент выставления счёта (Payment.amount_kopeks,
payload Stars) — если скидка закончится, пока клиент платит, он всё равно
получит тариф по цене, которую видел.
"""

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import Config, PlanView, plan_view
from shared.db.models import BotUser
from shared.db.models import Plan as PlanRow


@dataclass(frozen=True, slots=True)
class Price:
    base_kopeks: int
    final_kopeks: int
    percent: int

    @property
    def discounted(self) -> bool:
        return self.final_kopeks < self.base_kopeks


def global_discount(config: Config, now: datetime | None = None) -> int:
    """Действующая сейчас общая скидка, 0 — если её нет или она истекла."""
    if config.discount_percent <= 0:
        return 0
    if config.discount_until is not None:
        until = config.discount_until
        if until.tzinfo is None:
            until = until.replace(tzinfo=UTC)
        if until <= (now or datetime.now(UTC)):
            return 0
    return min(config.discount_percent, 100)


def price_for(config: Config, plan: PlanView, user: BotUser | None) -> Price:
    percent = 0 if plan.is_personal else global_discount(config)
    if user is not None:
        percent = max(percent, min(user.pending_discount_percent or 0, 100))
    final = plan.price_kopeks * (100 - percent) // 100
    return Price(base_kopeks=plan.price_kopeks, final_kopeks=final, percent=percent)


def rub(kopeks: int) -> str:
    """199 ₽ / 149.50 ₽ — без лишних «.00» у круглых сумм."""
    if kopeks % 100 == 0:
        return f"{kopeks // 100} ₽"
    return f"{kopeks / 100:.2f} ₽"


def _strike(text: str) -> str:
    # В inline-кнопках нет HTML — зачёркиваем комбинируемым символом U+0336.
    return "".join(ch + "̶" for ch in text)


def button_price(price: Price) -> str:
    """Цена для текста кнопки: «1̶9̶9̶ ₽ 149 ₽» при скидке."""
    if not price.discounted:
        return rub(price.final_kopeks)
    return f"{_strike(rub(price.base_kopeks))} {rub(price.final_kopeks)}"


def text_price(price: Price) -> str:
    """Цена для текста сообщения (HTML): «<s>199 ₽</s> 149 ₽ (−25%)»."""
    if not price.discounted:
        return rub(price.final_kopeks)
    return f"<s>{rub(price.base_kopeks)}</s> {rub(price.final_kopeks)} (−{price.percent}%)"


async def personal_plans(db: AsyncSession, user: BotUser) -> list[PlanView]:
    rows = await db.scalars(
        select(PlanRow)
        .options(selectinload(PlanRow.category))
        .where(PlanRow.owner_user_id == user.id, PlanRow.is_active.is_(True))
        .order_by(PlanRow.sort_order, PlanRow.days)
    )
    return [plan_view(row) for row in rows]


async def plans_for(db: AsyncSession, config: Config, user: BotUser) -> list[PlanView]:
    """Персональные тарифы пользователя (сверху) + общие."""
    return [*await personal_plans(db, user), *config.plans]


async def find_plan(
    db: AsyncSession, config: Config, user: BotUser, plan_id: int
) -> PlanView | None:
    """Тариф, который этот пользователь вправе купить: общий активный или
    свой персональный. Чужой персональный тариф не находится — id из
    callback_data подделать легко."""
    plan = next((p for p in config.plans if p.id == plan_id), None)
    if plan is not None:
        return plan
    row = await db.scalar(
        select(PlanRow)
        .options(selectinload(PlanRow.category))
        .where(
            PlanRow.id == plan_id,
            PlanRow.owner_user_id == user.id,
            PlanRow.is_active.is_(True),
        )
    )
    return plan_view(row) if row is not None else None
