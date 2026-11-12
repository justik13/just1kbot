from datetime import datetime
from typing import TypedDict

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified

from config.constants import (
    DEFAULT_XRAY_ORIGIN_MAX_CLIENTS,
    VLESS_PROTOCOL,
    XRAY_ORIGIN_CAPABILITY,
    XRAY_PROTOCOL,
)
from config.enums import ServerHealthState, ServerLifecycleStatus
from database.models import Server

V2_NODE_ROLES = ("ru_hub", "eu_hub", "exit_node")


def get_server_role(server) -> str | None:
    """Extract normalized v2 node role from Server.extra_data/capabilities."""
    extra = getattr(server, "extra_data", None)
    if isinstance(extra, dict):
        role = str(extra.get("role") or "").strip().lower()
        if role in V2_NODE_ROLES:
            return role
    caps = getattr(server, "capabilities", None) or []
    if isinstance(caps, (list, tuple, set)):
        for role in V2_NODE_ROLES:
            if role in caps:
                return role
        if "bridge_hub" in caps:
            return "eu_hub"
    return None


def is_v2_node_server(server) -> bool:
    """Return True if server has an identified v2 modular role."""
    return get_server_role(server) in V2_NODE_ROLES


def is_vless_capable_server(server) -> bool:
    """Whether a server can serve direct VLESS TLS clients.

    True if 'vless' or 'xray_vless' capability is present, or protocol is 'vless'.
    """
    if not server:
        return False
    caps = getattr(server, "capabilities", None) or []
    if not isinstance(caps, (list, tuple, set)):
        caps = []
    raw_proto = getattr(server, "protocol", None)
    proto = raw_proto.strip().lower() if isinstance(raw_proto, str) else ""
    return "vless" in caps or "xray_vless" in caps or proto == VLESS_PROTOCOL


def is_white_internet_origin_server(server) -> bool:
    """Whether a server is an origin server for White Internet contour."""
    if not server:
        return False
    if get_server_role(server) in ("eu_hub", "exit_node"):
        return False
    caps = getattr(server, "capabilities", None) or []
    if not isinstance(caps, (list, tuple, set)):
        caps = []
    raw_proto = getattr(server, "protocol", None)
    proto = raw_proto.strip().lower() if isinstance(raw_proto, str) else ""
    if XRAY_ORIGIN_CAPABILITY in caps:
        return True
    return proto == XRAY_PROTOCOL and not is_vless_capable_server(server)


def is_pure_vless_server(server) -> bool:
    """Whether a server is a pure direct VLESS server (not White Internet Origin)."""
    return is_vless_capable_server(server) and not is_white_internet_origin_server(server)


def get_server_xray_api_url(server) -> str | None:
    """Return explicit Xray API URL for the server."""
    extra = getattr(server, "extra_data", None) if isinstance(getattr(server, "extra_data", None), dict) else {}
    return extra.get("xray_api_url") or getattr(server, "api_url", None)


def derive_v2_capabilities(
    status_data: dict | None = None,
    health_data: dict | None = None,
) -> list[str]:
    """Derive canonical Server.capabilities list from v2 node /status and /health responses."""
    caps: list[str] = []

    def _add(cap: str) -> None:
        if cap and cap not in caps:
            caps.append(cap)

    status_d = status_data if isinstance(status_data, dict) else {}
    health_d = health_data if isinstance(health_data, dict) else {}
    role = str(status_d.get("role") or health_d.get("role") or "").strip().lower()
    if role in V2_NODE_ROLES:
        _add(role)

    for raw_cap in (health_d.get("capabilities") or []):
        if isinstance(raw_cap, str) and raw_cap.strip():
            _add(raw_cap.strip().lower())

    for svc in (health_d.get("services") or []):
        if isinstance(svc, str) and svc.strip():
            s = svc.strip().lower()
            _add(s)
            if s == "white_internet":
                _add(XRAY_ORIGIN_CAPABILITY)
            elif s == "vless":
                _add("xray_vless")

    modules = status_d.get("modules") or health_d.get("modules") or {}
    if isinstance(modules, dict):
        vless_mod = modules.get("vless_tls")
        if isinstance(vless_mod, dict) and vless_mod.get("enabled"):
            _add("vless")
            _add("xray_vless")
        xhttp_mod = modules.get("vless_xhttp")
        if isinstance(xhttp_mod, dict) and xhttp_mod.get("enabled"):
            _add("white_internet")
            _add(XRAY_ORIGIN_CAPABILITY)

    if role == "eu_hub":
        _add("bridge_hub")
    elif role == "exit_node":
        _add("exit_node")

    return caps


