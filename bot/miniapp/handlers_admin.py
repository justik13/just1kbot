"""Thin HTTP handlers for Mini App identity, admin 2FA, node management, and user stats."""

from __future__ import annotations

import asyncio
import html
import logging
import os
from datetime import timedelta
from decimal import Decimal
from typing import Any, TypeVar
from urllib.parse import unquote
from uuid import uuid4

from aiogram.exceptions import TelegramForbiddenError
from aiohttp import web
from pydantic import BaseModel, ValidationError
from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from bot.miniapp.auth import require_admin_user, require_authenticated_user
from bot.miniapp.handlers_user import _build_backup_sub_url, _build_sub_url
from bot.miniapp.schemas import (
    AddNodePayload,
    AdminDirectMessagePayload,
    AdminUserAdjustPayload,
    AdminUserOptionalNotifyPayload,
    BroadcastPreviewPayload,
    BroadcastStartPayload,
    IncyGlobalSettingsPayload,
    MassBonusPayload,
    MassExtendPayload,
    NodeLifecyclePayload,
    PaymentsMaintenancePayload,
    ToggleNodePayload,
    UpdateNodePayload,
    Verify2FAPayload,
)
from config import constants
from config.enums import (
    AdminAuditAction,
    ServerHealthState,
    WebhookInboxStatus,
    WhiteInternetProvisioningStatus,
    WhiteInternetStatus,
)
from database.connection import queue_post_commit_task, session_scope
from database.models import (
    AccountLedgerEntry,
    AuditLog,
    BroadcastProgress,
    DeviceRegistrationEvent,
    Order,
    User,
    UserDevice,
    VlessSubscription,
    WebhookInbox,
    WhiteInternetSubscription,
)
from database.repositories import servers_repo, user_devices_repo, users_repo, vless_subscription_repo, white_internet_repo
from database.repositories.account_ledger_repo import (
    create_admin_adjustment,
    forgive_real_debt,
    get_account_balance,
    get_account_balances_batch,
    get_account_history,
)
from database.repositories.system_settings_repo import get_system_setting, set_system_setting
from database.repositories.vless_subscription_repo import get_or_create_subscription
from database.repositories.white_internet_repo import get_subscription_by_user_id
from services import admin_nodes_service, admin_tools_service, broadcast_service
from services.admin_nodes_service import AdminNodeServiceError
from services.audit_service import AuditService
from services.miniapp_auth_service import (
    build_totp_uri,
    generate_totp_secret,
    get_admin_2fa_config,
    get_admin_session_epoch,
    issue_totp_setup_token,
    revoke_all_admin_sessions,
    verify_admin_2fa,
    verify_admin_session_token,
)
from services.order_service import OrderService, acquire_user_account_lock
from services.subscription import SubscriptionService
from services.vless_subscription_service import VlessSubscriptionService
from services.white_internet_service import WhiteInternetService
from services.workers import get_worker_health_snapshot
from utils.datetime_helpers import now_utc
from utils.telegram import safe_send_message

SchemaT = TypeVar("SchemaT", bound=BaseModel)
logger = logging.getLogger(__name__)
_background_tasks: set[asyncio.Task[object]] = set()
_MAX_INT32 = 2_147_483_647
_MAX_INT64 = 9_223_372_036_854_775_807


def _parse_positive_int(
    request: web.Request,
    name: str,
    *,
    label: str | None = None,
    max_value: int = _MAX_INT32,
) -> tuple[int | None, web.Response | None]:
    raw_id = request.match_info.get(name, "")
    err_label = label or name
    if not raw_id.isdigit():
        return None, web.json_response({"ok": False, "error": f"Invalid {err_label}"}, status=400)
    val = int(raw_id)
    if val < 1 or val > max_value:
        return None, web.json_response({"ok": False, "error": f"Invalid {err_label}"}, status=400)
    return val, None


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


async def _queue_wi_inline_sync(
    session: AsyncSession,
    wi_sub: WhiteInternetSubscription,
    reason: str,
) -> None:
    """Queue post-commit inline sync to WI hub nodes when provisioning state requires it."""
    if (
        wi_sub.status not in (WhiteInternetStatus.ACTIVE, WhiteInternetStatus.PENDING)
        or wi_sub.provisioning_status
        not in (
            WhiteInternetProvisioningStatus.PENDING_CREATE,
            WhiteInternetProvisioningStatus.PENDING_UPDATE,
        )
    ):
        return
    hubs = await WhiteInternetService.get_hub_nodes(session)
    target_hubs = [
        (h.id, h.api_url, h.api_key, h.xray_instance_epoch)
        for h in hubs
        if h.api_url and h.api_key
    ]
    if not target_hubs:
        return

    sub_id_snap = wi_sub.id
    ver_snap = wi_sub.desired_version

    async def _sync_wi_after_admin_op() -> None:
        async with session_scope() as bg_sess:
            bg_sub = await white_internet_repo.get_subscription_by_id(bg_sess, sub_id_snap)
            if (
                bg_sub is not None
                and bg_sub.status in (WhiteInternetStatus.ACTIVE, WhiteInternetStatus.PENDING)
                and bg_sub.provisioning_status
                in (
                    WhiteInternetProvisioningStatus.PENDING_CREATE,
                    WhiteInternetProvisioningStatus.PENDING_UPDATE,
                )
            ):
                await WhiteInternetService._try_inline_sync_multi(
                    bg_sess,
                    bg_sub,
                    target_hubs,
                    idempotency_key=f"admin_{reason}:{sub_id_snap}:{ver_snap}",
                )

    queue_post_commit_task(session, _sync_wi_after_admin_op)


async def _queue_wi_revoke_uuids(
    session: AsyncSession,
    wi_sub: WhiteInternetSubscription,
    uuids: list[str] | tuple[str, ...],
    *,
    context: str,
) -> None:
    """Queue post-commit deprovisioning of specific UUIDs on all WI revocation ru_hubs."""
    clean_uuids = list(dict.fromkeys(str(u) for u in uuids if u))
    if not clean_uuids:
        return
    wi_hubs = await servers_repo.get_wi_revocation_ru_hubs(session)
    if not wi_hubs:
        return
    from services import white_internet_service

    version = wi_sub.desired_version
    for hub in wi_hubs:
        for rev_u in clean_uuids:
            white_internet_service._dispatch_deprovision(
                hub,
                rev_u,
                version,
                session=session,
                context=context,
            )


async def _queue_wi_deprovision_all(
    session: AsyncSession,
    user_id: int,
    wi_sub: WhiteInternetSubscription,
    *,
    context: str,
) -> None:
    """Queue post-commit deprovisioning of subscription and all bound device UUIDs on WI hubs."""
    dev_rows = (
        await session.scalars(
            select(UserDevice.device_uuid).where(
                UserDevice.user_id == user_id,
                UserDevice.device_uuid.is_not(None),
            )
        )
    ).all()
    uuids_to_revoke = [wi_sub.uuid, *[str(u) for u in dev_rows if u]]
    await _queue_wi_revoke_uuids(session, wi_sub, uuids_to_revoke, context=context)


def _queue_user_notification(
    session: AsyncSession,
    bot: Any,
    *,
    user_id: int,
    telegram_id: int,
    text: str,
) -> None:
    """Queue a post-commit Telegram DM using safe_send_message, marking is_bot_blocked on 403."""
    if bot is None or not text:
        return

    async def _send() -> None:
        try:
            await safe_send_message(bot, telegram_id, text, raise_forbidden=True)
        except TelegramForbiddenError:
            try:
                async with session_scope() as bg_sess:
                    await bg_sess.execute(
                        update(User).where(User.id == user_id).values(is_bot_blocked=True)
                    )
            except Exception:
                pass
        except Exception:
            pass

    queue_post_commit_task(session, _send)


def _safe_admin_error_response(
    exc: Exception,
    *,
    status: int = 400,
    default_msg: str = "Не удалось выполнить операцию",
) -> web.Response:
    """Return safe error JSON exposing only domain/validation errors."""
    if isinstance(exc, AdminNodeServiceError):
        return web.json_response({"ok": False, "error": exc.message}, status=exc.status_code)
    if isinstance(exc, (white_internet_repo.WhiteInternetError, ValueError, LookupError)):
        msg = str(exc).strip() or default_msg
        return web.json_response({"ok": False, "error": msg}, status=status)
    logger.warning("Unexpected admin handler error (%s): %s", type(exc).__name__, exc)
    return web.json_response({"ok": False, "error": default_msg}, status=status)


async def api_me_handler(request: web.Request) -> web.Response:
    """Return authenticated Telegram Mini App user role and real 2FA status."""
    ctx, err_resp = require_authenticated_user(request)
    if err_resp is not None or ctx is None:
        return err_resp  # type: ignore[return-value]

    display_name = " ".join(part for part in (ctx.first_name, ctx.last_name) if part).strip()
    if ctx.is_admin:
        session_token = request.headers.get("X-Admin-Session")
        async with session_scope() as session:
            expected_epoch = await get_admin_session_epoch(session)
            session_valid = verify_admin_session_token(
                session_token,
                ctx.telegram_id,
                expected_epoch=expected_epoch,
            )
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
            await AuditService.log_action(
                session,
                admin_id=ctx.telegram_id,
                action=(
                    AdminAuditAction.ADMIN_2FA_LOCKED
                    if result.get("locked")
                    else AdminAuditAction.ADMIN_2FA_VERIFY_FAILED
                ),
                target_type="admin",
                target_id=ctx.telegram_id,
                details={
                    "locked": bool(result.get("locked")),
                    "newly_locked": bool(result.get("newly_locked")),
                    "retry_after": result.get("retry_after"),
                },
            )

    if not result.get("verified"):
        if result.get("newly_locked"):
            bot = request.app.get("bot")
            if bot is not None:
                from config.settings import get_settings

                admin_ids = list(get_settings().ADMIN_IDS)
                retry_after = result.get("retry_after", 300)
                alert = (
                    "⚠️ <b>Блокировка 2FA в Mini App</b>\n"
                    f"Администратор ID: <code>{ctx.telegram_id}</code>\n"
                    f"Причина: превышен лимит неверных кодов (блокировка на {retry_after} сек)."
                )

                async def _send_lockout_alerts() -> None:
                    for aid in admin_ids:
                        try:
                            await safe_send_message(bot, aid, alert, parse_mode="HTML")
                        except Exception as exc:
                            logger.warning(
                                "Failed to send 2FA lockout alert to admin %s: %s",
                                aid,
                                exc,
                            )

                task = asyncio.create_task(_send_lockout_alerts())
                _background_tasks.add(task)
                task.add_done_callback(_background_tasks.discard)
        if result.get("locked"):
            retry_after = int(result.get("retry_after") or 300)
            return web.json_response(
                {
                    "ok": False,
                    "locked": True,
                    "retry_after": retry_after,
                    "error": result.get("error", "Слишком много попыток"),
                },
                status=429,
                headers={"Retry-After": str(retry_after)},
            )
        return web.json_response({"ok": False, "error": result.get("error", "Неверный код")}, status=403)
    return web.json_response({"ok": True, **result})


