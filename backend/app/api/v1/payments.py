from datetime import UTC, datetime

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.api.deps import CurrentAdmin, DbSession
from app.core.security import mask
from app.services import settings_service as cfg
from shared.db.models import AuditLog, BotUser, Payment

router = APIRouter(prefix="/payments", tags=["payments"])


# ── Провайдеры ────────────────────────────────────────────────────────
class ProvidersOut(BaseModel):
    platega_enabled: bool
    platega_merchant_id: str | None
    platega_secret_masked: str | None
    cryptobot_enabled: bool
    cryptobot_token_masked: str | None
    stars_enabled: bool


@router.get("/providers", response_model=ProvidersOut)
async def get_providers(admin: CurrentAdmin, db: DbSession) -> ProvidersOut:
    values = await cfg.get_many(
        db,
        cfg.PLATEGA_ENABLED,
        cfg.PLATEGA_MERCHANT_ID,
        cfg.PLATEGA_SECRET,
        cfg.CRYPTOBOT_ENABLED,
        cfg.CRYPTOBOT_TOKEN,
        cfg.STARS_ENABLED,
    )
    return ProvidersOut(
        platega_enabled=values[cfg.PLATEGA_ENABLED] == "true",
        platega_merchant_id=values[cfg.PLATEGA_MERCHANT_ID],
        platega_secret_masked=mask(values[cfg.PLATEGA_SECRET])
        if values[cfg.PLATEGA_SECRET]
        else None,
        cryptobot_enabled=values[cfg.CRYPTOBOT_ENABLED] == "true",
        cryptobot_token_masked=mask(values[cfg.CRYPTOBOT_TOKEN])
        if values[cfg.CRYPTOBOT_TOKEN]
        else None,
        stars_enabled=values[cfg.STARS_ENABLED] == "true",
    )


class PlategaIn(BaseModel):
    enabled: bool
    merchant_id: str = Field(default="", max_length=128)
    # Пусто = оставить прежний секрет — так же, как токен Remnawave/бота.
    secret: str = Field(default="", max_length=256)


@router.put("/providers/platega", response_model=ProvidersOut)
async def save_platega(
    data: PlategaIn, admin: CurrentAdmin, db: DbSession, request: Request
) -> ProvidersOut:
    await cfg.set_(db, cfg.PLATEGA_ENABLED, "true" if data.enabled else "false")
    await cfg.set_(db, cfg.PLATEGA_MERCHANT_ID, data.merchant_id or None)
    if data.secret:
        await cfg.set_(db, cfg.PLATEGA_SECRET, data.secret)
    db.add(
        AuditLog(
            admin_id=admin.id,
            action="payments.platega",
            ip=request.client.host if request.client else None,
            created_at=datetime.now(UTC),
        )
    )
    from shared import bus

    await bus.publish(bus.CMD_RELOAD)
    return await get_providers(admin, db)


class CryptoBotIn(BaseModel):
    enabled: bool
    token: str = Field(default="", max_length=256)


@router.put("/providers/cryptobot", response_model=ProvidersOut)
async def save_cryptobot(
    data: CryptoBotIn, admin: CurrentAdmin, db: DbSession, request: Request
) -> ProvidersOut:
    await cfg.set_(db, cfg.CRYPTOBOT_ENABLED, "true" if data.enabled else "false")
    if data.token:
        await cfg.set_(db, cfg.CRYPTOBOT_TOKEN, data.token)
    db.add(
        AuditLog(
            admin_id=admin.id,
            action="payments.cryptobot",
            ip=request.client.host if request.client else None,
            created_at=datetime.now(UTC),
        )
    )
    from shared import bus

    await bus.publish(bus.CMD_RELOAD)
    return await get_providers(admin, db)


class StarsIn(BaseModel):
    enabled: bool


@router.put("/providers/stars", response_model=ProvidersOut)
async def save_stars(
    data: StarsIn, admin: CurrentAdmin, db: DbSession, request: Request
) -> ProvidersOut:
    await cfg.set_(db, cfg.STARS_ENABLED, "true" if data.enabled else "false")
    db.add(
        AuditLog(
            admin_id=admin.id,
            action="payments.stars",
            ip=request.client.host if request.client else None,
            created_at=datetime.now(UTC),
        )
    )
    from shared import bus

    await bus.publish(bus.CMD_RELOAD)
    return await get_providers(admin, db)


# ── Журнал платежей ───────────────────────────────────────────────────
class PaymentOut(BaseModel):
    id: int
    telegram_id: int
    username: str | None
    provider: str
    purpose: str
    amount_rub: float
    status: str
    created_at: datetime
    paid_at: datetime | None


@router.get("", response_model=list[PaymentOut])
async def list_payments(
    admin: CurrentAdmin,
    db: DbSession,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> list[PaymentOut]:
    rows = await db.execute(
        select(Payment, BotUser)
        .join(BotUser, BotUser.id == Payment.user_id)
        .order_by(Payment.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    return [
        PaymentOut(
            id=payment.id,
            telegram_id=user.telegram_id,
            username=user.username,
            provider=str(payment.provider),
            purpose=str(payment.purpose),
            amount_rub=payment.amount_kopeks / 100,
            status=str(payment.status),
            created_at=payment.created_at,
            paid_at=payment.paid_at,
        )
        for payment, user in rows
    ]
