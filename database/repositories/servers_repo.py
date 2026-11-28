from datetime import datetime
from typing import TypedDict

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified

from config.constants import (
    VLESS_PROTOCOL,
    WHITE_INTERNET_CAPABILITY,
    XRAY_PROTOCOL,
)
from config.enums import ServerHealthState, ServerLifecycleStatus
from database.models import Server

V2_NODE_ROLES = ("ru_hub", "eu_hub", "exit_node")


def get_server_role(server) -> str | None:
    """Extract normalized v2 node role from Server.role / extra_data / capabilities."""
    if not server:
        return None
    direct_role = getattr(server, "role", None)
    if isinstance(direct_role, str):
        direct_role = direct_role.strip().lower()
        if direct_role in V2_NODE_ROLES:
            return direct_role
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


def is_vless_sync_target_server(server) -> bool:
    """Whether a server hosts individual user VLESS client credentials.

    In v2 topology, end-user VLESS credentials are authenticated strictly on ru_hub
    (where TLS is terminated and SNI routes are matched).
    eu_hub and exit_node communicate strictly over shared inter-hub bridge UUIDs
    and never host individual user credentials.
    """
    if not server:
        return False
    return get_server_role(server) == "ru_hub" and is_vless_capable_server(server)


def is_white_internet_hub_server(server) -> bool:
    """Whether a server is a ru_hub gateway for White Internet contour."""
    if not server:
        return False
    if get_server_role(server) in ("eu_hub", "exit_node"):
        return False
    caps = getattr(server, "capabilities", None) or []
    if not isinstance(caps, (list, tuple, set)):
        caps = []
    raw_proto = getattr(server, "protocol", None)
    proto = raw_proto.strip().lower() if isinstance(raw_proto, str) else ""
    if WHITE_INTERNET_CAPABILITY in caps:
        return True
    return proto == XRAY_PROTOCOL and not is_vless_capable_server(server)


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
            if s == "vless":
                _add("xray_vless")

    modules = status_d.get("modules") or health_d.get("modules") or {}
    if isinstance(modules, dict):
        vless_mod = modules.get("vless_tls")
        if isinstance(vless_mod, dict) and vless_mod.get("enabled"):
            _add("vless")
            _add("xray_vless")
        xhttp_mod = modules.get("vless_xhttp")
        has_cdn_mod = any(
            str(mod_name).startswith("cdn_") and isinstance(mod_cfg, dict) and mod_cfg.get("enabled")
            for mod_name, mod_cfg in modules.items()
        )
        if isinstance(xhttp_mod, dict) and xhttp_mod.get("enabled") and has_cdn_mod:
            _add(WHITE_INTERNET_CAPABILITY)

    if role == "eu_hub":
        _add("bridge_hub")
    elif role == "exit_node":
        _add("exit_node")

    return caps


def merge_v2_extra_data(
    existing_extra: dict | None,
    status_data: dict | None = None,
    health_data: dict | None = None,
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
        "country_code",
        "domain",
        "cdn_domain",
        "cdn_domains",
        "sub_path_prefix",
        "secret_base_path",
        "bridge_uuid",
        "modules",
        "module_errors",
        "sni_routes",
        "xhttp_locations",
        "inbounds",
        "traffic_limit",
        "disk_usage",
        "last_apply",
        "ssl_cert",
    ):
        val = _pick(key)
        if val is not None:
            merged[key] = val
        elif key in ("last_apply", "ssl_cert") and (key in status_d or key in health_d):
            merged.pop(key, None)

    raw_loc_code = _pick("location_code")
    if isinstance(raw_loc_code, str) and raw_loc_code.strip():
        import re
        clean_loc = re.sub(r"[^a-z0-9_-]+", "", raw_loc_code.strip().lower())[:32]
        if clean_loc:
            merged["location_code"] = clean_loc

    cdn_domains = merged.get("cdn_domains")
    if isinstance(cdn_domains, dict):
        tf_dom = cdn_domains.get("cdn_turboflare")
        if tf_dom:
            merged["turboflare_domain"] = tf_dom
        else:
            merged.pop("turboflare_domain", None)

    node_links = _pick("links")
    if isinstance(node_links, dict):
        merged["links"] = node_links
        if "sni_routes" not in merged or not isinstance(merged.get("sni_routes"), list):
            std_sec = node_links.get("standard")
            if isinstance(std_sec, dict) and isinstance(std_sec.get("sni_routes"), list):
                merged["sni_routes"] = std_sec["sni_routes"]

    return merged


class ServerUpdateFields(TypedDict, total=False):
    name: str
    role: str
    country_flag: str | None
    api_url: str
    api_key: str
    protocol: str
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
    "capabilities",
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