async def api_admin_2fa_logout_handler(request: web.Request) -> web.Response:
    """Revoke all active admin 2FA sessions and write audit log."""
    ctx, err_resp = require_admin_user(request)
    if err_resp is not None or ctx is None:
        return err_resp  # type: ignore[return-value]

    async with session_scope() as session:
        await revoke_all_admin_sessions(session, admin_telegram_id=ctx.telegram_id)
        await AuditService.log_action(
            session,
            admin_id=ctx.telegram_id,
            action=AdminAuditAction.ADMIN_2FA_LOGOUT_ALL,
            target_type="admin",
            target_id=ctx.telegram_id,
        )
    return web.json_response({"ok": True, "message": "Все сессии администратора завершены"})


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
        result = await admin_nodes_service.discover_and_add_node(
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

    server_id, id_err = _parse_positive_int(request, "server_id", label="node id")
    if id_err is not None or server_id is None:
        return id_err  # type: ignore[return-value]

    try:
        node = await admin_nodes_service.refresh_node(
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

    server_id, id_err = _parse_positive_int(request, "server_id", label="node id")
    if id_err is not None or server_id is None:
        return id_err  # type: ignore[return-value]

    payload, parse_err = await _parse_json_model(request, UpdateNodePayload)
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
        return _service_error_response(exc)


async def api_admin_nodes_toggle_handler(request: web.Request) -> web.Response:
    ctx, err_resp = require_admin_user(request)
    if err_resp is not None or ctx is None:
        return err_resp  # type: ignore[return-value]

    server_id, id_err = _parse_positive_int(request, "server_id", label="node id")
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

    server_id, id_err = _parse_positive_int(request, "server_id", label="node id")
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
    sort_order = await get_system_setting(session, "vless_sort_order", "none")
    hide_url_raw = await get_system_setting(session, "vless_hide_url", "true")
    hide_check_raw = await get_system_setting(session, "vless_hide_check", "false")
    frag_raw = await get_system_setting(session, "vless_fragmentation_enabled", "true")
    frag_packets = await get_system_setting(session, "vless_fragmentation_packets", "tlshello")
    frag_length = await get_system_setting(session, "vless_fragmentation_length", "10-30")
    frag_interval = await get_system_setting(session, "vless_fragmentation_interval", "10-30")
    no_limit_mode = await get_system_setting(session, "vless_no_limit_mode", "all")
    dns_resolve_raw = await get_system_setting(session, "vless_dns_resolve_enabled", "true")
    dns_resolve_domain = await get_system_setting(session, "vless_dns_resolve_domain", "https://common.dot.dns.yandex.net/dns-query")
    dns_resolve_ip = await get_system_setting(session, "vless_dns_resolve_ip", "77.88.8.8")
    per_app_raw = await get_system_setting(session, "vless_per_app_proxy_enabled", "false")
    per_app_mode = await get_system_setting(session, "vless_per_app_proxy_mode", "bypass")
    per_app_list = await get_system_setting(session, "vless_per_app_proxy_list", "")
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
        "sort_order": str(sort_order or "none"),
        "hide_url": _as_bool_setting(hide_url_raw, True),
        "hide_check": _as_bool_setting(hide_check_raw, False),
        "fragmentation_enabled": _as_bool_setting(frag_raw, True),
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
    ctx, err_resp = require_admin_user(request)
    if err_resp is not None or ctx is None:
        return err_resp  # type: ignore[return-value]

    async with session_scope() as session:
        settings_payload = await _read_incy_settings_dict(session)
    return web.json_response({"ok": True, "settings": settings_payload})


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
                action=AdminAuditAction.INCY_SETTINGS_UPDATED,
                target_type="system",
                details=changed,
            )

        settings_payload = await _read_incy_settings_dict(session)

    return web.json_response({"ok": True, "settings": settings_payload})


async def api_admin_users_handler(request: web.Request) -> web.Response:
    """Return user statistics and paginated user list with balances and subscriptions."""
    ctx, err_resp = require_admin_user(request)
    if err_resp is not None or ctx is None:
        return err_resp  # type: ignore[return-value]

    q = (request.query.get("q") or "").strip()
    flt = (request.query.get("filter") or "all").strip()
    try:
        limit = min(50, max(1, int(request.query.get("limit", "10"))))
    except ValueError:
        limit = 10
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
            await session.scalar(
                select(func.coalesce(func.sum(AccountLedgerEntry.amount), 0))
                .join(User, User.id == AccountLedgerEntry.user_id)
                .where(User.is_deleted.is_(False))
            )
            or 0
        )

        stmt = select(User).where(User.is_deleted.is_(False))
        if flt == "active":
            stmt = stmt.where(users_repo.get_effective_active_condition(now))
        elif flt == "active_standard":
            stmt = stmt.where(
                User.id.in_(
                    select(VlessSubscription.user_id).where(VlessSubscription.expires_at > now)
                )
            )
        elif flt == "active_wi":
            stmt = stmt.where(users_repo.get_effective_wi_active_condition(now))
        elif flt == "expiring_3d":
            stmt = stmt.where(users_repo.get_effective_expiring_3d_condition(now))
        elif flt == "new_7d":
            stmt = stmt.where(User.created_at >= now - timedelta(days=7))
        elif flt == "expired":
            stmt = stmt.where(users_repo.get_effective_expired_condition(now))
        elif flt == "never":
            stmt = stmt.where(users_repo.get_effective_never_condition())
        elif flt == "banned":
            stmt = stmt.where(User.is_bot_blocked.is_(True))

        if q:
            if q.isdigit() and int(q) <= _MAX_INT64:
                q_num = int(q)
                id_conds = [User.telegram_id == q_num]
                if 0 < q_num <= _MAX_INT32:
                    id_conds.append(User.id == q_num)
                stmt = stmt.where(or_(*id_conds))
            else:
                pattern = f"%{q.lstrip('@')}%"
                stmt = stmt.where(
                    or_(
                        User.username.ilike(pattern),
                        User.first_name.ilike(pattern),
                        User.id.in_(
                            select(UserDevice.user_id).where(UserDevice.hwid.ilike(pattern))
                        ),
                        User.id.in_(
                            select(VlessSubscription.user_id).where(
                                or_(
                                    VlessSubscription.uuid.ilike(pattern),
                                    VlessSubscription.token.ilike(pattern),
                                )
                            )
                        ),
                        User.id.in_(
                            select(WhiteInternetSubscription.user_id).where(
                                or_(
                                    WhiteInternetSubscription.uuid.ilike(pattern),
                                    WhiteInternetSubscription.token.ilike(pattern),
                                )
                            )
                        ),
                    )
                )

        total_matching = int(
            await session.scalar(select(func.count()).select_from(stmt.subquery())) or 0
        )

        stmt = stmt.order_by(User.created_at.desc(), User.id.desc()).limit(limit).offset(offset)
        users = (await session.scalars(stmt)).all()

        user_ids = [u.id for u in users]
        wi_map: dict[int, WhiteInternetSubscription] = {}
        vless_map: dict[int, VlessSubscription] = {}
        if user_ids:
            wi_rows = (
                await session.scalars(
                    select(WhiteInternetSubscription)
                    .where(WhiteInternetSubscription.user_id.in_(user_ids))
                    .order_by(WhiteInternetSubscription.id.asc())
                )
            ).all()
            wi_map = {w.user_id: w for w in wi_rows}
            vless_rows = (
                await session.scalars(
                    select(VlessSubscription).where(
                        VlessSubscription.user_id.in_(user_ids)
                    )
                )
            ).all()
            vless_map = {v.user_id: v for v in vless_rows}

        balances_map = await get_account_balances_batch(session, user_ids)
        items: list[dict] = []
        for u in users:
            bal = balances_map[u.id]
            wi = wi_map.get(u.id)
            vless = vless_map.get(u.id)
            std_end = vless.expires_at if vless else None
            std_limit = int(vless.device_limit or 0) if vless else 0
            std_active = bool(std_end and std_end > now)
            wi_active = bool(
                wi
                and wi.status in (
                    WhiteInternetStatus.ACTIVE,
                    WhiteInternetStatus.PENDING,
                    WhiteInternetStatus.EXHAUSTED,
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
                "standard_end": std_end.isoformat() if std_end else None,
                "standard_device_limit": std_limit,
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


async def api_admin_user_adjust_handler(request: web.Request) -> web.Response:
    """Admin action: adjust user balance or grant/extend Standard or White Internet access."""
    ctx, err_resp = require_admin_user(request)
    if err_resp is not None:
        return err_resp
    if ctx is None:
        return web.json_response({"ok": False, "error": "Unauthorized"}, status=401)

    target_user_id, id_err = _parse_positive_int(request, "user_id", label="user id")
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
            await acquire_user_account_lock(session, user.id)
            bal_before = await get_account_balance(session, user_id=user.id, for_update=True)
            if payload.amount_rub < 0 and abs(payload.amount_rub) > bal_before.bonus_available:
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
            bal_after = await get_account_balance(session, user_id=user.id)
            await AuditService.log_action(
                session,
                admin_id=ctx.telegram_id,
                action=(
                    AdminAuditAction.ADMIN_BALANCE_TOPUP
                    if payload.amount_rub >= 0
                    else AdminAuditAction.ADMIN_BALANCE_DEDUCT
                ),
                target_type="user",
                target_id=user.id,
                details={
                    "amount_rub": payload.amount_rub,
                    "before": {
                        "real_balance": int(bal_before.real_available),
                        "bonus_balance": int(bal_before.bonus_available),
                        "total_balance": int(bal_before.available),
                    },
                    "after": {
                        "real_balance": int(bal_after.real_available),
                        "bonus_balance": int(bal_after.bonus_available),
                        "total_balance": int(bal_after.available),
                    },
                    "note": note or default_desc,
                },
            )
            bot = request.app.get("bot")
            should_notify = True if payload.notify_user is None else bool(payload.notify_user)
            if should_notify and bot is not None and payload.amount_rub != 0:
                sign_word = "начислено" if payload.amount_rub > 0 else "списано"
                msg_text = (
                    f"💳 Администратор изменил ваш бонусный баланс: "
                    f"{sign_word} {abs(payload.amount_rub)} ₽.\n"
                    f"Текущий баланс: {int(bal_after.available)} ₽."
                )
                if payload.send_note_to_user and note:
                    msg_text += f"\nКомментарий: {html.escape(note)}"
                _queue_user_notification(
                    session,
                    bot,
                    user_id=user.id,
                    telegram_id=user.telegram_id,
                    text=msg_text,
                )
            return web.json_response({
                "ok": True,
                "action": "balance",
                "real_balance": int(bal_after.real_available),
                "bonus_balance": int(bal_after.bonus_available),
                "total_balance": int(bal_after.available),
                "message": f"Баланс обновлён ({payload.amount_rub:+d} ₽)",
            })

        if payload.action == "forgive_debt":
            await acquire_user_account_lock(session, user.id)
            bal_before = await get_account_balance(session, user_id=user.id, for_update=True)
            if bal_before.debt <= 0:
                return web.json_response(
                    {"ok": False, "error": "У пользователя нет задолженности"},
                    status=400,
                )
            forgive_amount = bal_before.debt.quantize(Decimal("1.00"))
            if payload.amount_rub is not None:
                forgive_amount = min(forgive_amount, Decimal(payload.amount_rub).quantize(Decimal("1.00")))
            await forgive_real_debt(
                session,
                user_id=user.id,
                amount_rub=int(forgive_amount),
                admin_telegram_id=ctx.telegram_id,
                note=note or None,
            )
            bal_after = await get_account_balance(session, user_id=user.id)
            await AuditService.log_action(
                session,
                admin_id=ctx.telegram_id,
                action=AdminAuditAction.ADMIN_BALANCE_TOPUP,
                target_type="user",
                target_id=user.id,
                details={
                    "action": "forgive_debt",
                    "forgiven_rub": int(forgive_amount),
                    "debt_before": int(bal_before.debt),
                    "debt_after": int(bal_after.debt),
                    "note": note,
                },
            )
            return web.json_response({
                "ok": True,
                "action": "forgive_debt",
                "forgiven_rub": int(forgive_amount),
                "debt": int(bal_after.debt),
                "real_position": int(bal_after.real_position),
                "real_balance": int(bal_after.real_available),
                "bonus_balance": int(bal_after.bonus_available),
                "total_balance": int(bal_after.available),
                "message": f"Задолженность уменьшена на {int(forgive_amount)} ₽",
            })

        if payload.action == "grant_standard":
            if payload.days is None:
                return web.json_response({"ok": False, "error": "Укажите количество дней"}, status=400)
            if payload.days == 0 and payload.device_limit is None:
                return web.json_response({"ok": False, "error": "Укажите дни или лимит устройств"}, status=400)
            if payload.device_limit is not None and not (
                constants.STANDARD_MIN_DEVICES <= payload.device_limit <= constants.STANDARD_MAX_DEVICES
            ):
                return web.json_response(
                    {
                        "ok": False,
                        "error": (
                            f"Лимит устройств должен быть от {constants.STANDARD_MIN_DEVICES} "
                            f"до {constants.STANDARD_MAX_DEVICES}"
                        ),
                    },
                    status=400,
                )
            await acquire_user_account_lock(session, user.id)
            locked_user = (
                await session.execute(
                    select(User)
                    .where(User.id == user.id, User.is_deleted.is_(False))
                    .with_for_update()
                )
            ).scalar_one_or_none()
            if locked_user is None:
                return web.json_response({"ok": False, "error": "Пользователь не найден"}, status=404)

            if payload.days < 0:
                vless_sub = await vless_subscription_repo.get_subscription_by_user_id(
                    session, user.id
                )
                now = now_utc()
                if not vless_sub or not vless_sub.expires_at or vless_sub.expires_at <= now:
                    return web.json_response(
                        {"ok": False, "error": "У пользователя нет активной подписки «Стандарт»"},
                        status=400,
                    )
                before_end = vless_sub.expires_at.isoformat() if vless_sub.expires_at else None
                before_limit = int(vless_sub.device_limit or 0)
                if payload.device_limit is not None:
                    slot_usage = await SubscriptionService.get_device_slot_usage(
                        session, locked_user, vless_sub=vless_sub, now=now
                    )
                    fits, limit_reason = SubscriptionService.check_standard_limit_fits(
                        slot_usage, payload.device_limit
                    )
                    if not fits:
                        return web.json_response(
                            {
                                "ok": False,
                                "error": limit_reason or f"Нельзя снизить лимит до {payload.device_limit}.",
                            },
                            status=400,
                        )
                new_end = vless_sub.expires_at - timedelta(days=abs(payload.days))
                if new_end <= now:
                    vless_sub.expires_at = now - timedelta(hours=constants.VPN_ACCESS_GRACE_HOURS + 1)
                else:
                    vless_sub.expires_at = new_end
                limit_changed = (
                    payload.device_limit is not None
                    and payload.device_limit != before_limit
                )
                if payload.device_limit is not None:
                    vless_sub.device_limit = payload.device_limit
                await session.flush()
                await AuditService.log_action(
                    session,
                    admin_id=ctx.telegram_id,
                    action=AdminAuditAction.REDUCE,
                    target_type="user",
                    target_id=user.id,
                    details={
                        "service": "standard",
                        "days": payload.days,
                        "before": {"expires_at": before_end, "device_limit": before_limit},
                        "after": {
                            "expires_at": vless_sub.expires_at.isoformat() if vless_sub.expires_at else None,
                            "device_limit": int(vless_sub.device_limit or 0),
                        },
                        "note": note,
                    },
                )
                if new_end <= now:
                    vless_sub.is_active = False
                    vless_sub.version = (getattr(vless_sub, "version", 1) or 1) + 1
                    await session.flush()
                    VlessSubscriptionService.ensure_synced_background(
                        user.id, is_active=False, session=session
                    )
                elif limit_changed:
                    vless_sub.version = (getattr(vless_sub, "version", 1) or 1) + 1
                    await session.flush()
                    VlessSubscriptionService.ensure_synced_background(
                        user.id, session=session
                    )
                if payload.notify_user is True:
                    _queue_user_notification(
                        session,
                        request.app.get("bot"),
                        user_id=user.id,
                        telegram_id=user.telegram_id,
                        text=f"⚡ Параметры вашей подписки «Стандарт» обновлены (сокращение на {abs(payload.days)} дн.).",
                    )
                return web.json_response({
                    "ok": True,
                    "action": "grant_standard",
                    "standard_end": vless_sub.expires_at.isoformat() if vless_sub.expires_at else None,
                    "standard_device_limit": int(vless_sub.device_limit or 0),
                    "message": f"Подписка «Стандарт» сокращена на {abs(payload.days)} дн.",
                })

            cur_vless = await get_or_create_subscription(session, user.id)
            before_end = cur_vless.expires_at.isoformat() if cur_vless.expires_at else None
            before_limit = int(getattr(cur_vless, "device_limit", 0) or 0)
            target_limit = payload.device_limit or (before_limit if before_limit >= 2 else 2)
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
                return _safe_admin_error_response(exc)
            if updated_user is None:
                return web.json_response({"ok": False, "error": "Пользователь не найден"}, status=404)
            updated_vless = await vless_subscription_repo.get_subscription_by_user_id(session, updated_user.id)
            await AuditService.log_action(
                session,
                admin_id=ctx.telegram_id,
                action=(
                    AdminAuditAction.ADMIN_SUB_EXTEND
                    if payload.days > 0
                    else AdminAuditAction.ADMIN_SUB_CHANGE
                ),
                target_type="user",
                target_id=user.id,
                details={
                    "service": "standard",
                    "days": payload.days,
                    "device_limit": target_limit,
                    "before": {"expires_at": before_end, "device_limit": before_limit},
                    "after": {
                        "expires_at": updated_vless.expires_at.isoformat() if updated_vless and updated_vless.expires_at else None,
                        "device_limit": int(updated_vless.device_limit or 0) if updated_vless else 0,
                    },
                    "note": note,
                },
            )
            msg_done = (
                "Лимит устройств «Стандарт» обновлён"
                if payload.days == 0
                else f"Подписка «Стандарт» выдана/продлена на {payload.days} дн."
            )
            if payload.notify_user is True:
                dm_text = (
                    f"⚡ Лимит устройств вашей подписки «Стандарт» обновлён: {target_limit}."
                    if payload.days == 0
                    else f"🎁 Ваша подписка «Стандарт» продлена на +{payload.days} дн."
                )
                _queue_user_notification(
                    session,
                    request.app.get("bot"),
                    user_id=user.id,
                    telegram_id=user.telegram_id,
                    text=dm_text,
                )
            return web.json_response({
                "ok": True,
                "action": "grant_standard",
                "standard_end": updated_vless.expires_at.isoformat() if updated_vless and updated_vless.expires_at else None,
                "standard_device_limit": int(updated_vless.device_limit or 0) if updated_vless else 0,
                "message": msg_done,
            })

        if payload.action == "wi_traffic":
            await acquire_user_account_lock(session, user.id)
            locked_user = (
                await session.execute(
                    select(User)
                    .where(User.id == user.id, User.is_deleted.is_(False))
                    .with_for_update()
                )
            ).scalar_one_or_none()
            if locked_user is None:
                return web.json_response({"ok": False, "error": "Пользователь не найден"}, status=404)
            wi_sub = await get_subscription_by_user_id(session, user.id)
            if wi_sub is None:
                return web.json_response({"ok": False, "error": "Подписка «Белый интернет» не найдена"}, status=404)
            before_used = int(wi_sub.traffic_used_bytes or 0)
            before_base = int(wi_sub.base_traffic_bytes or 0)
            before_extra = int(wi_sub.extra_traffic_bytes or 0)
            t_action = payload.traffic_action
            if t_action == "reset_used":
                try:
                    updated_sub = await white_internet_repo.reset_traffic_used_atomic(session, wi_sub.id)
                except white_internet_repo.WhiteInternetError as exc:
                    return _safe_admin_error_response(exc)
                await _queue_wi_inline_sync(session, updated_sub, "wi_traffic_reset")
                await AuditService.log_action(
                    session,
                    admin_id=ctx.telegram_id,
                    action=AdminAuditAction.WHITE_INTERNET_TRAFFIC_RESET,
                    target_type="user",
                    target_id=user.id,
                    details={
                        "subscription_id": wi_sub.id,
                        "before": {"traffic_used_bytes": before_used},
                        "after": {"traffic_used_bytes": int(updated_sub.traffic_used_bytes or 0)},
                        "note": note,
                    },
                )
                if payload.notify_user is True:
                    _queue_user_notification(
                        session,
                        request.app.get("bot"),
                        user_id=user.id,
                        telegram_id=user.telegram_id,
                        text="🔄 Счётчик трафика вашей подписки «Белый интернет» сброшен.",
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
                        dm_text = f"🎁 Вам добавлено +{payload.gb} ГБ трафика в подписке «Белый интернет»."
                    else:
                        updated_sub = await white_internet_repo.set_base_traffic_quota_atomic(
                            session, wi_sub.id, bytes_val
                        )
                        audit_act = AdminAuditAction.WHITE_INTERNET_QUOTA_SET
                        msg_ok = f"Базовая квота установлена: {payload.gb} ГБ"
                        dm_text = f"⚡ Базовая квота трафика «Белый интернет» обновлена: {payload.gb} ГБ."
                except white_internet_repo.WhiteInternetError as exc:
                    return _safe_admin_error_response(exc)
                await _queue_wi_inline_sync(session, updated_sub, "wi_traffic")
                await AuditService.log_action(
                    session,
                    admin_id=ctx.telegram_id,
                    action=audit_act,
                    target_type="user",
                    target_id=user.id,
                    details={
                        "subscription_id": wi_sub.id,
                        "gb": payload.gb,
                        "before": {
                            "base_traffic_bytes": before_base,
                            "extra_traffic_bytes": before_extra,
                        },
                        "after": {
                            "base_traffic_bytes": int(updated_sub.base_traffic_bytes or 0),
                            "extra_traffic_bytes": int(updated_sub.extra_traffic_bytes or 0),
                        },
                        "note": note,
                    },
                )
                if payload.notify_user is True:
                    _queue_user_notification(
                        session,
                        request.app.get("bot"),
                        user_id=user.id,
                        telegram_id=user.telegram_id,
                        text=dm_text,
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
            await acquire_user_account_lock(session, user.id)
            locked_user = (
                await session.execute(
                    select(User)
                    .where(User.id == user.id, User.is_deleted.is_(False))
                    .with_for_update()
                )
            ).scalar_one_or_none()
            if locked_user is None:
                return web.json_response({"ok": False, "error": "Пользователь не найден"}, status=404)
            before_state = {
                "financial_hold": bool(locked_user.financial_hold),
                "topup_blocked": bool(locked_user.topup_blocked),
                "financial_block_reason": locked_user.financial_block_reason,
            }
            if payload.financial_hold is not None:
                locked_user.financial_hold = bool(payload.financial_hold)
                if not locked_user.financial_hold:
                    locked_user.financial_block_reason = None
            if payload.topup_blocked is not None:
                locked_user.topup_blocked = bool(payload.topup_blocked)
            await session.flush()
            await SubscriptionService.sync_access_state(session, locked_user)

            now = now_utc()
            wi_reactivated = False
            hold_cleared = (
                before_state["financial_hold"] is True
                and locked_user.financial_hold is False
            )
            should_reactivate_wi = hold_cleared or bool(payload.reactivate_wi)
            if should_reactivate_wi and not locked_user.financial_hold:
                wi_sub = await get_subscription_by_user_id(session, user.id)
                if (
                    wi_sub is not None
                    and wi_sub.status == WhiteInternetStatus.DISABLED
                    and wi_sub.expires_at
                    and wi_sub.expires_at > now
                    and wi_sub.status_reason in (None, "", "financial_hold")
                ):
                    other_live_wi_id = await session.scalar(
                        select(WhiteInternetSubscription.id)
                        .where(
                            WhiteInternetSubscription.user_id == user.id,
                            WhiteInternetSubscription.id != wi_sub.id,
                            WhiteInternetSubscription.status.in_([
                                WhiteInternetStatus.PENDING,
                                WhiteInternetStatus.ACTIVE,
                                WhiteInternetStatus.EXHAUSTED,
                            ]),
                        )
                        .limit(1)
                    )
                    if other_live_wi_id is None:
                        locked_wi = await white_internet_repo.get_subscription_with_lock(
                            session, wi_sub.id
                        )
                        if (
                            locked_wi is not None
                            and locked_wi.status == WhiteInternetStatus.DISABLED
                            and locked_wi.expires_at
                            and locked_wi.expires_at > now
                            and locked_wi.status_reason in (None, "", "financial_hold")
                        ):
                            total_quota = (locked_wi.base_traffic_bytes or 0) + (
                                locked_wi.extra_traffic_bytes or 0
                            )
                            used_quota = max(
                                0,
                                (locked_wi.traffic_used_bytes or 0)
                                - (locked_wi.traffic_overage_bytes or 0),
                            )
                            if total_quota > 0 and used_quota < total_quota:
                                locked_wi.status = WhiteInternetStatus.ACTIVE
                                locked_wi.status_reason = None
                                locked_wi.provisioning_status = (
                                    WhiteInternetProvisioningStatus.PENDING_UPDATE
                                )
                                locked_wi.desired_version = (locked_wi.desired_version or 1) + 1
                                await session.flush()
                                await _queue_wi_inline_sync(
                                    session, locked_wi, "restrictions_reactivate_wi"
                                )
                                wi_reactivated = True
                            elif total_quota > 0 and used_quota >= total_quota:
                                locked_wi.status = WhiteInternetStatus.EXHAUSTED
                                locked_wi.status_reason = "quota_exhausted"
                                locked_wi.provisioning_status = (
                                    WhiteInternetProvisioningStatus.SYNCED_INACTIVE
                                )
                                await session.flush()

            if not locked_user.financial_hold:
                held_stmt = select(Order).where(
                    Order.user_id == locked_user.id,
                    Order.status == "paid",
                    Order.metadata_["settlement_held"].astext == "true",
                )
                if locked_user.topup_blocked:
                    held_stmt = held_stmt.where(Order.service_type != "topup")
                held_orders = (await session.scalars(held_stmt)).all()
                for held_order in held_orders:
                    await OrderService.mark_order_paid(session, held_order.id)
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
                    "before": before_state,
                    "after": {
                        "financial_hold": bool(locked_user.financial_hold),
                        "topup_blocked": bool(locked_user.topup_blocked),
                        "financial_block_reason": locked_user.financial_block_reason,
                        "wi_reactivated": wi_reactivated,
                    },
                    "note": note,
                },
            )
            return web.json_response({
                "ok": True,
                "action": "restrictions",
                "financial_hold": bool(locked_user.financial_hold),
                "topup_blocked": bool(locked_user.topup_blocked),
                "wi_reactivated": wi_reactivated,
                "message": "Ограничения пользователя обновлены",
            })

        # grant_wi
        if payload.days is None:
            return web.json_response({"ok": False, "error": "Укажите количество дней"}, status=400)
        if payload.days == 0 and payload.device_limit is None:
            return web.json_response({"ok": False, "error": "Укажите дни или лимит устройств"}, status=400)
        if payload.device_limit is not None and not (
            1 <= payload.device_limit <= constants.WHITE_INTERNET_MAX_DEVICE_LIMIT
        ):
            return web.json_response(
                {
                    "ok": False,
                    "error": f"Лимит устройств должен быть от 1 до {constants.WHITE_INTERNET_MAX_DEVICE_LIMIT}",
                },
                status=400,
            )
        await acquire_user_account_lock(session, user.id)
        locked_user = (
            await session.execute(
                select(User)
                .where(User.id == user.id, User.is_deleted.is_(False))
                .with_for_update()
            )
        ).scalar_one_or_none()
        if locked_user is None:
            return web.json_response({"ok": False, "error": "Пользователь не найден"}, status=404)
        if locked_user.financial_hold:
            return web.json_response(
                {
                    "ok": False,
                    "error": "На аккаунте установлена финансовая блокировка. Сначала снимите её.",
                },
                status=400,
            )
        wi_sub = await get_subscription_by_user_id(session, user.id)
        if wi_sub is not None and wi_sub.status == WhiteInternetStatus.DISABLED and payload.days <= 0:
            return web.json_response(
                {
                    "ok": False,
                    "error": "Подписка «Белый интернет» отключена. Для активации укажите положительное число дней.",
                },
                status=400,
            )
        if wi_sub is None or (
            wi_sub.status == WhiteInternetStatus.DISABLED and payload.days > 0
        ):
            if payload.days <= 0:
                return web.json_response(
                    {"ok": False, "error": "Для новой активации укажите положительное число дней"},
                    status=400,
                )
            ok, msg, created = await WhiteInternetService.purchase_subscription(
                session,
                user.id,
                device_limit=payload.device_limit or 1,
                duration_days=payload.days,
            )
            if not ok:
                return web.json_response({"ok": False, "error": msg}, status=400)
            await AuditService.log_action(
                session,
                admin_id=ctx.telegram_id,
                action=AdminAuditAction.WHITE_INTERNET_EXTEND,
                target_type="user",
                target_id=user.id,
                details={"days": payload.days, "device_limit": payload.device_limit or 1, "note": note},
            )
            if payload.notify_user is True:
                _queue_user_notification(
                    session,
                    request.app.get("bot"),
                    user_id=user.id,
                    telegram_id=user.telegram_id,
                    text=f"🎁 Вам активирована подписка «Белый интернет» на {payload.days} дн.",
                )
            end_iso = created.expires_at.isoformat() if created and created.expires_at else None
            return web.json_response({
                "ok": True,
                "action": "grant_wi",
                "wi_end": end_iso,
                "message": f"Подписка «Белый интернет» активирована на {payload.days} дн.",
            })

        locked_wi = await white_internet_repo.get_subscription_with_lock(session, wi_sub.id)
        if locked_wi is None:
            return web.json_response({"ok": False, "error": "Подписка не найдена"}, status=404)
        wi_sub = locked_wi
        before_wi_end = wi_sub.expires_at.isoformat() if wi_sub.expires_at else None
        before_wi_limit = int(wi_sub.device_limit or 0)
        now = now_utc()

        # Validate days before applying any device_limit ORM mutations so HTTP 400 never commits partial changes.
        if payload.days < 0:
            if not wi_sub.expires_at or wi_sub.expires_at <= now:
                return web.json_response(
                    {"ok": False, "error": "Подписка «Белый интернет» уже истекла"},
                    status=400,
                )
        elif payload.days > 0:
            try:
                wi_sub = await white_internet_repo.extend_subscription_atomic(
                    session, wi_sub.id, payload.days
                )
            except (white_internet_repo.WhiteInternetError, ValueError) as exc:
                return _safe_admin_error_response(exc)

        devlimit_changed = False
        transitioned_to_exhausted = False
        if payload.device_limit is not None and payload.device_limit != wi_sub.device_limit:
            devlimit_changed = True
            wi_sub.device_limit = payload.device_limit
            wi_sub.base_traffic_bytes = payload.device_limit * constants.WHITE_INTERNET_BASE_TRAFFIC_BYTES
            wi_sub.pending_device_limit = None
            wi_sub.pending_from = None
            wi_sub.desired_version = (wi_sub.desired_version or 1) + 1
            total_quota = (wi_sub.base_traffic_bytes or 0) + (wi_sub.extra_traffic_bytes or 0)
            used_quota = max(0, (wi_sub.traffic_used_bytes or 0) - (wi_sub.traffic_overage_bytes or 0))
            if (
                wi_sub.status == WhiteInternetStatus.ACTIVE
                and total_quota > 0
                and used_quota >= total_quota
            ):
                wi_sub.status = WhiteInternetStatus.EXHAUSTED
                wi_sub.status_reason = "quota_exhausted"
                wi_sub.provisioning_status = WhiteInternetProvisioningStatus.PENDING_DELETE
                transitioned_to_exhausted = True
            elif wi_sub.status == WhiteInternetStatus.EXHAUSTED and total_quota > used_quota:
                wi_sub.status = WhiteInternetStatus.ACTIVE
                wi_sub.status_reason = None
                wi_sub.provisioning_status = WhiteInternetProvisioningStatus.PENDING_UPDATE
            elif wi_sub.status == WhiteInternetStatus.ACTIVE:
                wi_sub.provisioning_status = WhiteInternetProvisioningStatus.PENDING_UPDATE
            await session.flush()
            await AuditService.log_action(
                session,
                admin_id=ctx.telegram_id,
                action=AdminAuditAction.WHITE_INTERNET_DEVLIMIT_SET,
                target_type="user",
                target_id=user.id,
                details={
                    "before": {"device_limit": before_wi_limit},
                    "after": {"device_limit": payload.device_limit},
                    "note": note,
                },
            )

        if payload.days == 0:
            if transitioned_to_exhausted:
                await _queue_wi_deprovision_all(
                    session, user.id, wi_sub, context="admin_devlimit_exhausted"
                )
            elif devlimit_changed:
                await _queue_wi_inline_sync(session, wi_sub, "grant_wi_devlimit")
            if payload.notify_user is True:
                _queue_user_notification(
                    session,
                    request.app.get("bot"),
                    user_id=user.id,
                    telegram_id=user.telegram_id,
                    text=f"⚡ Лимит устройств вашей подписки «Белый интернет» обновлён: {wi_sub.device_limit}.",
                )
            end_iso = wi_sub.expires_at.isoformat() if wi_sub.expires_at else None
            return web.json_response({
                "ok": True,
                "action": "grant_wi",
                "wi_end": end_iso,
                "message": "Лимит устройств «Белый интернет» обновлён",
            })

        if payload.days < 0:
            new_end = wi_sub.expires_at - timedelta(days=abs(payload.days))
            if new_end <= now:
                wi_sub.expires_at = now
                wi_sub.status = WhiteInternetStatus.EXPIRED
                wi_sub.provisioning_status = WhiteInternetProvisioningStatus.PENDING_DELETE
                wi_sub.desired_version = (wi_sub.desired_version or 1) + 1
                await session.flush()
                await _queue_wi_deprovision_all(
                    session, user.id, wi_sub, context="admin_reduce_expired"
                )
            else:
                wi_sub.expires_at = new_end
                await session.flush()
                if transitioned_to_exhausted:
                    await _queue_wi_deprovision_all(
                        session, user.id, wi_sub, context="admin_devlimit_exhausted"
                    )
                elif devlimit_changed:
                    await _queue_wi_inline_sync(session, wi_sub, "grant_wi_reduce_devlimit")
            await AuditService.log_action(
                session,
                admin_id=ctx.telegram_id,
                action=AdminAuditAction.REDUCE,
                target_type="user",
                target_id=user.id,
                details={
                    "service": "white_internet",
                    "days": payload.days,
                    "before": {"expires_at": before_wi_end},
                    "after": {"expires_at": wi_sub.expires_at.isoformat() if wi_sub.expires_at else None},
                    "note": note,
                },
            )
            if payload.notify_user is True:
                _queue_user_notification(
                    session,
                    request.app.get("bot"),
                    user_id=user.id,
                    telegram_id=user.telegram_id,
                    text=f"⚡ Параметры вашей подписки «Белый интернет» обновлены (сокращение на {abs(payload.days)} дн.).",
                )
            return web.json_response({
                "ok": True,
                "action": "grant_wi",
                "wi_end": wi_sub.expires_at.isoformat() if wi_sub.expires_at else None,
                "message": f"Подписка «Белый интернет» сокращена на {abs(payload.days)} дн.",
            })

        if transitioned_to_exhausted:
            await _queue_wi_deprovision_all(
                session, user.id, wi_sub, context="admin_extend_devlimit_exhausted"
            )
        else:
            await _queue_wi_inline_sync(session, wi_sub, "grant_wi_extend")
        await AuditService.log_action(
            session,
            admin_id=ctx.telegram_id,
            action=AdminAuditAction.WHITE_INTERNET_EXTEND,
            target_type="user",
            target_id=user.id,
            details={
                "days": payload.days,
                "before": {"expires_at": before_wi_end},
                "after": {"expires_at": wi_sub.expires_at.isoformat() if wi_sub and wi_sub.expires_at else None},
                "note": note,
            },
        )
        if payload.notify_user is True:
            _queue_user_notification(
                session,
                request.app.get("bot"),
                user_id=user.id,
                telegram_id=user.telegram_id,
                text=f"🎁 Ваша подписка «Белый интернет» продлена на +{payload.days} дн.",
            )
        end_iso = wi_sub.expires_at.isoformat() if wi_sub and wi_sub.expires_at else None
        return web.json_response({
            "ok": True,
            "action": "grant_wi",
            "wi_end": end_iso,
            "message": f"Подписка «Белый интернет» продлена на {payload.days} дн.",
        })


def _serialize_admin_ledger_entry(entry: AccountLedgerEntry) -> dict[str, Any]:
    """Serialize an AccountLedgerEntry for admin user details."""
    meta = entry.metadata_ if isinstance(entry.metadata_, dict) else {}
    entry_type = entry.entry_type
    if entry_type == "payment_credit" and meta.get("source") == "admin_debt_forgiveness":
        entry_type = "debt_forgiveness"
        note = str(meta.get("note") or "").strip()
        description = "Погашение долга" + (f" · {note}" if note else "")
    else:
        description = str(meta.get("description") or meta.get("reason") or "")
    return {
        "id": entry.id,
        "entry_type": entry_type,
        "bucket": entry.bucket,
        "amount": int(entry.amount),
        "description": description,
        "created_at": entry.created_at.isoformat() if entry.created_at else None,
    }


async def api_admin_user_details_handler(request: web.Request) -> web.Response:
    ctx, err_resp = require_admin_user(request)
    if err_resp is not None or ctx is None:
        return err_resp  # type: ignore[return-value]

    target_user_id, id_err = _parse_positive_int(request, "user_id", label="user id")
    if id_err is not None or target_user_id is None:
        return id_err  # type: ignore[return-value]

    async with session_scope() as session:
        user = await session.get(User, target_user_id)
        if user is None or getattr(user, "is_deleted", False):
            return web.json_response({"ok": False, "error": "Пользователь не найден"}, status=404)

        bal = await get_account_balance(session, user_id=user.id)
        vless_sub = await vless_subscription_repo.get_subscription_by_user_id(session, user.id)
        wi_sub = await get_subscription_by_user_id(session, user.id)
        slot_usage = await SubscriptionService.get_device_slot_usage(
            session, user, vless_sub=vless_sub, wi_sub=wi_sub
        )
        devices = slot_usage["devices"]

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

        now = now_utc()
        churn_events_24h = int(
            (
                await session.scalar(
                    select(func.count(DeviceRegistrationEvent.id)).where(
                        DeviceRegistrationEvent.user_id == user.id,
                        DeviceRegistrationEvent.created_at >= now - timedelta(hours=24),
                    )
                )
            )
            or 0
        )

        recent_orders_rows = (
            await session.scalars(
                select(Order)
                .where(Order.user_id == user.id)
                .order_by(Order.created_at.desc(), Order.id.desc())
                .limit(20)
            )
        ).all()
        recent_ledger_rows = await get_account_history(session, user_id=user.id, limit=25)
        recent_device_event_rows = (
            await session.scalars(
                select(DeviceRegistrationEvent)
                .where(DeviceRegistrationEvent.user_id == user.id)
                .order_by(DeviceRegistrationEvent.created_at.desc(), DeviceRegistrationEvent.id.desc())
                .limit(20)
            )
        ).all()
        recent_audit_rows = (
            await session.scalars(
                select(AuditLog)
                .where(
                    func.lower(AuditLog.target_type) == "user",
                    or_(AuditLog.target_id == user.id, AuditLog.target_id == user.telegram_id),
                )
                .order_by(AuditLog.id.desc())
                .limit(20)
            )
        ).all()

        device_meta_by_hwid = {
            str(d.get("hwid")): d
            for d in devices
            if d.get("hwid")
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
                "last_payment_at": user.last_payment_at.isoformat() if user.last_payment_at else None,
                "is_bot_blocked": bool(getattr(user, "is_bot_blocked", False)),
                "real_balance": int(bal.real_available),
                "bonus_balance": int(bal.bonus_available),
                "total_balance": int(bal.available),
                "debt": int(bal.debt),
                "real_position": int(bal.real_position),
                "standard_end": vless_sub.expires_at.isoformat() if vless_sub and vless_sub.expires_at else None,
                "standard_device_limit": int(vless_sub.device_limit or 0) if vless_sub else 0,
                "standard": (
                    {
                        "is_active": bool(vless_sub.is_active),
                        "auto_renew_enabled": bool(vless_sub.auto_renew_enabled),
                        "last_synced_at": vless_sub.last_synced_at.isoformat() if vless_sub.last_synced_at else None,
                    }
                    if vless_sub
                    else None
                ),
                "financial_hold": bool(getattr(user, "financial_hold", False)),
                "topup_blocked": bool(getattr(user, "topup_blocked", False)),
                "financial_block_reason": user.financial_block_reason,
                "monthly_vless_bytes": int(getattr(user, "monthly_vless_bytes", 0) or 0),
                "total_traffic_bytes": int(getattr(user, "total_traffic_bytes", 0) or 0),
                "monthly_wi_bytes": int(getattr(user, "monthly_wi_bytes", 0) or 0),
                "total_wi_traffic_bytes": int(getattr(user, "total_wi_traffic_bytes", 0) or 0),
                "churn_events_24h": churn_events_24h,
                "subscription_url": _build_sub_url(sub_token) if sub_token else None,
                "subscription_backup_url": _build_backup_sub_url(sub_token) if sub_token else None,
                "wi": (
                    {
                        "id": wi_sub.id,
                        "status": str(wi_sub.status),
                        "status_reason": wi_sub.status_reason,
                        "provisioning_status": str(wi_sub.provisioning_status),
                        "expires_at": wi_sub.expires_at.isoformat() if wi_sub.expires_at else None,
                        "device_limit": int(wi_sub.device_limit or 0),
                        "pending_device_limit": wi_sub.pending_device_limit,
                        "auto_renew_enabled": bool(wi_sub.auto_renew_enabled),
                        "desired_version": int(wi_sub.desired_version or 1),
                        "actual_version": int(wi_sub.actual_version or 0),
                        "node_sync_state": wi_sub.node_sync_state if isinstance(wi_sub.node_sync_state, dict) else {},
                        "last_synced_at": wi_sub.last_synced_at.isoformat() if wi_sub.last_synced_at else None,
                        "last_sync_error": wi_sub.last_sync_error,
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
                        "is_std_priority": int(d.get("std_priority") or 0) > 0,
                        "is_std_assigned": bool(d.get("is_std_assigned", False)),
                        "is_bi_priority": int(d.get("bi_priority") or 0) > 0,
                        "is_bi_assigned": bool(d.get("is_bi_assigned", False)),
                        "last_seen_at": d.get("last_seen_at"),
                    }
                    for d in devices
                ],
                "recent_orders": [
                    {
                        "id": str(o.id),
                        "status": o.status,
                        "kind": o.service_type,
                        "amount_rub": float(o.amount_rub),
                        "description": o.description or "",
                        "external_id": o.external_id,
                        "created_at": o.created_at.isoformat() if o.created_at else None,
                        "paid_at": o.paid_at.isoformat() if o.paid_at else None,
                        "refunded_at": o.refunded_at.isoformat() if o.refunded_at else None,
                        "metadata_flags": admin_tools_service.extract_order_metadata_flags(o.metadata_),
                    }
                    for o in recent_orders_rows
                ],
                "recent_ledger": [
                    _serialize_admin_ledger_entry(entry)
                    for entry in recent_ledger_rows
                ],
                "recent_device_events": [
                    {
                        "id": ev.id,
                        "hwid": ev.hwid,
                        "device_model": (device_meta_by_hwid.get(ev.hwid) or {}).get("device_name"),
                        "platform": (device_meta_by_hwid.get(ev.hwid) or {}).get("platform"),
                        "created_at": ev.created_at.isoformat() if ev.created_at else None,
                    }
                    for ev in recent_device_event_rows
                ],
                "recent_audit": [
                    {
                        "id": a.id,
                        "action": a.action,
                        "admin_telegram_id": a.admin_id,
                        "details": a.details or "",
                        "created_at": a.created_at.isoformat() if a.created_at else None,
                    }
                    for a in recent_audit_rows
                ],
            },
        })


