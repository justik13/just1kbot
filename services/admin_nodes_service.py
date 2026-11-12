"""Admin domain service for v2 just1knode management and cascade link orchestration."""

from __future__ import annotations

import asyncio
import ipaddress
import logging
import socket
from typing import Any
from urllib.parse import urlparse

import aiohttp
from sqlalchemy.ext.asyncio import AsyncSession

from config.constants import VLESS_PROTOCOL, XRAY_PROTOCOL
from config.enums import AdminAuditAction, ServerHealthState, ServerLifecycleStatus
from database.models import Server
from database.repositories.servers_repo import (
    create_server,
    delete_server,
    derive_v2_capabilities,
    get_all_servers,
    get_server_by_api_url,
    get_server_by_id,
    get_server_role,
    merge_v2_extra_data,
    update_server,
)
from services.audit_service import AuditService
from services.xray_node_client import XrayNodeClient
from utils.datetime_helpers import now_utc
from utils.security import is_safe_url

logger = logging.getLogger(__name__)


class AdminNodeServiceError(Exception):
    """Structured domain error mapped cleanly to HTTP responses."""

    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.message = message
        self.status_code = status_code


def _normalize_api_url(raw_url: str) -> str:
    clean = (raw_url or "").strip().rstrip("/")
    parsed = urlparse(clean)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise AdminNodeServiceError("Invalid api_url format", status_code=400)
    return clean


def _derive_default_name(api_url: str, status_data: dict | None, role: str | None) -> str:
    if isinstance(status_data, dict):
        domain = str(status_data.get("domain") or "").strip()
        if domain:
            return domain
    host = urlparse(api_url).hostname or "node"
    prefix = role.upper() if role else "NODE"
    return f"{prefix}-{host}"[:120]


def _derive_protocol(capabilities: list[str], role: str | None) -> str:
    if "white_internet" in capabilities or "xray_origin" in capabilities or role in {"eu_hub", "exit_node"}:
        return XRAY_PROTOCOL
    return VLESS_PROTOCOL


def country_code_to_flag(code: str | None) -> str:
    """Convert 2-letter ISO country code into emoji flag."""
    if not code:
        return ""
    clean = str(code).strip().upper()
    if clean == "UK":
        clean = "GB"
    if len(clean) == 2 and clean.isalpha():
        return "".join(chr(127397 + ord(c)) for c in clean)
    return ""


async def _fetch_ip_country_code(ip: str) -> str | None:
    """Query geoip providers with short timeout for country code."""
    try:
        timeout = aiohttp.ClientTimeout(total=2.5)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            try:
                async with session.get(f"http://ip-api.com/json/{ip}?fields=status,countryCode") as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        if isinstance(data, dict) and data.get("status") == "success":
                            code = str(data.get("countryCode") or "").strip().lower()
                            if len(code) == 2:
                                return code
            except Exception:
                pass

            try:
                async with session.get(f"https://ipwho.is/{ip}") as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        if isinstance(data, dict) and data.get("success"):
                            code = str(data.get("country_code") or "").strip().lower()
                            if len(code) == 2:
                                return code
            except Exception:
                pass
    except Exception as exc:
        logger.debug("GeoIP lookup failed for %s: %s", ip, exc)
    return None


