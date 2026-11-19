"""Thin HTTP handlers for Mini App identity, admin 2FA, node management, and user stats."""

from __future__ import annotations

import logging
import os
from datetime import timedelta
from decimal import Decimal
from typing import TypeVar
from uuid import uuid4

from aiohttp import web
from pydantic import BaseModel, ValidationError
from sqlalchemy import delete, func, or_, select

from bot.miniapp.auth import require_admin_user, require_authenticated_user
from bot.miniapp.handlers_user import _build_backup_sub_url, _build_sub_url
from bot.miniapp.schemas import (
    AddNodePayload,
    AdminDirectMessagePayload,
    AdminUserAdjustPayload,
    BroadcastPreviewPayload,
    BroadcastStartPayload,
    IncyGlobalSettingsPayload,
    MassBonusPayload,
    MassExtendPayload,
    PaymentsMaintenancePayload,
    ToggleNodePayload,
    UpdateNodePayload,
    Verify2FAPayload,
)
from config import constants
from config.enums import (
    AdminAuditAction,
    WhiteInternetProvisioningStatus,
    WhiteInternetStatus,
)
from database.connection import session_scope
from database.models import AccountLedgerEntry, User, UserDevice, WhiteInternetSubscription
from database.repositories import user_devices_repo, users_repo, vless_subscription_repo, white_internet_repo
from database.repositories.account_ledger_repo import (
    create_admin_adjustment,
    get_account_balance,
)
from database.repositories.system_settings_repo import get_system_setting, set_system_setting
from database.repositories.vless_subscription_repo import get_or_create_subscription
from database.repositories.white_internet_repo import get_subscription_by_user_id
from services import admin_nodes_service, admin_tools_service, broadcast_service
from services.audit_service import AuditService
from services.vless_subscription_service import VlessSubscriptionService
from services.admin_nodes_service import AdminNodeServiceError
from services.miniapp_auth_service import (
    build_totp_uri,
    generate_totp_secret,
    get_admin_2fa_config,
    issue_totp_setup_token,
    verify_admin_2fa,
    verify_admin_session_token,
)
from services.subscription import SubscriptionService
from services.white_internet_service import WhiteInternetService
from utils.datetime_helpers import now_utc

SchemaT = TypeVar("SchemaT", bound=BaseModel)
logger = logging.getLogger(__name__)


def _parse_server_id(request: web.Request) -> tuple[int | None, web.Response | None]:
    raw_id = request.match_info.get("server_id", "")
    if not raw_id.isdigit():
        return None, web.json_response({"ok": False, "error": "Invalid node id"}, status=400)
    server_id = int(raw_id)
    if server_id < 1 or server_id > 2_147_483_647:
        return None, web.json_response({"ok": False, "error": "Invalid node id"}, status=400)
    return server_id, None


async def _parse_json_model(
    request: web.Request,
    model_cls: type[SchemaT],
) -> tuple[SchemaT | None, web.Response | None]:
    try:
        raw_body = await request.json()
    except Exception:
        return None, web.json_response({"ok": False, "error": "Invalid JSON body"}, status=400)

    if not isinstance(raw_body, dict):
        return None, web.json_response({"ok": False, "error": "JSON object expected"}, status=400)

    try:
        return model_cls.model_validate(raw_body), None
    except ValidationError as exc:
        first_err = exc.errors()[0] if exc.errors() else {}
        loc = ".".join(str(part) for part in first_err.get("loc", ()))
        msg = first_err.get("msg", "Validation error")
        detail = f"{loc}: {msg}" if loc else msg
        return None, web.json_response({"ok": False, "error": detail}, status=400)


def _service_error_response(exc: AdminNodeServiceError) -> web.Response:
    return web.json_response({"ok": False, "error": exc.message}, status=exc.status_code)


async def api_me_handler(request: web.Request) -> web.Response:
    """Return authenticated Telegram Mini App user role and real 2FA status."""
    ctx, err_resp = require_authenticated_user(request)
    if err_resp is not None or ctx is None:
        return err_resp  # type: ignore[return-value]

    display_name = " ".join(part for part in (ctx.first_name, ctx.last_name) if part).strip()
    if ctx.is_admin:
        session_token = request.headers.get("X-Admin-Session")
        session_valid = verify_admin_session_token(session_token, ctx.telegram_id)
        async with session_scope() as session:
            cfg = await get_admin_2fa_config(session, ctx.telegram_id)
        security_payload = {
            "two_factor_required": True,
            "two_factor_verified": session_valid,
            "two_factor_configured": cfg["configured"],
            "two_factor_mode": cfg["mode"],
        }
    else:
        security_payload = {
            "two_factor_required": False,
            "two_factor_verified": True,
        }

    return web.json_response({
        "ok": True,
        "user": {
            "telegram_id": ctx.telegram_id,
            "name": display_name or (f"@{ctx.username}" if ctx.username else str(ctx.telegram_id)),
            "username": ctx.username,
            "is_admin": ctx.is_admin,
        },
        "security": security_payload,
    })


async def api_admin_2fa_setup_handler(request: web.Request) -> web.Response:
    """Generate a temporary TOTP secret & otpauth URI for initial admin 2FA setup."""
    ctx, err_resp = require_admin_user(request, require_2fa=False)
    if err_resp is not None or ctx is None:
        return err_resp  # type: ignore[return-value]

    async with session_scope() as session:
        cfg = await get_admin_2fa_config(session, ctx.telegram_id)
        if cfg["configured"]:
            return web.json_response(
                {"ok": False, "error": "2FA уже настроена"},
                status=409,
            )

    secret = generate_totp_secret()
    account = f"@{ctx.username}" if ctx.username else str(ctx.telegram_id)
    setup_token = issue_totp_setup_token(ctx.telegram_id, secret)
    return web.json_response({
        "ok": True,
        "secret": secret,
        "otpauth_uri": build_totp_uri(secret, account),
        "setup_token": setup_token,
    })


async def api_admin_2fa_verify_handler(request: web.Request) -> web.Response:
    """Verify TOTP code and issue signed X-Admin-Session token."""
    ctx, err_resp = require_admin_user(request, require_2fa=False)
    if err_resp is not None or ctx is None:
        return err_resp  # type: ignore[return-value]

    payload, parse_err = await _parse_json_model(request, Verify2FAPayload)
    if parse_err is not None or payload is None:
        return parse_err  # type: ignore[return-value]

    async with session_scope() as session:
        result = await verify_admin_2fa(
            session,
            ctx.telegram_id,
            payload.code,
            setup_token=payload.setup_token,
        )

    if not result.get("verified"):
        status = 429 if result.get("locked") else 403
        return web.json_response({"ok": False, "error": result.get("error", "Неверный код")}, status=status)
    return web.json_response({"ok": True, **result})