async def api_admin_user_devices_reset_handler(request: web.Request) -> web.Response:
    ctx, err_resp = require_admin_user(request)
    if err_resp is not None or ctx is None:
        return err_resp  # type: ignore[return-value]

    target_user_id, id_err = _parse_positive_int(request, "user_id", label="user id")
    if id_err is not None or target_user_id is None:
        return id_err  # type: ignore[return-value]

    notify_user = False
    raw_text = await request.text()
    if raw_text.strip():
        opt_payload, parse_err = await _parse_json_model(request, AdminUserOptionalNotifyPayload)
        if parse_err is not None or opt_payload is None:
            return parse_err  # type: ignore[return-value]
        notify_user = bool(opt_payload.notify_user)

    async with session_scope() as session:
        await acquire_user_account_lock(session, target_user_id)
        user = await session.scalar(
            select(User).where(User.id == target_user_id).with_for_update()
        )
        if user is None or getattr(user, "is_deleted", False):
            return web.json_response({"ok": False, "error": "Пользователь не найден"}, status=404)

        vless_sub = await vless_subscription_repo.get_subscription_by_user_id(session, user.id)
        wi_sub = await get_subscription_by_user_id(session, user.id)
        old_vless_uuid: str | None = None
        if vless_sub is not None:
            old_vless_uuid, _new_vless_uuid = await vless_subscription_repo.reset_hwids(
                session, vless_sub.id
            )
            vless_sub = (
                await vless_subscription_repo.get_subscription_by_user_id(session, user.id)
                or vless_sub
            )

        existing_devs = (await session.scalars(select(UserDevice).where(UserDevice.user_id == user.id))).all()
        dev_uuids = [d.device_uuid for d in existing_devs if getattr(d, "device_uuid", None)]
        if vless_sub is not None and dev_uuids:
            vless_sub = (
                await vless_subscription_repo.add_pending_revocation_uuids(
                    session, vless_sub.id, dev_uuids
                )
                or vless_sub
            )

        dev_del_res = await session.execute(
            delete(UserDevice).where(UserDevice.user_id == user.id)
        )
        events_del_res = await session.execute(
            delete(DeviceRegistrationEvent).where(DeviceRegistrationEvent.user_id == user.id)
        )

        if vless_sub is not None:
            sub_ver = getattr(vless_sub, "version", None)
            sub_id = vless_sub.id
            vless_rev_uuids = list(
                dict.fromkeys([u for u in [old_vless_uuid, *dev_uuids] if u])
            )
            for u_uuid in vless_rev_uuids:
                VlessSubscriptionService.deprovision_background(
                    u_uuid,
                    version=sub_ver,
                    sub_id=sub_id,
                    session=session,
                    services=("vless",),
                )
            VlessSubscriptionService.ensure_synced_background(user.id, session=session)

        if wi_sub is not None and dev_uuids:
            wi_sub = (
                await white_internet_repo.get_subscription_with_lock(session, wi_sub.id)
                or wi_sub
            )
            await white_internet_repo.add_pending_revocation_uuids(
                session, wi_sub.id, dev_uuids
            )
            if wi_sub.status in (WhiteInternetStatus.ACTIVE, WhiteInternetStatus.PENDING):
                wi_sub.desired_version = (wi_sub.desired_version or 1) + 1
                wi_sub.provisioning_status = WhiteInternetProvisioningStatus.PENDING_UPDATE
            await session.flush()
            await _queue_wi_revoke_uuids(
                session, wi_sub, dev_uuids, context="admin_devices_reset"
            )
            if wi_sub.status in (WhiteInternetStatus.ACTIVE, WhiteInternetStatus.PENDING):
                await _queue_wi_inline_sync(session, wi_sub, "devices_reset")
        await AuditService.log_action(
            session,
            admin_id=ctx.telegram_id,
            action=AdminAuditAction.VLESS_HWID_RESET,
            target_type="user",
            target_id=user.id,
            details={
                "deleted_devices": int(dev_del_res.rowcount or 0),
                "deleted_churn_events": int(events_del_res.rowcount or 0),
            },
        )
        if notify_user:
            _queue_user_notification(
                session,
                request.app.get("bot"),
                user_id=user.id,
                telegram_id=user.telegram_id,
                text="🔄 Привязки устройств к вашей подписке сброшены администратором. Подключите устройства заново.",
            )

    return web.json_response({"ok": True, "message": "Привязки устройств сброшены"})


