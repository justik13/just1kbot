"""Admin HTTP handlers for v2 node management, lifecycle, topology, and INCY settings."""

from __future__ import annotations

import os
from typing import Any

from aiohttp import web
from sqlalchemy.ext.asyncio import AsyncSession

import bot.miniapp.handlers_admin as _ha
from bot.miniapp.schemas import (
    AddNodePayload,
    IncyGlobalSettingsPayload,
    NodeLifecyclePayload,
    ToggleNodePayload,
    UpdateNodePayload,
)
from config import constants
from config.enums import AdminAuditAction
from services.admin_nodes_service import AdminNodeServiceError


async def api_admin_nodes_list_handler(request: web.Request) -> web.Response:
    ctx, err_resp = _ha.require_admin_user(request)
    if err_resp is not None or ctx is None:
        return err_resp  # type: ignore[return-value]

    async with _ha.session_scope() as session:
        nodes = await _ha.admin_nodes_service.list_nodes(session)
        return web.json_response({"ok": True, "nodes": nodes})


async def api_admin_nodes_add_handler(request: web.Request) -> web.Response:
    ctx, err_resp = _ha.require_admin_user(request)
    if err_resp is not None or ctx is None:
        return err_resp  # type: ignore[return-value]

    payload, parse_err = await _ha._parse_json_model(request, AddNodePayload)
    if parse_err is not None or payload is None:
        return parse_err  # type: ignore[return-value]

    try:
        result = await _ha.admin_nodes_service.discover_and_add_node(
            admin_id=ctx.telegram_id,
            api_url=payload.api_url,
            api_key=payload.api_key,
            name=payload.name,
            country_flag=payload.country_flag,
        )
        return web.json_response({"ok": True, **result}, status=201)
    except AdminNodeServiceError as exc:
        return _ha._service_error_response(exc)


async def api_admin_nodes_refresh_handler(request: web.Request) -> web.Response:
    ctx, err_resp = _ha.require_admin_user(request)
    if err_resp is not None or ctx is None:
        return err_resp  # type: ignore[return-value]

    server_id, id_err = _ha._parse_positive_int(request, "server_id", label="node id")
    if id_err is not None or server_id is None:
        return id_err  # type: ignore[return-value]

    try:
        node = await _ha.admin_nodes_service.refresh_node(
            admin_id=ctx.telegram_id,
            server_id=server_id,
        )
        return web.json_response({"ok": True, "node": node})
    except AdminNodeServiceError as exc:
        return _ha._service_error_response(exc)


async def api_admin_nodes_patch_handler(request: web.Request) -> web.Response:
    ctx, err_resp = _ha.require_admin_user(request)
    if err_resp is not None or ctx is None:
        return err_resp  # type: ignore[return-value]

    server_id, id_err = _ha._parse_positive_int(request, "server_id", label="node id")
    if id_err is not None or server_id is None:
        return id_err  # type: ignore[return-value]

    payload, parse_err = await _ha._parse_json_model(request, UpdateNodePayload)
    if parse_err is not None or payload is None:
        return parse_err  # type: ignore[return-value]

    try:
        node = await _ha.admin_nodes_service.update_node_settings(
            admin_id=ctx.telegram_id,
            server_id=server_id,
            name=payload.name,
            country_flag=payload.country_flag,
            api_url=payload.api_url,
            api_key=payload.api_key,
            in_standard=payload.in_standard,
            in_white_internet=payload.in_white_internet,
            vless_name=payload.vless_name,
            server_description=payload.server_description,
            wi_yandex_name=payload.wi_yandex_name,
            wi_yandex_badge=payload.wi_yandex_badge,
            wi_turboflare_name=payload.wi_turboflare_name,
            wi_turboflare_badge=payload.wi_turboflare_badge,
        )
        return web.json_response({"ok": True, "node": node})
    except AdminNodeServiceError as exc:
        return _ha._service_error_response(exc)


