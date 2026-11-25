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

from sqlalchemy.ext.asyncio import AsyncSession

from config.enums import ServerHealthState, ServerLifecycleStatus
from database.models import Server
from database.repositories.servers_repo import (
    get_all_servers,
    get_server_role,
    get_server_xray_api_url,
    is_v2_node_server,
)
from services.xray_node_client import XrayNodeClient, _sanitize_url

logger = logging.getLogger(__name__)

_CODE_CLEAN_RE = re.compile(r"[^a-z0-9_-]+")
_ROUTE_UUID_NAMESPACE = uuid.UUID("7f3b9a10-5c2d-4e8a-9f1b-2d4c6e8a0b1c")


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
    return (
        bool(srv.is_active)
        and srv.health_state in (ServerHealthState.ONLINE, ServerHealthState.WAITING_CONFIRMATION)
        and getattr(srv, "lifecycle_status", None) in (None, ServerLifecycleStatus.ACTIVE)
        and is_v2_node_server(srv)
    )


def build_v2_topology_links(servers: list[Server]) -> dict[int, dict[str, Any]]:
    """Build per-node links.json payloads for all active v2 ru_hub, eu_hub, and exit_node servers."""
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
        seen_bi_locs: set[str] = set()

        multi_eu = len(valid_eu) > 1
        for eu, eu_ip, eu_dom, eu_uid in valid_eu:
            for ex, _ex_ip, _ex_dom, _ex_uid, loc_code in valid_exit:
                ex_extra = ex.extra_data if isinstance(ex.extra_data, dict) else {}
                r_uid = _deterministic_route_uuid(eu.id, ex.id, eu_uid)
                bridge_id = f"{loc_code}-eu{eu.id}" if multi_eu else loc_code
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

        payloads[rh.id] = {
            "standard": {
                "bridges": std_bridges,
            },
            "bi": {
                "bridges": bi_bridges,
                "locations": bi_locations,
            },
        }

    return payloads


async def push_v2_topology_links(servers: list[Server]) -> dict[int, bool]:
    """Compute and push v2 links.json to all eligible v2 nodes without holding a DB session."""
    for srv in servers:
        if srv.is_active and srv.health_state == ServerHealthState.ONLINE and not _extract_ipv4(srv):
            ip_res = await _extract_ipv4_async(srv)
            if ip_res:
                extra = dict(srv.extra_data or {})
                extra["ip"] = ip_res
                srv.extra_data = extra
    payloads = build_v2_topology_links(servers)

    targets: list[tuple[int, str, str, dict[str, Any]]] = []
    for srv in servers:
        if not srv.is_active or srv.health_state != ServerHealthState.ONLINE:
            continue
        if srv.lifecycle_status not in (None, ServerLifecycleStatus.ACTIVE):
            continue
        if not is_v2_node_server(srv):
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


async def sync_v2_topology_to_nodes(session: AsyncSession) -> dict[int, bool]:
    """Compute and push v2 links.json to all active v2 nodes via PUT /v1/node/links."""
    servers = list(await get_all_servers(session))
    return await push_v2_topology_links(servers)


async def sync_v2_topology_to_nodes_nonblocking() -> dict[int, bool]:
    """Read servers in a short session and push v2 links.json outside any DB transaction."""
    from database.connection import session_scope

    async with session_scope() as session:
        servers = list(await get_all_servers(session))
    return await push_v2_topology_links(servers)