async def auto_detect_node_country_flag(
    api_url: str,
    status_data: dict[str, Any] | None = None,
    role: str | None = None,
) -> str:
    """Auto-detect flag emoji from node status metadata or IP geolocation."""
    if isinstance(status_data, dict):
        flag_val = str(status_data.get("country_flag") or "").strip()
        if flag_val:
            return flag_val[:10]
        code_val = str(status_data.get("country_code") or "").strip()
        flag_from_code = country_code_to_flag(code_val)
        if flag_from_code:
            return flag_from_code

    hostname = urlparse(api_url).hostname or ""
    if hostname:
        resolved_ip = ""
        try:
            ipaddress.ip_address(hostname)
            resolved_ip = hostname
        except ValueError:
            try:
                loop = asyncio.get_running_loop()
                addr_info = await loop.getaddrinfo(hostname, None, family=socket.AF_INET)
                if addr_info and addr_info[0] and addr_info[0][4]:
                    resolved_ip = addr_info[0][4][0]
            except Exception as exc:
                logger.debug("Could not resolve hostname %s to IP: %s", hostname, exc)

        if resolved_ip:
            code = await _fetch_ip_country_code(resolved_ip)
            if code:
                flag = country_code_to_flag(code)
                if flag:
                    return flag

    if role == "ru_hub":
        return "🇷🇺"

    return "🌐"


def serialize_server_node(server: Server) -> dict[str, Any]:
    """Serialize Server row into admin-safe v2 node payload (never exposes api_key)."""
    extra = server.extra_data if isinstance(server.extra_data, dict) else {}
    caps = list(server.capabilities or []) if isinstance(server.capabilities, (list, tuple, set)) else []
    role = get_server_role(server) or str(extra.get("role") or "unconfigured")

    flag = server.country_flag
    if not flag:
        flag = "🇷🇺" if role == "ru_hub" else "🌐"

    return {
        "id": server.id,
        "name": server.name,
        "country_flag": flag,
        "api_url": server.api_url,
        "has_api_key": bool(server.api_key),
        "protocol": server.protocol,
        "role": role,
        "is_active": bool(server.is_active),
        "health_state": server.health_state,
        "lifecycle_status": server.lifecycle_status,
        "capabilities": caps,
        "domain": extra.get("domain") or "",
        "cdn_domain": extra.get("cdn_domain") or "",
        "sub_path_prefix": extra.get("sub_path_prefix") or "",
        "secret_base_path": extra.get("secret_base_path") or "",
        "bridge_uuid": extra.get("bridge_uuid") or "",
        "modules": extra.get("modules") if isinstance(extra.get("modules"), dict) else {},
        "module_errors": extra.get("module_errors") if isinstance(extra.get("module_errors"), dict) else {},
        "links": extra.get("links") if isinstance(extra.get("links"), dict) else {},
        "xhttp_locations": extra.get("xhttp_locations") if isinstance(extra.get("xhttp_locations"), list) else [],
        "inbounds": extra.get("inbounds") if isinstance(extra.get("inbounds"), list) else [],
        "traffic_limit": extra.get("traffic_limit") if isinstance(extra.get("traffic_limit"), dict) else None,
        "tls_bootstrap": bool(extra.get("tls_bootstrap")),
        "last_successful_check": (
            server.last_successful_check.isoformat() if server.last_successful_check else None
        ),
        "created_at": server.created_at.isoformat() if server.created_at else None,
    }


async def _probe_v2_node(
    api_url: str,
    api_key: str,
    *,
    allow_insecure_tls: bool = False,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None, bool, str | None]:
    if not await is_safe_url(api_url):
        raise AdminNodeServiceError("Unsafe or forbidden node URL", status_code=400)

    async with XrayNodeClient(
        timeout=8.0,
        max_retries=1,
        allow_insecure_tls=allow_insecure_tls,
    ) as client:
        status_ok, status_data, status_err = await client.get_node_status(api_url, api_key)
        health_ok, _epoch, health_data = await client.check_health(api_url, api_key)

    return status_data, health_data, (status_ok or health_ok), status_err


async def list_nodes(session: AsyncSession) -> list[dict[str, Any]]:
    servers = await get_all_servers(session)
    return [serialize_server_node(s) for s in servers]


