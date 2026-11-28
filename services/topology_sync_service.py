"""Automatic v2 topology orchestrator (SSOT = just1kbot -> PUT /v1/node/links)."""

from __future__ import annotations

import asyncio
import ipaddress
import logging
import re
import socket
import uuid
from typing import Any
from urllib.parse import urlsplit

from config.enums import ServerHealthState, ServerLifecycleStatus
from database.models import Server
from database.repositories.servers_repo import (
    get_all_servers,
    get_server_role,
    get_server_xray_api_url,
    is_v2_node_server,
)
from database.repositories.system_settings_repo import (
    get_system_setting,
    set_system_setting,
)
from services.xray_node_client import XrayNodeClient, _sanitize_url

logger = logging.getLogger(__name__)

_CODE_CLEAN_RE = re.compile(r"[^a-z0-9_-]+")
_ROUTE_UUID_NAMESPACE = uuid.UUID("7f3b9a10-5c2d-4e8a-9f1b-2d4c6e8a0b1c")
WI_SECRET_BASE_PATH_SETTING_KEY = "wi_secret_base_path"
_SECRET_BASE_PATH_RE = re.compile(r"^/[A-Za-z0-9._/~-]+$")


def _normalize_wi_secret_base_path(value: Any) -> str | None:
    """Validate and normalize a shared White Internet XHTTP secret_base_path."""
    if not isinstance(value, str):
        return None
    clean = value.strip()
    if len(clean) > 1 and clean.endswith("/"):
        clean = clean.rstrip("/")
    if not clean.startswith("/") or len(clean) < 3:
        return None
    if not _SECRET_BASE_PATH_RE.fullmatch(clean):
        return None
    if ".." in clean or "//" in clean or "/./" in clean:
        return None
    if clean == "/sub" or clean.startswith("/sub/") or clean == "/std" or clean.startswith("/std/"):
        return None
    return clean


def _select_initial_wi_secret_base_path(servers: list[Server]) -> str:
    """Pick an existing ru_hub secret_base_path (preferring active CDN nodes) or generate one."""
    ru_candidates: list[tuple[tuple[int, int, int], str]] = []
    for srv in servers:
        if get_server_role(srv) != "ru_hub":
            continue
        extra = srv.extra_data if isinstance(srv.extra_data, dict) else {}
        norm = _normalize_wi_secret_base_path(extra.get("secret_base_path"))
        if not norm:
            continue
        priority = (
            0 if srv.is_active else 1,
            0 if bool(extra.get("cdn_turboflare") or extra.get("cdn_domain")) else 1,
            srv.id,
        )
        ru_candidates.append((priority, norm))
    if ru_candidates:
        ru_candidates.sort(key=lambda item: item[0])
        return ru_candidates[0][1]
    return f"/assets_{uuid.uuid4().hex[:16]}"
_topology_sync_lock: asyncio.Lock | None = None
_topology_sync_lock_loop: asyncio.AbstractEventLoop | None = None
_topology_sync_running: bool = False
_resync_requested: bool = False
_sync_req_seq: int = 0
_sync_completed_seq: int = 0
_last_topology_sync_results: dict[int, bool] = {}
_topology_retry_needed: bool = False


def is_topology_retry_pending() -> bool:
    """Return True if the last nonblocking topology sync had failed nodes or raised."""
    return _topology_retry_needed


def _get_topology_sync_lock() -> asyncio.Lock:
    global _topology_sync_lock, _topology_sync_lock_loop
    global _topology_sync_running, _resync_requested, _sync_req_seq, _sync_completed_seq, _last_topology_sync_results, _topology_retry_needed
    try:
        current_loop = asyncio.get_running_loop()
    except RuntimeError:
        current_loop = None
    if _topology_sync_lock is None or _topology_sync_lock_loop is not current_loop:
        _topology_sync_lock = asyncio.Lock()
        _topology_sync_lock_loop = current_loop
        _topology_sync_running = False
        _resync_requested = False
        _sync_req_seq = 0
        _sync_completed_seq = 0
        _last_topology_sync_results = {}
        _topology_retry_needed = False
    return _topology_sync_lock


def _format_bridge_code(loc_code: str, eu_id: int) -> str:
    suffix = f"-eu{eu_id}"
    max_loc_len = max(0, 32 - len(suffix))
    return f"{loc_code[:max_loc_len]}{suffix}"[:32]


def _clean_code(raw: Any, fallback: str) -> str:
    val = _CODE_CLEAN_RE.sub("", str(raw or "").strip().lower())
    return val[:32] if val else fallback


