from fastapi import APIRouter
from pydantic import BaseModel
from sqlalchemy import text

from app.api.deps import DbSession
from app.core.config import settings

router = APIRouter(tags=["health"])


class Health(BaseModel):
    status: str
    database: bool
    deploy_mode: str


@router.get("/health", response_model=Health)
async def health(db: DbSession) -> Health:
    try:
        await db.execute(text("SELECT 1"))
        db_ok = True
    except Exception:
        db_ok = False
    return Health(
        status="ok" if db_ok else "degraded",
        database=db_ok,
        deploy_mode=settings.deploy_mode,
    )
