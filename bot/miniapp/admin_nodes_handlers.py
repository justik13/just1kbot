"""Admin HTTP handlers for v2 node management, lifecycle, topology, and INCY settings."""

from __future__ import annotations

import os
from typing import Any

from aiohttp import web
from sqlalchemy.ext.asyncio import AsyncSession

from bot.miniapp.admin_common import (
    parse_json_model,
    parse_positive_int,
    service_error_response,
)
from bot.miniapp.auth import require_admin_user
from bot.miniapp.schemas import (
    AddNodePayload,
    IncyGlobalSettingsPayload,
    NodeLifecyclePayload,
    ToggleNodePayload,
    UpdateNodePayload,
)
from config import constants
from config.enums import AdminAuditAction
from database.connection import session_scope
from database.repositories.system_settings_repo import (
    get_system_setting,
    set_system_setting,
)
from services import admin_nodes_service
from services.admin_nodes_service import AdminNodeServiceError
from services.audit_service import AuditService


async def api_admin_nodes_list_handler(request: web.Request) -> web.Response:
    ctx, err_resp = await require_admin_user(request)
    if err_resp is not None or ctx is None:
        return err_resp  # type: ignore[return-value]

    async with session_scope() as session:
        nodes = await admin_nodes_service.list_nodes(session)
        return web.json_response({"ok": True, "nodes": nodes})


async def api_admin_nodes_refresh_handler(request: web.Request) -> web.Response:
    ctx, err_resp = await require_admin_user(request)
    if err_resp is not None or ctx is None:
        return err_resp  # type: ignore[return-value]

    try:
        result = await admin_nodes_service.refresh_nodes()
        return web.json_response({"ok": True, **result})
    except AdminNodeServiceError as exc:
        return service_error_response(exc)


async def api_admin_nodes_add_handler(request: web.Request) -> web.Response:
    ctx, err_resp = await require_admin_user(request)
    if err_resp is not None or ctx is None:
        return err_resp  # type: ignore[return-value]

    payload, parse_err = await parse_json_model(request, AddNodePayload)
    if parse_err is not None or payload is None:
        return parse_err  # type: ignore[return-value]

    try:
        result = await admin_nodes_service.discover_and_add_node(
            admin_id=ctx.telegram_id,
            api_url=payload.api_url,
            api_key=payload.api_key,
            name=payload.name,
            country_flag=payload.country_flag,
        )
        return web.json_response({"ok": True, **result}, status=201)
    except AdminNodeServiceError as exc:
        return service_error_response(exc)


