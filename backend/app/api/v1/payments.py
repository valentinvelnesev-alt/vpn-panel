from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, Query, Request, status
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
    rollypay_enabled: bool
    rollypay_api_key_masked: str | None
    rollypay_signing_secret_masked: str | None = None
    cispay_enabled: bool = False
    cispay_shop_id: str | None = None
    cispay_api_key_masked: str | None = None
    cryptobot_enabled: bool
    cryptobot_token_masked: str | None
    stars_enabled: bool
    stars_rub_per_star: float = cfg.DEFAULT_RUB_PER_STAR
    # Адреса колбэков — их нужно вписать в кабинете провайдера вручную,
    # при создании платежа они не передаются.
    webhook_urls: dict[str, str] = {}


def _webhook_urls() -> dict[str, str]:
    from app.core.config import settings

    base = (settings.public_url or "").rstrip("/")
    # Платёжки шлют колбэки только на домен с HTTPS: панели по IP адреса
    # не показываем — их всё равно не примет ни один кабинет провайдера.
    if not base.startswith("https://"):
        return {}
    return {
        "platega": f"{base}/api/v1/webhooks/platega",
        "rollypay": f"{base}/api/v1/webhooks/rollypay",
        "cispay": f"{base}/api/v1/webhooks/cispay",
        "cryptobot": f"{base}/api/v1/webhooks/cryptobot",
    }


@router.get("/providers", response_model=ProvidersOut)
async def get_providers(admin: CurrentAdmin, db: DbSession) -> ProvidersOut:
    values = await cfg.get_many(
        db,
        cfg.PLATEGA_ENABLED,
        cfg.PLATEGA_MERCHANT_ID,
        cfg.PLATEGA_SECRET,
        cfg.ROLLYPAY_ENABLED,
        cfg.ROLLYPAY_API_KEY,
        cfg.ROLLYPAY_SIGNING_SECRET,
        cfg.CISPAY_ENABLED,
        cfg.CISPAY_SHOP_ID,
        cfg.CISPAY_API_KEY,
        cfg.CRYPTOBOT_ENABLED,
        cfg.CRYPTOBOT_TOKEN,
        cfg.STARS_ENABLED,
        cfg.STARS_RUB_PER_STAR,
    )
    return ProvidersOut(
        platega_enabled=values[cfg.PLATEGA_ENABLED] == "true",
        platega_merchant_id=values[cfg.PLATEGA_MERCHANT_ID],
        platega_secret_masked=mask(values[cfg.PLATEGA_SECRET])
        if values[cfg.PLATEGA_SECRET]
        else None,
        rollypay_enabled=values[cfg.ROLLYPAY_ENABLED] == "true",
        rollypay_api_key_masked=mask(values[cfg.ROLLYPAY_API_KEY])
        if values[cfg.ROLLYPAY_API_KEY]
        else None,
        rollypay_signing_secret_masked=mask(values[cfg.ROLLYPAY_SIGNING_SECRET])
        if values[cfg.ROLLYPAY_SIGNING_SECRET]
        else None,
        cispay_enabled=values[cfg.CISPAY_ENABLED] == "true",
        cispay_shop_id=values[cfg.CISPAY_SHOP_ID],
        cispay_api_key_masked=mask(values[cfg.CISPAY_API_KEY])
        if values[cfg.CISPAY_API_KEY]
        else None,
        cryptobot_enabled=values[cfg.CRYPTOBOT_ENABLED] == "true",
        cryptobot_token_masked=mask(values[cfg.CRYPTOBOT_TOKEN])
        if values[cfg.CRYPTOBOT_TOKEN]
        else None,
        stars_enabled=values[cfg.STARS_ENABLED] == "true",
        stars_rub_per_star=float(values[cfg.STARS_RUB_PER_STAR] or cfg.DEFAULT_RUB_PER_STAR),
        webhook_urls=_webhook_urls(),
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
    if data.enabled:
        existing_secret = await cfg.get(db, cfg.PLATEGA_SECRET)
        if not data.merchant_id or not (data.secret or existing_secret):
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                "Чтобы включить Platega, укажите Merchant ID и секрет",
            )
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

    await db.commit()  # видно другим сессиям ДО pub/sub-уведомления бота
    await bus.publish(bus.CMD_RELOAD)
    return await get_providers(admin, db)


class RollyPayIn(BaseModel):
    enabled: bool
    # Пусто = оставить прежний ключ — так же, как секреты остальных провайдеров.
    api_key: str = Field(default="", max_length=256)
    # Секрет подписи вебхуков (кабинет RollyPay → касса → Webhook secret).
    # Пусто — не менять; с ним колбэки без валидной подписи отбрасываются.
    signing_secret: str = Field(default="", max_length=256)


@router.put("/providers/rollypay", response_model=ProvidersOut)
async def save_rollypay(
    data: RollyPayIn, admin: CurrentAdmin, db: DbSession, request: Request
) -> ProvidersOut:
    if data.enabled and not (data.api_key or await cfg.get(db, cfg.ROLLYPAY_API_KEY)):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "Чтобы включить RollyPay, укажите API-ключ кассы",
        )
    await cfg.set_(db, cfg.ROLLYPAY_ENABLED, "true" if data.enabled else "false")
    if data.api_key:
        await cfg.set_(db, cfg.ROLLYPAY_API_KEY, data.api_key)
    if data.signing_secret:
        await cfg.set_(db, cfg.ROLLYPAY_SIGNING_SECRET, data.signing_secret)
    db.add(
        AuditLog(
            admin_id=admin.id,
            action="payments.rollypay",
            ip=request.client.host if request.client else None,
            created_at=datetime.now(UTC),
        )
    )
    from shared import bus

    await db.commit()  # видно другим сессиям ДО pub/sub-уведомления бота
    await bus.publish(bus.CMD_RELOAD)
    return await get_providers(admin, db)


