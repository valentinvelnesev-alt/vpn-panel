from datetime import UTC, datetime, timedelta

from fastapi import APIRouter
from pydantic import BaseModel
from sqlalchemy import func, select

from app.api.deps import CurrentAdmin, DbSession
from app.services import cache
from app.services import settings_service as cfg
from app.services.remnawave_provider import Remnawave
from shared.db.models import BotUser, Payment, PaymentStatus
from shared.remnawave import RemnawaveError

router = APIRouter(prefix="/dashboard", tags=["dashboard"])

STATS_TTL = 30  # секунд: цифры остаются живыми, но Remnawave не долбим


class DayValue(BaseModel):
    date: str
    value: float


class NodeLoad(BaseModel):
    name: str
    country_code: str | None
    users_online: int
    online: bool


class Overview(BaseModel):
    configured: bool
    error: str | None = None

    users_total: int = 0
    users_active: int = 0
    users_expired: int = 0
    users_limited: int = 0
    users_disabled: int = 0

    online_now: int = 0
    online_last_day: int = 0

    nodes_total: int = 0
    nodes_online: int = 0
    traffic_lifetime_bytes: int = 0

    # Ряды для графиков на дашборде — берутся из нашей БД (платежи и
    # регистрации в боте), а не из Remnawave: там этих данных нет.
    revenue_daily: list[DayValue] = []
    new_users_daily: list[DayValue] = []
    revenue_total_rub: float = 0
    revenue_today_rub: float = 0
    nodes_load: list[NodeLoad] = []


CHART_DAYS = 14


def _daily_series(rows: list[tuple], days: int) -> list[DayValue]:
    """Заполняет пропущенные дни нулями — иначе график «скачет» по оси X."""
    by_day = {str(day): float(value) for day, value in rows}
    today = datetime.now(UTC).date()
    return [
        DayValue(date=str(d), value=by_day.get(str(d), 0.0))
        for d in (today - timedelta(days=days - 1 - i) for i in range(days))
    ]


async def _charts(db: DbSession) -> dict:
    since = datetime.now(UTC) - timedelta(days=CHART_DAYS)

    revenue_rows = (
        await db.execute(
            select(func.date(Payment.paid_at), func.sum(Payment.amount_kopeks) / 100.0)
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

    total = (
        await db.scalar(
            select(func.coalesce(func.sum(Payment.amount_kopeks), 0) / 100.0).where(
                Payment.status == PaymentStatus.PAID
            )
        )
    ) or 0.0

    revenue_daily = _daily_series(revenue_rows, CHART_DAYS)
    return {
        "revenue_daily": [d.model_dump() for d in revenue_daily],
        "new_users_daily": [
            d.model_dump() for d in _daily_series(users_rows, CHART_DAYS)
        ],
        "revenue_total_rub": round(float(total), 2),
        "revenue_today_rub": revenue_daily[-1].value if revenue_daily else 0.0,
    }


@router.get("/overview", response_model=Overview)
async def overview(admin: CurrentAdmin, db: DbSession) -> Overview:
    # Дашборд — первый экран после входа: пока Remnawave не подключена, он
    # должен объяснять это, а не отдавать ошибку.
    charts = await _charts(db)

    if not await cfg.is_configured(db):
        return Overview(configured=False, **charts)

    from app.services.remnawave_provider import get_client

    client = await get_client(db)

    async def collect() -> dict:
        stats = await client.get_stats()
        nodes = await client.get_nodes()
        counts = {k.upper(): v for k, v in stats.users.status_counts.items()}
        return {
            "nodes_load": [
                {
                    "name": n.name,
                    "country_code": n.country_code,
                    "users_online": n.users_online,
                    "online": n.is_online,
                }
                for n in sorted(nodes, key=lambda n: n.users_online, reverse=True)[:6]
            ],
            "users_total": stats.users.total_users,
            "users_active": counts.get("ACTIVE", 0),
            "users_expired": counts.get("EXPIRED", 0),
            "users_limited": counts.get("LIMITED", 0),
            "users_disabled": counts.get("DISABLED", 0),
            "online_now": stats.online_stats.online_now,
            "online_last_day": stats.online_stats.last_day,
            "nodes_total": len(nodes),
            "nodes_online": sum(1 for n in nodes if n.is_online),
            "traffic_lifetime_bytes": stats.nodes.total_bytes_lifetime,
        }

    try:
        data = await cache.cached_json("dashboard:overview", STATS_TTL, collect)
    except RemnawaveError as exc:
        return Overview(configured=True, error=str(exc), **charts)

    return Overview(configured=True, **data, **charts)


@router.get("/nodes-health")
async def nodes_health(admin: CurrentAdmin, client: Remnawave) -> list[dict]:
    """Короткая сводка по нодам для виджета на дашборде."""
    nodes = await client.get_nodes()
    return [
        {
            "uuid": n.uuid,
            "name": n.name,
            "online": n.is_online,
            "users_online": n.users_online,
            "country_code": n.country_code,
        }
        for n in sorted(nodes, key=lambda n: n.view_position)
    ]
