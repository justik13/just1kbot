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

from config.constants import VLESS_PROTOCOL, WHITE_INTERNET_CAPABILITY, XRAY_PROTOCOL
from config.enums import AdminAuditAction, ServerHealthState, ServerLifecycleStatus
from database.models import Server
from database.repositories.servers_repo import (
    create_server,
    delete_server,
    derive_v2_capabilities,
    get_all_servers,
    get_origin_server_subscription_counts,
    get_server_by_api_url,
    get_server_by_id,
    get_server_role,
    merge_v2_extra_data,
    update_server,
)
from services.audit_service import AuditService
from services.workers.node_monitor import reset_server_monitor_state
from services.xray_node_client import XrayNodeClient
from utils.datetime_helpers import now_utc
from utils.security import is_safe_url, normalize_public_domain

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
    if WHITE_INTERNET_CAPABILITY in capabilities or role in {"eu_hub", "exit_node"}:
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


async def _fetch_ip_geo_info(ip: str) -> dict[str, str]:
    """Query geoip providers with lang=ru for localized country name, code and flag."""
    try:
        timeout = aiohttp.ClientTimeout(total=3.0)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            # 1. Try ipwho.is with lang=ru
            try:
                async with session.get(f"https://ipwho.is/{ip}?lang=ru") as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        if isinstance(data, dict) and data.get("success"):
                            country = str(data.get("country") or "").strip()
                            code = str(data.get("country_code") or "").strip().lower()
                            flag = str(data.get("flag", {}).get("emoji") or "").strip()
                            if not flag and code:
                                flag = country_code_to_flag(code)
                            return {"country": country, "code": code, "flag": flag}
            except Exception:
                pass

            # 2. Fallback to ip-api.com with lang=ru
            try:
                async with session.get(f"http://ip-api.com/json/{ip}?lang=ru") as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        if isinstance(data, dict) and data.get("status") == "success":
                            country = str(data.get("country") or "").strip()
                            code = str(data.get("countryCode") or "").strip().lower()
                            flag = country_code_to_flag(code) if code else ""
                            return {"country": country, "code": code, "flag": flag}
            except Exception:
                pass
    except Exception as exc:
        logger.debug("GeoIP lookup failed for %s: %s", ip, exc)
    return {}


async def auto_detect_node_geo(
    api_url: str,
    status_data: dict[str, Any] | None = None,
    role: str | None = None,
) -> tuple[str, str]:
    """Auto-detect localized country name and flag emoji from node metadata or IP geolocation."""
    detected_flag = ""
    if isinstance(status_data, dict):
        flag_val = str(status_data.get("country_flag") or "").strip()
        if flag_val:
            detected_flag = flag_val[:10]
        else:
            code_val = str(status_data.get("country_code") or "").strip()
            if code_val:
                detected_flag = country_code_to_flag(code_val)

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
            geo = await _fetch_ip_geo_info(resolved_ip)
            country = geo.get("country") or ""
            flag = geo.get("flag") or (country_code_to_flag(geo.get("code")) if geo.get("code") else "")
            if country or flag:
                final_flag = detected_flag or flag or (
                    "🇷🇺" if role == "ru_hub" else "🌐"
                )
                return country, final_flag

    final_flag = detected_flag or ("🇷🇺" if role == "ru_hub" else "🌐")
    return "", final_flag