async def api_admin_nodes_list_handler(request: web.Request) -> web.Response:
    ctx, err_resp = require_admin_user(request)
    if err_resp is not None or ctx is None:
        return err_resp  # type: ignore[return-value]

    async with session_scope() as session:
        nodes = await admin_nodes_service.list_nodes(session)
        return web.json_response({"ok": True, "nodes": nodes})


async def api_admin_nodes_add_handler(request: web.Request) -> web.Response:
    ctx, err_resp = require_admin_user(request)
    if err_resp is not None or ctx is None:
        return err_resp  # type: ignore[return-value]

    payload, parse_err = await _parse_json_model(request, AddNodePayload)
    if parse_err is not None or payload is None:
        return parse_err  # type: ignore[return-value]

    try:
        async with session_scope() as session:
            result = await admin_nodes_service.discover_and_add_node(
                session,
                admin_id=ctx.telegram_id,
                api_url=payload.api_url,
                api_key=payload.api_key,
                name=payload.name,
                country_flag=payload.country_flag,
            )
            return web.json_response({"ok": True, **result}, status=201)
    except AdminNodeServiceError as exc:
        return _service_error_response(exc)


async def api_admin_nodes_refresh_handler(request: web.Request) -> web.Response:
    ctx, err_resp = require_admin_user(request)
    if err_resp is not None or ctx is None:
        return err_resp  # type: ignore[return-value]

    server_id, id_err = _parse_server_id(request)
    if id_err is not None or server_id is None:
        return id_err  # type: ignore[return-value]

    try:
        async with session_scope() as session:
            node = await admin_nodes_service.refresh_node(
                session,
                admin_id=ctx.telegram_id,
                server_id=server_id,
            )
            return web.json_response({"ok": True, "node": node})
    except AdminNodeServiceError as exc:
        return _service_error_response(exc)


async def api_admin_nodes_patch_handler(request: web.Request) -> web.Response:
    ctx, err_resp = require_admin_user(request)
    if err_resp is not None or ctx is None:
        return err_resp  # type: ignore[return-value]

    server_id, id_err = _parse_server_id(request)
    if id_err is not None or server_id is None:
        return id_err  # type: ignore[return-value]

    payload, parse_err = await _parse_json_model(request, UpdateNodePayload)
    if parse_err is not None or payload is None:
        return parse_err  # type: ignore[return-value]

    try:
        async with session_scope() as session:
            node = await admin_nodes_service.update_node_settings(
                session,
                admin_id=ctx.telegram_id,
                server_id=server_id,
                name=payload.name,
                country_flag=payload.country_flag,
                api_url=payload.api_url,
                api_key=payload.api_key,
                in_standard=payload.in_standard,
                in_white_internet=payload.in_white_internet,
                std_sni=payload.std_sni,
                vless_name=payload.vless_name,
                server_description=payload.server_description,
                wi_yandex_name=payload.wi_yandex_name,
                wi_yandex_badge=payload.wi_yandex_badge,
                wi_turboflare_name=payload.wi_turboflare_name,
                wi_turboflare_badge=payload.wi_turboflare_badge,
            )
            return web.json_response({"ok": True, "node": node})
    except AdminNodeServiceError as exc:
        return _service_error_response(exc)


async def api_admin_nodes_toggle_handler(request: web.Request) -> web.Response:
    ctx, err_resp = require_admin_user(request)
    if err_resp is not None or ctx is None:
        return err_resp  # type: ignore[return-value]

    server_id, id_err = _parse_server_id(request)
    if id_err is not None or server_id is None:
        return id_err  # type: ignore[return-value]

    raw_text = await request.text()
    target_active: bool | None = None
    if raw_text.strip():
        payload, parse_err = await _parse_json_model(request, ToggleNodePayload)
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
        return _service_error_response(exc)


async def api_admin_nodes_delete_handler(request: web.Request) -> web.Response:
    ctx, err_resp = require_admin_user(request)
    if err_resp is not None or ctx is None:
        return err_resp  # type: ignore[return-value]

    server_id, id_err = _parse_server_id(request)
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
        return _service_error_response(exc)


def _as_bool_setting(val: object, default: bool) -> bool:
    if val is None:
        return default
    if isinstance(val, bool):
        return val
    if isinstance(val, str):
        return val.strip().lower() in ("1", "true", "yes", "on")
    return bool(val)


async def api_admin_incy_get_handler(request: web.Request) -> web.Response:
    ctx, err_resp = require_admin_user(request)
    if err_resp is not None or ctx is None:
        return err_resp  # type: ignore[return-value]

    default_interval = os.getenv("VLESS_PROFILE_UPDATE_INTERVAL", "6").strip()
    if not (default_interval.isdigit() and int(default_interval) > 0):
        default_interval = "6"

    async with session_scope() as session:
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
        sort_order = await get_system_setting(session, "vless_sort_order", "none")
        hide_url_raw = await get_system_setting(session, "vless_hide_url", "true")
        hide_check_raw = await get_system_setting(session, "vless_hide_check", "false")
        frag_raw = await get_system_setting(session, "vless_fragmentation_enabled", "true")
        frag_packets = await get_system_setting(session, "vless_fragmentation_packets", "tlshello")
        frag_length = await get_system_setting(session, "vless_fragmentation_length", "10-30")
        frag_interval = await get_system_setting(session, "vless_fragmentation_interval", "10-30")
        no_limit_mode = await get_system_setting(session, "vless_no_limit_mode", "all")
        dns_resolve_raw = await get_system_setting(session, "vless_dns_resolve_enabled", "false")
        dns_resolve_domain = await get_system_setting(session, "vless_dns_resolve_domain", "")
        dns_resolve_ip = await get_system_setting(session, "vless_dns_resolve_ip", "")
        per_app_raw = await get_system_setting(session, "vless_per_app_proxy_enabled", "false")
        per_app_mode = await get_system_setting(session, "vless_per_app_proxy_mode", "bypass")
        per_app_list = await get_system_setting(session, "vless_per_app_proxy_list", "")
        cdn_label_mode = await get_system_setting(session, "bi_cdn_label_mode", "name")
        cdn_yandex_label = await get_system_setting(session, "bi_cdn_yandex_label", "Yandex")
        cdn_turboflare_label = await get_system_setting(session, "bi_cdn_turboflare_label", "Turboflare")

    interval_val = int(interval_raw) if str(interval_raw or "").strip().isdigit() else int(default_interval)
    return web.json_response(
        {
            "ok": True,
            "settings": {
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
                "fragmentation_enabled": _as_bool_setting(frag_raw, True),
                "fragmentation_packets": str(frag_packets or "tlshello"),
                "fragmentation_length": str(frag_length or "10-30"),
                "fragmentation_interval": str(frag_interval or "10-30"),
                "no_limit_mode": str(no_limit_mode or "all"),
                "dns_resolve_enabled": _as_bool_setting(dns_resolve_raw, False),
                "dns_resolve_domain": str(dns_resolve_domain or ""),
                "dns_resolve_ip": str(dns_resolve_ip or ""),
                "per_app_proxy_enabled": _as_bool_setting(per_app_raw, False),
                "per_app_proxy_mode": str(per_app_mode or "bypass"),
                "per_app_proxy_list": str(per_app_list or ""),
                "cdn_label_mode": str(cdn_label_mode or "name"),
                "cdn_yandex_label": str(cdn_yandex_label or "Yandex"),
                "cdn_turboflare_label": str(cdn_turboflare_label or "Turboflare"),
            },
        }
    )