def merge_v2_extra_data(
    existing_extra: dict | None,
    status_data: dict | None = None,
    health_data: dict | None = None,
    *,
    links_override: dict | None = None,
    tls_bootstrap: bool | None = None,
) -> dict:
    """Merge v2 auto-discovery metadata into Server.extra_data cleanly without losing monitor state."""
    merged = dict(existing_extra) if isinstance(existing_extra, dict) else {}
    status_d = status_data if isinstance(status_data, dict) else {}
    health_d = health_data if isinstance(health_data, dict) else {}

    def _pick(key: str, default=None):
        val = status_d.get(key)
        if val is None:
            val = health_d.get(key)
        return default if val is None else val

    role = str(_pick("role", merged.get("role") or "unconfigured")).strip().lower()
    merged["role"] = role
    for key in (
        "domain",
        "cdn_domain",
        "sub_path_prefix",
        "secret_base_path",
        "bridge_uuid",
        "modules",
        "module_errors",
        "xhttp_locations",
        "inbounds",
        "traffic_limit",
    ):
        val = _pick(key)
        if val is not None:
            merged[key] = val

    if links_override is not None:
        merged["links"] = links_override
    else:
        node_links = _pick("links")
        if isinstance(node_links, dict):
            merged["links"] = node_links

    if tls_bootstrap is not None:
        merged["tls_bootstrap"] = bool(tls_bootstrap)

    return merged


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


