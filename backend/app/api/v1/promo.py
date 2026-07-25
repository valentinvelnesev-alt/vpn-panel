from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import delete, select

from app.api.deps import CurrentAdmin, DbSession
from shared.db.models import AuditLog, PromoCode

router = APIRouter(prefix="/bot/promo-codes", tags=["promo"])


class PromoIn(BaseModel):
    code: str = Field(min_length=3, max_length=32, pattern=r"^[A-Za-z0-9_-]+$")
    bonus_days: int = Field(default=0, ge=0, le=3650)
    discount_percent: int = Field(default=0, ge=0, le=100)
    max_uses: int | None = Field(default=None, ge=1)
    expires_at: datetime | None = None
    is_active: bool = True


class PromoOut(PromoIn):
    id: int
    uses_count: int


def _out(p: PromoCode) -> PromoOut:
    return PromoOut(
        id=p.id,
        code=p.code,
        bonus_days=p.bonus_days,
        discount_percent=p.discount_percent,
        max_uses=p.max_uses,
        expires_at=p.expires_at,
        is_active=p.is_active,
        uses_count=p.uses_count,
    )


@router.get("", response_model=list[PromoOut])
async def list_promo(admin: CurrentAdmin, db: DbSession) -> list[PromoOut]:
    rows = await db.scalars(select(PromoCode).order_by(PromoCode.created_at.desc()))
    return [_out(p) for p in rows]


@router.post("", response_model=PromoOut, status_code=status.HTTP_201_CREATED)
async def create_promo(
    data: PromoIn, admin: CurrentAdmin, db: DbSession, request: Request
) -> PromoOut:
    if data.bonus_days == 0 and data.discount_percent == 0:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "Укажите бонусные дни или скидку — иначе промокод ничего не даёт",
        )
    code = data.code.strip().upper()
    if await db.scalar(select(PromoCode).where(PromoCode.code == code)):
        raise HTTPException(status.HTTP_409_CONFLICT, "Такой промокод уже существует")

    promo = PromoCode(
        code=code,
        bonus_days=data.bonus_days,
        discount_percent=data.discount_percent,
        max_uses=data.max_uses,
        expires_at=data.expires_at,
        is_active=data.is_active,
    )
    db.add(promo)
    db.add(
        AuditLog(
            admin_id=admin.id,
            action="promo.create",
            target=code,
            ip=request.client.host if request.client else None,
            created_at=datetime.now(UTC),
        )
    )
    await db.flush()
    return _out(promo)


@router.patch("/{promo_id}", response_model=PromoOut)
async def update_promo(
    promo_id: int,
    data: PromoIn,
    admin: CurrentAdmin,
    db: DbSession,
    request: Request,
) -> PromoOut:
    promo = await db.get(PromoCode, promo_id)
    if promo is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Промокод не найден")

    promo.bonus_days = data.bonus_days
    promo.discount_percent = data.discount_percent
    promo.max_uses = data.max_uses
    promo.expires_at = data.expires_at
    promo.is_active = data.is_active
    # Код (первичный человекочитаемый идентификатор) не переименовываем —
    # уже разосланный промокод не должен меняться.

    db.add(
        AuditLog(
            admin_id=admin.id,
            action="promo.update",
            target=promo.code,
            ip=request.client.host if request.client else None,
            created_at=datetime.now(UTC),
        )
    )
    return _out(promo)


@router.delete("/{promo_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_promo(
    promo_id: int, admin: CurrentAdmin, db: DbSession, request: Request
) -> None:
    result = await db.execute(delete(PromoCode).where(PromoCode.id == promo_id))
    if result.rowcount == 0:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Промокод не найден")
    db.add(
        AuditLog(
            admin_id=admin.id,
            action="promo.delete",
            target=str(promo_id),
            ip=request.client.host if request.client else None,
            created_at=datetime.now(UTC),
        )
    )