async def api_admin_incy_put_handler(request: web.Request) -> web.Response:
    ctx, err_resp = require_admin_user(request)
    if err_resp is not None or ctx is None:
        return err_resp  # type: ignore[return-value]

    payload, parse_err = await _parse_json_model(request, IncyGlobalSettingsPayload)
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
                await set_system_setting(session, setting_key, val, updated_by=ctx.telegram_id)
                changed[field_name] = bool(raw_bool)

        if changed:
            await AuditService.log_action(
                session,
                admin_id=ctx.telegram_id,
                action="INCY_SETTINGS_UPDATED",
                target_type="system",
                details=changed,
            )

    return await api_admin_incy_get_handler(request)


async def api_admin_users_handler(request: web.Request) -> web.Response:
    """Return user statistics and paginated user list with balances and subscriptions."""
    ctx, err_resp = require_admin_user(request)
    if err_resp is not None or ctx is None:
        return err_resp  # type: ignore[return-value]

    q = (request.query.get("q") or "").strip()
    flt = (request.query.get("filter") or "all").strip()
    try:
        limit = min(50, max(1, int(request.query.get("limit", "25"))))
    except ValueError:
        limit = 25
    try:
        offset = max(0, int(request.query.get("offset", "0")))
    except ValueError:
        offset = 0

    now = now_utc()

    async with session_scope() as session:
        filter_counts = await users_repo.get_user_filter_counts(session)
        total_users = filter_counts.get("all", 0)
        active_standard = filter_counts.get("active_standard", 0)
        active_wi = filter_counts.get("active_wi", 0)
        total_balance_rub = Decimal(
            await session.scalar(select(func.coalesce(func.sum(AccountLedgerEntry.amount), 0))) or 0
        )

        stmt = select(User).where(User.is_deleted.is_(False))
        if flt == "active_standard":
            stmt = stmt.where(User.subscription_end.is_not(None), User.subscription_end > now)
        elif flt == "active_wi":
            stmt = stmt.where(users_repo.get_effective_wi_active_condition(now))
        elif flt == "expired":
            stmt = stmt.where(users_repo.get_effective_expired_condition(now))
        elif flt == "never":
            stmt = stmt.where(users_repo.get_effective_never_condition())

        if q:
            if q.isdigit():
                stmt = stmt.where(
                    or_(
                        User.telegram_id == int(q),
                        User.id == int(q),
                    )
                )
            else:
                pattern = f"%{q.lstrip('@')}%"
                stmt = stmt.where(
                    or_(
                        User.username.ilike(pattern),
                        User.first_name.ilike(pattern),
                    )
                )

        total_matching = int(
            await session.scalar(select(func.count()).select_from(stmt.subquery())) or 0
        )

        stmt = stmt.order_by(User.created_at.desc(), User.id.desc()).limit(limit).offset(offset)
        users = (await session.scalars(stmt)).all()

        user_ids = [u.id for u in users]
        wi_map: dict[int, WhiteInternetSubscription] = {}
        if user_ids:
            wi_rows = (
                await session.scalars(
                    select(WhiteInternetSubscription).where(
                        WhiteInternetSubscription.user_id.in_(user_ids)
                    )
                )
            ).all()
            wi_map = {w.user_id: w for w in wi_rows}

        items: list[dict] = []
        for u in users:
            bal = await get_account_balance(session, user_id=u.id)
            wi = wi_map.get(u.id)
            std_active = bool(u.subscription_end and u.subscription_end > now)
            wi_active = bool(
                wi
                and wi.status in (
                    WhiteInternetStatus.ACTIVE.value,
                    WhiteInternetStatus.PENDING.value,
                    WhiteInternetStatus.EXHAUSTED.value,
                )
                and wi.expires_at
                and wi.expires_at > now
            )
            items.append({
                "id": u.id,
                "telegram_id": u.telegram_id,
                "username": u.username,
                "first_name": u.first_name,
                "real_balance": int(bal.real_available),
                "bonus_balance": int(bal.bonus_available),
                "total_balance": int(bal.available),
                "standard_active": std_active,
                "standard_end": u.subscription_end.isoformat() if u.subscription_end else None,
                "standard_device_limit": int(u.device_limit or 0),
                "wi_active": wi_active,
                "wi_end": wi.expires_at.isoformat() if wi and wi.expires_at else None,
                "wi_device_limit": int(wi.device_limit or 0) if wi else 0,
                "financial_hold": bool(getattr(u, "financial_hold", False)),
                "topup_blocked": bool(getattr(u, "topup_blocked", False)),
                "created_at": u.created_at.isoformat() if u.created_at else None,
            })

        return web.json_response({
            "ok": True,
            "stats": {
                "total_users": total_users,
                "active_standard": active_standard,
                "active_wi": active_wi,
                "total_balance_rub": int(max(Decimal(0), total_balance_rub)),
            },
            "filter_counts": filter_counts,
            "total": total_matching,
            "offset": offset,
            "limit": limit,
            "users": items,
        })


def _parse_user_id(request: web.Request) -> tuple[int | None, web.Response | None]:
    raw_id = request.match_info.get("user_id", "")
    if not raw_id.isdigit():
        return None, web.json_response({"ok": False, "error": "Invalid user id"}, status=400)
    user_id = int(raw_id)
    if user_id < 1 or user_id > 2_147_483_647:
        return None, web.json_response({"ok": False, "error": "Invalid user id"}, status=400)
    return user_id, None