async def api_admin_user_device_delete_handler(request: web.Request) -> web.Response:
    """Delete a single bound HWID device for a user and queue deprovisioning/sync."""
    ctx, err_resp = require_admin_user(request)
    if err_resp is not None or ctx is None:
        return err_resp  # type: ignore[return-value]

    target_user_id, id_err = _parse_positive_int(request, "user_id", label="user id")
    if id_err is not None or target_user_id is None:
        return id_err  # type: ignore[return-value]

    hwid = unquote(request.match_info.get("hwid", "")).strip()
    if not hwid or len(hwid) > 128:
        return web.json_response({"ok": False, "error": "Invalid HWID"}, status=400)

    async with session_scope() as session:
        await acquire_user_account_lock(session, target_user_id)
        user = await session.scalar(
            select(User).where(User.id == target_user_id).with_for_update()
        )
        if user is None or getattr(user, "is_deleted", False):
            return web.json_response({"ok": False, "error": "Пользователь не найден"}, status=404)

        deleted = await user_devices_repo.delete_user_device(session, user.id, hwid)
        if deleted is None:
            return web.json_response({"ok": False, "error": "Устройство не найдено"}, status=404)

        deleted_uuid = getattr(deleted, "device_uuid", None)
        if deleted_uuid:
            vless_sub = await vless_subscription_repo.get_subscription_by_user_id(session, user.id)
            sub_id = vless_sub.id if vless_sub else None
            if vless_sub is not None:
                await vless_subscription_repo.add_pending_revocation_uuids(session, vless_sub.id, [deleted_uuid])
            sub_ver = getattr(vless_sub, "version", None) if vless_sub else None

            VlessSubscriptionService.deprovision_background(
                deleted_uuid,
                version=sub_ver,
                sub_id=sub_id,
                session=session,
                services=("vless",),
            )
            VlessSubscriptionService.ensure_synced_background(user.id, session=session)

            wi_sub = await get_subscription_by_user_id(session, user.id)
            if wi_sub is not None:
                wi_sub = await white_internet_repo.get_subscription_with_lock(session, wi_sub.id) or wi_sub
                await white_internet_repo.add_pending_revocation_uuids(session, wi_sub.id, [deleted_uuid])
                if wi_sub.status in (WhiteInternetStatus.ACTIVE, WhiteInternetStatus.PENDING):
                    wi_sub.desired_version = (wi_sub.desired_version or 1) + 1
                    wi_sub.provisioning_status = WhiteInternetProvisioningStatus.PENDING_UPDATE
                await session.flush()
                await _queue_wi_revoke_uuids(
                    session, wi_sub, [deleted_uuid], context="admin_device_delete"
                )
                if wi_sub.status in (WhiteInternetStatus.ACTIVE, WhiteInternetStatus.PENDING):
                    await _queue_wi_inline_sync(session, wi_sub, "device_delete")

        await AuditService.log_action(
            session,
            admin_id=ctx.telegram_id,
            action=AdminAuditAction.VLESS_HWID_RESET,
            target_type="user",
            target_id=user.id,
            details={"action": "delete_device", "hwid": hwid},
        )

    return web.json_response({"ok": True, "hwid": hwid, "message": "Устройство удалено"})