class CisPayIn(BaseModel):
    enabled: bool
    shop_id: str = Field(default="", max_length=64)
    # Пусто = оставить прежний ключ.
    api_key: str = Field(default="", max_length=256)


class CisPayCheckOut(BaseModel):
    ok: bool
    message: str


@router.post("/providers/cispay/check", response_model=CisPayCheckOut)
async def check_cispay(data: CisPayIn, admin: CurrentAdmin, db: DbSession) -> CisPayCheckOut:
    """Проверяет Shop ID и ключ и показывает, какие способы оплаты включены
    у магазина — до того, как клиенты увидят кнопку в боте."""
    from shared.payments.cispay import CisPayClient, CisPayError

    api_key = data.api_key or await cfg.get(db, cfg.CISPAY_API_KEY)
    shop_id = data.shop_id.strip() or await cfg.get(db, cfg.CISPAY_SHOP_ID)
    if not shop_id or not api_key:
        return CisPayCheckOut(ok=False, message="Укажите Shop ID и API-ключ")
    try:
        info = await CisPayClient(shop_id, api_key).capabilities()
    except CisPayError as exc:
        return CisPayCheckOut(ok=False, message=str(exc))
    names = {"CARD": "карта", "SBP": "СБП", "CRYPTO": "криптовалюта"}
    active = [
        names.get(str(m.get("payment_method")), str(m.get("payment_method")))
        for m in info.get("payment_methods") or []
        if m.get("is_active")
    ]
    if not info.get("is_active", True):
        return CisPayCheckOut(ok=False, message="Ключи верные, но магазин в cisPay не активен")
    if not active:
        return CisPayCheckOut(
            ok=False, message="Ключи верные, но у магазина не подключён ни один способ оплаты"
        )
    store = info.get("store_name") or "магазин"
    return CisPayCheckOut(ok=True, message=f"Подключено: «{store}», способы: {', '.join(active)}")


@router.put("/providers/cispay", response_model=ProvidersOut)
async def save_cispay(
    data: CisPayIn, admin: CurrentAdmin, db: DbSession, request: Request
) -> ProvidersOut:
    shop_id = data.shop_id.strip()
    if data.enabled and not (
        (shop_id or await cfg.get(db, cfg.CISPAY_SHOP_ID))
        and (data.api_key or await cfg.get(db, cfg.CISPAY_API_KEY))
    ):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "Чтобы включить cisPay, укажите Shop ID и API-ключ магазина",
        )
    await cfg.set_(db, cfg.CISPAY_ENABLED, "true" if data.enabled else "false")
    if shop_id:
        await cfg.set_(db, cfg.CISPAY_SHOP_ID, shop_id)
    if data.api_key:
        await cfg.set_(db, cfg.CISPAY_API_KEY, data.api_key.strip())
    db.add(
        AuditLog(
            admin_id=admin.id,
            action="payments.cispay",
            ip=request.client.host if request.client else None,
            created_at=datetime.now(UTC),
        )
    )
    from shared import bus

    await db.commit()  # видно другим сессиям ДО pub/sub-уведомления бота
    await bus.publish(bus.CMD_RELOAD)
    return await get_providers(admin, db)


class CryptoBotIn(BaseModel):
    enabled: bool
    token: str = Field(default="", max_length=256)


@router.put("/providers/cryptobot", response_model=ProvidersOut)
async def save_cryptobot(
    data: CryptoBotIn, admin: CurrentAdmin, db: DbSession, request: Request
) -> ProvidersOut:
    if data.enabled and not (data.token or await cfg.get(db, cfg.CRYPTOBOT_TOKEN)):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "Чтобы включить CryptoBot, укажите токен приложения",
        )
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

    await db.commit()  # видно другим сессиям ДО pub/sub-уведомления бота
    await bus.publish(bus.CMD_RELOAD)
    return await get_providers(admin, db)


class StarsIn(BaseModel):
    enabled: bool
    # Пусто — оставить прежний курс.
    rub_per_star: float | None = Field(default=None, ge=0.1, le=100)


@router.put("/providers/stars", response_model=ProvidersOut)
async def save_stars(
    data: StarsIn, admin: CurrentAdmin, db: DbSession, request: Request
) -> ProvidersOut:
    await cfg.set_(db, cfg.STARS_ENABLED, "true" if data.enabled else "false")
    if data.rub_per_star is not None:
        await cfg.set_(db, cfg.STARS_RUB_PER_STAR, str(round(data.rub_per_star, 4)))
    db.add(
        AuditLog(
            admin_id=admin.id,
            action="payments.stars",
            ip=request.client.host if request.client else None,
            created_at=datetime.now(UTC),
        )
    )
    from shared import bus

    await db.commit()  # видно другим сессиям ДО pub/sub-уведомления бота
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