def serialize_server_node(
    server: Server,
    *,
    wi_subscriptions_count: int = 0,
) -> dict[str, Any]:
    """Serialize Server row into admin-safe v2 node payload (never exposes api_key)."""
    extra = server.extra_data if isinstance(server.extra_data, dict) else {}
    caps = list(server.capabilities or []) if isinstance(server.capabilities, (list, tuple, set)) else []
    role = get_server_role(server) or str(extra.get("role") or "unconfigured")

    flag = server.country_flag
    if not flag:
        flag = "🇷🇺" if role == "ru_hub" else "🌐"

    raw_in_std = extra.get("in_standard")
    in_standard = True if raw_in_std is None else bool(raw_in_std)
    in_white_internet = bool(extra.get("in_white_internet", False))
    location_code = str(extra.get("location_code") or "").strip().lower()
    std_sni = str(extra.get("std_sni") or "").strip().lower()

    return {
        "id": server.id,
        "name": server.name,
        "country_flag": flag,
        "country_code": extra.get("country_code") or "",
        "api_url": server.api_url,
        "has_api_key": bool(server.api_key),
        "role": role,
        "is_active": bool(server.is_active),
        "health_state": server.health_state,
        "lifecycle_status": server.lifecycle_status,
        "capabilities": caps,
        "wi_subscriptions_count": wi_subscriptions_count,
        "in_standard": in_standard,
        "in_white_internet": in_white_internet,
        "location_code": location_code,
        "std_sni": std_sni,
        "domain": extra.get("domain") or "",
        "cdn_domain": extra.get("cdn_domain") or "",
        "cdn_domains": extra.get("cdn_domains") if isinstance(extra.get("cdn_domains"), dict) else {},
        "sub_path_prefix": extra.get("sub_path_prefix") or "",
        "secret_base_path": extra.get("secret_base_path") or "",
        "bridge_uuid": extra.get("bridge_uuid") or "",
        "modules": extra.get("modules") if isinstance(extra.get("modules"), dict) else {},
        "module_errors": extra.get("module_errors") if isinstance(extra.get("module_errors"), dict) else {},
        "links": extra.get("links") if isinstance(extra.get("links"), dict) else {},
        "sni_routes": extra.get("sni_routes") if isinstance(extra.get("sni_routes"), list) else [],
        "xhttp_locations": extra.get("xhttp_locations") if isinstance(extra.get("xhttp_locations"), list) else [],
        "inbounds": extra.get("inbounds") if isinstance(extra.get("inbounds"), list) else [],
        "traffic_limit": extra.get("traffic_limit") if isinstance(extra.get("traffic_limit"), dict) else None,
        "last_successful_check": (
            server.last_successful_check.isoformat() if server.last_successful_check else None
        ),
        "created_at": server.created_at.isoformat() if server.created_at else None,
    }


async def _probe_v2_node(
    api_url: str,
    api_key: str,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None, bool, str | None]:
    if not await is_safe_url(api_url):
        raise AdminNodeServiceError("Unsafe or forbidden node URL", status_code=400)

    async with XrayNodeClient(
        timeout=8.0,
        max_retries=1,
    ) as client:
        status_ok, status_data, status_err = await client.get_node_status(api_url, api_key)
        health_ok, _epoch, health_data = await client.check_health(api_url, api_key)

    return status_data, health_data, (status_ok or health_ok), status_err


async def list_nodes(session: AsyncSession) -> list[dict[str, Any]]:
    servers = await get_all_servers(session)
    wi_counts = await get_origin_server_subscription_counts(session)
    return [serialize_server_node(s, wi_subscriptions_count=wi_counts.get(s.id, 0)) for s in servers]