async def api_admin_user_resync_handler(request: web.Request) -> web.Response:
    """Manually trigger background/inline sync for user's Standard and WI subscriptions."""
    ctx, err_resp = require_admin_user(request)
    if err_resp is not None or ctx is None:
        return err_resp  # type: ignore[return-value]

    target_user_id, id_err = _parse_positive_int(request, "user_id", label="user id")
    if id_err is not None or target_user_id is None:
        return id_err  # type: ignore[return-value]

    async with session_scope() as session:
        await acquire_user_account_lock(session, target_user_id)
        user = await session.scalar(
            select(User).where(User.id == target_user_id).with_for_update()
        )
        if user is None or getattr(user, "is_deleted", False):
            return web.json_response({"ok": False, "error": "Пользователь не найден"}, status=404)

        vless_sub = await vless_subscription_repo.get_subscription_by_user_id(session, user.id)
        wi_sub = await get_subscription_by_user_id(session, user.id)
        synced_standard = False
        synced_wi = False

        if vless_sub is not None:
            VlessSubscriptionService.ensure_synced_background(user.id, session=session)
            synced_standard = True
        if wi_sub is not None and wi_sub.status in (
            WhiteInternetStatus.ACTIVE,
            WhiteInternetStatus.PENDING,
        ):
            locked_wi = await white_internet_repo.get_subscription_with_lock(session, wi_sub.id)
            if locked_wi is not None and locked_wi.status in (
                WhiteInternetStatus.ACTIVE,
                WhiteInternetStatus.PENDING,
            ):
                locked_wi.desired_version = (locked_wi.desired_version or 1) + 1
                locked_wi.provisioning_status = WhiteInternetProvisioningStatus.PENDING_UPDATE
                await session.flush()
                await _queue_wi_inline_sync(session, locked_wi, "manual_resync")
                synced_wi = True

        await AuditService.log_action(
            session,
            admin_id=ctx.telegram_id,
            action=AdminAuditAction.ADMIN_SUB_CHANGE,
            target_type="user",
            target_id=user.id,
            details={"action": "manual_resync", "standard": synced_standard, "wi": synced_wi},
        )

    return web.json_response({
        "ok": True,
        "synced_standard": synced_standard,
        "synced_wi": synced_wi,
        "message": "Синхронизация запущена",
    })