async def discover_and_add_node(
    session: AsyncSession,
    *,
    admin_id: int,
    api_url: str,
    api_key: str,
    name: str = "",
    country_flag: str | None = None,
    allow_bootstrap_tls: bool = True,
) -> dict[str, Any]:
    """Validate URL, probe bare v2 node for auto-discovery, persist Server, and write audit log."""
    clean_url = _normalize_api_url(api_url)
    clean_key = (api_key or "").strip()
    if not clean_key:
        raise AdminNodeServiceError("api_key is required", status_code=400)

    existing = await get_server_by_api_url(session, clean_url)
    if existing is not None:
        raise AdminNodeServiceError(
            f"Node with api_url already exists (id={existing.id})",
            status_code=409,
        )

    status_data, health_data, probe_ok, probe_error = await _probe_v2_node(
        clean_url,
        clean_key,
        allow_insecure_tls=allow_bootstrap_tls,
    )
    if not probe_ok or not isinstance(status_data, dict):
        raise AdminNodeServiceError(
            f"Node probe failed: {probe_error or 'unreachable'}",
            status_code=502,
        )

    caps = derive_v2_capabilities(status_data, health_data)
    extra = merge_v2_extra_data(
        {},
        status_data,
        health_data,
        tls_bootstrap=allow_bootstrap_tls,
    )
    role = extra.get("role")
    resolved_name = (name or "").strip()[:255] or _derive_default_name(clean_url, status_data, role)
    if country_flag and country_flag.strip():
        raw_flag = country_flag.strip()[:10]
        resolved_flag = country_code_to_flag(raw_flag) or raw_flag
    else:
        resolved_flag = await auto_detect_node_country_flag(clean_url, status_data, role)
    protocol = _derive_protocol(caps, role)

    server = await create_server(
        session=session,
        name=resolved_name,
        api_url=clean_url,
        api_key=clean_key,
        country_flag=resolved_flag,
        protocol=protocol,
        capabilities=caps,
        lifecycle_status=ServerLifecycleStatus.ACTIVE,
        extra_data=extra,
        is_active=False,
    )
    # Новая нода добавляется выключенной (как после ручного «Выключить»):
    # админ включает её сам, когда роль, TLS и связи готовы.
    server.health_state = ServerHealthState.MANUAL_DISABLED
    server.disabled_reason = "manual"
    server.disabled_at = now_utc()
    if isinstance(health_data, dict) and health_data.get("status") == "ok":
        server.last_successful_check = now_utc()
        if health_data.get("node_epoch"):
            server.xray_instance_epoch = str(health_data.get("node_epoch"))
        if health_data.get("boot_id"):
            server.xray_instance_boot_id = str(health_data.get("boot_id"))
        if health_data.get("starttime") is not None:
            server.xray_instance_starttime = int(health_data["starttime"])
    await session.flush()
    await session.refresh(server)

    await AuditService.log_action(
        session,
        admin_id=admin_id,
        action=AdminAuditAction.ADD_SERVER,
        target_type="server",
        target_id=server.id,
        details={
            "name": server.name,
            "role": role,
            "capabilities": caps,
            "tls_bootstrap": allow_bootstrap_tls,
        },
    )
    return {
        "node": serialize_server_node(server),
        "probe_ok": True,
    }


async def refresh_node(
    session: AsyncSession,
    *,
    admin_id: int,
    server_id: int,
) -> dict[str, Any]:
    """Re-query /v1/node/status and /v1/health and synchronize DB metadata."""
    server = await get_server_by_id(session, server_id, for_update=True)
    if server is None:
        raise AdminNodeServiceError("Node not found", status_code=404)

    extra_cur = server.extra_data if isinstance(server.extra_data, dict) else {}
    allow_insecure = bool(extra_cur.get("tls_bootstrap"))
    status_data, health_data, probe_ok, probe_error = await _probe_v2_node(
        server.api_url,
        server.api_key,
        allow_insecure_tls=allow_insecure,
    )
    if not probe_ok:
        raise AdminNodeServiceError(
            f"Node unreachable: {probe_error or 'probe failed'}",
            status_code=502,
        )

    caps = derive_v2_capabilities(status_data, health_data)
    extra = merge_v2_extra_data(extra_cur, status_data, health_data)
    role = extra.get("role")

    updates: dict[str, Any] = {
        "capabilities": caps or list(server.capabilities or []),
        "extra_data": extra,
        "protocol": _derive_protocol(caps, role),
    }
    if isinstance(health_data, dict) and health_data.get("status") == "ok":
        updates["health_state"] = ServerHealthState.ONLINE
        updates["last_successful_check"] = now_utc()
        updates["consecutive_fails"] = 0

    updated = await update_server(session, server, **updates)
    logger.info("Admin %s refreshed v2 node %s (role=%s)", admin_id, server.id, role)
    return serialize_server_node(updated)


