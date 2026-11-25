"""Admin domain service for v2 just1knode management and cascade link orchestration."""

from __future__ import annotations

import asyncio
import ipaddress
import logging
import re
import socket
from typing import Any
from urllib.parse import urlparse

import aiohttp
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from config.constants import VLESS_PROTOCOL, WHITE_INTERNET_CAPABILITY, XRAY_PROTOCOL
from config.enums import AdminAuditAction, ServerHealthState, ServerLifecycleStatus
from database.models import Server, VlessSubscription, WhiteInternetSubscription
from database.repositories.servers_repo import (
    V2_NODE_ROLES,
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
from database.connection import queue_post_commit_task, session_scope
from services.audit_service import AuditService
from services.workers.node_monitor import reset_server_monitor_state
from services.xray_node_client import XrayNodeClient
from utils.datetime_helpers import now_utc
from utils.security import is_safe_url, validate_public_fqdn
from utils.traffic_helpers import get_server_hosting_usage

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
    try:
        ipaddress.ip_address(parsed.hostname)
        raise AdminNodeServiceError(
            "Добавление узла разрешено исключительно по доменному имени (FQDN). Прямые IP-адреса запрещены.",
            status_code=400,
        )
    except ValueError:
        pass
    if not validate_public_fqdn(parsed.hostname):
        raise AdminNodeServiceError(
            "Добавление узла разрешено исключительно по доменному имени (FQDN). Прямые IP-адреса запрещены.",
            status_code=400,
        )
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


async def _resolve_hostname_ipv4(hostname: str) -> str:
    """Resolve hostname or domain to an IPv4 string, or empty string on failure."""
    if not hostname:
        return ""
    try:
        ip = ipaddress.ip_address(hostname)
        return hostname if ip.version == 4 else ""
    except ValueError:
        pass
    try:
        loop = asyncio.get_running_loop()
        addr_info = await loop.getaddrinfo(hostname, None, family=socket.AF_INET)
        if addr_info and addr_info[0] and addr_info[0][4]:
            return str(addr_info[0][4][0])
    except Exception as exc:
        logger.debug("Could not resolve hostname %s to IPv4: %s", hostname, exc)
    return ""


async def auto_detect_node_geo(
    api_url: str,
    status_data: dict[str, Any] | None = None,
    role: str | None = None,
    *,
    resolved_ip: str | None = None,
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

    ip_to_lookup = (resolved_ip or "").strip()
    if not ip_to_lookup:
        hostname = urlparse(api_url).hostname or ""
        if hostname:
            ip_to_lookup = await _resolve_hostname_ipv4(hostname)

    if ip_to_lookup:
        geo = await _fetch_ip_geo_info(ip_to_lookup)
        country = geo.get("country") or ""
        flag = geo.get("flag") or (country_code_to_flag(geo.get("code")) if geo.get("code") else "")
        if country or flag:
            final_flag = detected_flag or flag or (
                "🇷🇺" if role == "ru_hub" else "🌐"
            )
            return country, final_flag

    final_flag = detected_flag or ("🇷🇺" if role == "ru_hub" else "🌐")
    return "", final_flag


def serialize_server_node(server: Server) -> dict[str, Any]:
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
    if role == "exit_node" and not location_code:
        location_code = f"exit{server.id}"

    module_errors = extra.get("module_errors") if isinstance(extra.get("module_errors"), dict) else {}
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
        "disabled_reason": server.disabled_reason,
        "consecutive_fails": int(server.consecutive_fails or 0),
        "consecutive_ingress_fails": int(extra.get("consecutive_ingress_fails", 0) or 0),
        "consecutive_cdn_fails": int(extra.get("consecutive_cdn_fails", 0) or 0),
        "ingress_problem": bool(extra.get("ingress_problem", False)),
        "cdn_problem": bool(extra.get("cdn_problem", False)),
        "problem_started_at": (
            server.problem_started_at.isoformat() if server.problem_started_at else None
        ),
        "vless_synced_epoch": extra.get("vless_synced_epoch") or None,
        "xray_instance_epoch": server.xray_instance_epoch,
        "system_load": extra.get("system_load") if isinstance(extra.get("system_load"), dict) else None,
        "disk_usage": extra.get("disk_usage") if isinstance(extra.get("disk_usage"), dict) else None,
        "capabilities": caps,
        "in_standard": in_standard,
        "in_white_internet": in_white_internet,
        "location_code": location_code,
        "vless_name": str(extra.get("vless_name") or "").strip(),
        "server_description": str(extra.get("server_description") or "").strip(),
        "wi_yandex_name": str(extra.get("wi_yandex_name") or "").strip(),
        "wi_yandex_badge": str(extra.get("wi_yandex_badge") or "").strip(),
        "wi_turboflare_name": str(extra.get("wi_turboflare_name") or "").strip(),
        "wi_turboflare_badge": str(extra.get("wi_turboflare_badge") or "").strip(),
        "domain": extra.get("domain") or "",
        "cdn_domain": extra.get("cdn_domain") or "",
        "cdn_domains": extra.get("cdn_domains") if isinstance(extra.get("cdn_domains"), dict) else {},
        "sub_path_prefix": extra.get("sub_path_prefix") or "",
        "secret_base_path": extra.get("secret_base_path") or "",
        "bridge_uuid": extra.get("bridge_uuid") or "",
        "modules": extra.get("modules") if isinstance(extra.get("modules"), dict) else {},
        "module_errors": module_errors,
        "module_errors_count": len(module_errors),
        "links": extra.get("links") if isinstance(extra.get("links"), dict) else {},
        "sni_routes": extra.get("sni_routes") if isinstance(extra.get("sni_routes"), list) else [],
        "xhttp_locations": extra.get("xhttp_locations") if isinstance(extra.get("xhttp_locations"), list) else [],
        "inbounds": extra.get("inbounds") if isinstance(extra.get("inbounds"), list) else [],
        "traffic_limit": (
            {
                "used_bytes": usage.host_bytes,
                "limit_bytes": usage.limit_gb * (1024 ** 3) if usage.limit_gb and usage.limit_gb > 0 else 0,
                "pct": round(usage.pct, 1),
                "exceeded": bool(
                    usage.limit_gb and usage.limit_gb > 0
                    and usage.host_bytes >= usage.limit_gb * (1024 ** 3)
                ),
                "reset_day": usage.reset_day,
            }
            if (usage := get_server_hosting_usage(extra)) and (usage.host_bytes > 0 or (usage.limit_gb and usage.limit_gb > 0))
            else None
        ),
        "last_apply": extra.get("last_apply") if isinstance(extra.get("last_apply"), dict) else None,
        "ssl_cert": extra.get("ssl_cert") if isinstance(extra.get("ssl_cert"), dict) else None,
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
    return [serialize_server_node(s) for s in servers]


async def _ensure_unique_exit_location_code(
    session: AsyncSession,
    server_id: int,
    raw_code: Any,
) -> str:
    """Ensure every exit_node has a unique location_code across all exit_node servers."""
    base_code = re.sub(r"[^a-z0-9_-]+", "", str(raw_code or "").strip().lower())[:32]
    if not base_code:
        base_code = f"exit{server_id}"
    all_srvs = await get_all_servers(session)
    taken = {
        str((s.extra_data or {}).get("location_code") or "").strip().lower()
        for s in all_srvs
        if s.id != server_id
        and get_server_role(s) == "exit_node"
        and isinstance(s.extra_data, dict)
    }
    if base_code not in taken:
        return base_code
    candidate = f"{base_code}{server_id}"[:32]
    if candidate not in taken:
        return candidate
    return f"exit{server_id}"


async def discover_and_add_node(
    *,
    admin_id: int,
    api_url: str,
    api_key: str,
    name: str = "",
    country_flag: str | None = None,
) -> dict[str, Any]:
    """Validate URL, probe bare v2 node outside DB transaction, persist Server, and write audit log."""
    clean_url = _normalize_api_url(api_url)
    clean_key = (api_key or "").strip()
    if not clean_key:
        raise AdminNodeServiceError("api_key is required", status_code=400)

    async def _check_duplicate(s: AsyncSession) -> None:
        existing = await get_server_by_api_url(s, clean_url)
        if existing is not None:
            raise AdminNodeServiceError(
                f"Node with api_url already exists (id={existing.id})",
                status_code=409,
            )

    async with session_scope() as session:
        await _check_duplicate(session)

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
    hostname = urlparse(clean_url).hostname or ""
    resolved_ip = await _resolve_hostname_ipv4(hostname) if hostname else ""
    if resolved_ip:
        extra["ip"] = resolved_ip
    role = str(extra.get("role") or "").strip().lower()
    if role not in V2_NODE_ROLES:
        raise AdminNodeServiceError(
            f"Узел имеет ненастроенную или недопустимую роль ('{role or 'none'}'). "
            "Допустимые роли: ru_hub, eu_hub, exit_node. Завершите настройку узла перед добавлением в бот.",
            status_code=400,
        )
    detected_name, detected_flag = await auto_detect_node_geo(
        clean_url, status_data, role, resolved_ip=resolved_ip
    )

    clean_name = (name or "").strip()[:255]
    resolved_name = clean_name or detected_name or _derive_default_name(clean_url, status_data, role)

    if country_flag and country_flag.strip():
        raw_flag = country_flag.strip()[:10]
        resolved_flag = country_code_to_flag(raw_flag) or raw_flag
    else:
        resolved_flag = detected_flag
    protocol = _derive_protocol(caps, role)

    async def _persist_new_node(s: AsyncSession) -> dict[str, Any]:
        await _check_duplicate(s)
        server = await create_server(
            session=s,
            name=resolved_name,
            role=role,
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
        if role == "exit_node":
            unique_loc = await _ensure_unique_exit_location_code(
                s, server.id, extra.get("location_code")
            )
            if extra.get("location_code") != unique_loc:
                extra["location_code"] = unique_loc
                server.extra_data = dict(extra)
                from sqlalchemy.orm.attributes import flag_modified
                flag_modified(server, "extra_data")
        if isinstance(health_data, dict) and health_data.get("status") == "ok":
            server.last_successful_check = now_utc()
            if health_data.get("node_epoch"):
                server.xray_instance_epoch = str(health_data.get("node_epoch"))
            if health_data.get("boot_id"):
                server.xray_instance_boot_id = str(health_data.get("boot_id"))
            if health_data.get("starttime") is not None:
                server.xray_instance_starttime = int(health_data["starttime"])
        await s.flush()
        await s.refresh(server)

        await AuditService.log_action(
            s,
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

    async with session_scope() as session:
        return await _persist_new_node(session)


def _queue_topology_sync(session: AsyncSession) -> None:
    """Queue v2 topology sync to run after the current transaction commits and releases row locks."""
    async def _sync_topology() -> None:
        from services.topology_sync_service import sync_v2_topology_to_nodes_nonblocking

        await sync_v2_topology_to_nodes_nonblocking()

    queue_post_commit_task(session, _sync_topology)


async def refresh_node(
    *,
    admin_id: int,
    server_id: int,
) -> dict[str, Any]:
    """Re-query /v1/node/status and /v1/health outside DB transaction and synchronize DB metadata."""
    async with session_scope() as session:
        server_ro = await get_server_by_id(session, server_id, for_update=False)
        if server_ro is None:
            raise AdminNodeServiceError("Node not found", status_code=404)
        api_url, api_key = server_ro.api_url, server_ro.api_key
    status_data, health_data, probe_ok, probe_error = await _probe_v2_node(
        api_url,
        api_key,
    )
    if not probe_ok:
        raise AdminNodeServiceError(
            f"Node unreachable: {probe_error or 'probe failed'}",
            status_code=502,
        )

    resolved_ip = ""
    if api_url:
        host = urlparse(api_url).hostname or ""
        resolved_ip = await _resolve_hostname_ipv4(host) if host else ""

    async def _apply_refresh(s: AsyncSession) -> dict[str, Any]:
        server = await get_server_by_id(s, server_id, for_update=True)
        if server is None:
            raise AdminNodeServiceError("Node not found", status_code=404)

        extra_cur = server.extra_data if isinstance(server.extra_data, dict) else {}
        caps = derive_v2_capabilities(status_data, health_data)
        extra = merge_v2_extra_data(extra_cur, status_data, health_data)
        if resolved_ip:
            extra["ip"] = resolved_ip
        role = extra.get("role")
        if role == "exit_node":
            extra["location_code"] = await _ensure_unique_exit_location_code(
                s, server.id, extra.get("location_code")
            )
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
            if server.is_active:
                updates["health_state"] = ServerHealthState.ONLINE
            updates["last_successful_check"] = now_utc()
            updates["consecutive_fails"] = 0
            if health_data.get("node_epoch"):
                updates["xray_instance_epoch"] = str(health_data.get("node_epoch"))
            if health_data.get("boot_id"):
                updates["xray_instance_boot_id"] = str(health_data.get("boot_id"))
            if health_data.get("starttime") is not None:
                updates["xray_instance_starttime"] = int(health_data["starttime"])

        updated = await update_server(s, server, **updates)
        _queue_topology_sync(s)
        await AuditService.log_action(
            s,
            admin_id=admin_id,
            action=AdminAuditAction.EDIT_SERVER,
            target_type="server",
            target_id=updated.id,
            details={"action": "refresh", "role": role},
        )
        logger.info("Admin %s refreshed v2 node %s (role=%s)", admin_id, server.id, role)
        return serialize_server_node(updated)

    async with session_scope() as session:
        return await _apply_refresh(session)


async def update_node_settings(
    *,
    admin_id: int,
    server_id: int,
    name: str | None = None,
    country_flag: str | None = None,
    api_url: str | None = None,
    api_key: str | None = None,
    in_standard: bool | None = None,
    in_white_internet: bool | None = None,
    vless_name: str | None = None,
    server_description: str | None = None,
    wi_yandex_name: str | None = None,
    wi_yandex_badge: str | None = None,
    wi_turboflare_name: str | None = None,
    wi_turboflare_badge: str | None = None,
) -> dict[str, Any]:
    """Update admin-editable node metadata and credentials, probing changed endpoints outside DB tx."""
    clean_url: str | None = None
    if api_url is not None:
        clean_url = _normalize_api_url(api_url)
        if not await is_safe_url(clean_url):
            raise AdminNodeServiceError("Unsafe or forbidden node URL", status_code=400)

    clean_key: str | None = None
    if api_key is not None:
        clean_key = api_key.strip()
        if not clean_key:
            raise AdminNodeServiceError("api_key cannot be empty", status_code=400)

    async with session_scope() as session:
        server_ro = await get_server_by_id(session, server_id, for_update=False)
        if server_ro is None:
            raise AdminNodeServiceError("Node not found", status_code=404)
        if clean_url is not None:
            existing = await get_server_by_api_url(session, clean_url)
            if existing is not None and existing.id != server_id:
                raise AdminNodeServiceError(
                    "Node with this api_url already exists",
                    status_code=409,
                )
        cur_url, cur_key = server_ro.api_url, server_ro.api_key
    url_changed = clean_url is not None and clean_url != cur_url
    key_changed = clean_key is not None and clean_key != cur_key

    if url_changed or key_changed:
        target_url = clean_url if clean_url is not None else cur_url
        target_key = clean_key if clean_key is not None else cur_key
        _status, _health, probe_ok, probe_err = await _probe_v2_node(target_url, target_key)
        if not probe_ok:
            raise AdminNodeServiceError(
                f"Узел недоступен по новым параметрам: {probe_err or 'unreachable'}",
                status_code=400,
            )

    resolved_ip = ""
    if clean_url is not None:
        host = urlparse(clean_url).hostname or ""
        resolved_ip = await _resolve_hostname_ipv4(host) if host else ""

    async def _apply_update(s: AsyncSession) -> dict[str, Any]:
        server = await get_server_by_id(s, server_id, for_update=True)
        if server is None:
            raise AdminNodeServiceError("Node not found", status_code=404)

        updates: dict[str, Any] = {}
        ip_changed = False
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
        if clean_url is not None:
            existing = await get_server_by_api_url(s, clean_url)
            if existing is not None and existing.id != server_id:
                raise AdminNodeServiceError("Node with this api_url already exists", status_code=409)
            if clean_url != server.api_url:
                updates["api_url"] = clean_url
            if resolved_ip:
                cur_extra = server.extra_data if isinstance(server.extra_data, dict) else {}
                if cur_extra.get("ip") != resolved_ip:
                    extra_for_ip = dict(cur_extra)
                    extra_for_ip["ip"] = resolved_ip
                    updates["extra_data"] = extra_for_ip
                    ip_changed = True
        if clean_key is not None and clean_key != server.api_key:
            updates["api_key"] = clean_key

        extra_fields = (
            in_standard,
            in_white_internet,
            vless_name,
            server_description,
            wi_yandex_name,
            wi_yandex_badge,
            wi_turboflare_name,
            wi_turboflare_badge,
        )
        if any(v is not None for v in extra_fields):
            base_extra = updates.get("extra_data")
            extra = (
                dict(base_extra)
                if isinstance(base_extra, dict)
                else (dict(server.extra_data) if isinstance(server.extra_data, dict) else {})
            )
            if in_standard is not None:
                extra["in_standard"] = bool(in_standard)
            if in_white_internet is not None:
                extra["in_white_internet"] = bool(in_white_internet)
            for key, raw_val, max_len in (
                ("vless_name", vless_name, 120),
                ("server_description", server_description, 30),
                ("wi_yandex_name", wi_yandex_name, 120),
                ("wi_yandex_badge", wi_yandex_badge, 30),
                ("wi_turboflare_name", wi_turboflare_name, 120),
                ("wi_turboflare_badge", wi_turboflare_badge, 30),
            ):
                if raw_val is not None:
                    clean_val = raw_val.strip()[:max_len]
                    if clean_val:
                        extra[key] = clean_val
                    else:
                        extra.pop(key, None)
            updates["extra_data"] = extra

        if not updates:
            return serialize_server_node(server)

        updated = await update_server(s, server, **updates)
        if (
            "api_url" in updates
            or ip_changed
            or any(v is not None for v in (in_standard, in_white_internet))
        ):
            _queue_topology_sync(s)
        await AuditService.log_action(
            s,
            admin_id=admin_id,
            action=AdminAuditAction.EDIT_SERVER,
            target_type="server",
            target_id=updated.id,
            details={"updated_fields": sorted(updates.keys())},
        )
        return serialize_server_node(updated)

    async with session_scope() as session:
        return await _apply_update(session)


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
    if target_active and server.lifecycle_status == ServerLifecycleStatus.DECOMMISSIONING:
        raise AdminNodeServiceError(
            "Сервер в статусе вывода (Drain). Сначала верните его в ACTIVE.",
            status_code=400,
        )
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
    _queue_topology_sync(session)
    if target_active:
        role = get_server_role(updated)
        if role == "ru_hub":
            from services.vless_subscription_service import VlessSubscriptionService
            sid = updated.id

            async def _spawn_ru_sync(sid: int = sid) -> None:
                VlessSubscriptionService.sync_all_active_to_server_background(sid)

            queue_post_commit_task(session, _spawn_ru_sync)
        elif role == "exit_node":
            from services.vless_subscription_service import VlessSubscriptionService
            ru_hubs = await VlessSubscriptionService.get_configured_vless_servers(session)
            for hub in ru_hubs:
                hid = hub.id

                async def _spawn_hub_sync(hid: int = hid) -> None:
                    VlessSubscriptionService.sync_all_active_to_server_background(hid)

                queue_post_commit_task(session, _spawn_hub_sync)
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
    force: bool = False,
) -> dict[str, Any]:
    """Delete a server record from DB, guarding active nodes unless force=True."""
    server = await get_server_by_id(session, server_id, for_update=True)
    if server is None:
        raise AdminNodeServiceError("Node not found", status_code=404)

    if not force and server.is_active:
        role = get_server_role(server)
        all_srvs = await get_all_servers(session)
        other_active_same_role = [
            s
            for s in all_srvs
            if s.id != server.id and s.is_active and get_server_role(s) == role
        ]
        msg = "Сервер сейчас активен. Выключите его или подтвердите принудительное удаление."
        if not other_active_same_role:
            msg += f" Внимание: это последний активный узел роли {role}."
        raise AdminNodeServiceError(msg, status_code=409)

    deleted_name = server.name
    await delete_server(session, server)
    sid_key = str(server_id)
    await session.execute(
        update(WhiteInternetSubscription)
        .where(WhiteInternetSubscription.node_sync_state.has_key(sid_key))
        .values(node_sync_state=WhiteInternetSubscription.node_sync_state.op("-")(sid_key))
    )
    await session.execute(
        update(VlessSubscription)
        .where(VlessSubscription.node_traffic_snapshots.has_key(sid_key))
        .values(node_traffic_snapshots=VlessSubscription.node_traffic_snapshots.op("-")(sid_key))
    )
    from services.workers.node_monitor import clear_server_monitor_state
    clear_server_monitor_state(server_id)
    _queue_topology_sync(session)
    await AuditService.log_action(
        session,
        admin_id=admin_id,
        action=AdminAuditAction.DELETE_SERVER,
        target_type="server",
        target_id=server_id,
        details={"name": deleted_name, "force": force},
    )
    return {"deleted_id": server_id, "name": deleted_name}


async def set_node_lifecycle(
    server_id: int,
    lifecycle_status: str,
    admin_telegram_id: int,
) -> dict[str, Any]:
    """Set node lifecycle_status to ACTIVE or DECOMMISSIONING and queue topology sync."""
    normalized = (lifecycle_status or "").strip().upper()
    allowed = {ServerLifecycleStatus.ACTIVE.value, ServerLifecycleStatus.DECOMMISSIONING.value}
    if normalized not in allowed:
        raise AdminNodeServiceError(
            f"Недопустимый lifecycle_status: {lifecycle_status!r}. Допустимые: ACTIVE, DECOMMISSIONING.",
            status_code=400,
        )

    async with session_scope() as session:
        server = await get_server_by_id(session, server_id, for_update=True)
        if server is None:
            raise AdminNodeServiceError("Node not found", status_code=404)

        if (
            normalized == ServerLifecycleStatus.ACTIVE.value
            and server.lifecycle_status != ServerLifecycleStatus.DECOMMISSIONING.value
            and (
                server.health_state == ServerHealthState.MANUAL_DISABLED
                or not server.is_active
            )
        ):
            raise AdminNodeServiceError(
                "Сначала включите сервер через переключатель активности.",
                status_code=400,
            )

        # Preserve active/manual-disabled state across lifecycle transitions; never silently re-enable.
        preserve_active = bool(
            server.is_active and server.health_state != ServerHealthState.MANUAL_DISABLED
        )
        update_kwargs: dict[str, Any] = {
            "lifecycle_status": normalized,
            "is_active": preserve_active,
        }
        if normalized == ServerLifecycleStatus.DECOMMISSIONING.value:
            role = get_server_role(server)
            if role != "ru_hub":
                raise AdminNodeServiceError(
                    "Плавный вывод (Drain) поддерживается только для ru_hub.",
                    status_code=400,
                )

        updated = await update_server(session, server, **update_kwargs)
        _queue_topology_sync(session)
        await AuditService.log_action(
            session,
            admin_id=admin_telegram_id,
            action=AdminAuditAction.EDIT_SERVER,
            target_type="server",
            target_id=updated.id,
            details={"lifecycle_status": normalized},
        )
        return serialize_server_node(updated)


async def trigger_topology_sync(admin_telegram_id: int) -> dict[str, Any]:
    """Trigger immediate v2 topology sync across all eligible nodes outside DB tx and log to AuditLog."""
    from services.topology_sync_service import sync_v2_topology_to_nodes_nonblocking

    raw_results = await sync_v2_topology_to_nodes_nonblocking()
    results = {str(k): bool(v) for k, v in raw_results.items()}
    synced = sum(1 for v in raw_results.values() if v)
    failed = sum(1 for v in raw_results.values() if not v)

    async with session_scope() as session:
        await AuditService.log_action(
            session,
            admin_id=admin_telegram_id,
            action=AdminAuditAction.EDIT_SERVER,
            target_type="server",
            target_id=None,
            details={"action": "topology_sync", "synced": synced, "failed": failed},
        )
    return {"results": results, "synced": synced, "failed": failed}


async def trigger_node_clients_resync(
    server_id: int,
    admin_telegram_id: int,
) -> dict[str, Any]:
    """Queue VLESS client resync for an active ru_hub or exit_node and log to AuditLog."""
    from services.vless_subscription_service import VlessSubscriptionService

    target_server_ids: list[int] = []
    async with session_scope() as session:
        server = await get_server_by_id(session, server_id, for_update=False)
        if server is None:
            raise AdminNodeServiceError("Node not found", status_code=404)
        if server.lifecycle_status == ServerLifecycleStatus.DECOMMISSIONING:
            raise AdminNodeServiceError(
                "Сервер в статусе вывода (Drain). Сначала верните его в ACTIVE.",
                status_code=400,
            )
        if not server.is_active:
            raise AdminNodeServiceError(
                "Сервер выключен. Включите его перед синхронизацией клиентов.",
                status_code=400,
            )
        role = get_server_role(server)
        if role not in ("ru_hub", "exit_node"):
            raise AdminNodeServiceError(
                f"Синхронизация VLESS-клиентов поддерживается только для ru_hub и exit_node (роль: {role}).",
                status_code=400,
            )

        if role == "ru_hub":
            target_server_ids = [server_id]
        else:
            ru_hubs = await VlessSubscriptionService.get_configured_vless_servers(session)
            target_server_ids = [hub.id for hub in ru_hubs]

        await AuditService.log_action(
            session,
            admin_id=admin_telegram_id,
            action=AdminAuditAction.EDIT_SERVER,
            target_type="server",
            target_id=server_id,
            details={
                "action": "clients_resync",
                "role": role,
                "target_server_ids": target_server_ids,
            },
        )

    for target_sid in target_server_ids:
        VlessSubscriptionService.sync_all_active_to_server_background(target_sid)

    return {"ok": True, "server_id": server_id, "target_server_ids": target_server_ids}
