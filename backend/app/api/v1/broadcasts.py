import asyncio
import os
import uuid
from datetime import UTC, datetime

from fastapi import (
    APIRouter,
    HTTPException,
    Request,
    UploadFile,
    WebSocket,
    WebSocketDisconnect,
    status,
)
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, select

from app.api.deps import CurrentAdmin, DbSession
from app.core.config import settings
from app.db.session import SessionLocal
from shared import bus
from shared.db.models import AuditLog, Broadcast, BroadcastSegment, BroadcastStatus
from shared.segments import recipients_query

router = APIRouter(prefix="/broadcasts", tags=["broadcasts"])

MAX_PHOTO_BYTES = 8 * 1024 * 1024  # 8 МБ — с запасом под лимит Telegram (10 МБ)
ALLOWED_TYPES = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp"}


@router.post("/upload-photo")
async def upload_photo(admin: CurrentAdmin, file: UploadFile) -> dict:
    """Сохраняет фото для рассылки и отдаёт публичный URL — его скачает
    Telegram при отправке сообщения, поэтому файл должен быть доступен
    без авторизации (см. caddy/panel.snippet, /uploads/*)."""
    ext = ALLOWED_TYPES.get(file.content_type or "")
    if ext is None:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "Поддерживаются только JPEG, PNG и WebP",
        )

    body = await file.read(MAX_PHOTO_BYTES + 1)
    if len(body) > MAX_PHOTO_BYTES:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "Файл больше 8 МБ")

    os.makedirs(settings.upload_dir, exist_ok=True)
    name = f"{uuid.uuid4().hex}{ext}"
    with open(os.path.join(settings.upload_dir, name), "wb") as f:
        f.write(body)

    return {"url": f"{settings.public_url}/uploads/{name}"}


class ButtonIn(BaseModel):
    text: str = Field(min_length=1, max_length=64)
    url: str = Field(min_length=1, max_length=512)


class BroadcastIn(BaseModel):
    text: str = Field(min_length=1, max_length=4000)
    photo_url: str | None = Field(default=None, max_length=512)
    buttons: list[ButtonIn] = Field(default_factory=list, max_length=8)
    segment: BroadcastSegment = BroadcastSegment.ALL
    # Пусто = отправить сейчас.
    scheduled_at: datetime | None = None

    @field_validator("scheduled_at")
    @classmethod
    def _not_in_past(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        now = datetime.now(UTC)
        check = value if value.tzinfo else value.replace(tzinfo=UTC)
        if check < now:
            raise ValueError("Время отправки не может быть в прошлом")
        return value


class BroadcastOut(BaseModel):
    id: int
    text: str
    photo_url: str | None
    buttons: list[ButtonIn]
    segment: str
    status: str
    scheduled_at: datetime
    total_recipients: int
    sent_count: int
    failed_count: int
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None


def _out(b: Broadcast) -> BroadcastOut:
    return BroadcastOut(
        id=b.id,
        text=b.text,
        photo_url=b.photo_url,
        buttons=[ButtonIn.model_validate(x) for x in (b.buttons or [])],
        segment=str(b.segment),
        status=str(b.status),
        scheduled_at=b.scheduled_at,
        total_recipients=b.total_recipients,
        sent_count=b.sent_count,
        failed_count=b.failed_count,
        created_at=b.created_at,
        started_at=b.started_at,
        finished_at=b.finished_at,
    )


@router.get("", response_model=list[BroadcastOut])
async def list_broadcasts(admin: CurrentAdmin, db: DbSession) -> list[BroadcastOut]:
    rows = await db.scalars(select(Broadcast).order_by(Broadcast.created_at.desc()))
    return [_out(b) for b in rows]


@router.get("/segments/counts")
async def segment_counts(admin: CurrentAdmin, db: DbSession) -> dict[str, int]:
    """Предпросмотр размера сегмента до отправки — чтобы не разослать
    рассылку «всем», думая, что она уйдёт десятку активных клиентов."""
    result = {}
    for segment in BroadcastSegment:
        count = await db.scalar(
            select(func.count()).select_from(recipients_query(segment).subquery())
        )
        result[segment.value] = count or 0
    return result


@router.post("", response_model=BroadcastOut, status_code=status.HTTP_201_CREATED)
async def create_broadcast(
    data: BroadcastIn, admin: CurrentAdmin, db: DbSession, request: Request
) -> BroadcastOut:
    now = datetime.now(UTC)
    send_now = data.scheduled_at is None

    broadcast = Broadcast(
        text=data.text,
        photo_url=data.photo_url,
        buttons=[b.model_dump() for b in data.buttons],
        segment=data.segment,
        status=BroadcastStatus.SCHEDULED,
        scheduled_at=data.scheduled_at or now,
        created_by_admin_id=admin.id,
        created_at=now,
    )
    db.add(broadcast)
    db.add(
        AuditLog(
            admin_id=admin.id,
            action="broadcast.create",
            details={"segment": data.segment.value, "scheduled": not send_now},
            ip=request.client.host if request.client else None,
            created_at=now,
        )
    )
    await db.flush()

    if send_now:
        await bus.publish(bus.EVENT_BROADCAST_READY)

    return _out(broadcast)


@router.delete("/{broadcast_id}", status_code=status.HTTP_204_NO_CONTENT)
async def cancel_broadcast(
    broadcast_id: int, admin: CurrentAdmin, db: DbSession, request: Request
) -> None:
    broadcast = await db.get(Broadcast, broadcast_id)
    if broadcast is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Рассылка не найдена")
    if broadcast.status != BroadcastStatus.SCHEDULED:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Рассылка уже отправляется или завершена — отменить нельзя",
        )
    broadcast.status = BroadcastStatus.CANCELLED
    db.add(
        AuditLog(
            admin_id=admin.id,
            action="broadcast.cancel",
            target=str(broadcast_id),
            ip=request.client.host if request.client else None,
            created_at=datetime.now(UTC),
        )
    )


@router.websocket("/{broadcast_id}/ws")
async def broadcast_progress(
    websocket: WebSocket, broadcast_id: int, admin: CurrentAdmin
) -> None:
    """Живой прогресс отправки. Закрывается сам, когда статус — терминальный.

    `admin: CurrentAdmin` резолвится FastAPI ещё до входа в функцию — так же,
    как для обычного HTTP-эндпоинта: неавторизованный handshake будет
    отклонён раньше, чем мы примем соединение.
    """
    await websocket.accept()
    try:
        while True:
            async with SessionLocal() as db:
                broadcast = await db.get(Broadcast, broadcast_id)
                if broadcast is None:
                    await websocket.send_json({"error": "not_found"})
                    break
                await websocket.send_json(_out(broadcast).model_dump(mode="json"))
                if broadcast.status in (
                    BroadcastStatus.COMPLETED,
                    BroadcastStatus.CANCELLED,
                ):
                    break
            await asyncio.sleep(1)
    except WebSocketDisconnect:
        pass
    finally:
        try:
            await websocket.close()
        except RuntimeError:
            pass  # уже закрыт клиентом
