from typing import Annotated

import jwt
from fastapi import Cookie, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import decode_token
from shared.db.models import Admin
from app.db.session import get_db

DbSession = Annotated[AsyncSession, Depends(get_db)]

_UNAUTHORIZED = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="Требуется вход в панель",
)


async def get_current_admin(
    db: DbSession,
    access_token: Annotated[str | None, Cookie()] = None,
) -> Admin:
    if not access_token:
        raise _UNAUTHORIZED
    try:
        payload = decode_token(access_token, "access")
    except jwt.InvalidTokenError as exc:
        raise _UNAUTHORIZED from exc

    admin = await db.get(Admin, int(payload["sub"]))
    if admin is None or not admin.is_active:
        raise _UNAUTHORIZED
    return admin


CurrentAdmin = Annotated[Admin, Depends(get_current_admin)]