def _extract_ipv4(server: Server) -> str | None:
    extra = server.extra_data if isinstance(server.extra_data, dict) else {}
    candidates = [extra.get("ip")]
    if server.api_url:
        try:
            candidates.append(urlsplit(server.api_url).hostname)
        except Exception:
            pass
    for raw in candidates:
        cand = str(raw or "").strip()
        if not cand:
            continue
        try:
            ip_obj = ipaddress.ip_address(cand)
            if ip_obj.version == 4:
                return cand
        except ValueError:
            pass
    return None


async def _extract_ipv4_async(server: Server) -> str | None:
    ip = _extract_ipv4(server)
    if ip:
        return ip
    if server.api_url:
        try:
            cand = str(urlsplit(server.api_url).hostname or "").strip()
            if cand:
                resolved = await asyncio.to_thread(socket.gethostbyname, cand)
                if resolved:
                    return resolved
        except Exception as exc:
            logger.debug("DNS resolution failed for server %s: %s", getattr(server, "id", "?"), exc)
    return None


def _extract_domain(server: Server) -> str | None:
    extra = server.extra_data if isinstance(server.extra_data, dict) else {}
    dom = str(extra.get("domain") or "").strip().lower().rstrip(".")
    if dom and "." in dom:
        return dom
    if server.api_url:
        try:
            host = str(urlsplit(server.api_url).hostname or "").strip().lower().rstrip(".")
            if host and "." in host:
                try:
                    ipaddress.ip_address(host)
                except ValueError:
                    return host
        except Exception:
            pass
    return None


def _extract_bridge_uuid(server: Server) -> str | None:
    extra = server.extra_data if isinstance(server.extra_data, dict) else {}
    raw = str(extra.get("bridge_uuid") or "").strip()
    if not raw:
        return None
    try:
        return str(uuid.UUID(raw)).lower()
    except ValueError:
        return None


def _deterministic_route_uuid(eu_hub_id: int, exit_node_id: int, eu_bridge_uuid: str) -> str:
    return str(
        uuid.uuid5(_ROUTE_UUID_NAMESPACE, f"{eu_hub_id}:{exit_node_id}:{eu_bridge_uuid}")
    ).lower()


def _is_node_eligible(srv: Server) -> bool:
    if not bool(srv.is_active):
        return False
    if srv.health_state not in (ServerHealthState.ONLINE, ServerHealthState.WAITING_CONFIRMATION):
        return False
    if not is_v2_node_server(srv):
        return False
    role = get_server_role(srv)
    allowed_lifecycle = (
        (None, ServerLifecycleStatus.ACTIVE, ServerLifecycleStatus.DECOMMISSIONING)
        if role == "ru_hub"
        else (None, ServerLifecycleStatus.ACTIVE)
    )
    return getattr(srv, "lifecycle_status", None) in allowed_lifecycle