async def api_admin_user_adjust_handler(request: web.Request) -> web.Response:
    """Admin action: adjust user balance or grant/extend Standard or White Internet access."""
    ctx, err_resp = require_admin_user(request)
    if err_resp is not None:
        return err_resp
    if ctx is None:
        return web.json_response({"ok": False, "error": "Unauthorized"}, status=401)

    target_user_id, id_err = _parse_user_id(request)
    if id_err is not None or target_user_id is None:
        return id_err  # type: ignore[return-value]

    payload, parse_err = await _parse_json_model(request, AdminUserAdjustPayload)
    if parse_err is not None or payload is None:
        return parse_err  # type: ignore[return-value]

    note = (payload.note or "").strip()

    async with session_scope() as session:
        user = await session.get(User, target_user_id)
        if user is None or getattr(user, "is_deleted", False):
            return web.json_response({"ok": False, "error": "Пользователь не найден"}, status=404)

        if payload.action == "balance":
            assert payload.amount_rub is not None
            if payload.amount_rub < 0:
                bal_before = await get_account_balance(session, user_id=user.id)
                if abs(payload.amount_rub) > bal_before.bonus_available:
                    return web.json_response(
                        {"ok": False, "error": "Недостаточно бонусных средств на балансе пользователя"},
                        status=400,
                    )
            default_desc = "Бонусное пополнение (админ)" if payload.amount_rub > 0 else "Списание администратором"
            await create_admin_adjustment(
                session,
                user_id=user.id,
                signed_amount=payload.amount_rub,
                idempotency_key=f"miniapp:admin:{ctx.telegram_id}:{user.id}:{uuid4().hex}",
                metadata={
                    "source": "miniapp_admin",
                    "admin_telegram_id": ctx.telegram_id,
                    "description": note or default_desc,
                },
            )
            await AuditService.log_action(
                session,
                admin_id=ctx.telegram_id,
                action=AdminAuditAction.ADMIN_BALANCE_TOPUP if payload.amount_rub >= 0 else AdminAuditAction.REDUCE,
                target_type="user",
                target_id=user.id,
                details={"amount_rub": payload.amount_rub, "note": note or default_desc},
            )
            bal_after = await get_account_balance(session, user_id=user.id)
            bot = request.app.get("bot")
            if bot is not None and payload.amount_rub != 0:
                try:
                    sign_word = "начислено" if payload.amount_rub > 0 else "списано"
                    msg_text = (
                        f"💳 Администратор изменил ваш бонусный баланс: "
                        f"{sign_word} {abs(payload.amount_rub)} ₽.\n"
                        f"Текущий баланс: {int(bal_after.available)} ₽."
                    )
                    if note:
                        msg_text += f"\nКомментарий: {note}"
                    await bot.send_message(user.telegram_id, msg_text)
                except Exception:
                    pass
            return web.json_response({
                "ok": True,
                "action": "balance",
                "real_balance": int(bal_after.real_available),
                "bonus_balance": int(bal_after.bonus_available),
                "total_balance": int(bal_after.available),
                "message": f"Баланс обновлён ({payload.amount_rub:+d} ₽)",
            })

        if payload.action == "grant_standard":
            if payload.days is None:
                return web.json_response({"ok": False, "error": "Укажите количество дней"}, status=400)
            await get_or_create_subscription(session, user.id)
            if payload.days < 0:
                locked_user = (
                    await session.execute(
                        select(User)
                        .where(User.id == user.id, User.is_deleted.is_(False))
                        .with_for_update()
                    )
                ).scalar_one_or_none()
                if locked_user is None:
                    return web.json_response({"ok": False, "error": "Пользователь не найден"}, status=404)
                now = now_utc()
                if not locked_user.subscription_end or locked_user.subscription_end <= now:
                    return web.json_response(
                        {"ok": False, "error": "У пользователя нет активной подписки «Стандарт»"},
                        status=400,
                    )
                new_end = locked_user.subscription_end - timedelta(days=abs(payload.days))
                if new_end <= now:
                    locked_user.subscription_end = now
                else:
                    locked_user.subscription_end = new_end
                if payload.device_limit is not None:
                    locked_user.device_limit = payload.device_limit
                await session.flush()
                await AuditService.log_action(
                    session,
                    admin_id=ctx.telegram_id,
                    action=AdminAuditAction.REDUCE,
                    target_type="user",
                    target_id=user.id,
                    details={"service": "standard", "days": payload.days, "note": note},
                )
                if new_end <= now:
                    vless_sub = await vless_subscription_repo.get_subscription_by_user_id(
                        session, user.id
                    )
                    if vless_sub is not None:
                        vless_sub.is_active = False
                        vless_sub.version = (getattr(vless_sub, "version", 1) or 1) + 1
                        await session.flush()
                        VlessSubscriptionService.ensure_synced_background(
                            user.id, is_active=False, session=session
                        )
                return web.json_response({
                    "ok": True,
                    "action": "grant_standard",
                    "standard_end": locked_user.subscription_end.isoformat() if locked_user.subscription_end else None,
                    "standard_device_limit": int(locked_user.device_limit or 0),
                    "message": f"Подписка «Стандарт» сокращена на {abs(payload.days)} дн.",
                })

            target_limit = payload.device_limit or (
                user.device_limit if (user.device_limit or 0) >= 2 else 2
            )
            try:
                updated_user = await SubscriptionService.extend_subscription(
                    session,
                    user.telegram_id,
                    days=payload.days,
                    new_device_limit=target_limit,
                    admin_id=ctx.telegram_id,
                    reason=note or "miniapp_admin_grant",
                )
            except ValueError as exc:
                return web.json_response({"ok": False, "error": str(exc)}, status=400)
            if updated_user is None:
                return web.json_response({"ok": False, "error": "Пользователь не найден"}, status=404)
            msg_done = (
                "Лимит устройств «Стандарт» обновлён"
                if payload.days == 0
                else f"Подписка «Стандарт» выдана/продлена на {payload.days} дн."
            )
            return web.json_response({
                "ok": True,
                "action": "grant_standard",
                "standard_end": updated_user.subscription_end.isoformat() if updated_user.subscription_end else None,
                "standard_device_limit": int(updated_user.device_limit or 0),
                "message": msg_done,
            })

        if payload.action == "wi_traffic":
            wi_sub = await get_subscription_by_user_id(session, user.id)
            if wi_sub is None:
                return web.json_response({"ok": False, "error": "Подписка «Белый интернет» не найдена"}, status=404)
            t_action = payload.traffic_action
            if t_action == "reset_used":
                try:
                    updated_sub = await white_internet_repo.reset_traffic_used_atomic(session, wi_sub.id)
                except white_internet_repo.WhiteInternetError as exc:
                    return web.json_response({"ok": False, "error": str(exc)}, status=400)
                await AuditService.log_action(
                    session,
                    admin_id=ctx.telegram_id,
                    action=AdminAuditAction.WHITE_INTERNET_TRAFFIC_RESET,
                    target_type="user",
                    target_id=user.id,
                    details={"subscription_id": wi_sub.id, "note": note},
                )
                return web.json_response({
                    "ok": True,
                    "action": "wi_traffic",
                    "traffic_used_bytes": int(updated_sub.traffic_used_bytes or 0),
                    "message": "Счётчик трафика «Белого интернета» сброшен",
                })
            if t_action in ("add_gb", "set_base_gb"):
                if payload.gb is None or payload.gb < 1:
                    return web.json_response({"ok": False, "error": "Укажите объём в ГБ (>= 1)"}, status=400)
                bytes_val = int(payload.gb) * 1024 * 1024 * 1024
                try:
                    if t_action == "add_gb":
                        updated_sub = await white_internet_repo.add_extra_traffic_atomic(
                            session, wi_sub.id, bytes_val
                        )
                        audit_act = AdminAuditAction.WHITE_INTERNET_TRAFFIC_ADDED
                        msg_ok = f"Добавлено +{payload.gb} ГБ трафика"
                    else:
                        updated_sub = await white_internet_repo.set_base_traffic_quota_atomic(
                            session, wi_sub.id, bytes_val
                        )
                        audit_act = AdminAuditAction.WHITE_INTERNET_QUOTA_SET
                        msg_ok = f"Базовая квота установлена: {payload.gb} ГБ"
                except white_internet_repo.WhiteInternetError as exc:
                    return web.json_response({"ok": False, "error": str(exc)}, status=400)
                await AuditService.log_action(
                    session,
                    admin_id=ctx.telegram_id,
                    action=audit_act,
                    target_type="user",
                    target_id=user.id,
                    details={"subscription_id": wi_sub.id, "gb": payload.gb, "note": note},
                )
                return web.json_response({
                    "ok": True,
                    "action": "wi_traffic",
                    "base_traffic_bytes": int(updated_sub.base_traffic_bytes or 0),
                    "extra_traffic_bytes": int(updated_sub.extra_traffic_bytes or 0),
                    "message": msg_ok,
                })
            return web.json_response({"ok": False, "error": "Укажите traffic_action"}, status=400)

        if payload.action == "restrictions":
            locked_user = (
                await session.execute(
                    select(User)
                    .where(User.id == user.id, User.is_deleted.is_(False))
                    .with_for_update()
                )
            ).scalar_one_or_none()
            if locked_user is None:
                return web.json_response({"ok": False, "error": "Пользователь не найден"}, status=404)
            if payload.financial_hold is not None:
                locked_user.financial_hold = bool(payload.financial_hold)
            if payload.topup_blocked is not None:
                locked_user.topup_blocked = bool(payload.topup_blocked)
            await session.flush()
            await SubscriptionService.sync_access_state(session, locked_user)
            await AuditService.log_action(
                session,
                admin_id=ctx.telegram_id,
                action=(
                    AdminAuditAction.PAYMENT_DISPUTE_OPENED
                    if (locked_user.financial_hold or locked_user.topup_blocked)
                    else AdminAuditAction.PAYMENT_DISPUTE_RESOLVED
                ),
                target_type="user",
                target_id=user.id,
                details={
                    "financial_hold": bool(locked_user.financial_hold),
                    "topup_blocked": bool(locked_user.topup_blocked),
                    "note": note,
                },
            )
            return web.json_response({
                "ok": True,
                "action": "restrictions",
                "financial_hold": bool(locked_user.financial_hold),
                "topup_blocked": bool(locked_user.topup_blocked),
                "message": "Ограничения пользователя обновлены",
            })

        # grant_wi
        if payload.days is None:
            return web.json_response({"ok": False, "error": "Укажите количество дней"}, status=400)
        wi_sub = await get_subscription_by_user_id(session, user.id)
        if wi_sub is None:
            if payload.days <= 0:
                return web.json_response(
                    {"ok": False, "error": "Для новой активации укажите положительное число дней"},
                    status=400,
                )
            ok, msg, created = await WhiteInternetService.purchase_subscription(
                session,
                user.id,
                device_limit=payload.device_limit or 1,
            )
            if not ok:
                return web.json_response({"ok": False, "error": msg}, status=400)
            if payload.days > 30 and created is not None:
                ok_ext, msg_ext, created_ext = await WhiteInternetService.extend_subscription(
                    session, user.id, payload.days - 30
                )
                if not ok_ext:
                    return web.json_response({"ok": False, "error": msg_ext}, status=400)
                created = created_ext
            elif payload.days < 30 and created is not None:
                created.expires_at = now_utc() + timedelta(days=payload.days)
                await session.flush()
            await AuditService.log_action(
                session,
                admin_id=ctx.telegram_id,
                action=AdminAuditAction.WHITE_INTERNET_EXTEND,
                target_type="user",
                target_id=user.id,
                details={"days": payload.days, "device_limit": payload.device_limit or 1, "note": note},
            )
            end_iso = created.expires_at.isoformat() if created and created.expires_at else None
            return web.json_response({
                "ok": True,
                "action": "grant_wi",
                "wi_end": end_iso,
                "message": f"Подписка «Белый интернет» активирована на {payload.days} дн.",
            })

        if payload.device_limit is not None and payload.device_limit != wi_sub.device_limit:
            wi_sub.device_limit = payload.device_limit
            wi_sub.base_traffic_bytes = payload.device_limit * constants.WHITE_INTERNET_BASE_TRAFFIC_BYTES
            if (
                wi_sub.status == WhiteInternetStatus.EXHAUSTED
                and (wi_sub.base_traffic_bytes + (wi_sub.extra_traffic_bytes or 0))
                > (wi_sub.traffic_used_bytes or 0)
            ):
                wi_sub.status = WhiteInternetStatus.ACTIVE
            wi_sub.desired_version = (wi_sub.desired_version or 1) + 1
            await session.flush()
            await AuditService.log_action(
                session,
                admin_id=ctx.telegram_id,
                action=AdminAuditAction.WHITE_INTERNET_DEVLIMIT_SET,
                target_type="user",
                target_id=user.id,
                details={"device_limit": payload.device_limit, "note": note},
            )

        if payload.days == 0:
            end_iso = wi_sub.expires_at.isoformat() if wi_sub.expires_at else None
            return web.json_response({
                "ok": True,
                "action": "grant_wi",
                "wi_end": end_iso,
                "message": "Лимит устройств «Белый интернет» обновлён",
            })

        if payload.days < 0:
            locked_wi = await white_internet_repo.get_subscription_with_lock(session, wi_sub.id)
            if locked_wi is None:
                return web.json_response({"ok": False, "error": "Подписка не найдена"}, status=404)
            now = now_utc()
            if not locked_wi.expires_at or locked_wi.expires_at <= now:
                return web.json_response(
                    {"ok": False, "error": "Подписка «Белый интернет» уже истекла"},
                    status=400,
                )
            new_end = locked_wi.expires_at - timedelta(days=abs(payload.days))
            if new_end <= now:
                locked_wi.expires_at = now
                locked_wi.status = WhiteInternetStatus.EXPIRED
                locked_wi.provisioning_status = WhiteInternetProvisioningStatus.PENDING_DELETE
                locked_wi.desired_version = (locked_wi.desired_version or 1) + 1
            else:
                locked_wi.expires_at = new_end
            await session.flush()
            await AuditService.log_action(
                session,
                admin_id=ctx.telegram_id,
                action=AdminAuditAction.REDUCE,
                target_type="user",
                target_id=user.id,
                details={"service": "white_internet", "days": payload.days, "note": note},
            )
            return web.json_response({
                "ok": True,
                "action": "grant_wi",
                "wi_end": locked_wi.expires_at.isoformat() if locked_wi.expires_at else None,
                "message": f"Подписка «Белый интернет» сокращена на {abs(payload.days)} дн.",
            })

        ok, msg, updated = await WhiteInternetService.extend_subscription(
            session, user.id, payload.days
        )
        if not ok:
            return web.json_response({"ok": False, "error": msg}, status=400)
        await AuditService.log_action(
            session,
            admin_id=ctx.telegram_id,
            action=AdminAuditAction.WHITE_INTERNET_EXTEND,
            target_type="user",
            target_id=user.id,
            details={"days": payload.days, "note": note},
        )
        end_iso = updated.expires_at.isoformat() if updated and updated.expires_at else None
        return web.json_response({
            "ok": True,
            "action": "grant_wi",
            "wi_end": end_iso,
            "message": f"Подписка «Белый интернет» продлена на {payload.days} дн.",
        })


