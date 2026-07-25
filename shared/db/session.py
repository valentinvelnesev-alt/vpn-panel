"""Подключение к БД для контейнера бота.

У панели своя фабрика сессий (app/db/session.py) с зависимостью FastAPI;
здесь минимум, нужный фоновым задачам бота.
"""

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

_url = os.environ["DATABASE_URL"]

# Настройки пула применимы только к серверным БД; у SQLite (тесты) свой пул,
# который таких аргументов не принимает.
_pool_options = (
    {"pool_size": 5, "max_overflow": 10} if not _url.startswith("sqlite") else {}
)

engine = create_async_engine(_url, pool_pre_ping=True, **_pool_options)

SessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


@asynccontextmanager
async def session() -> AsyncIterator[AsyncSession]:
    async with SessionLocal() as db:
        try:
            yield db
            await db.commit()
        except Exception:
            await db.rollback()
            raise