async def discover_and_add_node(
    session: AsyncSession,
    *,
    admin_id: int,
    api_url: str,
    api_key: str,
    name: str = "",
    country_flag: str | None = None,
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
    )
    role = extra.get("role")
    detected_name, detected_flag = await auto_detect_node_geo(clean_url, status_data, role)

    clean_name = (name or "").strip()[:255]
    resolved_name = clean_name or detected_name or _derive_default_name(clean_url, status_data, role)

    if country_flag and country_flag.strip():
        raw_flag = country_flag.strip()[:10]
        resolved_flag = country_code_to_flag(raw_flag) or raw_flag
    else:
        resolved_flag = detected_flag
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
    status_data, health_data, probe_ok, probe_error = await _probe_v2_node(
        server.api_url,
        server.api_key,
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
    if isinstance(status_data, dict):
        cc = str(status_data.get("country_code") or "").strip()
        if cc and (not server.country_flag or server.country_flag == "🌐"):
            cc_flag = country_code_to_flag(cc)
            if cc_flag:
                updates["country_flag"] = cc_flag
    if isinstance(health_data, dict) and health_data.get("status") == "ok":
        updates["health_state"] = ServerHealthState.ONLINE
        updates["last_successful_check"] = now_utc()
        updates["consecutive_fails"] = 0

    updated = await update_server(session, server, **updates)
    from services.topology_sync_service import sync_v2_topology_to_nodes
    await sync_v2_topology_to_nodes(session)
    logger.info("Admin %s refreshed v2 node %s (role=%s)", admin_id, server.id, role)
    return serialize_server_node(updated)


async def update_node_settings(
    session: AsyncSession,
    *,
    admin_id: int,
    server_id: int,
    name: str | None = None,
    country_flag: str | None = None,
    api_url: str | None = None,
    api_key: str | None = None,
    in_standard: bool | None = None,
    in_white_internet: bool | None = None,
    location_code: str | None = None,
    std_sni: str | None = None,
) -> dict[str, Any]:
    """Update admin-editable node metadata and credentials."""
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
    if api_url is not None:
        clean_url = _normalize_api_url(api_url)
        if not await is_safe_url(clean_url):
            raise AdminNodeServiceError("Unsafe or forbidden node URL", status_code=400)
        existing = await get_server_by_api_url(session, clean_url)
        if existing is not None and existing.id != server_id:
            raise AdminNodeServiceError("Node with this api_url already exists", status_code=409)
        updates["api_url"] = clean_url
    if api_key is not None:
        clean_key = api_key.strip()
        if not clean_key:
            raise AdminNodeServiceError("api_key cannot be empty", status_code=400)
        updates["api_key"] = clean_key

    if any(v is not None for v in (in_standard, in_white_internet, location_code, std_sni)):
        extra = dict(server.extra_data) if isinstance(server.extra_data, dict) else {}
        if in_standard is not None:
            extra["in_standard"] = bool(in_standard)
        if in_white_internet is not None:
            extra["in_white_internet"] = bool(in_white_internet)
        if location_code is not None:
            clean_loc = location_code.strip().lower()[:32]
            if clean_loc:
                extra["location_code"] = clean_loc
            else:
                extra.pop("location_code", None)
        if std_sni is not None:
            clean_sni = std_sni.strip().lower()
            if clean_sni:
                norm_sni = normalize_public_domain(clean_sni, require_fqdn=True)
                if not norm_sni:
                    raise AdminNodeServiceError("std_sni must be a valid public FQDN", status_code=400)
                extra["std_sni"] = norm_sni
            else:
                extra.pop("std_sni", None)
        updates["extra_data"] = extra

    if not updates:
        wi_counts = await get_origin_server_subscription_counts(session)
        return serialize_server_node(server, wi_subscriptions_count=wi_counts.get(server.id, 0))

    updated = await update_server(session, server, **updates)
    if any(v is not None for v in (in_standard, in_white_internet, location_code, std_sni)):
        from services.topology_sync_service import sync_v2_topology_to_nodes
        await sync_v2_topology_to_nodes(session)
    await AuditService.log_action(
        session,
        admin_id=admin_id,
        action=AdminAuditAction.EDIT_SERVER,
        target_type="server",
        target_id=updated.id,
        details={"updated_fields": sorted(updates.keys())},
    )
    wi_counts = await get_origin_server_subscription_counts(session)
    return serialize_server_node(updated, wi_subscriptions_count=wi_counts.get(updated.id, 0))


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
    reset_server_monitor_state(
        updated.id,
        new_state=updated.health_state or (ServerHealthState.ONLINE if target_active else ServerHealthState.MANUAL_DISABLED),
    )
    from services.topology_sync_service import sync_v2_topology_to_nodes
    await sync_v2_topology_to_nodes(session)
    await AuditService.log_action(
        session,
        admin_id=admin_id,
        action=AdminAuditAction.TOGGLE_SERVER,
        target_type="server",
        target_id=updated.id,
        details={"is_active": target_active},
    )
    wi_counts = await get_origin_server_subscription_counts(session)
    return serialize_server_node(updated, wi_subscriptions_count=wi_counts.get(updated.id, 0))


async def delete_node(
    session: AsyncSession,
    *,
    admin_id: int,
    server_id: int,
    force: bool = False,
) -> dict[str, Any]:
    """Delete a server record from DB (blocking if active WI subs exist unless force=True)."""
    server = await get_server_by_id(session, server_id, for_update=True)
    if server is None:
        raise AdminNodeServiceError("Node not found", status_code=404)

    wi_counts = await get_origin_server_subscription_counts(session)
    active_wi = wi_counts.get(server_id, 0)
    if active_wi > 0 and not force:
        raise AdminNodeServiceError(
            f"На сервере есть активные подписки ({active_wi} шт.). Перенесите их на другую ноду или подтвердите удаление.",
            status_code=409,
        )

    deleted_name = server.name
    await delete_server(session, server)
    from services.topology_sync_service import sync_v2_topology_to_nodes
    await sync_v2_topology_to_nodes(session)
    await AuditService.log_action(
        session,
        admin_id=admin_id,
        action=AdminAuditAction.SERVER_FORCE_PURGE if (force and active_wi > 0) else AdminAuditAction.DELETE_SERVER,
        target_type="server",
        target_id=server_id,
        details={"name": deleted_name, "force": force, "active_wi": active_wi},
    )
    return {"deleted_id": server_id, "name": deleted_name}