def build_v2_topology_links(
    servers: list[Server],
    *,
    wi_secret_base_path: str | None = None,
) -> dict[int, dict[str, Any]]:
    """Build per-node links.json payloads for all active v2 ru_hub, eu_hub, and exit_node servers."""
    resolved_wi_secret = _normalize_wi_secret_base_path(wi_secret_base_path)
    active_ru: list[Server] = []
    active_eu: list[Server] = []
    active_exit: list[Server] = []

    for srv in sorted(servers, key=lambda s: s.id):
        if not _is_node_eligible(srv):
            continue
        role = get_server_role(srv)
        if role == "ru_hub":
            active_ru.append(srv)
        elif role == "eu_hub":
            active_eu.append(srv)
        elif role == "exit_node":
            active_exit.append(srv)

    active_ru.sort(key=lambda s: int(s.id or 0))
    active_eu.sort(key=lambda s: int(s.id or 0))

    ru_ips = [ip for ip in (_extract_ipv4(s) for s in active_ru) if ip]
    eu_ips = [ip for ip in (_extract_ipv4(s) for s in active_eu) if ip]

    valid_eu: list[tuple[Server, str, str, str]] = []
    for eu in active_eu:
        ip = _extract_ipv4(eu)
        dom = _extract_domain(eu)
        b_uid = _extract_bridge_uuid(eu)
        if ip and dom and b_uid:
            valid_eu.append((eu, ip, dom, b_uid))

    valid_exit: list[tuple[Server, str, str, str, str]] = []
    for ex in active_exit:
        ip = _extract_ipv4(ex)
        dom = _extract_domain(ex)
        b_uid = _extract_bridge_uuid(ex)
        ex_extra = ex.extra_data if isinstance(ex.extra_data, dict) else {}
        loc_code = _clean_code(ex_extra.get("location_code"), f"exit{ex.id}")
        if ip and dom and b_uid:
            valid_exit.append((ex, ip, dom, b_uid, loc_code))

    payloads: dict[int, dict[str, Any]] = {}

    for ex, _ip, _dom, _b_uid, _loc_code in valid_exit:
        incoming_hubs = [
            {
                "code": f"eu{eu.id}",
                "uuid": eu_uid,
            }
            for eu, _eu_ip, _eu_dom, eu_uid in valid_eu
        ]
        payloads[ex.id] = {
            "allowed_hub_ips": list(dict.fromkeys(eu_ips)),
            "incoming_hubs": incoming_hubs,
        }

    incoming_ru = []
    for rh in active_ru:
        rh_uid = _extract_bridge_uuid(rh)
        if rh_uid:
            incoming_ru.append({"code": f"ru{rh.id}", "uuid": rh_uid})

    for eu, _eu_ip, _eu_dom, eu_uid in valid_eu:
        exit_specs = []
        routes_specs = []
        for idx, (ex, ex_ip, ex_dom, ex_uid, loc_code) in enumerate(valid_exit):
            exit_specs.append({
                "id": str(ex.id),
                "code": loc_code,
                "ip": ex_ip,
                "port": 443,
                "sni": ex_dom,
                "uuid": ex_uid,
                "is_default": idx == 0,
            })
            r_uid = _deterministic_route_uuid(eu.id, ex.id, eu_uid)
            routes_specs.append({
                "code": loc_code,
                "exit_id": str(ex.id),
                "ingress_uuid": r_uid,
                "route_user": f"route-{loc_code}@just1k",
                "exit_code": loc_code,
            })
        payloads[eu.id] = {
            "allowed_ru_ips": list(dict.fromkeys(ru_ips)),
            "incoming_ru_hubs": incoming_ru,
            "exit_nodes": exit_specs,
            "routes": routes_specs,
        }

    for rh in active_ru:
        std_bridges: list[dict[str, Any]] = []
        bi_bridges: list[dict[str, Any]] = []
        bi_locations: list[dict[str, str]] = []
        probe_bridges: list[dict[str, Any]] = []
        seen_bi_locs: set[str] = set()

        rh_extra = rh.extra_data if isinstance(rh.extra_data, dict) else {}
        raw_blocked = rh_extra.get("blocked_eu_ids") or []
        blocked_eu_ids: set[int] = set()
        for raw_bid in raw_blocked:
            try:
                blocked_eu_ids.add(int(raw_bid))
            except (TypeError, ValueError):
                pass

        available_eu = [item for item in valid_eu if item[0].id not in blocked_eu_ids]
        if available_eu:
            routed_eu = available_eu
            excluded_eu = [item for item in valid_eu if item[0].id in blocked_eu_ids]
        else:
            # Fail-safe: never leave ru_hub with zero bridges when all eu_hubs are flagged
            routed_eu = valid_eu
            excluded_eu = []

        multi_eu = len(routed_eu) > 1
        for eu, eu_ip, eu_dom, eu_uid in routed_eu:
            for ex, _ex_ip, _ex_dom, _ex_uid, loc_code in valid_exit:
                ex_extra = ex.extra_data if isinstance(ex.extra_data, dict) else {}
                r_uid = _deterministic_route_uuid(eu.id, ex.id, eu_uid)
                bridge_id = _format_bridge_code(loc_code, eu.id) if multi_eu else loc_code[:32]
                if ex_extra.get("in_standard") is not False:
                    std_bridges.append({
                        "code": bridge_id,
                        "location_code": loc_code,
                        "ip": eu_ip,
                        "port": 443,
                        "sni": eu_dom,
                        "uuid": r_uid,
                        "is_default": len(std_bridges) == 0,
                    })
                if bool(ex_extra.get("in_white_internet", False)):
                    bi_bridges.append({
                        "code": bridge_id,
                        "location_code": loc_code,
                        "ip": eu_ip,
                        "port": 443,
                        "sni": eu_dom,
                        "uuid": r_uid,
                        "is_default": len(bi_bridges) == 0,
                    })
                    if loc_code not in seen_bi_locs:
                        seen_bi_locs.add(loc_code)
                        bi_locations.append({"code": loc_code, "bridge_code": loc_code})

        if excluded_eu and valid_exit:
            probe_exits = valid_exit[:2]
            for eu, eu_ip, eu_dom, eu_uid in excluded_eu:
                for p_ex, _pip, _pdom, _puid, p_loc in probe_exits:
                    r_uid = _deterministic_route_uuid(eu.id, p_ex.id, eu_uid)
                    probe_bridges.append({
                        "code": _format_bridge_code(p_loc, eu.id),
                        "location_code": p_loc,
                        "ip": eu_ip,
                        "port": 443,
                        "sni": eu_dom,
                        "uuid": r_uid,
                        "is_default": False,
                    })

        bi_section: dict[str, Any] = {
            "bridges": bi_bridges,
            "locations": bi_locations,
        }
        if resolved_wi_secret:
            bi_section["secret_base_path"] = resolved_wi_secret

        rh_payload: dict[str, Any] = {
            "standard": {
                "bridges": std_bridges,
            },
            "bi": bi_section,
        }
        if probe_bridges:
            rh_payload["probe_bridges"] = probe_bridges
        payloads[rh.id] = rh_payload

    return payloads