async def api_admin_nodes_toggle_handler(request: web.Request) -> web.Response:
    ctx, err_resp = _ha.require_admin_user(request)
    if err_resp is not None or ctx is None:
        return err_resp  # type: ignore[return-value]

    server_id, id_err = _ha._parse_positive_int(request, "server_id", label="node id")
    if id_err is not None or server_id is None:
        return id_err  # type: ignore[return-value]

    raw_text = await request.text()
    target_active: bool | None = None
    if raw_text.strip():
        payload, parse_err = await _ha._parse_json_model(request, ToggleNodePayload)
        if parse_err is not None or payload is None:
            return parse_err  # type: ignore[return-value]
        target_active = payload.is_active

    try:
        async with _ha.session_scope() as session:
            node = await _ha.admin_nodes_service.toggle_node(
                session,
                admin_id=ctx.telegram_id,
                server_id=server_id,
                is_active=target_active,
            )
            return web.json_response({"ok": True, "node": node})
    except AdminNodeServiceError as exc:
        return _ha._service_error_response(exc)


async def api_admin_nodes_delete_handler(request: web.Request) -> web.Response:
    ctx, err_resp = _ha.require_admin_user(request)
    if err_resp is not None or ctx is None:
        return err_resp  # type: ignore[return-value]

    server_id, id_err = _ha._parse_positive_int(request, "server_id", label="node id")
    if id_err is not None or server_id is None:
        return id_err  # type: ignore[return-value]

    force = request.query.get("force", "").strip().lower() in ("1", "true", "yes")
    try:
        async with _ha.session_scope() as session:
            deleted = await _ha.admin_nodes_service.delete_node(
                session,
                admin_id=ctx.telegram_id,
                server_id=server_id,
                force=force,
            )
            return web.json_response({"ok": True, **deleted})
    except AdminNodeServiceError as exc:
        return _ha._service_error_response(exc)


def _as_bool_setting(val: object, default: bool) -> bool:
    if val is None:
        return default
    if isinstance(val, bool):
        return val
    if isinstance(val, str):
        return val.strip().lower() in ("1", "true", "yes", "on")
    return bool(val)


