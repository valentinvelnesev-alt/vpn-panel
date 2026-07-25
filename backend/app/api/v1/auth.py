from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, HTTPException, Request, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.api.deps import CurrentAdmin, DbSession
from app.core.config import settings
from app.core.security import (
    create_access_token,
    generate_refresh_token,
    hash_password,
    hash_refresh_token,
    needs_rehash,
    verify_password,
)
from shared.db.models import Admin, AuditLog, RefreshToken

router = APIRouter(prefix="/auth", tags=["auth"])

ACCESS_COOKIE = "access_token"
REFRESH_COOKIE = "refresh_token"


class LoginRequest(BaseModel):
    login: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=256)
    totp_code: str | None = Field(default=None, max_length=6)


class AdminOut(BaseModel):
    id: int
    login: str
    is_owner: bool
    totp_enabled: bool


def _as_utc(value: datetime) -> datetime:
    """Postgres отдаёт TIMESTAMPTZ уже с зоной, SQLite (в тестах) — без неё."""
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def _set_auth_cookies(response: Response, access: str, refresh: str) -> None:
    """Токены — в httponly-cookie: JS их не прочитает, XSS не уносит сессию.

    Secure ставим только при HTTPS: в режиме ip браузер отбросил бы такую
    cookie и вход был бы невозможен.
    """
    common = {
        "httponly": True,
        "samesite": "lax",
        "secure": settings.https_enabled,
        "path": "/",
    }
    response.set_cookie(
        ACCESS_COOKIE,
        access,
        max_age=settings.access_token_ttl_minutes * 60,
        **common,
    )
    response.set_cookie(
        REFRESH_COOKIE,
        refresh,
        max_age=settings.refresh_token_ttl_days * 86400,
        **common,
    )


async def _issue_session(
    db: DbSession, admin: Admin, request: Request, response: Response
) -> None:
    token, token_hash = generate_refresh_token()
    db.add(
        RefreshToken(
            admin_id=admin.id,
            token_hash=token_hash,
            expires_at=datetime.now(UTC)
            + timedelta(days=settings.refresh_token_ttl_days),
            user_agent=(request.headers.get("user-agent") or "")[:255],
            ip=request.client.host if request.client else None,
        )
    )
    _set_auth_cookies(response, create_access_token(admin.id), token)


@router.post("/login", response_model=AdminOut)
async def login(
    data: LoginRequest, request: Request, response: Response, db: DbSession
) -> Admin:
    admin = await db.scalar(select(Admin).where(Admin.login == data.login))

    # Проверяем пароль даже для несуществующего логина — иначе разница во
    # времени ответа выдаёт, какие логины заведены.
    stored_hash = admin.password_hash if admin else hash_password("dummy")
    password_ok = verify_password(data.password, stored_hash)

    if not admin or not password_ok or not admin.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Неверный логин или пароль",
        )

    if admin.totp_enabled:
        from app.core.totp import verify_totp

        if not data.totp_code or not verify_totp(admin.totp_secret or "", data.totp_code):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Неверный код двухфакторной аутентификации",
            )

    if needs_rehash(admin.password_hash):
        admin.password_hash = hash_password(data.password)

    admin.last_login_at = datetime.now(UTC)
    await _issue_session(db, admin, request, response)
    db.add(
        AuditLog(
            admin_id=admin.id,
            action="login",
            ip=request.client.host if request.client else None,
            created_at=datetime.now(UTC),
        )
    )
    return admin


@router.post("/refresh", response_model=AdminOut)
async def refresh(request: Request, response: Response, db: DbSession) -> Admin:
    raw = request.cookies.get(REFRESH_COOKIE)
    if not raw:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Сессия истекла")

    stored = await db.scalar(
        select(RefreshToken).where(RefreshToken.token_hash == hash_refresh_token(raw))
    )
    now = datetime.now(UTC)
    if stored is None or stored.revoked_at is not None or _as_utc(stored.expires_at) <= now:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Сессия истекла")

    admin = await db.get(Admin, stored.admin_id)
    if admin is None or not admin.is_active:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Сессия истекла")

    # Ротация: старый токен гасим, выдаём новый — украденный refresh
    # перестаёт работать, как только им воспользовался законный владелец.
    stored.revoked_at = now
    await _issue_session(db, admin, request, response)
    return admin


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(request: Request, response: Response, db: DbSession) -> None:
    raw = request.cookies.get(REFRESH_COOKIE)
    if raw:
        stored = await db.scalar(
            select(RefreshToken).where(
                RefreshToken.token_hash == hash_refresh_token(raw)
            )
        )
        if stored and stored.revoked_at is None:
            stored.revoked_at = datetime.now(UTC)
    response.delete_cookie(ACCESS_COOKIE, path="/")
    response.delete_cookie(REFRESH_COOKIE, path="/")


@router.get("/me", response_model=AdminOut)
async def me(admin: CurrentAdmin) -> Admin:
    return admin
