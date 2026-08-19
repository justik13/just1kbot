from datetime import datetime
from typing import List, Optional, TypedDict

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import Server, VPNProfile
from services.slots_cache import get_cached_peer_count


def _capacity_consuming_profiles_condition():
    """A profile consumes server capacity if an active peer is assigned (peer_id is not None),
    or if it is in an active/reserving/deletion-in-progress state. Only creation attempts that
    failed prior to peer allocation (create_failed with peer_id=None) are excluded from capacity."""
    return or_(
        VPNProfile.peer_id.is_not(None),
        VPNProfile.provisioning_status.notin_(("create_failed",)),
    )


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
    problem_started_at: datetime | None
    next_check_at: datetime | None
    consecutive_fails: int
    consecutive_successes: int
    recovery_notice_sent: bool
    last_alert_sent_state: str | None


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
    "recovery_notice_sent",
    "last_alert_sent_state",
}


async def get_all_servers(session: AsyncSession) -> List[Server]:
    result = await session.execute(select(Server).order_by(Server.id))
    return result.scalars().all()


async def get_active_servers(session: AsyncSession) -> List[Server]:
    result = await session.execute(
        select(Server).where(Server.is_active.is_(True)).order_by(Server.name)
    )
    return result.scalars().all()


async def get_server_peer_counts(session: AsyncSession) -> dict[int, int]:
    result = await session.execute(
        select(VPNProfile.server_id, func.count(VPNProfile.id))
        .where(_capacity_consuming_profiles_condition())
        .group_by(VPNProfile.server_id)
    )
    return {row[0]: row[1] for row in result.all()}


async def get_available_servers(session: AsyncSession) -> List[Server]:
    servers = await get_active_servers(session)
    if not servers:
        return []

    result = await session.execute(
        select(VPNProfile.server_id, func.count(VPNProfile.id))
        .where(_capacity_consuming_profiles_condition())
        .group_by(VPNProfile.server_id)
    )
    db_counts = {row[0]: row[1] for row in result.all()}

    available: List[Server] = []
    for server in servers:
        real_count = get_cached_peer_count(server.id)
        if real_count is None:
            real_count = db_counts.get(server.id, 0)
        if real_count < server.max_clients:
            available.append(server)
    return available


async def get_server_by_id(session: AsyncSession, server_id: int) -> Optional[Server]:
    result = await session.execute(select(Server).where(Server.id == server_id))
    return result.scalar_one_or_none()


async def create_server(
    session: AsyncSession,
    name: str,
    api_url: str,
    api_key: str,
    country_flag: str = None,
    protocol: str = "amneziawg2",
    max_clients: int = 50,
) -> Server:
    server = Server(
        name=name,
        api_url=api_url,
        api_key=api_key,
        country_flag=country_flag,
        protocol=protocol,
        max_clients=max_clients,
    )
    session.add(server)
    await session.flush()
    await session.refresh(server)
    return server


async def update_server(
    session: AsyncSession,
    server: Server,
    **kwargs: ServerUpdateFields,
) -> Server:
    """Update a server's fields directly without side-effects."""
    for key, value in kwargs.items():
        if key in PROTECTED_SERVER_FIELDS:
            continue
        if hasattr(server, key):
            setattr(server, key, value)

    await session.flush()
    await session.refresh(server)
    return server


async def update_server_health_snapshot(
    session: AsyncSession,
    server_id: int,
    *,
    expected_health_state: str,
    new_health_state: str,
    expected_consecutive_fails: Optional[int] = None,
    expected_consecutive_successes: Optional[int] = None,
    **health_kwargs: ServerUpdateFields,
) -> tuple[Optional[Server], bool]:
    """
    Safely update server health state from the node monitor using CAS.

    - Admin action (is_active == False or MANUAL_DISABLED) always takes precedence.
    - If current health_state != expected_health_state, the update is rejected as stale.
    - If expected_consecutive_fails or expected_consecutive_successes is provided and DB mismatches, rejected as stale.
    - Returns (current_db_server, applied_successfully).
    """
    result = await session.execute(
        select(Server)
        .where(Server.id == server_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    current = result.scalar_one_or_none()
    if current is None:
        return None, False

    # 1. Admin disable takes absolute precedence: never overwrite an inactive/MANUAL_DISABLED server
    is_auto_disabled = (
        not current.is_active
        and current.health_state == "AUTO_DISABLED"
        and current.disabled_reason == "AUTO_UNAVAILABLE"
        and expected_health_state == "AUTO_DISABLED"
    )
    if (not current.is_active and not is_auto_disabled) or current.health_state == "MANUAL_DISABLED":
        return current, False

    # 2. Compare-and-Swap (CAS) guard: reject stale monitor snapshot
    if current.health_state != expected_health_state:
        return current, False

    if expected_consecutive_fails is not None and current.consecutive_fails != expected_consecutive_fails:
        return current, False

    if expected_consecutive_successes is not None and current.consecutive_successes != expected_consecutive_successes:
        return current, False

    # 3. Apply health updates
    kwargs = {"health_state": new_health_state, **health_kwargs}
    for key, value in kwargs.items():
        if key in PROTECTED_SERVER_FIELDS:
            continue
        if hasattr(current, key):
            setattr(current, key, value)

    await session.flush()
    await session.refresh(current)
    return current, True


async def delete_server(session: AsyncSession, server: Server) -> None:
    await session.delete(server)
    await session.flush()


async def get_total_free_ips(session: AsyncSession) -> int:
    active_servers = await get_active_servers(session)
    if not active_servers:
        return 0

    result = await session.execute(
        select(VPNProfile.server_id, func.count(VPNProfile.id))
        .where(_capacity_consuming_profiles_condition())
        .group_by(VPNProfile.server_id)
    )
    db_counts = {row[0]: row[1] for row in result.all()}

    total_free = 0
    for server in active_servers:
        real_count = get_cached_peer_count(server.id)
        if real_count is None:
            real_count = db_counts.get(server.id, 0)
        total_free += max(0, server.max_clients - real_count)
    return total_free


async def get_server_count(session: AsyncSession) -> int:
    result = await session.execute(select(func.count(Server.id)))
    return result.scalar_one()


async def get_servers_paginated(
    session: AsyncSession,
    page: int = 1,
    per_page: int = 10,
) -> list[Server]:
    offset = (page - 1) * per_page
    result = await session.execute(
        select(Server).order_by(Server.name).offset(offset).limit(per_page)
    )
    return result.scalars().all()


async def get_server_by_api_url(session: AsyncSession, api_url: str) -> Optional[Server]:
    result = await session.execute(select(Server).where(Server.api_url == api_url))
    return result.scalar_one_or_none()


async def delete_profiles_by_server_id(session: AsyncSession, server_id: int) -> int:
    from sqlalchemy import delete as sql_delete

    result = await session.execute(sql_delete(VPNProfile).where(VPNProfile.server_id == server_id))
    await session.flush()
    return result.rowcount