async def _read_incy_settings_dict(session: AsyncSession) -> dict[str, Any]:
    default_interval = os.getenv("VLESS_PROFILE_UPDATE_INTERVAL", "6").strip()
    if not (default_interval.isdigit() and int(default_interval) > 0):
        default_interval = "6"

    profile_title = await _ha.get_system_setting(
        session,
        "vless_profile_title",
        constants.DEFAULT_VLESS_PROFILE_TITLE,
    )
    announce = await _ha.get_system_setting(session, "vless_announce", "")
    announce_url = await _ha.get_system_setting(session, "vless_announce_url", "")
    show_devices_raw = await _ha.get_system_setting(session, "vless_profile_show_devices", "false")
    interval_raw = await _ha.get_system_setting(session, "vless_profile_update_interval", default_interval)
    support_url = await _ha.get_system_setting(session, "vless_support_url", "")
    web_page_url = await _ha.get_system_setting(session, "vless_web_page_url", "")
    sort_order = await _ha.get_system_setting(session, "vless_sort_order", "none")
    hide_url_raw = await _ha.get_system_setting(session, "vless_hide_url", "true")
    hide_check_raw = await _ha.get_system_setting(session, "vless_hide_check", "false")
    frag_raw = await _ha.get_system_setting(session, "vless_fragmentation_enabled", "false")
    frag_packets = await _ha.get_system_setting(session, "vless_fragmentation_packets", "tlshello")
    frag_length = await _ha.get_system_setting(session, "vless_fragmentation_length", "10-30")
    frag_interval = await _ha.get_system_setting(session, "vless_fragmentation_interval", "10-30")
    no_limit_mode = await _ha.get_system_setting(session, "vless_no_limit_mode", "all")
    dns_resolve_raw = await _ha.get_system_setting(session, "vless_dns_resolve_enabled", "true")
    dns_resolve_domain = await _ha.get_system_setting(
        session, "vless_dns_resolve_domain", "https://common.dot.dns.yandex.net/dns-query"
    )
    dns_resolve_ip = await _ha.get_system_setting(session, "vless_dns_resolve_ip", "77.88.8.8")
    per_app_raw = await _ha.get_system_setting(session, "vless_per_app_proxy_enabled", "false")
    per_app_mode = await _ha.get_system_setting(session, "vless_per_app_proxy_mode", "bypass")
    per_app_list = await _ha.get_system_setting(session, "vless_per_app_proxy_list", "")
    cdn_label_mode = await _ha.get_system_setting(session, "bi_cdn_label_mode", "name")
    cdn_yandex_label = await _ha.get_system_setting(session, "bi_cdn_yandex_label", "Yandex")
    cdn_turboflare_label = await _ha.get_system_setting(session, "bi_cdn_turboflare_label", "Turboflare")
    mtproto_proxy_url = await _ha.get_system_setting(session, "mtproto_proxy_url", "")

    interval_val = int(interval_raw) if str(interval_raw or "").strip().isdigit() else int(default_interval)
    return {
        "profile_title": str(profile_title or constants.DEFAULT_VLESS_PROFILE_TITLE),
        "announce": str(announce or ""),
        "announce_url": str(announce_url or ""),
        "profile_show_devices": _as_bool_setting(show_devices_raw, False),
        "profile_update_interval": max(1, min(720, interval_val)),
        "support_url": str(support_url or ""),
        "web_page_url": str(web_page_url or ""),
        "sort_order": str(sort_order or "none"),
        "hide_url": _as_bool_setting(hide_url_raw, True),
        "hide_check": _as_bool_setting(hide_check_raw, False),
        "fragmentation_enabled": _as_bool_setting(frag_raw, False),
        "fragmentation_packets": str(frag_packets or "tlshello"),
        "fragmentation_length": str(frag_length or "10-30"),
        "fragmentation_interval": str(frag_interval or "10-30"),
        "no_limit_mode": str(no_limit_mode or "all"),
        "dns_resolve_enabled": _as_bool_setting(dns_resolve_raw, True),
        "dns_resolve_domain": str(dns_resolve_domain or "https://common.dot.dns.yandex.net/dns-query"),
        "dns_resolve_ip": str(dns_resolve_ip or "77.88.8.8"),
        "per_app_proxy_enabled": _as_bool_setting(per_app_raw, False),
        "per_app_proxy_mode": str(per_app_mode or "bypass"),
        "per_app_proxy_list": str(per_app_list or ""),
        "cdn_label_mode": str(cdn_label_mode or "name"),
        "cdn_yandex_label": str(cdn_yandex_label or "Yandex"),
        "cdn_turboflare_label": str(cdn_turboflare_label or "Turboflare"),
        "mtproto_proxy_url": str(mtproto_proxy_url or ""),
    }


async def api_admin_incy_get_handler(request: web.Request) -> web.Response:
    ctx, err_resp = _ha.require_admin_user(request)
    if err_resp is not None or ctx is None:
        return err_resp  # type: ignore[return-value]

    async with _ha.session_scope() as session:
        settings_payload = await _read_incy_settings_dict(session)
    return web.json_response({"ok": True, "settings": settings_payload})


