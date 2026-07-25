from datetime import UTC, datetime
from typing import Literal

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel

from app.api.deps import CurrentAdmin, DbSession
from shared.db.models import AuditLog
from app.services import cache
from app.services.remnawave_provider import Remnawave
from shared.remnawave import RemnawaveError

router = APIRouter(prefix="/nodes", tags=["nodes"])

NODES_TTL = 15
CACHE_KEYS = ("nodes:list", "dashboard:overview")


class NodeOut(BaseModel):
    uuid: str
    name: str
    address: str
    country_code: str | None
    online: bool
    disabled: bool
    connecting: bool
    users_online: int
    traffic_used_bytes: int
    traffic_limit_bytes: int
    xray_uptime: float
    last_status_message: str | None
    last_status_change: datetime | None
    xray_version: str | None
    node_version: str | None


@router.get("", response_model=list[NodeOut])
async def list_nodes(admin: CurrentAdmin, client: Remnawave) -> list[NodeOut]:
    async def collect() -> list[dict]:
        nodes = await client.get_nodes()
        return [
            NodeOut(
                uuid=n.uuid,
                name=n.name,
                address=n.address,
                country_code=n.country_code,
                online=n.is_online,
                disabled=n.is_disabled,
                connecting=n.is_connecting,
                users_online=n.users_online,
                traffic_used_bytes=n.traffic_used_bytes,
                traffic_limit_bytes=n.traffic_limit_bytes,
                xray_uptime=n.xray_uptime,
                last_status_message=n.last_status_message,
                last_status_change=n.last_status_change,
                xray_version=n.versions.xray if n.versions else None,
                node_version=n.versions.node if n.versions else None,
            ).model_dump()
            for n in sorted(nodes, key=lambda n: n.view_position)
        ]

    data = await cache.cached_json("nodes:list", NODES_TTL, collect)
    return [NodeOut.model_validate(item) for item in data]


Action = Literal["enable", "disable", "restart"]


@router.post("/{uuid}/{action}", status_code=status.HTTP_204_NO_CONTENT)
async def node_action(
    uuid: str,
    action: Action,
    admin: CurrentAdmin,
    db: DbSession,
    client: Remnawave,
    request: Request,
) -> None:
    handlers = {
        "enable": client.enable_node,
        "disable": client.disable_node,
        "restart": client.restart_node,
    }
    try:
        await handlers[action](uuid)
    except RemnawaveError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc

    db.add(
        AuditLog(
            admin_id=admin.id,
            action=f"node.{action}",
            target=uuid,
            ip=request.client.host if request.client else None,
            created_at=datetime.now(UTC),
        )
    )
    # Состояние ноды изменилось — показывать закэшированное было бы обманом.
    await cache.invalidate(*CACHE_KEYS)


@router.post("/restart-all", status_code=status.HTTP_204_NO_CONTENT)
async def restart_all(
    admin: CurrentAdmin, db: DbSession, client: Remnawave, request: Request
) -> None:
    try:
        await client.restart_all_nodes()
    except RemnawaveError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc

    db.add(
        AuditLog(
            admin_id=admin.id,
            action="node.restart-all",
            ip=request.client.host if request.client else None,
            created_at=datetime.now(UTC),
        )
    )
    await cache.invalidate(*CACHE_KEYS)