async def api_admin_user_details_handler(request: web.Request) -> web.Response:
    ctx, err_resp = require_admin_user(request)
    if err_resp is not None or ctx is None:
        return err_resp  # type: ignore[return-value]

    target_user_id, id_err = _parse_user_id(request)
    if id_err is not None or target_user_id is None:
        return id_err  # type: ignore[return-value]

    async with session_scope() as session:
        user = await session.get(User, target_user_id)
        if user is None or getattr(user, "is_deleted", False):
            return web.json_response({"ok": False, "error": "Пользователь не найден"}, status=404)

        bal = await get_account_balance(session, user_id=user.id)
        vless_sub = await vless_subscription_repo.get_subscription_by_user_id(session, user.id)
        wi_sub = await get_subscription_by_user_id(session, user.id)
        devices = await user_devices_repo.list_user_devices(
            session,
            user.id,
            wi_device_limit=int(wi_sub.device_limit or 0) if wi_sub else 0,
        )

        inviter = None
        if user.referred_by:
            ref_user = await session.scalar(select(User).where(User.telegram_id == user.referred_by))
            if ref_user is not None:
                inviter = {
                    "id": ref_user.id,
                    "telegram_id": ref_user.telegram_id,
                    "username": ref_user.username,
                    "first_name": ref_user.first_name,
                }

        sub_token = vless_sub.token if vless_sub and vless_sub.token else (wi_sub.token if wi_sub else None)
        return web.json_response({
            "ok": True,
            "user": {
                "id": user.id,
                "telegram_id": user.telegram_id,
                "username": user.username,
                "first_name": user.first_name,
                "created_at": user.created_at.isoformat() if user.created_at else None,
                "real_balance": int(bal.real_available),
                "bonus_balance": int(bal.bonus_available),
                "total_balance": int(bal.available),
                "standard_end": user.subscription_end.isoformat() if user.subscription_end else None,
                "standard_device_limit": int(user.device_limit or 0),
                "financial_hold": bool(getattr(user, "financial_hold", False)),
                "topup_blocked": bool(getattr(user, "topup_blocked", False)),
                "subscription_url": _build_sub_url(sub_token) if sub_token else None,
                "subscription_backup_url": _build_backup_sub_url(sub_token) if sub_token else None,
                "wi": (
                    {
                        "id": wi_sub.id,
                        "status": str(wi_sub.status),
                        "provisioning_status": str(wi_sub.provisioning_status),
                        "expires_at": wi_sub.expires_at.isoformat() if wi_sub.expires_at else None,
                        "device_limit": int(wi_sub.device_limit or 0),
                        "base_traffic_bytes": int(wi_sub.base_traffic_bytes or 0),
                        "extra_traffic_bytes": int(wi_sub.extra_traffic_bytes or 0),
                        "traffic_used_bytes": int(wi_sub.traffic_used_bytes or 0),
                    }
                    if wi_sub
                    else None
                ),
                "inviter": inviter,
                "devices": [
                    {
                        "hwid": d.get("hwid"),
                        "device_model": d.get("device_name"),
                        "platform": d.get("platform"),
                        "app_version": d.get("app_version"),
                        "is_bi_priority": bool(d.get("bi_priority", 0)),
                        "is_bi_assigned": bool(d.get("is_bi_assigned", False)),
                        "last_seen_at": d.get("last_seen_at"),
                    }
                    for d in devices
                ],
            },
        })


