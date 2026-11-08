from datetime import datetime
from typing import TypedDict

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from config.constants import (
    VLESS_PROTOCOL,
    XRAY_ORIGIN_CAPABILITY,
    XRAY_PROTOCOL,
)
from config.enums import ServerLifecycleStatus
from database.models import Server

CAPACITY_CONSUMING_STATUSES = (
    "pending_create",
    "active",
    "pending_update",
    "update_failed",
    "deleting",
    "delete_failed",
    "create_cleanup_pending",
)


def is_vless_capable_server(server: Server | None) -> bool:
    """Whether a server can serve direct VLESS TLS clients."""
    if not server:
        return False
    caps = getattr(server, "capabilities", None) or []
    if not isinstance(caps, (list, tuple, set)):
        caps = []
    raw_proto = getattr(server, "protocol", None)
    proto = raw_proto.strip().lower() if isinstance(raw_proto, str) else ""
    return "vless" in caps or "xray_vless" in caps or proto == VLESS_PROTOCOL


def is_white_internet_origin_server(server: Server | None) -> bool:
    """Whether a server is an origin server for White Internet contour."""
    if not server:
        return False
    caps = getattr(server, "capabilities", None) or []
    if not isinstance(caps, (list, tuple, set)):
        caps = []
    raw_proto = getattr(server, "protocol", None)
    proto = raw_proto.strip().lower() if isinstance(raw_proto, str) else ""
    if XRAY_ORIGIN_CAPABILITY in caps:
        return True
    return proto == XRAY_PROTOCOL and not is_vless_capable_server(server)


def is_pure_vless_server(server: Server | None) -> bool:
    """Whether a server is a pure direct VLESS server (not White Internet Origin)."""
    return is_vless_capable_server(server) and not is_white_internet_origin_server(server)


def get_server_xray_api_url(server: Server | None) -> str | None:
    """Return explicit Xray API URL for the server."""
    if not server:
        return None
    extra = getattr(server, "extra_data", None) if isinstance(getattr(server, "extra_data", None), dict) else {}
    return getattr(server, "api_url", None) or extra.get("xray_api_url")


class ServerUpdateFields(TypedDict, total=False):
    name: str
    country_flag: str | None
    api_url: str
    api_key: str
    protocol: str
    max_clients: int
    is_active: bool
    disabled_reason: str | None
    disabled_at: datetime | None
    last_successful_check: datetime | None
    health_state: str
    lifecycle_status: str | ServerLifecycleStatus
    problem_started_at: datetime | None
    next_check_at: datetime | None
    consecutive_fails: int
    consecutive_successes: int
    last_alert_sent_state: str | None
    capabilities: list
    extra_data: dict
    xray_instance_epoch: str | None
    xray_instance_boot_id: str | None
    xray_instance_starttime: int | None


PROTECTED_SERVER_FIELDS = {"id", "created_at"}
HEALTH_UPDATE_FIELDS = {
    "is_active",
    "disabled_reason",
    "disabled_at",
    "last_successful_check",
    "health_state",
    "problem_started_at",
    "next_check_at",
    "consecutive_fails",
    "consecutive_successes",
    "last_alert_sent_state",
    "extra_data",
}


async def get_all_servers(session: AsyncSession) -> list[Server]:
    result = await session.execute(select(Server).order_by(Server.name))
    return list(result.scalars().all())


async def get_active_servers(session: AsyncSession) -> list[Server]:
    result = await session.execute(
        select(Server)
        .where(
            Server.is_active.is_(True),
            Server.lifecycle_status == ServerLifecycleStatus.ACTIVE,
        )
        .order_by(Server.name)
    )
    return list(result.scalars().all())


def capacity_consuming_wl_condition():
    """A White Internet subscription consumes server capacity if active or pending."""
    from config.enums import WhiteInternetProvisioningStatus, WhiteInternetStatus
    from database.models import WhiteInternetSubscription

    return or_(
        WhiteInternetSubscription.status.in_([
            WhiteInternetStatus.PENDING,
            WhiteInternetStatus.ACTIVE,
            WhiteInternetStatus.EXHAUSTED,
        ]),
        WhiteInternetSubscription.provisioning_status.in_([
            WhiteInternetProvisioningStatus.PENDING_CREATE,
            WhiteInternetProvisioningStatus.ACTIVE,
            WhiteInternetProvisioningStatus.PENDING_UPDATE,
            WhiteInternetProvisioningStatus.FAILED,
        ]),
    )


async def get_total_free_ips(session: AsyncSession) -> int:
    return 0


async def get_server_by_id(session: AsyncSession, server_id: int) -> Server | None:
    return await session.get(Server, server_id)


async def create_server(session: AsyncSession, **kwargs) -> Server:
    server = Server(**kwargs)
    session.add(server)
    await session.flush()
    await session.refresh(server)
    return server


async def update_server(session: AsyncSession, server: Server, **kwargs) -> Server:
    for key, value in kwargs.items():
        if key not in PROTECTED_SERVER_FIELDS:
            setattr(server, key, value)
    await session.flush()
    await session.refresh(server)
    return server