async def api_admin_user_token_rotate_handler(request: web.Request) -> web.Response:
    ctx, err_resp = require_admin_user(request)
    if err_resp is not None or ctx is None:
        return err_resp  # type: ignore[return-value]

    target_user_id, id_err = _parse_positive_int(request, "user_id", label="user id")
    if id_err is not None or target_user_id is None:
        return id_err  # type: ignore[return-value]

    notify_user = False
    raw_text = await request.text()
    if raw_text.strip():
        opt_payload, parse_err = await _parse_json_model(request, AdminUserOptionalNotifyPayload)
        if parse_err is not None or opt_payload is None:
            return parse_err  # type: ignore[return-value]
        notify_user = bool(opt_payload.notify_user)

    async with session_scope() as session:
        await acquire_user_account_lock(session, target_user_id)
        user = await session.scalar(
            select(User).where(User.id == target_user_id).with_for_update()
        )
        if user is None or getattr(user, "is_deleted", False):
            return web.json_response({"ok": False, "error": "Пользователь не найден"}, status=404)

        vless_sub = await vless_subscription_repo.get_subscription_by_user_id(session, user.id)
        wi_sub = await get_subscription_by_user_id(session, user.id)
        new_token: str | None = None
        old_vless_uuid: str | None = None
        old_wi_uuid: str | None = wi_sub.uuid if wi_sub is not None else None
        if vless_sub is not None:
            new_token, old_vless_uuid, _new_u = await vless_subscription_repo.rotate_token(
                session, vless_sub.id, reset_hwids=True
            )
            vless_sub = (
                await vless_subscription_repo.get_subscription_by_user_id(session, user.id)
                or vless_sub
            )
        if wi_sub is not None:
            wi_token = await white_internet_repo.reset_subscription_token_atomic(
                session, wi_sub.id, new_token=new_token, reset_hwids=True
            )
            new_token = new_token or wi_token
            wi_sub = (
                await white_internet_repo.get_subscription_with_lock(session, wi_sub.id)
                or wi_sub
            )

        existing_devs = (await session.scalars(select(UserDevice).where(UserDevice.user_id == user.id))).all()
        dev_uuids = [d.device_uuid for d in existing_devs if getattr(d, "device_uuid", None)]

        if vless_sub is not None:
            if dev_uuids:
                vless_sub = (
                    await vless_subscription_repo.add_pending_revocation_uuids(
                        session, vless_sub.id, dev_uuids
                    )
                    or vless_sub
                )
            vless_rev_uuids = list(
                dict.fromkeys([u for u in [old_vless_uuid, *dev_uuids] if u])
            )
            sub_ver = getattr(vless_sub, "version", None)
            for u_uuid in vless_rev_uuids:
                VlessSubscriptionService.deprovision_background(
                    u_uuid,
                    version=sub_ver,
                    sub_id=vless_sub.id,
                    session=session,
                    services=("vless",),
                )

        if wi_sub is not None:
            wi_rev_uuids = list(
                dict.fromkeys([u for u in [old_wi_uuid, *dev_uuids] if u])
            )
            if wi_rev_uuids:
                await white_internet_repo.add_pending_revocation_uuids(
                    session, wi_sub.id, wi_rev_uuids
                )
                if wi_sub.status in (WhiteInternetStatus.ACTIVE, WhiteInternetStatus.PENDING):
                    wi_sub.desired_version = (wi_sub.desired_version or 1) + 1
                    wi_sub.provisioning_status = WhiteInternetProvisioningStatus.PENDING_UPDATE
                await session.flush()
                await _queue_wi_revoke_uuids(
                    session, wi_sub, wi_rev_uuids, context="admin_token_rotate"
                )

        await session.execute(
            delete(UserDevice).where(UserDevice.user_id == user.id)
        )
        if vless_sub is not None:
            VlessSubscriptionService.ensure_synced_background(user.id, session=session)
        if wi_sub is not None:
            await _queue_wi_inline_sync(session, wi_sub, "token_rotate")
        await AuditService.log_action(
            session,
            admin_id=ctx.telegram_id,
            action=AdminAuditAction.VLESS_TOKEN_RESET,
            target_type="user",
            target_id=user.id,
        )
        if notify_user:
            _queue_user_notification(
                session,
                request.app.get("bot"),
                user_id=user.id,
                telegram_id=user.telegram_id,
                text="🔑 Ссылка вашей подписки перевыпущена администратором. Откройте приложение для обновления.",
            )

    return web.json_response({
        "ok": True,
        "subscription_url": _build_sub_url(new_token) if new_token else None,
        "subscription_backup_url": _build_backup_sub_url(new_token) if new_token else None,
        "message": "Токен подписки перевыпущен, старая ссылка отключена",
    })