async def api_admin_incy_put_handler(request: web.Request) -> web.Response:
    ctx, err_resp = _ha.require_admin_user(request)
    if err_resp is not None or ctx is None:
        return err_resp  # type: ignore[return-value]

    payload, parse_err = await _ha._parse_json_model(request, IncyGlobalSettingsPayload)
    if parse_err is not None or payload is None:
        return parse_err  # type: ignore[return-value]

    async with _ha.session_scope() as session:
        changed: dict[str, object] = {}

        str_fields = (
            ("profile_title", "vless_profile_title", constants.DEFAULT_VLESS_PROFILE_TITLE),
            ("announce", "vless_announce", ""),
            ("announce_url", "vless_announce_url", ""),
            ("support_url", "vless_support_url", ""),
            ("web_page_url", "vless_web_page_url", ""),
            ("sort_order", "vless_sort_order", "none"),
            ("fragmentation_packets", "vless_fragmentation_packets", "tlshello"),
            ("fragmentation_length", "vless_fragmentation_length", "10-30"),
            ("fragmentation_interval", "vless_fragmentation_interval", "10-30"),
            ("no_limit_mode", "vless_no_limit_mode", "all"),
            ("dns_resolve_domain", "vless_dns_resolve_domain", ""),
            ("dns_resolve_ip", "vless_dns_resolve_ip", ""),
            ("per_app_proxy_mode", "vless_per_app_proxy_mode", "bypass"),
            ("per_app_proxy_list", "vless_per_app_proxy_list", ""),
            ("cdn_label_mode", "bi_cdn_label_mode", "name"),
            ("cdn_yandex_label", "bi_cdn_yandex_label", "Yandex"),
            ("cdn_turboflare_label", "bi_cdn_turboflare_label", "Turboflare"),
            ("mtproto_proxy_url", "mtproto_proxy_url", ""),
        )
        for field_name, setting_key, default_val in str_fields:
            raw_val = getattr(payload, field_name)
            if raw_val is not None:
                clean_val = str(raw_val).strip()
                await _ha.set_system_setting(
                    session,
                    setting_key,
                    clean_val or None,
                    updated_by=ctx.telegram_id,
                )
                changed[field_name] = clean_val or default_val

        if payload.profile_update_interval is not None:
            interval_str = str(int(payload.profile_update_interval))
            await _ha.set_system_setting(
                session,
                "vless_profile_update_interval",
                interval_str,
                updated_by=ctx.telegram_id,
            )
            changed["profile_update_interval"] = int(payload.profile_update_interval)

        bool_fields = (
            ("profile_show_devices", "vless_profile_show_devices"),
            ("hide_url", "vless_hide_url"),
            ("hide_check", "vless_hide_check"),
            ("fragmentation_enabled", "vless_fragmentation_enabled"),
            ("dns_resolve_enabled", "vless_dns_resolve_enabled"),
            ("per_app_proxy_enabled", "vless_per_app_proxy_enabled"),
        )
        for field_name, setting_key in bool_fields:
            raw_bool = getattr(payload, field_name)
            if raw_bool is not None:
                val = "true" if raw_bool else "false"
                await _ha.set_system_setting(session, setting_key, val, updated_by=ctx.telegram_id)
                changed[field_name] = bool(raw_bool)

        if changed:
            await _ha.AuditService.log_action(
                session,
                admin_id=ctx.telegram_id,
                action=AdminAuditAction.INCY_SETTINGS_UPDATED,
                target_type="system",
                details=changed,
            )

        settings_payload = await _read_incy_settings_dict(session)

    return web.json_response({"ok": True, "settings": settings_payload})


async def api_admin_nodes_lifecycle_handler(request: web.Request) -> web.Response:
    """Set node lifecycle_status (active | decommissioning)."""
    ctx, err_resp = _ha.require_admin_user(request)
    if err_resp is not None or ctx is None:
        return err_resp  # type: ignore[return-value]

    server_id, id_err = _ha._parse_positive_int(request, "server_id", label="node id")
    if id_err is not None or server_id is None:
        return id_err  # type: ignore[return-value]

    payload, parse_err = await _ha._parse_json_model(request, NodeLifecyclePayload)
    if parse_err is not None or payload is None:
        return parse_err  # type: ignore[return-value]

    try:
        node = await _ha.admin_nodes_service.set_node_lifecycle(
            server_id,
            payload.lifecycle_status,
            ctx.telegram_id,
        )
        return web.json_response({"ok": True, "node": node})
    except AdminNodeServiceError as exc:
        return _ha._service_error_response(exc)
