"""Модели ответов Remnawave.

Описаны только поля, которые панель действительно использует.
`extra="ignore"` — новые поля Remnawave не ломают панель.
"""

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class _Base(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)


class UserStatus(StrEnum):
    ACTIVE = "ACTIVE"
    DISABLED = "DISABLED"
    LIMITED = "LIMITED"
    EXPIRED = "EXPIRED"


class TrafficLimitStrategy(StrEnum):
    NO_RESET = "NO_RESET"
    DAY = "DAY"
    WEEK = "WEEK"
    MONTH = "MONTH"
    MONTH_ROLLING = "MONTH_ROLLING"


# ── Пользователи ──────────────────────────────────────────────────────
class UserTraffic(_Base):
    used_traffic_bytes: int = Field(default=0, alias="usedTrafficBytes")
    lifetime_used_traffic_bytes: int = Field(default=0, alias="lifetimeUsedTrafficBytes")
    online_at: datetime | None = Field(default=None, alias="onlineAt")


class Squad(_Base):
    uuid: str
    name: str


class User(_Base):
    # API >= 2.9: первичный ключ — целочисленный id, uuid убран.
    id: int
    short_uuid: str | None = Field(default=None, alias="shortUuid")
    username: str
    status: UserStatus
    expire_at: datetime | None = Field(default=None, alias="expireAt")
    telegram_id: int | None = Field(default=None, alias="telegramId")
    email: str | None = None
    description: str | None = None
    tag: str | None = None
    hwid_device_limit: int | None = Field(default=None, alias="hwidDeviceLimit")
    traffic_limit_bytes: int = Field(default=0, alias="trafficLimitBytes")
    traffic_limit_strategy: TrafficLimitStrategy = Field(
        default=TrafficLimitStrategy.NO_RESET, alias="trafficLimitStrategy"
    )
    subscription_url: str | None = Field(default=None, alias="subscriptionUrl")
    created_at: datetime | None = Field(default=None, alias="createdAt")
    active_internal_squads: list[Squad] = Field(
        default_factory=list, alias="activeInternalSquads"
    )
    user_traffic: UserTraffic | None = Field(default=None, alias="userTraffic")

    @property
    def used_traffic_bytes(self) -> int:
        return self.user_traffic.used_traffic_bytes if self.user_traffic else 0

    @property
    def online_at(self) -> datetime | None:
        return self.user_traffic.online_at if self.user_traffic else None


class UserPage(_Base):
    users: list[User] = Field(default_factory=list)
    total: int = 0


# ── Ноды ──────────────────────────────────────────────────────────────
class NodeVersions(_Base):
    xray: str | None = None
    node: str | None = None


class Node(_Base):
    uuid: str
    name: str
    address: str
    port: int | None = None
    country_code: str | None = Field(default=None, alias="countryCode")
    is_connected: bool = Field(default=False, alias="isConnected")
    is_disabled: bool = Field(default=False, alias="isDisabled")
    is_connecting: bool = Field(default=False, alias="isConnecting")
    last_status_change: datetime | None = Field(default=None, alias="lastStatusChange")
    last_status_message: str | None = Field(default=None, alias="lastStatusMessage")
    traffic_used_bytes: int = Field(default=0, alias="trafficUsedBytes")
    traffic_limit_bytes: int = Field(default=0, alias="trafficLimitBytes")
    users_online: int = Field(default=0, alias="usersOnline")
    xray_uptime: float = Field(default=0, alias="xrayUptime")
    view_position: int = Field(default=0, alias="viewPosition")
    versions: NodeVersions | None = None

    @property
    def is_online(self) -> bool:
        return self.is_connected and not self.is_disabled


# ── Системная статистика ──────────────────────────────────────────────
class UsersStats(_Base):
    total_users: int = Field(default=0, alias="totalUsers")
    status_counts: dict[str, int] = Field(default_factory=dict, alias="statusCounts")


class OnlineStats(_Base):
    online_now: int = Field(default=0, alias="onlineNow")
    last_day: int = Field(default=0, alias="lastDay")
    last_week: int = Field(default=0, alias="lastWeek")
    never_online: int = Field(default=0, alias="neverOnline")


class NodesStats(_Base):
    total_online: int = Field(default=0, alias="totalOnline")
    total_bytes_lifetime: int = Field(default=0, alias="totalBytesLifetime")


class SystemStats(_Base):
    users: UsersStats = Field(default_factory=UsersStats)
    online_stats: OnlineStats = Field(default_factory=OnlineStats, alias="onlineStats")
    nodes: NodesStats = Field(default_factory=NodesStats)
    uptime: float = 0
    memory: dict[str, Any] = Field(default_factory=dict)


# ── Устройства (HWID) ─────────────────────────────────────────────────
class Device(_Base):
    hwid: str
    platform: str | None = None
    os_version: str | None = Field(default=None, alias="osVersion")
    device_model: str | None = Field(default=None, alias="deviceModel")
    user_agent: str | None = Field(default=None, alias="userAgent")
    created_at: datetime | None = Field(default=None, alias="createdAt")
    updated_at: datetime | None = Field(default=None, alias="updatedAt")
