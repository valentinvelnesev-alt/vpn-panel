from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import settings

# Настройки пула применимы только к серверным БД; у SQLite (тесты) свой пул,
# который таких аргументов не принимает.
_pool_options = (
    {"pool_size": 10, "max_overflow": 20}
    if not settings.database_url.startswith("sqlite")
    else {}
)

engine = create_async_engine(
    settings.database_url,
    pool_pre_ping=True,
    echo=False,
    **_pool_options,
)

SessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


async def get_db() -> AsyncIterator[AsyncSession]:
    """Зависимость FastAPI: сессия на запрос, коммит по успеху."""
    async with SessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
