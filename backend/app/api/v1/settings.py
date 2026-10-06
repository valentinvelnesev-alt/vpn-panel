from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, Field, HttpUrl, field_validator

from app.api.deps import CurrentAdmin, DbSession
from shared.db.models import AuditLog
from app.core.security import mask
from app.services import remnawave_provider
from app.services import settings_service as cfg
from shared.panel import make_client, panel_type
from shared.remnawave import RemnawaveError

router = APIRouter(prefix="/settings", tags=["settings"])


class RemnawaveSettingsOut(BaseModel):
    url: str | None
    token_masked: str | None
    verify_tls: bool
    configured: bool
    subscription_redirect_enabled: bool = False
    # Тип панели задаётся при установке (PANEL_TYPE в .env): UI по нему
    # подписывает поля и называет сквады инбаундами.
    panel_type: str = "remnawave"


class RemnawaveSettingsIn(BaseModel):
    url: HttpUrl
    # Пустой токен = «оставить прежний»: UI показывает маску, а не сам секрет.
    token: str = Field(default="", max_length=4096)
    verify_tls: bool = True
    subscription_redirect_enabled: bool = False


class ConnectionCheck(BaseModel):
    ok: bool
    message: str
    version: str | None = None


@router.get("/remnawave", response_model=RemnawaveSettingsOut)
async def get_remnawave(admin: CurrentAdmin, db: DbSession) -> RemnawaveSettingsOut:
    values = await cfg.get_many(
        db,
        cfg.REMNAWAVE_URL,
        cfg.REMNAWAVE_TOKEN,
        cfg.REMNAWAVE_VERIFY_TLS,
        cfg.SUBSCRIPTION_REDIRECT_ENABLED,
    )
    token = values[cfg.REMNAWAVE_TOKEN]
    return RemnawaveSettingsOut(
        url=values[cfg.REMNAWAVE_URL],
        token_masked=mask(token) if token else None,
        verify_tls=values[cfg.REMNAWAVE_VERIFY_TLS] != "false",
        configured=bool(values[cfg.REMNAWAVE_URL] and token),
        subscription_redirect_enabled=values[cfg.SUBSCRIPTION_REDIRECT_ENABLED] == "true",
        panel_type=panel_type(),
    )


@router.put("/remnawave", response_model=RemnawaveSettingsOut)
async def save_remnawave(
    data: RemnawaveSettingsIn, admin: CurrentAdmin, db: DbSession, request: Request
) -> RemnawaveSettingsOut:
    await cfg.set_(db, cfg.REMNAWAVE_URL, str(data.url).rstrip("/"))
    await cfg.set_(db, cfg.REMNAWAVE_VERIFY_TLS, "true" if data.verify_tls else "false")
    await cfg.set_(
        db,
        cfg.SUBSCRIPTION_REDIRECT_ENABLED,
        "true" if data.subscription_redirect_enabled else "false",
    )
    if data.token:
        await cfg.set_(db, cfg.REMNAWAVE_TOKEN, data.token)

    db.add(
        AuditLog(
            admin_id=admin.id,
            action="settings.remnawave",
            target=str(data.url),
            ip=request.client.host if request.client else None,
            created_at=datetime.now(UTC),
        )
    )
    await db.flush()
    # Настройки изменились — кэшированные клиенты держат старый токен.
    await remnawave_provider.close_all()
    from shared import bus

    await db.commit()  # видно другим сессиям ДО pub/sub-уведомления бота
    await bus.publish(bus.CMD_RELOAD)
    return await get_remnawave(admin, db)


@router.post("/remnawave/check", response_model=ConnectionCheck)
async def check_remnawave(
    data: RemnawaveSettingsIn, admin: CurrentAdmin, db: DbSession
) -> ConnectionCheck:
    """Проверяет пару адрес+токен до сохранения, чтобы не сломать рабочую связь."""
    token = data.token or await cfg.get(db, cfg.REMNAWAVE_TOKEN)
    if not token:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Укажите токен")

    client = make_client(str(data.url), token, verify_tls=data.verify_tls)
    try:
        meta = await client.check_connection()
    except RemnawaveError as exc:
        return ConnectionCheck(ok=False, message=str(exc))
    finally:
        await client.aclose()

    version = None
    if isinstance(meta, dict):
        version = meta.get("version") or (meta.get("remnawave") or {}).get("version")
    return ConnectionCheck(
        ok=True,
        message="Подключение работает",
        version=str(version) if version else None,
    )


# ── Название и логотип панели ───────────────────────────────────────────
DEFAULT_TITLE = "Panel"


class PublicBrandOut(BaseModel):
    title: str
    logo_url: str | None


@router.get("/brand/public", response_model=PublicBrandOut)
async def get_public_brand(db: DbSession) -> PublicBrandOut:
    """Без авторизации: нужно странице входа для <title> и шапки. Отдаёт
    только то, что админ сам задал показывать, — по умолчанию нейтральное
    «Panel», а не «VPN Panel», чтобы вкладка не выдавала, что это за сервис."""
    values = await cfg.get_many(db, cfg.BRAND_NAME, cfg.BRAND_LOGO_URL)
    return PublicBrandOut(
        title=(values[cfg.BRAND_NAME] or "").strip() or DEFAULT_TITLE,
        logo_url=values[cfg.BRAND_LOGO_URL] or None,
    )


class BrandOut(BaseModel):
    brand_name: str | None
    brand_logo_url: str | None
    hide_powered_by: bool


class BrandIn(BaseModel):
    brand_name: str | None = Field(default=None, max_length=64)
    brand_logo_url: str | None = Field(default=None, max_length=512)
    hide_powered_by: bool = False

    @field_validator("brand_logo_url")
    @classmethod
    def _logo_url(cls, value: str | None) -> str | None:
        value = (value or "").strip() or None
        if value and not value.startswith(("https://", "http://", "/", "data:image/")):
            raise ValueError("Ссылка на логотип должна начинаться с https://")
        return value


@router.get("/brand", response_model=BrandOut)
async def get_brand(admin: CurrentAdmin, db: DbSession) -> BrandOut:
    values = await cfg.get_many(
        db, cfg.BRAND_NAME, cfg.BRAND_LOGO_URL, cfg.HIDE_POWERED_BY
    )
    return BrandOut(
        brand_name=values[cfg.BRAND_NAME],
        brand_logo_url=values[cfg.BRAND_LOGO_URL],
        hide_powered_by=values[cfg.HIDE_POWERED_BY] == "true",
    )


@router.put("/brand", response_model=BrandOut)
async def save_brand(
    data: BrandIn, admin: CurrentAdmin, db: DbSession, request: Request
) -> BrandOut:
    db.add(
        AuditLog(
            admin_id=admin.id,
            action="settings.brand",
            ip=request.client.host if request.client else None,
            created_at=datetime.now(UTC),
        )
    )
    await cfg.set_(db, cfg.BRAND_NAME, (data.brand_name or "").strip() or None)
    await cfg.set_(db, cfg.BRAND_LOGO_URL, data.brand_logo_url)
    await cfg.set_(db, cfg.HIDE_POWERED_BY, "true" if data.hide_powered_by else "false")
    return await get_brand(admin, db)