async def api_admin_user_devices_reset_handler(request: web.Request) -> web.Response:
    ctx, err_resp = require_admin_user(request)
    if err_resp is not None or ctx is None:
        return err_resp  # type: ignore[return-value]

    target_user_id, id_err = _parse_user_id(request)
    if id_err is not None or target_user_id is None:
        return id_err  # type: ignore[return-value]

    async with session_scope() as session:
        user = await session.get(User, target_user_id)
        if user is None or getattr(user, "is_deleted", False):
            return web.json_response({"ok": False, "error": "Пользователь не найден"}, status=404)

        vless_sub = await vless_subscription_repo.get_subscription_by_user_id(session, user.id)
        if vless_sub is not None:
            await vless_subscription_repo.reset_hwids(session, vless_sub.id)

        existing_devs = (await session.scalars(select(UserDevice).where(UserDevice.user_id == user.id))).all()
        dev_uuids = [d.device_uuid for d in existing_devs if getattr(d, "device_uuid", None)]
        if vless_sub is not None and dev_uuids:
            await vless_subscription_repo.add_pending_revocation_uuids(session, vless_sub.id, dev_uuids)

        await session.execute(
            delete(UserDevice).where(UserDevice.user_id == user.id)
        )
        await AuditService.log_action(
            session,
            admin_id=ctx.telegram_id,
            action=AdminAuditAction.VLESS_HWID_RESET,
            target_type="user",
            target_id=user.id,
        )

    if vless_sub is not None:
        VlessSubscriptionService.ensure_synced_background(user.id)
    return web.json_response({"ok": True, "message": "Привязки устройств сброшены"})


