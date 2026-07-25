import csv
import io
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import func, select

from app.api.deps import CurrentAdmin, DbSession
from shared.db.models import BotUser, Payment, PaymentStatus, Plan, Purchase

router = APIRouter(prefix="/analytics", tags=["analytics"])

WINDOW_DAYS = 30


class DayValue(BaseModel):
    date: str
    value: float


class TrialConversion(BaseModel):
    trial_users: int
    converted: int
    rate: float


class PlanPopularity(BaseModel):
    title: str
    purchases: int
    revenue_rub: float


class AnalyticsOverview(BaseModel):
    revenue_daily: list[DayValue]
    new_users_daily: list[DayValue]
    trial_conversion: TrialConversion
    top_plans: list[PlanPopularity]


def _daily_series(rows: list[tuple], days: int) -> list[DayValue]:
    """Заполняет пропущенные дни нулями — иначе график «скакал» бы по оси X."""
    by_day = {str(day): float(value) for day, value in rows}
    today = datetime.now(UTC).date()
    return [
        DayValue(date=str(d), value=by_day.get(str(d), 0.0))
        for d in (today - timedelta(days=days - 1 - i) for i in range(days))
    ]


@router.get("/overview", response_model=AnalyticsOverview)
async def overview(admin: CurrentAdmin, db: DbSession) -> AnalyticsOverview:
    since = datetime.now(UTC) - timedelta(days=WINDOW_DAYS)

    revenue_rows = (
        await db.execute(
            select(
                func.date(Payment.paid_at),
                func.sum(Payment.amount_kopeks) / 100.0,
            )
            .where(Payment.status == PaymentStatus.PAID, Payment.paid_at >= since)
            .group_by(func.date(Payment.paid_at))
        )
    ).all()

    users_rows = (
        await db.execute(
            select(func.date(BotUser.created_at), func.count(BotUser.id))
            .where(BotUser.created_at >= since)
            .group_by(func.date(BotUser.created_at))
        )
    ).all()

    trial_users = (
        await db.scalar(
            select(func.count()).select_from(BotUser).where(BotUser.trial_used.is_(True))
        )
    ) or 0
    converted = (
        await db.scalar(
            select(func.count(func.distinct(Purchase.user_id))).where(
                Purchase.source != "trial",
                Purchase.user_id.in_(
                    select(BotUser.id).where(BotUser.trial_used.is_(True))
                ),
            )
        )
    ) or 0

    plan_rows = (
        await db.execute(
            select(
                Plan.title,
                func.count(Purchase.id),
                func.sum(Purchase.amount_kopeks) / 100.0,
            )
            .join(Purchase, Purchase.plan_id == Plan.id)
            .group_by(Plan.id, Plan.title)
            .order_by(func.count(Purchase.id).desc())
            .limit(10)
        )
    ).all()

    return AnalyticsOverview(
        revenue_daily=_daily_series(revenue_rows, WINDOW_DAYS),
        new_users_daily=_daily_series(users_rows, WINDOW_DAYS),
        trial_conversion=TrialConversion(
            trial_users=trial_users,
            converted=converted,
            rate=round(converted / trial_users * 100, 1) if trial_users else 0.0,
        ),
        top_plans=[
            PlanPopularity(title=title, purchases=count, revenue_rub=revenue or 0.0)
            for title, count, revenue in plan_rows
        ],
    )


@router.get("/export/payments.csv")
async def export_payments_csv(admin: CurrentAdmin, db: DbSession) -> StreamingResponse:
    rows = await db.execute(
        select(Payment, BotUser)
        .join(BotUser, BotUser.id == Payment.user_id)
        .order_by(Payment.created_at.desc())
    )

    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(
        ["id", "telegram_id", "username", "provider", "external_id", "purpose",
         "amount_rub", "status", "created_at", "paid_at"]
    )
    for payment, user in rows:
        writer.writerow(
            [
                payment.id,
                user.telegram_id,
                user.username or "",
                payment.provider,
                payment.external_id,
                payment.purpose,
                f"{payment.amount_kopeks / 100:.2f}",
                payment.status,
                payment.created_at.isoformat(),
                payment.paid_at.isoformat() if payment.paid_at else "",
            ]
        )
    buffer.seek(0)

    return StreamingResponse(
        iter([buffer.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=payments.csv"},
    )
