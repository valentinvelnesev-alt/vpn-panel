"""Персональные тарифы клиента и поиск тарифа, который ему можно купить.

Общие тарифы лежат в Config.plans (снимок при старте бота), персональные —
читаются из БД под конкретного клиента: админ заводит их в карточке
клиента, и перезапускать бота ради этого не нужно.
"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import Config, PlanView, plan_view
from shared.db.models import BotUser
from shared.db.models import Plan as PlanRow


async def personal_plans(db: AsyncSession, user: BotUser) -> list[PlanView]:
    rows = await db.scalars(
        select(PlanRow)
        .options(selectinload(PlanRow.category))
        .where(PlanRow.owner_user_id == user.id, PlanRow.is_active.is_(True))
        .order_by(PlanRow.sort_order, PlanRow.days)
    )
    return [plan_view(row) for row in rows]


async def find_plan(
    db: AsyncSession, config: Config, user: BotUser, plan_id: int
) -> PlanView | None:
    """Общий активный тариф или СВОЙ персональный. Чужой персональный не
    находится — id в callback_data подделать легко."""
    plan = config.plan(plan_id)
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
