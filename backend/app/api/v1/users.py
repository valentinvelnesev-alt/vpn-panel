from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, HTTPException, Query, Request, status
from pydantic import BaseModel, Field

from app.api.deps import CurrentAdmin, DbSession
from shared.db.models import AuditLog
from app.services import cache
from app.services.remnawave_provider import Remnawave
from shared.remnawave import RemnawaveError, User

router = APIRouter(prefix="/users", tags=["users"])


class SquadOut(BaseModel):
    uuid: str
    name: str


class UserOut(BaseModel):
    uuid: str
    username: str
    status: str
    expire_at: datetime | None
    telegram_id: int | None
    email: str | None
    tag: str | None
    description: str | None
    hwid_device_limit: int | None
    used_traffic_bytes: int
    traffic_limit_bytes: int
    traffic_limit_strategy: str
    online_at: datetime | None
    created_at: datetime | None
    subscription_url: str | None
    squads: list[SquadOut]

    @classmethod
    def of(cls, user: User) -> "UserOut":
        return cls(
            uuid=user.uuid,
            username=user.username,
            status=user.status.value,
            expire_at=user.expire_at,
            telegram_id=user.telegram_id,
            email=user.email,
            tag=user.tag,
            description=user.description,
            hwid_device_limit=user.hwid_device_limit,
            used_traffic_bytes=user.used_traffic_bytes,
            traffic_limit_bytes=user.traffic_limit_bytes,
            traffic_limit_strategy=user.traffic_limit_strategy.value,
            online_at=user.online_at,
            created_at=user.created_at,
            subscription_url=user.subscription_url,
            squads=[
                SquadOut(uuid=s.uuid, name=s.name) for s in user.active_internal_squads
            ],
        )


class UserPageOut(BaseModel):
    users: list[UserOut]
    total: int


class DeviceOut(BaseModel):
    hwid: str
    platform: str | None
    device_model: str | None
    os_version: str | None
    updated_at: datetime | None


@router.get("", response_model=UserPageOut)
async def list_users(
    admin: CurrentAdmin,
    client: Remnawave,
    start: int = Query(0, ge=0),
    size: int = Query(50, ge=1, le=500),
    search: str = Query("", max_length=128),
) -> UserPageOut:
    """Список с постраничной выдачей; поиск идёт по точным эндпоинтам API.

    В Remnawave у /api/users нет параметра поиска, поэтому по строке запроса
    выбираем подходящий эндпоинт: телеграм-id, e-mail или имя пользователя.
    """
    query = search.strip()
    if not query:
        page = await client.get_users(start=start, size=size)
        return UserPageOut(
            users=[UserOut.of(u) for u in page.users], total=page.total
        )

    try:
        if query.isdigit():
            found = await client.get_users_by_telegram_id(int(query))
        elif "@" in query:
            found = await client.get_users_by_email(query)
        else:
            found = await client.get_users_by_username(query)
    except RemnawaveError as exc:
        # 404 на поиске — это «не найдено», а не сбой.
        if exc.status_code == 404:
            return UserPageOut(users=[], total=0)
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc

    return UserPageOut(users=[UserOut.of(u) for u in found], total=len(found))


class StatusCounts(BaseModel):
    total: int
    active: int
    expired: int
    limited: int
    disabled: int


@router.get("/status-counts", response_model=StatusCounts)
async def status_counts(admin: CurrentAdmin, client: Remnawave) -> StatusCounts:
    """Плашки-счётчики над таблицей. Берутся из /api/system/stats, а не
    пересчитываются по странице — иначе цифры зависели бы от пагинации."""
    try:
        stats = await client.get_stats()
    except RemnawaveError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc

    counts = {k.upper(): v for k, v in stats.users.status_counts.items()}
    return StatusCounts(
        total=stats.users.total_users,
        active=counts.get("ACTIVE", 0),
        expired=counts.get("EXPIRED", 0),
        limited=counts.get("LIMITED", 0),
        disabled=counts.get("DISABLED", 0),
    )


class UserUpdateIn(BaseModel):
    """Поля, которые панель разрешает править. Всё необязательное —
    отправляем в Remnawave только то, что реально пришло."""

    expire_at: datetime | None = None
    status: str | None = Field(default=None, pattern="^(ACTIVE|DISABLED)$")
    traffic_limit_bytes: int | None = Field(default=None, ge=0)
    traffic_limit_strategy: str | None = Field(
        default=None, pattern="^(NO_RESET|DAY|WEEK|MONTH|MONTH_ROLLING)$"
    )
    hwid_device_limit: int | None = Field(default=None, ge=0, le=1000)
    telegram_id: int | None = None
    email: str | None = Field(default=None, max_length=255)
    tag: str | None = Field(default=None, max_length=64)
    description: str | None = Field(default=None, max_length=1000)
    squad_uuids: list[str] | None = None


