from datetime import datetime
from typing import TypedDict

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from config.constants import AMNEZIA_PROTOCOL
from config.enums import ServerHealthState, ServerLifecycleStatus
from database.models import Server, VPNProfile
from services.slots_cache import get_cached_peer_count

CAPACITY_CONSUMING_STATUSES = (
    "pending_create",
    "active",
    "pending_update",
    "update_failed",
    "deleting",
    "delete_failed",
    "create_cleanup_pending",
)


def _capacity_consuming_profiles_condition():
    """A profile consumes server capacity if an active peer is assigned (peer_id is not None),
    or if it is in an explicit capacity-consuming lifecycle state."""
    return or_(
        VPNProfile.peer_id.is_not(None),
        VPNProfile.provisioning_status.in_(CAPACITY_CONSUMING_STATUSES),
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
    lifecycle_status: str | ServerLifecycleStatus
    problem_started_at: datetime | None
    next_check_at: datetime | None
    consecutive_fails: int
    consecutive_successes: int
    recovery_notice_sent: bool
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
    "recovery_notice_sent",
    "last_alert_sent_state",
    "extra_data",
}


async def get_all_servers(session: AsyncSession) -> list[Server]:
    result = await session.execute(select(Server).order_by(Server.name))
    return result.scalars().all()


async def get_active_servers(session: AsyncSession) -> list[Server]:
    result = await session.execute(
        select(Server)
        .where(
            Server.is_active.is_(True),
            Server.lifecycle_status == ServerLifecycleStatus.ACTIVE,
        )
        .order_by(Server.name)
    )
    return result.scalars().all()


def capacity_consuming_wl_condition():
    """A White Internet subscription consumes server capacity if it is active,
    exhausted, pending, or in a transitional provisioning lifecycle state."""
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
            WhiteInternetProvisioningStatus.PENDING_UPDATE,
            WhiteInternetProvisioningStatus.PENDING_DELETE,
        ]),
    )


async def get_server_peer_counts(session: AsyncSession) -> dict[int, int]:
    # 1. VPNProfile counts (AWG)
    vpn_result = await session.execute(
        select(VPNProfile.server_id, func.count(VPNProfile.id))
        .where(_capacity_consuming_profiles_condition())
        .group_by(VPNProfile.server_id)
    )
    counts = {row[0]: row[1] for row in vpn_result.all()}

    # 2. WhiteInternetSubscription counts (Xray Origin)
    from database.models import WhiteInternetSubscription

    wl_result = await session.execute(
        select(WhiteInternetSubscription.origin_node_id, func.count(WhiteInternetSubscription.id))
        .where(
            WhiteInternetSubscription.origin_node_id.is_not(None),
            capacity_consuming_wl_condition(),
        )
        .group_by(WhiteInternetSubscription.origin_node_id)
    )
    for srv_id, count in wl_result.all():
        counts[srv_id] = counts.get(srv_id, 0) + count

    return counts


async def get_available_servers(session: AsyncSession) -> list[Server]:
    servers = await get_active_servers(session)
    if not servers:
        return []

    # Filter servers for AWG allocation: strictly require AMNEZIA_PROTOCOL and exclude Xray Origin nodes
    awg_servers = [
        s for s in servers
        if s.protocol == AMNEZIA_PROTOCOL and "xray_origin" not in (s.capabilities or [])
    ]
    if not awg_servers:
        return []

    db_counts = await get_server_peer_counts(session)
    available: list[Server] = []
    for server in awg_servers:
        cached_count = get_cached_peer_count(server.id)
        db_count = db_counts.get(server.id, 0)
        effective_count = max(cached_count, db_count) if cached_count is not None else db_count
        if effective_count < server.max_clients:
            available.append(server)
    return available