async def api_admin_nodes_patch_handler(request: web.Request) -> web.Response:
    ctx, err_resp = await require_admin_user(request)
    if err_resp is not None or ctx is None:
        return err_resp  # type: ignore[return-value]

    server_id, id_err = parse_positive_int(request, "server_id", label="node id")
    if id_err is not None or server_id is None:
        return id_err  # type: ignore[return-value]

    payload, parse_err = await parse_json_model(request, UpdateNodePayload)
    if parse_err is not None or payload is None:
        return parse_err  # type: ignore[return-value]

    try:
        node = await admin_nodes_service.update_node_settings(
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
        return service_error_response(exc)


async def api_admin_nodes_toggle_handler(request: web.Request) -> web.Response:
    ctx, err_resp = await require_admin_user(request)
    if err_resp is not None or ctx is None:
        return err_resp  # type: ignore[return-value]

    server_id, id_err = parse_positive_int(request, "server_id", label="node id")
    if id_err is not None or server_id is None:
        return id_err  # type: ignore[return-value]

    raw_text = await request.text()
    target_active: bool | None = None
    if raw_text.strip():
        payload, parse_err = await parse_json_model(request, ToggleNodePayload)
        if parse_err is not None or payload is None:
            return parse_err  # type: ignore[return-value]
        target_active = payload.is_active

    try:
        async with session_scope() as session:
            node = await admin_nodes_service.toggle_node(
                session,
                admin_id=ctx.telegram_id,
                server_id=server_id,
                is_active=target_active,
            )
            return web.json_response({"ok": True, "node": node})
    except AdminNodeServiceError as exc:
        return service_error_response(exc)


async def api_admin_nodes_delete_handler(request: web.Request) -> web.Response:
    ctx, err_resp = await require_admin_user(request)
    if err_resp is not None or ctx is None:
        return err_resp  # type: ignore[return-value]

    server_id, id_err = parse_positive_int(request, "server_id", label="node id")
    if id_err is not None or server_id is None:
        return id_err  # type: ignore[return-value]

    force = request.query.get("force", "").strip().lower() in ("1", "true", "yes")
    try:
        async with session_scope() as session:
            deleted = await admin_nodes_service.delete_node(
                session,
                admin_id=ctx.telegram_id,
                server_id=server_id,
                force=force,
            )
            return web.json_response({"ok": True, **deleted})
    except AdminNodeServiceError as exc:
        return service_error_response(exc)


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

    profile_title = await get_system_setting(
        session,
        "vless_profile_title",
        constants.DEFAULT_VLESS_PROFILE_TITLE,
    )
    announce = await get_system_setting(session, "vless_announce", "")
    announce_url = await get_system_setting(session, "vless_announce_url", "")
    show_devices_raw = await get_system_setting(session, "vless_profile_show_devices", "false")
    interval_raw = await get_system_setting(session, "vless_profile_update_interval", default_interval)
    support_url = await get_system_setting(session, "vless_support_url", "")
    web_page_url = await get_system_setting(session, "vless_web_page_url", "")
    cdn_label_mode = await get_system_setting(session, "bi_cdn_label_mode", "name")
    cdn_yandex_label = await get_system_setting(session, "bi_cdn_yandex_label", "Yandex")
    cdn_turboflare_label = await get_system_setting(session, "bi_cdn_turboflare_label", "Turboflare")
    mtproto_proxy_url = await get_system_setting(session, "mtproto_proxy_url", "")

    interval_val = int(interval_raw) if str(interval_raw or "").strip().isdigit() else int(default_interval)
    return {
        "profile_title": str(profile_title or constants.DEFAULT_VLESS_PROFILE_TITLE),
        "announce": str(announce or ""),
        "announce_url": str(announce_url or ""),
        "profile_show_devices": _as_bool_setting(show_devices_raw, False),
        "profile_update_interval": max(1, min(720, interval_val)),
        "support_url": str(support_url or ""),
        "web_page_url": str(web_page_url or ""),
        "cdn_label_mode": str(cdn_label_mode or "name"),
        "cdn_yandex_label": str(cdn_yandex_label or "Yandex"),
        "cdn_turboflare_label": str(cdn_turboflare_label or "Turboflare"),
        "mtproto_proxy_url": str(mtproto_proxy_url or ""),
    }


async def api_admin_incy_get_handler(request: web.Request) -> web.Response:
    ctx, err_resp = await require_admin_user(request)
    if err_resp is not None or ctx is None:
        return err_resp  # type: ignore[return-value]

    async with session_scope() as session:
        settings_payload = await _read_incy_settings_dict(session)
    return web.json_response({"ok": True, "settings": settings_payload})


async def api_admin_incy_put_handler(request: web.Request) -> web.Response:
    ctx, err_resp = await require_admin_user(request)
    if err_resp is not None or ctx is None:
        return err_resp  # type: ignore[return-value]

    payload, parse_err = await parse_json_model(request, IncyGlobalSettingsPayload)
    if parse_err is not None or payload is None:
        return parse_err  # type: ignore[return-value]

    async with session_scope() as session:
        changed: dict[str, object] = {}

        str_fields = (
            ("profile_title", "vless_profile_title", constants.DEFAULT_VLESS_PROFILE_TITLE),
            ("announce", "vless_announce", ""),
            ("announce_url", "vless_announce_url", ""),
            ("support_url", "vless_support_url", ""),
            ("web_page_url", "vless_web_page_url", ""),
            ("cdn_label_mode", "bi_cdn_label_mode", "name"),
            ("cdn_yandex_label", "bi_cdn_yandex_label", "Yandex"),
            ("cdn_turboflare_label", "bi_cdn_turboflare_label", "Turboflare"),
            ("mtproto_proxy_url", "mtproto_proxy_url", ""),
        )
        for field_name, setting_key, default_val in str_fields:
            raw_val = getattr(payload, field_name)
            if raw_val is not None:
                clean_val = str(raw_val).strip()
                await set_system_setting(
                    session,
                    setting_key,
                    clean_val or None,
                    updated_by=ctx.telegram_id,
                )
                changed[field_name] = clean_val or default_val

        if payload.profile_update_interval is not None:
            interval_str = str(int(payload.profile_update_interval))
            await set_system_setting(
                session,
                "vless_profile_update_interval",
                interval_str,
                updated_by=ctx.telegram_id,
            )
            changed["profile_update_interval"] = int(payload.profile_update_interval)

        if payload.profile_show_devices is not None:
            val = "true" if payload.profile_show_devices else "false"
            await set_system_setting(
                session, "vless_profile_show_devices", val, updated_by=ctx.telegram_id
            )
            changed["profile_show_devices"] = bool(payload.profile_show_devices)

        if changed:
            await AuditService.log_action(
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
    ctx, err_resp = await require_admin_user(request)
    if err_resp is not None or ctx is None:
        return err_resp  # type: ignore[return-value]

    server_id, id_err = parse_positive_int(request, "server_id", label="node id")
    if id_err is not None or server_id is None:
        return id_err  # type: ignore[return-value]

    payload, parse_err = await parse_json_model(request, NodeLifecyclePayload)
    if parse_err is not None or payload is None:
        return parse_err  # type: ignore[return-value]

    try:
        node = await admin_nodes_service.set_node_lifecycle(
            server_id,
            payload.lifecycle_status,
            ctx.telegram_id,
        )
        return web.json_response({"ok": True, "node": node})
    except AdminNodeServiceError as exc:
        return service_error_response(exc)
