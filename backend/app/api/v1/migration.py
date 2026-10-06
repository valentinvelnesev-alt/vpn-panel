"""Перенос клиентов из других ботов — пока из Bedolaga."""

import os
import shutil
import tempfile
from datetime import UTC, datetime

from fastapi import APIRouter, Form, HTTPException, Request, UploadFile, status
from pydantic import BaseModel

from app.api.deps import CurrentAdmin, DbSession
from app.services import bedolaga_import
from shared.db.models import AuditLog

router = APIRouter(prefix="/bot/import", tags=["import"])


class ImportReport(BaseModel):
    dry_run: bool
    users_created: int
    users_updated: int
    users_skipped: int
    balances_moved: int
    balance_total_rub: float
    subscriptions_imported: int
    referrals_linked: int


@router.post("/bedolaga", response_model=ImportReport)
async def import_bedolaga(
    admin: CurrentAdmin,
    db: DbSession,
    request: Request,
    file: UploadFile,
    dry_run: bool = Form(True),
) -> ImportReport:
    """Принимает бэкап Bedolaga (backup_*.tar.gz или database.sql/.json/.sqlite).

    dry_run — только посчитать, что будет перенесено, ничего не записывая:
    админ сначала видит цифры, потом подтверждает."""
    fd, path = tempfile.mkstemp(prefix="bedolaga-", suffix=os.path.splitext(file.filename or "")[1])
    try:
        with os.fdopen(fd, "wb") as out:
            shutil.copyfileobj(file.file, out, 1 << 20)
        try:
            rows = bedolaga_import.read_backup(path)
        except bedolaga_import.ImportError_ as exc:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
        except Exception as exc:  # noqa: BLE001 — битый архив, чужой формат
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY, f"Не удалось прочитать файл: {exc}"
            ) from exc
    finally:
        if os.path.exists(path):
            os.unlink(path)

    report = await bedolaga_import.apply(db, rows, dry_run=dry_run)
    if not dry_run:
        db.add(
            AuditLog(
                admin_id=admin.id,
                action="import.bedolaga",
                details={
                    "created": report.users_created,
                    "updated": report.users_updated,
                    "subscriptions": report.subscriptions_imported,
                    "balance_kopeks": report.balance_total_kopeks,
                },
                ip=request.client.host if request.client else None,
                created_at=datetime.now(UTC),
            )
        )
    return ImportReport(
        dry_run=dry_run,
        users_created=report.users_created,
        users_updated=report.users_updated,
        users_skipped=report.users_skipped,
        balances_moved=report.balances_moved,
        balance_total_rub=report.balance_total_kopeks / 100,
        subscriptions_imported=report.subscriptions_imported,
        referrals_linked=report.referrals_linked,
    )