async def allocate_origin_server_atomic(session: AsyncSession) -> Server | None:
    """
    Atomically selects and locks an available Xray origin server with spare capacity.

    1. Select candidate server IDs that are active, online, and lifecycle ACTIVE.
    2. Sequentially acquire row locks (SELECT ... FOR UPDATE) on candidates.
    3. Re-verify is_active, health_state == ONLINE, lifecycle_status == ACTIVE,
       valid api_url / api_key, and 'xray_origin' in capabilities.
    4. Count capacity-consuming White Internet subscriptions on that origin node.
    5. Return the locked Server instance if active consumers < max_clients.
    6. Return None if no origin server has available capacity.
    """
    candidate_ids = (
        await session.scalars(
            select(Server.id)
            .where(
                Server.is_active.is_(True),
                Server.health_state == ServerHealthState.ONLINE,
                Server.lifecycle_status == ServerLifecycleStatus.ACTIVE,
                Server.api_url.is_not(None),
                Server.api_key.is_not(None),
            )
            .order_by(Server.id.asc())
        )
    ).all()

    for srv_id in candidate_ids:
        server = await session.scalar(
            select(Server)
            .where(Server.id == srv_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if server is None:
            continue
        if (
            not server.is_active
            or server.health_state != ServerHealthState.ONLINE
            or server.lifecycle_status != ServerLifecycleStatus.ACTIVE
            or not (server.api_url and server.api_url.strip())
            or not (server.api_key and server.api_key.strip())
            or "xray_origin" not in (server.capabilities or [])
        ):
            continue

        relays = (server.extra_data or {}).get("relays", [])
        if not relays or len(relays) == 0:
            continue

        from database.models import WhiteInternetSubscription

        active_count = (
            await session.scalar(
                select(func.count(WhiteInternetSubscription.id)).where(
                    WhiteInternetSubscription.origin_node_id == server.id,
                    capacity_consuming_wl_condition(),
                )
            )
            or 0
        )
        if active_count < server.max_clients:
            return server

    return None


async def get_server_by_id(
    session: AsyncSession,
    server_id: int,
    *,
    for_update: bool = False,
) -> Server | None:
    stmt = select(Server).where(Server.id == server_id)
    if for_update:
        stmt = stmt.with_for_update()
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


async def create_server(
    session: AsyncSession,
    name: str,
    api_url: str,
    api_key: str,
    country_flag: str = None,
    protocol: str = AMNEZIA_PROTOCOL,
    max_clients: int = 50,
    capabilities: list[str] | None = None,
    lifecycle_status: str | ServerLifecycleStatus = ServerLifecycleStatus.ACTIVE,
) -> Server:
    server = Server(
        name=name,
        api_url=api_url,
        api_key=api_key,
        country_flag=country_flag,
        protocol=protocol,
        max_clients=max_clients,
        capabilities=capabilities or [],
        lifecycle_status=str(lifecycle_status),
        is_active=(str(lifecycle_status) == ServerLifecycleStatus.ACTIVE),
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

    if "lifecycle_status" in kwargs:
        server.is_active = (str(server.lifecycle_status) == ServerLifecycleStatus.ACTIVE)

    await session.flush()
    await session.refresh(server)
    return server


async def update_server_health_snapshot(
    session: AsyncSession,
    server_id: int,
    *,
    expected_health_state: str,
    new_health_state: str,
    expected_consecutive_fails: int | None = None,
    expected_consecutive_successes: int | None = None,
    **health_kwargs: ServerUpdateFields,
) -> tuple[Server | None, bool]:
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
        if key in PROTECTED_SERVER_FIELDS or key not in HEALTH_UPDATE_FIELDS:
            continue
        if key == "extra_data" and isinstance(value, dict):
            existing_extra = dict(current.extra_data or {})
            existing_extra.update(value)
            setattr(current, key, existing_extra)
        elif hasattr(current, key):
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

    db_counts = await get_server_peer_counts(session)

    total_free = 0
    for server in active_servers:
        cached_count = get_cached_peer_count(server.id)
        db_count = db_counts.get(server.id, 0)
        effective_count = max(cached_count, db_count) if cached_count is not None else db_count
        total_free += max(0, server.max_clients - effective_count)
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


async def get_server_by_api_url(session: AsyncSession, api_url: str) -> Server | None:
    result = await session.execute(select(Server).where(Server.api_url == api_url))
    return result.scalar_one_or_none()


async def delete_profiles_by_server_id(session: AsyncSession, server_id: int) -> int:
    from sqlalchemy import delete as sql_delete

    result = await session.execute(sql_delete(VPNProfile).where(VPNProfile.server_id == server_id))
    await session.flush()
    return result.rowcount


async def update_server_xray_epoch_cas(
    session: AsyncSession,
    server_id: int,
    *,
    expected_boot_id: str | None,
    expected_starttime: int | None,
    new_epoch: str,
    new_boot_id: str | None,
    new_starttime: int | None,
) -> tuple[bool, Server | None]:
    """
    Atomically updates Server.xray_instance_epoch using compare-and-swap (CAS)
    against expected_boot_id and expected_starttime under row-level lock.
    Returns (True, server) if generation was accepted/updated, (False, None) if rejected as stale.
    """
    stmt = select(Server).where(Server.id == server_id).with_for_update()
    server = (await session.execute(stmt)).scalar_one_or_none()
    if server is None:
        return False, None

    # Primary registration: server has no recorded boot_id/epoch yet
    if server.xray_instance_boot_id is None:
        server.xray_instance_epoch = new_epoch
        server.xray_instance_boot_id = new_boot_id
        server.xray_instance_starttime = new_starttime
        await session.flush()
        return True, server

    # Verify expected state matches current database generation
    cur_boot = server.xray_instance_boot_id
    cur_st = server.xray_instance_starttime or 0

    if (cur_boot, cur_st) != (expected_boot_id, expected_starttime or 0):
        # Stale writer: database generation advanced while worker was gathering snapshot
        return False, None

    # Apply new generation:
    # 1. New boot generation (reboot)
    # 2. Same boot, newer starttime (process restart)
    # 3. Same exact generation (idempotent no-op)
    if new_boot_id != cur_boot:
        server.xray_instance_epoch = new_epoch
        server.xray_instance_boot_id = new_boot_id
        server.xray_instance_starttime = new_starttime
        await session.flush()
        return True, server

    new_st = new_starttime or 0
    if new_st > cur_st:
        server.xray_instance_epoch = new_epoch
        server.xray_instance_boot_id = new_boot_id
        server.xray_instance_starttime = new_starttime
        await session.flush()
        return True, server

    if new_st == cur_st:
        if server.xray_instance_epoch == new_epoch:
            return True, server
        # If starttime matches due to 10ms clock tick granularity during rapid restart,
        # accept the new epoch to prevent billing stall:
        server.xray_instance_epoch = new_epoch
        server.xray_instance_boot_id = new_boot_id
        server.xray_instance_starttime = new_starttime
        await session.flush()
        return True, server

    # Stale starttime within same boot
    return False, None