async def api_admin_user_message_handler(request: web.Request) -> web.Response:
    ctx, err_resp = require_admin_user(request)
    if err_resp is not None or ctx is None:
        return err_resp  # type: ignore[return-value]

    user_id, id_err = _parse_positive_int(request, "user_id", label="user id")
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
        return _safe_admin_error_response(exc, status=404)
    except ValueError as exc:
        return _safe_admin_error_response(exc)

    return web.json_response({"ok": True, **res, "message": "Сообщение отправлено"})


async def api_admin_broadcasts_list_handler(request: web.Request) -> web.Response:
    ctx, err_resp = require_admin_user(request)
    if err_resp is not None or ctx is None:
        return err_resp  # type: ignore[return-value]

    try:
        limit = min(50, max(1, int(request.query.get("limit", "15"))))
    except ValueError:
        limit = 15
    try:
        offset = max(0, int(request.query.get("offset", "0")))
    except ValueError:
        offset = 0

    async with session_scope() as session:
        items, total = await broadcast_service.list_broadcasts(session, limit=limit, offset=offset)
    return web.json_response({
        "ok": True,
        "broadcasts": items,
        "total": total,
        "offset": offset,
        "limit": limit,
    })


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
        return _safe_admin_error_response(exc)

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
    except broadcast_service.BroadcastConflictError as exc:
        return _safe_admin_error_response(exc, status=409)
    except ValueError as exc:
        return _safe_admin_error_response(exc, status=400)

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
        return _safe_admin_error_response(exc)

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
        return _safe_admin_error_response(exc)

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
    attention_only = request.query.get("attention_only", "").strip().lower() in ("1", "true", "yes")
    offset = max(0, int(request.query.get("offset", "0") or 0) if request.query.get("offset", "0").isdigit() else 0)
    raw_limit = int(request.query.get("limit", "25") or 25) if request.query.get("limit", "25").isdigit() else 25
    limit = min(100, max(1, raw_limit))

    async with session_scope() as session:
        data = await admin_tools_service.list_finances(
            session,
            kind=kind,
            status=status,
            q=query_str,
            attention_only=attention_only,
            offset=offset,
            limit=limit,
        )
    return web.json_response({"ok": True, **data})


