"""Резервные копии БД: список, ручной запуск, расписание, скачивание,
загрузка своего файла и восстановление (см. app/services/backups.py)."""

import os
from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, Request, UploadFile, status
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from app.api.deps import CurrentAdmin, DbSession
from app.services import backups as svc
from app.services import settings_service as cfg
from shared import bus
from shared.db.models import AuditLog

router = APIRouter(prefix="/backups", tags=["backups"])

# Фраза-подтверждение: восстановление перезаписывает всю базу.
RESTORE_CONFIRM = "ВОССТАНОВИТЬ"
MAX_UPLOAD_BYTES = 512 * 1024 * 1024


class BackupFileOut(BaseModel):
    name: str
    size_bytes: int
    created_at: datetime


class ScheduleOut(BaseModel):
    enabled: bool
    interval_hours: int
    keep: int


class BackupsOut(BaseModel):
    files: list[BackupFileOut]
    schedule: ScheduleOut
    busy: bool


class ScheduleIn(BaseModel):
    enabled: bool
    interval_hours: int = Field(ge=1, le=24 * 30)
    keep: int = Field(ge=1, le=365)


class RestoreIn(BaseModel):
    confirm: str


class RestoreOut(BaseModel):
    ok: bool
    message: str
    safety_backup: str


def _out(f: svc.BackupFile) -> BackupFileOut:
    return BackupFileOut(name=f.name, size_bytes=f.size_bytes, created_at=f.created_at)


def _audit(db, admin, action: str, request: Request, target: str | None = None) -> None:
    db.add(
        AuditLog(
            admin_id=admin.id,
            action=action,
            target=target,
            ip=request.client.host if request.client else None,
            created_at=datetime.now(UTC),
        )
    )


def _fail(exc: svc.BackupError, code: int = status.HTTP_422_UNPROCESSABLE_ENTITY):
    return HTTPException(code, str(exc))


@router.get("", response_model=BackupsOut)
async def list_backups(admin: CurrentAdmin) -> BackupsOut:
    schedule = await svc.load_schedule()
    return BackupsOut(
        files=[_out(f) for f in svc.list_files()],
        schedule=ScheduleOut(
            enabled=schedule.enabled, interval_hours=schedule.interval_hours, keep=schedule.keep
        ),
        busy=svc.busy(),
    )


@router.put("/schedule", response_model=ScheduleOut)
async def save_schedule(
    data: ScheduleIn, admin: CurrentAdmin, db: DbSession, request: Request
) -> ScheduleOut:
    await cfg.set_(db, svc.AUTO_ENABLED, "true" if data.enabled else "false")
    await cfg.set_(db, svc.INTERVAL_HOURS, str(data.interval_hours))
    await cfg.set_(db, svc.KEEP, str(data.keep))
    _audit(db, admin, "backups.schedule", request)
    return ScheduleOut(**data.model_dump())


@router.post("/run", response_model=BackupFileOut, status_code=status.HTTP_201_CREATED)
async def run_backup(admin: CurrentAdmin, db: DbSession, request: Request) -> BackupFileOut:
    if svc.busy():
        raise HTTPException(status.HTTP_409_CONFLICT, "Уже идёт другая операция с копиями")
    try:
        created = await svc.create()
    except svc.BackupError as exc:
        raise _fail(exc, status.HTTP_500_INTERNAL_SERVER_ERROR) from exc
    _audit(db, admin, "backups.run", request, created.name)
    return _out(created)


@router.get("/{name}/download")
async def download_backup(name: str, admin: CurrentAdmin) -> FileResponse:
    try:
        path = svc.path_of(name)
    except svc.BackupError as exc:
        raise _fail(exc) from exc
    if not os.path.isfile(path):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Резервная копия не найдена")
    return FileResponse(path, media_type="application/gzip", filename=name)


@router.delete("/{name}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_backup(name: str, admin: CurrentAdmin, db: DbSession, request: Request) -> None:
    try:
        svc.delete(name)
    except svc.BackupError as exc:
        raise _fail(exc, status.HTTP_404_NOT_FOUND) from exc
    _audit(db, admin, "backups.delete", request, name)


@router.post("/upload", response_model=BackupFileOut, status_code=status.HTTP_201_CREATED)
async def upload_backup(
    file: UploadFile, admin: CurrentAdmin, db: DbSession, request: Request
) -> BackupFileOut:
    """Копия с другого сервера — после загрузки её можно восстановить."""
    data = await file.read(MAX_UPLOAD_BYTES + 1)
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, "Файл больше 512 МБ")
    try:
        saved = svc.save_upload(data)
    except svc.BackupError as exc:
        raise _fail(exc) from exc
    _audit(db, admin, "backups.upload", request, saved.name)
    return _out(saved)


@router.post("/{name}/restore", response_model=RestoreOut)
async def restore_backup(
    name: str, data: RestoreIn, admin: CurrentAdmin, db: DbSession, request: Request
) -> RestoreOut:
    if data.confirm.strip().upper() != RESTORE_CONFIRM:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            f"Для подтверждения введите «{RESTORE_CONFIRM}»",
        )
    if svc.busy():
        raise HTTPException(status.HTTP_409_CONFLICT, "Уже идёт другая операция с копиями")
    # Своя сессия запроса держала бы соединение во время DROP TABLE.
    await db.commit()
    try:
        safety = await svc.restore(name)
    except svc.BackupError as exc:
        raise _fail(exc) from exc
    # После восстановления в базе другая история — журнал пишем уже в неё.
    _audit(db, admin, "backups.restore", request, name)
    # Тарифы, токен и настройки бота могли смениться.
    await bus.publish(bus.CMD_START)
    return RestoreOut(
        ok=True,
        message=f"База восстановлена из {name}. Состояние до восстановления сохранено в "
        f"{safety.name}. Войдите в панель заново, если сессия прервётся.",
        safety_backup=safety.name,
    )
