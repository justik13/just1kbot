"""Thin HTTP handlers for Mini App identity, admin 2FA, and modular admin handler re-exports."""

from __future__ import annotations

import asyncio
import logging
from typing import Any, TypeVar

from aiogram.exceptions import TelegramForbiddenError
from aiohttp import web
from pydantic import BaseModel, ValidationError
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from bot.miniapp.auth import require_admin_user, require_authenticated_user
from bot.miniapp.handlers_user import _build_backup_sub_url, _build_sub_url  # noqa: F401
from bot.miniapp.schemas import Verify2FAPayload
from config.enums import (
    AdminAuditAction,
    WhiteInternetProvisioningStatus,
    WhiteInternetStatus,
)
from database.connection import queue_post_commit_task, session_scope
from database.models import (
    User,
    UserDevice,
    WhiteInternetSubscription,
)
from database.repositories import (
    servers_repo,
    user_devices_repo,  # noqa: F401
    users_repo,  # noqa: F401
    vless_subscription_repo,  # noqa: F401
    white_internet_repo,
)
from database.repositories.system_settings_repo import (  # noqa: F401
    get_system_setting,
    set_system_setting,
)
from database.repositories.vless_subscription_repo import (  # noqa: F401
    get_or_create_subscription,
)
from database.repositories.white_internet_repo import (  # noqa: F401
    get_subscription_by_user_id,
)
from services import (  # noqa: F401
    admin_nodes_service,
    admin_tools_service,
    broadcast_service,
)
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
from services.order_service import (  # noqa: F401
    OrderService,
    acquire_user_account_lock,
)
from services.subscription import SubscriptionService  # noqa: F401
from services.vless_subscription_service import VlessSubscriptionService  # noqa: F401
from services.white_internet_service import WhiteInternetService
from services.workers import get_worker_health_snapshot  # noqa: F401
from utils.telegram import safe_send_message

SchemaT = TypeVar("SchemaT", bound=BaseModel)
logger = logging.getLogger(__name__)
_background_tasks: set[asyncio.Task[object]] = set()
_MAX_INT32 = 2_147_483_647
_MAX_INT64 = 9_223_372_036_854_775_807


async def cancel_admin_background_tasks() -> None:
    """Cancel and drain in-flight admin Mini App background tasks on shutdown."""
    tasks = list(_background_tasks)
    for task in tasks:
        task.cancel()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)
    _background_tasks.clear()


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


def _parse_bounded_int_query(
    request: web.Request,
    name: str,
    *,
    default: int,
    min_value: int,
    max_value: int,
) -> int:
    raw = (request.query.get(name) or "").strip()
    if not raw:
        return default
    try:
        val = int(raw)
    except ValueError:
        return default
    return min(max_value, max(min_value, val))


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
    uuids_to_revoke = list(dict.fromkeys(str(u) for u in [wi_sub.uuid, *dev_rows] if u))
    if uuids_to_revoke:
        await white_internet_repo.add_pending_revocation_uuids(
            session, wi_sub.id, uuids_to_revoke
        )
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
    ctx, err_resp = await require_admin_user(request, require_2fa=False)
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
    ctx, err_resp = await require_admin_user(request, require_2fa=False)
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
    ctx, err_resp = await require_admin_user(request)
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


from bot.miniapp.admin_nodes_handlers import (  # noqa: E402, F401
    api_admin_incy_get_handler,
    api_admin_incy_put_handler,
    api_admin_nodes_add_handler,
    api_admin_nodes_delete_handler,
    api_admin_nodes_lifecycle_handler,
    api_admin_nodes_list_handler,
    api_admin_nodes_patch_handler,
    api_admin_nodes_refresh_handler,
    api_admin_nodes_toggle_handler,
)
from bot.miniapp.admin_ops_handlers import (  # noqa: E402, F401
    api_admin_audit_logs_handler,
    api_admin_broadcast_cancel_handler,
    api_admin_broadcast_preview_handler,
    api_admin_broadcast_start_handler,
    api_admin_broadcasts_list_handler,
    api_admin_finances_handler,
    api_admin_mass_bonus_handler,
    api_admin_mass_extend_handler,
    api_admin_order_ack_review_handler,
    api_admin_overview_handler,
    api_admin_payments_maintenance_get_handler,
    api_admin_payments_maintenance_post_handler,
    api_admin_webhook_dismiss_handler,
    api_admin_webhook_retry_handler,
    api_admin_webhooks_handler,
)
from bot.miniapp.admin_users_handlers import (  # noqa: E402, F401
    api_admin_user_adjust_handler,
    api_admin_user_details_handler,
    api_admin_user_device_delete_handler,
    api_admin_user_devices_reset_handler,
    api_admin_user_message_handler,
    api_admin_user_token_rotate_handler,
    api_admin_users_handler,
)