async def get_origin_server_subscription_counts(session: AsyncSession) -> dict[int, int]:
    # WhiteInternetSubscription counts (Xray Origin)
    from database.models import WhiteInternetSubscription

    wl_result = await session.execute(
        select(WhiteInternetSubscription.origin_node_id, func.count(WhiteInternetSubscription.id))
        .where(
            WhiteInternetSubscription.origin_node_id.is_not(None),
            capacity_consuming_wl_condition(),
        )
        .group_by(WhiteInternetSubscription.origin_node_id)
    )
    return {row[0]: row[1] for row in wl_result.all()}


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
                Server.protocol == XRAY_PROTOCOL,
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
            .where(Server.id == srv_id, Server.protocol == XRAY_PROTOCOL)
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
    if not isinstance(server_id, int) or server_id < 1 or server_id > 2_147_483_647:
        return None
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
    protocol: str = VLESS_PROTOCOL,
    max_clients: int = DEFAULT_XRAY_ORIGIN_MAX_CLIENTS,
    capabilities: list[str] | None = None,
    lifecycle_status: str | ServerLifecycleStatus = ServerLifecycleStatus.ACTIVE,
    extra_data: dict | None = None,
    is_active: bool | None = None,
) -> Server:
    effective_is_active = (
        is_active
        if is_active is not None
        else (str(lifecycle_status) == ServerLifecycleStatus.ACTIVE)
    )
    server = Server(
        name=name,
        api_url=api_url,
        api_key=api_key,
        country_flag=country_flag,
        protocol=protocol,
        max_clients=max_clients,
        capabilities=capabilities or [],
        lifecycle_status=str(lifecycle_status),
        is_active=effective_is_active,
        extra_data=extra_data if isinstance(extra_data, dict) else {},
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
            if key in ("extra_data", "capabilities"):
                flag_modified(server, key)

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
    if not current.is_active or current.health_state == ServerHealthState.MANUAL_DISABLED:
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
            flag_modified(current, "extra_data")
        elif hasattr(current, key):
            setattr(current, key, value)

    await session.flush()
    await session.refresh(current)
    return current, True


async def delete_server(session: AsyncSession, server: Server) -> None:
    await session.delete(server)
    await session.flush()


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


async def migrate_origin_subscriptions(
    session: AsyncSession,
    source_id: int,
    target_id: int,
    admin_id: int | None = None,
) -> int:
    """Migrate all active White Internet subscriptions from source origin server to target origin server.

    1. Acquires row locks on source and target servers ordered by id ASC to avoid deadlocks.
    2. Verifies target server is active, Xray protocol, and has sufficient capacity using capacity_consuming_wl_condition.
    3. Sets source server lifecycle_status to DECOMMISSIONING.
    4. For each subscription:
       - Creates a WhiteInternetOrphanCleanup entry for source node (desired_version N).
       - Updates subscription: origin_node_id = target_id, desired_version = N + 1, provisioning_status = PENDING_UPDATE.
    """
    if source_id == target_id:
        raise ValueError("Source and target servers must be different.")

    # 1. Lock servers in deterministic id ASC order to prevent deadlocks
    first_id, second_id = sorted([source_id, target_id])
    first_server = (await session.execute(
        select(Server).where(Server.id == first_id).with_for_update()
    )).scalar_one_or_none()
    second_server = (await session.execute(
        select(Server).where(Server.id == second_id).with_for_update()
    )).scalar_one_or_none()

    source_server = first_server if first_id == source_id else second_server
    target_server = first_server if first_id == target_id else second_server

    if not source_server or not target_server:
        raise ValueError("Source or target server not found.")

    if target_server.protocol != XRAY_PROTOCOL or not target_server.is_active:
        raise ValueError("Target server is not an active Xray node.")

    if XRAY_ORIGIN_CAPABILITY not in (target_server.capabilities or []):
        raise ValueError("Target server does not have the required 'xray_origin' capability.")

    relays = (target_server.extra_data or {}).get("relays", [])
    if not relays or len(relays) == 0:
        raise ValueError("Target server does not have relays configured.")

    if target_server.health_state not in (ServerHealthState.ONLINE, ServerHealthState.WAITING_CONFIRMATION):
        raise ValueError("Target server health state is not eligible for migration.")

    from config.enums import WhiteInternetProvisioningStatus
    from database.models import WhiteInternetOrphanCleanup, WhiteInternetSubscription

    # 2. Check capacity under lock
    target_active = (await session.scalar(
        select(func.count(WhiteInternetSubscription.id)).where(
            WhiteInternetSubscription.origin_node_id == target_id,
            capacity_consuming_wl_condition(),
        )
    )) or 0

    to_migrate_subs = list((await session.execute(
        select(WhiteInternetSubscription).where(
            WhiteInternetSubscription.origin_node_id == source_id,
            capacity_consuming_wl_condition(),
        ).with_for_update()
    )).scalars().all())

    count = len(to_migrate_subs)
    if count == 0:
        return 0

    if target_active + count > target_server.max_clients:
        free_slots = max(0, target_server.max_clients - target_active)
        raise ValueError(
            f"Недостаточно мест на целевом сервере: свободно {free_slots}, требуется {count}."
        )

    # 3. Mark source server as DECOMMISSIONING so it is no longer allocated to new clients
    source_server.lifecycle_status = ServerLifecycleStatus.DECOMMISSIONING

    # 4. Migrate subscriptions with version fencing and orphan outbox
    for sub in to_migrate_subs:
        orphan = WhiteInternetOrphanCleanup(
            server_id=source_id,
            client_uuid=sub.uuid,
            desired_version=sub.desired_version,
            status="pending",
        )
        session.add(orphan)

        sub.origin_node_id = target_id
        sub.desired_version += 1
        sub.provisioning_status = WhiteInternetProvisioningStatus.PENDING_UPDATE

    await session.flush()
    return count