async def update_node_settings(
    session: AsyncSession,
    *,
    admin_id: int,
    server_id: int,
    name: str | None = None,
    country_flag: str | None = None,
    allow_bootstrap_tls: bool | None = None,
) -> dict[str, Any]:
    """Update admin-editable node metadata (name, flag, bootstrap TLS flag)."""
    server = await get_server_by_id(session, server_id, for_update=True)
    if server is None:
        raise AdminNodeServiceError("Node not found", status_code=404)

    updates: dict[str, Any] = {}
    if name is not None:
        clean_name = name.strip()[:255]
        if not clean_name:
            raise AdminNodeServiceError("name cannot be empty", status_code=400)
        updates["name"] = clean_name
    if country_flag is not None:
        raw_flag = country_flag.strip()[:10]
        if raw_flag:
            updates["country_flag"] = country_code_to_flag(raw_flag) or raw_flag
        else:
            updates["country_flag"] = None
    if allow_bootstrap_tls is not None:
        extra = dict(server.extra_data or {})
        extra["tls_bootstrap"] = bool(allow_bootstrap_tls)
        updates["extra_data"] = extra

    if not updates:
        return serialize_server_node(server)

    updated = await update_server(session, server, **updates)
    await AuditService.log_action(
        session,
        admin_id=admin_id,
        action=AdminAuditAction.EDIT_SERVER,
        target_type="server",
        target_id=updated.id,
        details={"updated_fields": sorted(updates.keys())},
    )
    return serialize_server_node(updated)


async def toggle_node(
    session: AsyncSession,
    *,
    admin_id: int,
    server_id: int,
    is_active: bool | None = None,
) -> dict[str, Any]:
    """Enable or disable a node cleanly, respecting MANUAL_DISABLED health state."""
    server = await get_server_by_id(session, server_id, for_update=True)
    if server is None:
        raise AdminNodeServiceError("Node not found", status_code=404)

    target_active = (not bool(server.is_active)) if is_active is None else bool(is_active)
    if target_active:
        updates = {
            "is_active": True,
            "disabled_reason": None,
            "disabled_at": None,
            "health_state": ServerHealthState.ONLINE,
            "consecutive_fails": 0,
        }
    else:
        updates = {
            "is_active": False,
            "disabled_reason": "manual",
            "disabled_at": now_utc(),
            "health_state": ServerHealthState.MANUAL_DISABLED,
        }

    updated = await update_server(session, server, **updates)
    await AuditService.log_action(
        session,
        admin_id=admin_id,
        action=AdminAuditAction.TOGGLE_SERVER,
        target_type="server",
        target_id=updated.id,
        details={"is_active": target_active},
    )
    return serialize_server_node(updated)


async def delete_node(
    session: AsyncSession,
    *,
    admin_id: int,
    server_id: int,
) -> dict[str, Any]:
    """Delete a server record from DB and record audit log."""
    server = await get_server_by_id(session, server_id, for_update=True)
    if server is None:
        raise AdminNodeServiceError("Node not found", status_code=404)

    deleted_name = server.name
    await delete_server(session, server)
    await AuditService.log_action(
        session,
        admin_id=admin_id,
        action=AdminAuditAction.DELETE_SERVER,
        target_type="server",
        target_id=server_id,
        details={"name": deleted_name},
    )
    return {"deleted_id": server_id, "name": deleted_name}