async def api_admin_audit_logs_handler(request: web.Request) -> web.Response:
    ctx, err_resp = require_admin_user(request)
    if err_resp is not None or ctx is None:
        return err_resp  # type: ignore[return-value]

    action = request.query.get("action", "").strip()
    target_type = request.query.get("target_type", "").strip()
    query_str = request.query.get("q", "").strip()
    offset = max(0, int(request.query.get("offset", "0") or 0) if request.query.get("offset", "0").isdigit() else 0)
    raw_limit = int(request.query.get("limit", "40") or 40) if request.query.get("limit", "40").isdigit() else 40
    limit = min(100, max(1, raw_limit))

    async with session_scope() as session:
        data = await admin_tools_service.list_audit_logs(
            session,
            action=action,
            target_type=target_type,
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


async def api_admin_overview_handler(request: web.Request) -> web.Response:
    """Return compact system pulse for admin overview dashboard."""
    ctx, err_resp = require_admin_user(request)
    if err_resp is not None or ctx is None:
        return err_resp  # type: ignore[return-value]

    async with session_scope() as session:
        nodes = await admin_nodes_service.list_nodes(session)
        online = 0
        degraded = 0
        offline = 0
        disabled = 0
        problems: list[dict[str, Any]] = []
        for n in nodes:
            is_active = bool(n.get("is_active", False))
            h_state = str(n.get("health_state") or "")
            disabled_reason = n.get("disabled_reason")
            is_manual_disabled = (
                h_state == ServerHealthState.MANUAL_DISABLED
                or str(disabled_reason or "").strip().lower() == "manual"
            )
            if not is_active or h_state == ServerHealthState.MANUAL_DISABLED:
                disabled += 1
            elif h_state == ServerHealthState.ONLINE:
                online += 1
            elif h_state == ServerHealthState.WAITING_CONFIRMATION:
                degraded += 1
            elif h_state == ServerHealthState.PROBLEM:
                offline += 1

            err_count = int(n.get("module_errors_count") or 0)
            ingress_prob = bool(n.get("ingress_problem", False))
            cdn_prob = bool(n.get("cdn_problem", False))
            if not is_manual_disabled and (
                not is_active
                or h_state != ServerHealthState.ONLINE
                or bool(disabled_reason)
                or ingress_prob
                or cdn_prob
                or err_count > 0
            ):
                problems.append({
                    "id": n.get("id"),
                    "name": n.get("name"),
                    "role": n.get("role"),
                    "health_state": h_state,
                    "disabled_reason": disabled_reason,
                    "ingress_problem": ingress_prob,
                    "cdn_problem": cdn_prob,
                    "module_errors_count": err_count,
                })

        maint_val = await get_system_setting(session, "payments_maintenance_enabled", "false")
        payments_maintenance_enabled = str(maint_val or "").strip().lower() in ("true", "1", "yes")

        finances_summary = await admin_tools_service.get_overview_finances_summary(session)
        user_counts = await users_repo.get_user_filter_counts(session)

        active_bp = await session.scalar(
            select(BroadcastProgress)
            .where(BroadcastProgress.status == "in_progress")
            .order_by(BroadcastProgress.id.desc())
            .limit(1)
        )
        active_broadcast = broadcast_service.serialize_broadcast(active_bp) if active_bp is not None else None

        pending_wh = int(
            (
                await session.scalar(
                    select(func.count(WebhookInbox.id)).where(
                        WebhookInbox.status.in_([WebhookInboxStatus.PENDING, WebhookInboxStatus.RETRY])
                    )
                )
            )
            or 0
        )
        dead_wh = int(
            (
                await session.scalar(
                    select(func.count(WebhookInbox.id)).where(
                        WebhookInbox.status == WebhookInboxStatus.DEAD
                    )
                )
            )
            or 0
        )

    return web.json_response({
        "ok": True,
        "nodes": {
            "total": len(nodes),
            "online": online,
            "degraded": degraded,
            "offline": offline,
            "disabled": disabled,
            "problems": problems,
        },
        "payments_maintenance_enabled": payments_maintenance_enabled,
        "finances": finances_summary,
        "users": {
            "total": user_counts.get("all", 0),
            "active_standard": user_counts.get("active_standard", 0),
            "active_wi": user_counts.get("active_wi", 0),
            "expiring_3d": user_counts.get("expiring_3d", 0),
        },
        "active_broadcast": active_broadcast,
        "workers": get_worker_health_snapshot(),
        "webhooks": {
            "pending": pending_wh,
            "dead": dead_wh,
        },
    })


async def api_admin_nodes_sync_topology_handler(request: web.Request) -> web.Response:
    """Trigger immediate topology sync across active nodes."""
    ctx, err_resp = require_admin_user(request)
    if err_resp is not None or ctx is None:
        return err_resp  # type: ignore[return-value]

    try:
        res = await admin_nodes_service.trigger_topology_sync(ctx.telegram_id)
        return web.json_response({"ok": True, **res})
    except AdminNodeServiceError as exc:
        return _service_error_response(exc)


async def api_admin_nodes_resync_clients_handler(request: web.Request) -> web.Response:
    """Trigger immediate client resync on a specific node."""
    ctx, err_resp = require_admin_user(request)
    if err_resp is not None or ctx is None:
        return err_resp  # type: ignore[return-value]

    server_id, id_err = _parse_positive_int(request, "server_id", label="node id")
    if id_err is not None or server_id is None:
        return id_err  # type: ignore[return-value]

    try:
        res = await admin_nodes_service.trigger_node_clients_resync(server_id, ctx.telegram_id)
        return web.json_response({"ok": True, **res})
    except AdminNodeServiceError as exc:
        return _service_error_response(exc)


async def api_admin_nodes_lifecycle_handler(request: web.Request) -> web.Response:
    """Set node lifecycle_status (active | decommissioning)."""
    ctx, err_resp = require_admin_user(request)
    if err_resp is not None or ctx is None:
        return err_resp  # type: ignore[return-value]

    server_id, id_err = _parse_positive_int(request, "server_id", label="node id")
    if id_err is not None or server_id is None:
        return id_err  # type: ignore[return-value]

    payload, parse_err = await _parse_json_model(request, NodeLifecyclePayload)
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
        return _service_error_response(exc)


async def api_admin_webhooks_handler(request: web.Request) -> web.Response:
    """Return latest 30 pending/retry/dead rows from WebhookInbox."""
    ctx, err_resp = require_admin_user(request)
    if err_resp is not None or ctx is None:
        return err_resp  # type: ignore[return-value]

    async with session_scope() as session:
        rows = (
            await session.scalars(
                select(WebhookInbox)
                .where(
                    WebhookInbox.status.in_([
                        WebhookInboxStatus.PENDING,
                        WebhookInboxStatus.RETRY,
                        WebhookInboxStatus.DEAD,
                    ])
                )
                .order_by(WebhookInbox.received_at.desc(), WebhookInbox.id.desc())
                .limit(30)
            )
        ).all()

    items = [
        {
            "id": row.id,
            "event_key": row.event_key,
            "provider": row.provider,
            "event_type": row.event_type,
            "provider_object_id": row.provider_object_id,
            "public_order_id": row.public_order_id,
            "payment_external_id": row.payment_external_id,
            "status": row.status,
            "received_at": row.received_at.isoformat() if row.received_at else None,
        }
        for row in rows
    ]
    return web.json_response({"ok": True, "webhooks": items})