@router.patch("/{uuid}", response_model=UserOut)
async def update_user(
    uuid: str,
    data: UserUpdateIn,
    admin: CurrentAdmin,
    db: DbSession,
    client: Remnawave,
    request: Request,
) -> UserOut:
    fields: dict = {}
    if data.expire_at is not None:
        fields["expireAt"] = data.expire_at.isoformat()
    if data.status is not None:
        fields["status"] = data.status
    if data.traffic_limit_bytes is not None:
        fields["trafficLimitBytes"] = data.traffic_limit_bytes
    if data.traffic_limit_strategy is not None:
        fields["trafficLimitStrategy"] = data.traffic_limit_strategy
    if data.hwid_device_limit is not None:
        fields["hwidDeviceLimit"] = data.hwid_device_limit
    if data.telegram_id is not None:
        fields["telegramId"] = data.telegram_id
    if data.email is not None:
        fields["email"] = data.email or None
    if data.tag is not None:
        fields["tag"] = data.tag or None
    if data.description is not None:
        fields["description"] = data.description
    if data.squad_uuids is not None:
        fields["activeInternalSquads"] = data.squad_uuids

    if not fields:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY, "Нечего сохранять — данные не изменились"
        )

    try:
        updated = await client.update_user(uuid, **fields)
    except RemnawaveError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc

    db.add(
        AuditLog(
            admin_id=admin.id,
            action="user.update",
            target=uuid,
            details={"fields": sorted(fields)},
            ip=request.client.host if request.client else None,
            created_at=datetime.now(UTC),
        )
    )
    await cache.invalidate("dashboard:overview")
    return UserOut.of(updated)


@router.get("/{uuid}", response_model=UserOut)
async def get_user(uuid: str, admin: CurrentAdmin, client: Remnawave) -> UserOut:
    try:
        return UserOut.of(await client.get_user(uuid))
    except RemnawaveError as exc:
        code = status.HTTP_404_NOT_FOUND if exc.status_code == 404 else status.HTTP_502_BAD_GATEWAY
        raise HTTPException(code, str(exc)) from exc


class ExtendIn(BaseModel):
    days: int = Field(ge=1, le=3650)


@router.post("/{uuid}/extend", response_model=UserOut)
async def extend_user(
    uuid: str,
    data: ExtendIn,
    admin: CurrentAdmin,
    db: DbSession,
    client: Remnawave,
    request: Request,
) -> UserOut:
    """Ручная выдача дней. Отсчёт от текущей даты, если подписка уже истекла."""
    try:
        user = await client.get_user(uuid)
        now = datetime.now(UTC)
        base = user.expire_at if user.expire_at and user.expire_at > now else now
        updated = await client.extend_expiration(uuid, base + timedelta(days=data.days))
    except RemnawaveError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc

    db.add(
        AuditLog(
            admin_id=admin.id,
            action="user.extend",
            target=uuid,
            details={"days": data.days},
            ip=request.client.host if request.client else None,
            created_at=datetime.now(UTC),
        )
    )
    await cache.invalidate("dashboard:overview")
    return UserOut.of(updated)


class StatusIn(BaseModel):
    status: str = Field(pattern="^(ACTIVE|DISABLED)$")


@router.post("/{uuid}/status", response_model=UserOut)
async def set_status(
    uuid: str,
    data: StatusIn,
    admin: CurrentAdmin,
    db: DbSession,
    client: Remnawave,
    request: Request,
) -> UserOut:
    try:
        updated = await client.set_status(uuid, data.status)
    except RemnawaveError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc

    db.add(
        AuditLog(
            admin_id=admin.id,
            action="user.status",
            target=uuid,
            details={"status": data.status},
            ip=request.client.host if request.client else None,
            created_at=datetime.now(UTC),
        )
    )
    await cache.invalidate("dashboard:overview")
    return UserOut.of(updated)


# ── Устройства ────────────────────────────────────────────────────────
@router.get("/{uuid}/devices", response_model=list[DeviceOut])
async def list_devices(
    uuid: str, admin: CurrentAdmin, client: Remnawave
) -> list[DeviceOut]:
    try:
        devices = await client.get_devices(uuid)
    except RemnawaveError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc
    return [
        DeviceOut(
            hwid=d.hwid,
            platform=d.platform,
            device_model=d.device_model,
            os_version=d.os_version,
            updated_at=d.updated_at,
        )
        for d in devices
    ]


@router.delete("/{uuid}/devices/{hwid}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_device(
    uuid: str,
    hwid: str,
    admin: CurrentAdmin,
    db: DbSession,
    client: Remnawave,
    request: Request,
) -> None:
    try:
        await client.delete_device(uuid, hwid)
    except RemnawaveError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc
    db.add(
        AuditLog(
            admin_id=admin.id,
            action="user.device.delete",
            target=uuid,
            details={"hwid": hwid},
            ip=request.client.host if request.client else None,
            created_at=datetime.now(UTC),
        )
    )


@router.delete("/{uuid}/devices", status_code=status.HTTP_204_NO_CONTENT)
async def reset_devices(
    uuid: str,
    admin: CurrentAdmin,
    db: DbSession,
    client: Remnawave,
    request: Request,
) -> None:
    try:
        await client.delete_all_devices(uuid)
    except RemnawaveError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc
    db.add(
        AuditLog(
            admin_id=admin.id,
            action="user.device.reset",
            target=uuid,
            ip=request.client.host if request.client else None,
            created_at=datetime.now(UTC),
        )
    )
