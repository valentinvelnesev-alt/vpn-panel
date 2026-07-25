"""Резервные копии БД (Pro).

Ручной запуск через панель + список файлов. Для автоматического расписания
проще всего добавить cron внутри контейнера `panel-api`, дёргающий этот же
эндпоинт, либо системный cron на хосте, вызывающий `docker compose exec`.
"""

import asyncio
import os
from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel

from app.api.deps import CurrentAdmin
from app.core.config import settings

router = APIRouter(prefix="/backups", tags=["backups"])


class BackupFile(BaseModel):
    name: str
    size_bytes: int
    created_at: str


@router.get("", response_model=list[BackupFile])
async def list_backups(admin: CurrentAdmin) -> list[BackupFile]:
    directory = settings.backup_dir
    if not os.path.isdir(directory):
        return []
    files = []
    for name in sorted(os.listdir(directory), reverse=True):
        if not name.endswith(".sql.gz"):
            continue
        path = os.path.join(directory, name)
        stat = os.stat(path)
        files.append(
            BackupFile(
                name=name,
                size_bytes=stat.st_size,
                created_at=datetime.fromtimestamp(stat.st_mtime, tz=UTC).isoformat(),
            )
        )
    return files


@router.post("/run", response_model=BackupFile, status_code=status.HTTP_201_CREATED)
async def run_backup(admin: CurrentAdmin) -> BackupFile:
    os.makedirs(settings.backup_dir, exist_ok=True)
    name = f"backup_{datetime.now(UTC):%Y%m%d_%H%M%S}.sql.gz"
    path = os.path.join(settings.backup_dir, name)

    # pg_dump | gzip — без временного несжатого файла на диске.
    process = await asyncio.create_subprocess_shell(
        f"pg_dump '{settings.database_url.replace('+asyncpg', '')}' | gzip > '{path}'",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await process.communicate()
    if process.returncode != 0:
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            f"pg_dump завершился с ошибкой: {stderr.decode()[:300]}",
        )

    stat = os.stat(path)
    return BackupFile(
        name=name,
        size_bytes=stat.st_size,
        created_at=datetime.fromtimestamp(stat.st_mtime, tz=UTC).isoformat(),
    )