async def push_v2_topology_links(
    servers: list[Server],
    *,
    wi_secret_base_path: str | None = None,
) -> dict[int, bool]:
    """Compute and push v2 links.json to all eligible v2 nodes without holding a DB session."""
    for srv in servers:
        if _is_node_eligible(srv) and not _extract_ipv4(srv):
            ip_res = await _extract_ipv4_async(srv)
            if ip_res:
                extra = dict(srv.extra_data or {})
                extra["ip"] = ip_res
                srv.extra_data = extra
    payloads = build_v2_topology_links(servers, wi_secret_base_path=wi_secret_base_path)

    targets: list[tuple[int, str, str, dict[str, Any]]] = []
    for srv in servers:
        if not srv.is_active or srv.health_state != ServerHealthState.ONLINE:
            continue
        if not is_v2_node_server(srv):
            continue
        role = get_server_role(srv)
        allowed_lifecycle = (
            (None, ServerLifecycleStatus.ACTIVE, ServerLifecycleStatus.DECOMMISSIONING)
            if role == "ru_hub"
            else (None, ServerLifecycleStatus.ACTIVE)
        )
        if getattr(srv, "lifecycle_status", None) not in allowed_lifecycle:
            continue
        payload = payloads.get(srv.id)
        if not payload:
            logger.warning(
                "Skipping topology push for active node %s (%s): missing required topology metadata",
                srv.id,
                srv.name,
            )
            continue
        api_url = get_server_xray_api_url(srv)
        if api_url and srv.api_key:
            targets.append((srv.id, api_url, srv.api_key, payload))

    if not targets:
        return {}

    async def _push_one(srv_id: int, api_url: str, api_key: str, payload: dict[str, Any]) -> tuple[int, bool]:
        try:
            async with XrayNodeClient(timeout=10.0) as client:
                ok, _data, err = await client.update_node_links(api_url, api_key, payload)
                if not ok:
                    logger.warning("Topology links sync failed for node %s (%s): %s", srv_id, _sanitize_url(api_url), err)
                return srv_id, ok
        except Exception as exc:
            logger.warning("Topology links sync error for node %s (%s): %s", srv_id, _sanitize_url(api_url), exc)
            return srv_id, False

    results = await asyncio.gather(*(_push_one(sid, url, key, pl) for sid, url, key, pl in targets))
    return dict(results)


async def sync_v2_topology_to_nodes_nonblocking() -> dict[int, bool]:
    """Read servers in a short session and push v2 links.json outside any DB transaction."""
    global _topology_sync_running, _resync_requested, _sync_req_seq, _sync_completed_seq, _last_topology_sync_results, _topology_retry_needed
    from database.connection import session_scope

    lock = _get_topology_sync_lock()
    _sync_req_seq += 1
    my_seq = _sync_req_seq
    if _topology_sync_running or lock.locked():
        _resync_requested = True

    async with lock:
        if _sync_completed_seq >= my_seq and not _resync_requested:
            return dict(_last_topology_sync_results)
        _topology_sync_running = True
        try:
            while True:
                _resync_requested = False
                target_seq = _sync_req_seq
                async with session_scope() as session:
                    servers = list(await get_all_servers(session))
                    wi_secret_base_path = _normalize_wi_secret_base_path(
                        await get_system_setting(session, WI_SECRET_BASE_PATH_SETTING_KEY)
                    )
                    if wi_secret_base_path is None:
                        wi_secret_base_path = _select_initial_wi_secret_base_path(servers)
                        await set_system_setting(
                            session,
                            WI_SECRET_BASE_PATH_SETTING_KEY,
                            wi_secret_base_path,
                        )
                results = await push_v2_topology_links(
                    servers,
                    wi_secret_base_path=wi_secret_base_path,
                )
                _last_topology_sync_results = dict(results)
                _topology_retry_needed = any(not ok for ok in results.values())
                _sync_completed_seq = target_seq
                if not _resync_requested:
                    return results
        except Exception:
            _topology_retry_needed = True
            raise
        finally:
            _topology_sync_running = False