async def api_admin_user_token_rotate_handler(request: web.Request) -> web.Response:
    ctx, err_resp = require_admin_user(request)
    if err_resp is not None or ctx is None:
        return err_resp  # type: ignore[return-value]

    target_user_id, id_err = _parse_user_id(request)
    if id_err is not None or target_user_id is None:
        return id_err  # type: ignore[return-value]

    async with session_scope() as session:
        user = await session.get(User, target_user_id)
        if user is None or getattr(user, "is_deleted", False):
            return web.json_response({"ok": False, "error": "Пользователь не найден"}, status=404)

        vless_sub = await vless_subscription_repo.get_subscription_by_user_id(session, user.id)
        wi_sub = await get_subscription_by_user_id(session, user.id)
        new_token: str | None = None
        if vless_sub is not None:
            new_token, _old_u, _new_u = await vless_subscription_repo.rotate_token(
                session, vless_sub.id, reset_hwids=True
            )
        if wi_sub is not None:
            wi_token = await white_internet_repo.reset_subscription_token_atomic(
                session, wi_sub.id, new_token=new_token, reset_hwids=True
            )
            new_token = new_token or wi_token

        existing_devs = (await session.scalars(select(UserDevice).where(UserDevice.user_id == user.id))).all()
        dev_uuids = [d.device_uuid for d in existing_devs if getattr(d, "device_uuid", None)]
        if vless_sub is not None and dev_uuids:
            await vless_subscription_repo.add_pending_revocation_uuids(session, vless_sub.id, dev_uuids)

        await session.execute(
            delete(UserDevice).where(UserDevice.user_id == user.id)
        )
        await AuditService.log_action(
            session,
            admin_id=ctx.telegram_id,
            action=AdminAuditAction.VLESS_TOKEN_RESET,
            target_type="user",
            target_id=user.id,
        )

    if vless_sub is not None:
        VlessSubscriptionService.ensure_synced_background(user.id)
    return web.json_response({
        "ok": True,
        "subscription_url": _build_sub_url(new_token) if new_token else None,
        "subscription_backup_url": _build_backup_sub_url(new_token) if new_token else None,
        "message": "Токен подписки перевыпущен, старая ссылка отключена",
    })


def _parse_positive_int(request: web.Request, name: str) -> tuple[int | None, web.Response | None]:
    raw = request.match_info.get(name, "")
    if not raw.isdigit() or int(raw) <= 0:
        return None, web.json_response({"ok": False, "error": f"Invalid {name}"}, status=400)
    return int(raw), None


async def api_admin_user_message_handler(request: web.Request) -> web.Response:
    ctx, err_resp = require_admin_user(request)
    if err_resp is not None or ctx is None:
        return err_resp  # type: ignore[return-value]

    user_id, id_err = _parse_user_id(request)
    if id_err is not None or user_id is None:
        return id_err  # type: ignore[return-value]

    payload, parse_err = await _parse_json_model(request, AdminDirectMessagePayload)
    if parse_err is not None or payload is None:
        return parse_err  # type: ignore[return-value]

    try:
        res = await admin_tools_service.send_direct_message(
            request.app.get("bot"),
            admin_id=ctx.telegram_id,
            user_id=user_id,
            text=payload.text,
        )
    except LookupError as exc:
        return web.json_response({"ok": False, "error": str(exc)}, status=404)
    except ValueError as exc:
        return web.json_response({"ok": False, "error": str(exc)}, status=400)

    return web.json_response({"ok": True, **res, "message": "Сообщение отправлено"})


async def api_admin_broadcasts_list_handler(request: web.Request) -> web.Response:
    ctx, err_resp = require_admin_user(request)
    if err_resp is not None or ctx is None:
        return err_resp  # type: ignore[return-value]

    async with session_scope() as session:
        items = await broadcast_service.list_broadcasts(session, limit=25)
    return web.json_response({"ok": True, "broadcasts": items})


async def api_admin_broadcast_preview_handler(request: web.Request) -> web.Response:
    ctx, err_resp = require_admin_user(request)
    if err_resp is not None or ctx is None:
        return err_resp  # type: ignore[return-value]

    payload, parse_err = await _parse_json_model(request, BroadcastPreviewPayload)
    if parse_err is not None or payload is None:
        return parse_err  # type: ignore[return-value]

    try:
        async with session_scope() as session:
            reach = await broadcast_service.count_broadcast_audience(
                session,
                payload.target_audience,
                admin_id=ctx.telegram_id,
                include_bot_blocked=False,
            )
            total_with_blocked = await broadcast_service.count_broadcast_audience(
                session,
                payload.target_audience,
                admin_id=ctx.telegram_id,
                include_bot_blocked=True,
            )
            label = await broadcast_service.resolve_audience_label(
                session, payload.target_audience
            )
    except ValueError as exc:
        return web.json_response({"ok": False, "error": str(exc)}, status=400)

    return web.json_response({
        "ok": True,
        "target_audience": payload.target_audience,
        "label": label,
        "reachable_count": reach,
        "total_count": total_with_blocked,
    })