def get_server_load_score(server: Server) -> float:
    """Return real-time hardware load percentage (0.0..100.0) based on CPU and RAM telemetry.

    Weighted: 70% CPU (TLS encryption and I/O overhead) + 30% RAM.
    """
    extra = server.extra_data if isinstance(server.extra_data, dict) else {}
    sys_load = extra.get("system_load")
    if isinstance(sys_load, dict):
        try:
            cpu = float(sys_load.get("cpu_percent") or 0.0)
            ram = float(sys_load.get("ram_percent") or 0.0)
            return round(max(0.0, min(100.0, cpu * 0.7 + ram * 0.3)), 1)
        except (ValueError, TypeError):
            pass
    return 0.0


def has_server_ingress_problem(server: Server) -> bool:
    """Return True when node_monitor recorded an active origin ingress /ping outage."""
    extra = server.extra_data if isinstance(server.extra_data, dict) else {}
    return bool(extra.get("ingress_problem") or extra.get("ingress_problem_started_at"))


def has_server_cdn_problem(server: Server) -> bool:
    """Return True when node_monitor recorded an active CDN /ping outage."""
    extra = server.extra_data if isinstance(server.extra_data, dict) else {}
    return bool(extra.get("cdn_problem") or extra.get("cdn_problem_started_at"))


def server_priority_sort_key(server: Server) -> tuple[bool, float, int]:
    """Sort key for servers: healthy ingress/CDN first, lower load score, server.id tie-breaker."""
    has_probe_issue = has_server_ingress_problem(server) or has_server_cdn_problem(server)
    return (has_probe_issue, get_server_load_score(server), int(server.id or 0))


_server_load_score = server_priority_sort_key


def _is_wi_hub_eligible(server: Server) -> bool:
    role = get_server_role(server)
    caps = server.capabilities or []
    return (role == "ru_hub" or role is None) and WHITE_INTERNET_CAPABILITY in caps


async def get_wi_capable_ru_hubs(session: AsyncSession) -> list[Server]:
    """Return all active online ru_hub servers capable of White Internet, ordered least-loaded first."""
    candidates = (
        await session.scalars(
            select(Server)
            .where(
                Server.protocol.in_([XRAY_PROTOCOL, VLESS_PROTOCOL]),
                Server.is_active.is_(True),
                Server.health_state == ServerHealthState.ONLINE,
                Server.lifecycle_status == ServerLifecycleStatus.ACTIVE,
                Server.api_url.is_not(None),
                Server.api_key.is_not(None),
            )
            .order_by(Server.id.asc())
        )
    ).all()
    eligible = [srv for srv in candidates if _is_wi_hub_eligible(srv)]
    eligible.sort(key=server_priority_sort_key)
    return eligible


async def get_wi_revocation_ru_hubs(session: AsyncSession) -> list[Server]:
    """Return all WI-capable ru_hub servers (ACTIVE or DECOMMISSIONING), including disabled/degraded hubs, for UUID revocation."""
    candidates = (
        await session.scalars(
            select(Server)
            .where(
                Server.protocol.in_([XRAY_PROTOCOL, VLESS_PROTOCOL]),
                Server.lifecycle_status.in_(
                    [ServerLifecycleStatus.ACTIVE, ServerLifecycleStatus.DECOMMISSIONING]
                ),
                Server.api_url.is_not(None),
                Server.api_key.is_not(None),
            )
            .order_by(Server.id.asc())
        )
    ).all()
    return [srv for srv in candidates if _is_wi_hub_eligible(srv)]


async def has_available_service_capacity(
    session: AsyncSession,
    service_type: str,
) -> bool:
    """Fast read-only check before order creation that at least one usable node exists."""
    if service_type == "standard":
        active_servers = await get_active_servers(session)
        return any(
            is_vless_sync_target_server(srv)
            and srv.health_state == ServerHealthState.ONLINE
            and bool(
                isinstance(getattr(srv, "extra_data", None), dict)
                and str(srv.extra_data.get("domain") or "").strip()
            )
            for srv in active_servers
        )

    if service_type == "white_internet":
        return bool(await get_wi_capable_ru_hubs(session))

    return True


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
    role: str = "ru_hub",
    country_flag: str = None,
    protocol: str = VLESS_PROTOCOL,
    capabilities: list[str] | None = None,
    lifecycle_status: str | ServerLifecycleStatus = ServerLifecycleStatus.ACTIVE,
    extra_data: dict | None = None,
    is_active: bool | None = None,
) -> Server:
    effective_is_active = (
        is_active
        if is_active is not None
        else (
            str(lifecycle_status)
            in (ServerLifecycleStatus.ACTIVE, ServerLifecycleStatus.DECOMMISSIONING)
        )
    )
    server = Server(
        name=name,
        role=role,
        api_url=api_url,
        api_key=api_key,
        country_flag=country_flag,
        protocol=protocol,
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

    if "lifecycle_status" in kwargs and "is_active" not in kwargs:
        server.is_active = str(server.lifecycle_status) in (
            ServerLifecycleStatus.ACTIVE,
            ServerLifecycleStatus.DECOMMISSIONING,
        )

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
            for ek, ev in value.items():
                if ev is None and ek in ("last_apply", "ssl_cert"):
                    existing_extra.pop(ek, None)
                else:
                    existing_extra[ek] = ev
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