async def update_node_links(
    session: AsyncSession,
    *,
    admin_id: int,
    server_id: int,
    links_payload: dict[str, Any],
) -> dict[str, Any]:
    """Push cascade topology links to node via PUT /v1/node/links and sync DB extra_data."""
    server = await get_server_by_id(session, server_id, for_update=True)
    if server is None:
        raise AdminNodeServiceError("Node not found", status_code=404)

    if not await is_safe_url(server.api_url):
        raise AdminNodeServiceError("Unsafe or forbidden node URL", status_code=400)

    extra_cur = server.extra_data if isinstance(server.extra_data, dict) else {}
    allow_insecure = bool(extra_cur.get("tls_bootstrap"))

    async with XrayNodeClient(
        timeout=10.0,
        max_retries=1,
        allow_insecure_tls=allow_insecure,
    ) as client:
        ok, resp_data, err = await client.update_node_links(
            server.api_url,
            server.api_key,
            links_payload,
        )

    if not ok or not isinstance(resp_data, dict):
        raise AdminNodeServiceError(
            f"Node rejected links update: {err or 'unknown error'}",
            status_code=502,
        )

    saved_links = resp_data.get("links") if isinstance(resp_data.get("links"), dict) else links_payload
    merged_extra = merge_v2_extra_data(extra_cur, links_override=saved_links)
    updated = await update_server(session, server, extra_data=merged_extra)

    await AuditService.log_action(
        session,
        admin_id=admin_id,
        action=AdminAuditAction.EDIT_SERVER,
        target_type="server",
        target_id=server.id,
        details={
            "operation": "update_node_links",
            "apply_queued": bool(resp_data.get("apply_queued")),
        },
    )
    return {
        "node": serialize_server_node(updated),
        "apply_queued": bool(resp_data.get("apply_queued")),
    }


async def push_node_certificate(
    session: AsyncSession,
    *,
    admin_id: int,
    server_id: int,
    domain: str,
    fullchain_pem: str,
    privkey_pem: str,
) -> dict[str, Any]:
    """Push TLS certificate bundle to node via POST /v1/system/certificate."""
    server = await get_server_by_id(session, server_id, for_update=True)
    if server is None:
        raise AdminNodeServiceError("Node not found", status_code=404)

    if not await is_safe_url(server.api_url):
        raise AdminNodeServiceError("Unsafe or forbidden node URL", status_code=400)

    clean_domain = (domain or "").strip().lower()
    if not clean_domain or not fullchain_pem.strip() or not privkey_pem.strip():
        raise AdminNodeServiceError("domain, fullchain_pem, and privkey_pem are required", status_code=400)

    extra_cur = server.extra_data if isinstance(server.extra_data, dict) else {}
    allow_insecure = bool(extra_cur.get("tls_bootstrap"))

    async with XrayNodeClient(
        timeout=15.0,
        max_retries=1,
        allow_insecure_tls=allow_insecure,
    ) as client:
        ok, err = await client.deploy_certificate(
            server.api_url,
            server.api_key,
            clean_domain,
            fullchain_pem,
            privkey_pem,
        )

    if not ok:
        raise AdminNodeServiceError(
            f"Certificate deployment failed: {err or 'unknown error'}",
            status_code=502,
        )

    extra_new = dict(extra_cur)
    extra_new["domain"] = clean_domain
    extra_new["tls_bootstrap"] = False
    updated = await update_server(session, server, extra_data=extra_new)

    await AuditService.log_action(
        session,
        admin_id=admin_id,
        action=AdminAuditAction.EDIT_SERVER,
        target_type="server",
        target_id=server.id,
        details={"operation": "deploy_certificate", "domain": clean_domain},
    )
    return {
        "node": serialize_server_node(updated),
        "domain": clean_domain,
    }