async def api_admin_broadcast_start_handler(request: web.Request) -> web.Response:
    ctx, err_resp = require_admin_user(request)
    if err_resp is not None or ctx is None:
        return err_resp  # type: ignore[return-value]

    payload, parse_err = await _parse_json_model(request, BroadcastStartPayload)
    if parse_err is not None or payload is None:
        return parse_err  # type: ignore[return-value]

    try:
        item = await broadcast_service.create_and_start_broadcast(
            request.app.get("bot"),
            admin_id=ctx.telegram_id,
            target_audience=payload.target_audience,
            text=payload.text,
            media_url=payload.media_url,
        )
    except ValueError as exc:
        return web.json_response({"ok": False, "error": str(exc)}, status=400)

    return web.json_response({"ok": True, "broadcast": item, "message": "Рассылка запущена"})


async def api_admin_broadcast_cancel_handler(request: web.Request) -> web.Response:
    ctx, err_resp = require_admin_user(request)
    if err_resp is not None or ctx is None:
        return err_resp  # type: ignore[return-value]

    broadcast_id, id_err = _parse_positive_int(request, "broadcast_id")
    if id_err is not None or broadcast_id is None:
        return id_err  # type: ignore[return-value]

    async with session_scope() as session:
        item = await broadcast_service.cancel_broadcast(session, broadcast_id, ctx.telegram_id)
    if item is None:
        return web.json_response({"ok": False, "error": "Рассылка не найдена"}, status=404)
    return web.json_response({"ok": True, "broadcast": item, "message": "Рассылка остановлена"})


async def api_admin_mass_bonus_handler(request: web.Request) -> web.Response:
    ctx, err_resp = require_admin_user(request)
    if err_resp is not None or ctx is None:
        return err_resp  # type: ignore[return-value]

    payload, parse_err = await _parse_json_model(request, MassBonusPayload)
    if parse_err is not None or payload is None:
        return parse_err  # type: ignore[return-value]

    try:
        res = await admin_tools_service.start_mass_bonus(
            request.app.get("bot"),
            admin_id=ctx.telegram_id,
            target_audience=payload.target_audience,
            amount_rub=payload.amount_rub,
            reason=payload.reason,
            notify_users=payload.notify_users,
        )
    except ValueError as exc:
        return web.json_response({"ok": False, "error": str(exc)}, status=400)

    return web.json_response({
        "ok": True,
        **res,
        "message": f"Начисление +{payload.amount_rub} ₽ запущено для {res['target_count']} польз.",
    })


async def api_admin_mass_extend_handler(request: web.Request) -> web.Response:
    ctx, err_resp = require_admin_user(request)
    if err_resp is not None or ctx is None:
        return err_resp  # type: ignore[return-value]

    payload, parse_err = await _parse_json_model(request, MassExtendPayload)
    if parse_err is not None or payload is None:
        return parse_err  # type: ignore[return-value]

    try:
        res = await admin_tools_service.start_mass_extend(
            request.app.get("bot"),
            admin_id=ctx.telegram_id,
            service_scope=payload.service_scope,
            target_audience=payload.target_audience,
            days=payload.days,
            reason=payload.reason,
            notify_users=payload.notify_users,
        )
    except ValueError as exc:
        return web.json_response({"ok": False, "error": str(exc)}, status=400)

    return web.json_response({
        "ok": True,
        **res,
        "message": f"Продление +{payload.days} дн. запущено для {res['target_count']} польз.",
    })


async def api_admin_finances_handler(request: web.Request) -> web.Response:
    ctx, err_resp = require_admin_user(request)
    if err_resp is not None or ctx is None:
        return err_resp  # type: ignore[return-value]

    kind = request.query.get("kind", "orders").strip()
    if kind not in ("orders", "subscriptions", "topups"):
        kind = "orders"
    status = request.query.get("status", "").strip()
    query_str = request.query.get("q", "").strip()
    offset = max(0, int(request.query.get("offset", "0") or 0) if request.query.get("offset", "0").isdigit() else 0)
    raw_limit = int(request.query.get("limit", "25") or 25) if request.query.get("limit", "25").isdigit() else 25
    limit = min(100, max(1, raw_limit))

    async with session_scope() as session:
        data = await admin_tools_service.list_finances(
            session,
            kind=kind,
            status=status,
            q=query_str,
            offset=offset,
            limit=limit,
        )
    return web.json_response({"ok": True, **data})


async def api_admin_audit_logs_handler(request: web.Request) -> web.Response:
    ctx, err_resp = require_admin_user(request)
    if err_resp is not None or ctx is None:
        return err_resp  # type: ignore[return-value]

    action = request.query.get("action", "").strip()
    query_str = request.query.get("q", "").strip()
    offset = max(0, int(request.query.get("offset", "0") or 0) if request.query.get("offset", "0").isdigit() else 0)
    raw_limit = int(request.query.get("limit", "40") or 40) if request.query.get("limit", "40").isdigit() else 40
    limit = min(100, max(1, raw_limit))

    async with session_scope() as session:
        data = await admin_tools_service.list_audit_logs(
            session,
            action=action,
            q=query_str,
            offset=offset,
            limit=limit,
        )
    return web.json_response({"ok": True, **data})


async def api_admin_payments_maintenance_get_handler(request: web.Request) -> web.Response:
    """Get status of payments maintenance mode."""
    ctx, err_resp = require_admin_user(request)
    if err_resp is not None or ctx is None:
        return err_resp  # type: ignore[return-value]

    async with session_scope() as session:
        val = await get_system_setting(session, "payments_maintenance_enabled", "false")
        enabled = str(val or "").strip().lower() in ("true", "1", "yes")

    return web.json_response({"ok": True, "enabled": enabled})


async def api_admin_payments_maintenance_post_handler(request: web.Request) -> web.Response:
    """Toggle payments maintenance mode."""
    ctx, err_resp = require_admin_user(request)
    if err_resp is not None or ctx is None:
        return err_resp  # type: ignore[return-value]

    payload, parse_err = await _parse_json_model(request, PaymentsMaintenancePayload)
    if parse_err is not None or payload is None:
        return parse_err  # type: ignore[return-value]

    new_val = "true" if payload.enabled else "false"
    async with session_scope() as session:
        await set_system_setting(
            session,
            key="payments_maintenance_enabled",
            value=new_val,
            updated_by=ctx.telegram_id,
        )
        await AuditService.log_action(
            session,
            admin_id=ctx.telegram_id,
            action=AdminAuditAction.TOGGLE_MAINTENANCE,
            target_type="system_setting",
            target_id=None,
            details={"key": "payments_maintenance_enabled", "enabled": payload.enabled},
        )

    logger.info("Payments maintenance mode toggled to %s by admin %s", payload.enabled, ctx.telegram_id)
    return web.json_response({"ok": True, "enabled": payload.enabled})

