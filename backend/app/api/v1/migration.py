"""Перенос клиентов из других ботов: Bedolaga и STEALTHNET.

Чей бэкап — определяется по содержимому (см. bedolaga_import.detect_source),
админу не нужно ничего выбирать.
"""

import logging
import os
import shutil
import tempfile
from datetime import UTC, datetime

from fastapi import APIRouter, Form, HTTPException, Request, UploadFile, status
from pydantic import BaseModel

from app.api.deps import CurrentAdmin, DbSession
from app.services import bedolaga_import, stealthnet_import
from shared.db.models import AuditLog

log = logging.getLogger("import")

router = APIRouter(prefix="/bot/import", tags=["import"])

SOURCE_TITLE = {"bedolaga": "Bedolaga", "stealthnet": "STEALTHNET"}


class ImportReport(BaseModel):
    source: str
    source_title: str
    dry_run: bool
    # Валюта баланса в исходном боте; не «rub» — нужен курс (rub_rate).
    currency: str
    needs_rate: bool
    users_created: int
    users_updated: int
    users_skipped: int
    balances_moved: int
    balance_total_rub: float
    subscriptions_imported: int
    referrals_linked: int
    warnings: list[str]


async def _remote_index(db) -> stealthnet_import.RemoteIndex | None:
    """Все пользователи Remnawave — чтобы сопоставить ключи. Нет связи —
    None: ключи подтянутся ботом при /start, остальное переносится."""
    from app.services.remnawave_provider import get_client

    try:
        client = await get_client(db)
        return await stealthnet_import.load_remote_index(client)
    except Exception as exc:  # noqa: BLE001 — не настроена, недоступна, чужая версия
        log.warning("Импорт: Remnawave недоступна: %s", exc)
        return None


@router.post("", response_model=ImportReport)
@router.post("/bedolaga", response_model=ImportReport, include_in_schema=False)
async def import_backup(
    admin: CurrentAdmin,
    db: DbSession,
    request: Request,
    file: UploadFile,
    dry_run: bool = Form(True),
    rub_rate: float | None = Form(None, gt=0, le=100_000),
) -> ImportReport:
    """Принимает бэкап Bedolaga (backup_*.tar.gz, database.sql/.json/.sqlite)
    или STEALTHNET (stealthnet-backup-*.sql).

    dry_run — только посчитать, что будет перенесено, ничего не записывая:
    админ сначала видит цифры, потом подтверждает."""
    fd, path = tempfile.mkstemp(prefix="import-", suffix=os.path.splitext(file.filename or "")[1])
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

    source = bedolaga_import.detect_source(rows)
    if source == "stealthnet":
        currency = stealthnet_import.store_currency(rows)
        needs_rate = currency != "rub"
        if needs_rate and rub_rate is None and not dry_run:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                f"Балансы в STEALTHNET в валюте {currency.upper()} — укажите курс к рублю",
            )
        report = await stealthnet_import.apply(
            db,
            rows,
            rub_rate=(rub_rate or 1.0) if needs_rate else 1.0,
            remote=await _remote_index(db),
            dry_run=dry_run,
        )
    else:
        currency, needs_rate = "rub", False
        report = await bedolaga_import.apply(db, rows, dry_run=dry_run)

    if not dry_run:
        db.add(
            AuditLog(
                admin_id=admin.id,
                action=f"import.{source}",
                details={
                    "created": report.users_created,
                    "updated": report.users_updated,
                    "subscriptions": report.subscriptions_imported,
                    "balance_kopeks": report.balance_total_kopeks,
                    "rub_rate": rub_rate,
                },
                ip=request.client.host if request.client else None,
                created_at=datetime.now(UTC),
            )
        )
    return ImportReport(
        source=source,
        source_title=SOURCE_TITLE[source],
        dry_run=dry_run,
        currency=currency,
        needs_rate=needs_rate,
        users_created=report.users_created,
        users_updated=report.users_updated,
        users_skipped=report.users_skipped,
        balances_moved=report.balances_moved,
        balance_total_rub=report.balance_total_kopeks / 100,
        subscriptions_imported=report.subscriptions_imported,
        referrals_linked=report.referrals_linked,
        warnings=report.warnings[:20],
    )
