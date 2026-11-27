"""Admin HTTP handlers for overview, webhooks, broadcasts, mass operations, finances, and audit logs."""

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from aiohttp import web
from sqlalchemy import select
from sqlalchemy.orm.attributes import flag_modified

from bot.miniapp.admin_common import (
    MAX_INT32,
    parse_bounded_int_query,
    parse_json_model,
    parse_positive_int,
    safe_admin_error_response,
)
from bot.miniapp.auth import require_admin_user
from bot.miniapp.schemas import (
    BroadcastPreviewPayload,
    BroadcastStartPayload,
    MassBonusPayload,
    MassExtendPayload,
    PaymentsMaintenancePayload,
)
from config.enums import AdminAuditAction, ServerHealthState
from database.connection import session_scope
from database.models import BroadcastProgress, Order
from database.repositories import users_repo
from database.repositories.system_settings_repo import (
    get_system_setting,
    set_system_setting,
)
from services import admin_nodes_service, admin_tools_service, broadcast_service
from services.audit_service import AuditService
from services.workers import get_worker_health_snapshot
from utils.datetime_helpers import now_utc

logger = logging.getLogger(__name__)


async def api_admin_broadcasts_list_handler(request: web.Request) -> web.Response:
    ctx, err_resp = await require_admin_user(request)
    if err_resp is not None or ctx is None:
        return err_resp  # type: ignore[return-value]

    limit = parse_bounded_int_query(request, "limit", default=15, min_value=1, max_value=50)
    offset = parse_bounded_int_query(request, "offset", default=0, min_value=0, max_value=MAX_INT32)

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
    ctx, err_resp = await require_admin_user(request)
    if err_resp is not None or ctx is None:
        return err_resp  # type: ignore[return-value]

    payload, parse_err = await parse_json_model(request, BroadcastPreviewPayload)
    if parse_err is not None or payload is None:
        return parse_err  # type: ignore[return-value]

    try:
        async with session_scope() as session:
            reach = await broadcast_service.count_broadcast_audience(
                session,
                payload.target_audience,
                admin_id=ctx.telegram_id,
                include_bot_blocked=False,
                service_scope=payload.service_scope,
            )
            total_with_blocked = await broadcast_service.count_broadcast_audience(
                session,
                payload.target_audience,
                admin_id=ctx.telegram_id,
                include_bot_blocked=True,
                service_scope=payload.service_scope,
            )
            label = await broadcast_service.resolve_audience_label(
                session, payload.target_audience
            )
    except ValueError as exc:
        return safe_admin_error_response(exc)

    return web.json_response({
        "ok": True,
        "target_audience": payload.target_audience,
        "label": label,
        "reachable_count": reach,
        "total_count": total_with_blocked,
    })


async def api_admin_broadcast_start_handler(request: web.Request) -> web.Response:
    ctx, err_resp = await require_admin_user(request)
    if err_resp is not None or ctx is None:
        return err_resp  # type: ignore[return-value]

    payload, parse_err = await parse_json_model(request, BroadcastStartPayload)
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
        return safe_admin_error_response(exc, status=409)
    except ValueError as exc:
        return safe_admin_error_response(exc, status=400)

    return web.json_response({"ok": True, "broadcast": item, "message": "Рассылка запущена"})


async def api_admin_broadcast_cancel_handler(request: web.Request) -> web.Response:
    ctx, err_resp = await require_admin_user(request)
    if err_resp is not None or ctx is None:
        return err_resp  # type: ignore[return-value]

    broadcast_id, id_err = parse_positive_int(request, "broadcast_id")
    if id_err is not None or broadcast_id is None:
        return id_err  # type: ignore[return-value]

    async with session_scope() as session:
        item = await broadcast_service.cancel_broadcast(session, broadcast_id, ctx.telegram_id)
    if item is None:
        return web.json_response({"ok": False, "error": "Рассылка не найдена"}, status=404)
    broadcast_service.cancel_broadcast_task(broadcast_id)
    return web.json_response({"ok": True, "broadcast": item, "message": "Рассылка остановлена"})


async def api_admin_mass_bonus_handler(request: web.Request) -> web.Response:
    ctx, err_resp = await require_admin_user(request)
    if err_resp is not None or ctx is None:
        return err_resp  # type: ignore[return-value]

    payload, parse_err = await parse_json_model(request, MassBonusPayload)
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
        return safe_admin_error_response(exc)

    return web.json_response({
        "ok": True,
        **res,
        "message": f"Начисление +{payload.amount_rub} ₽ запущено для {res['target_count']} польз.",
    })


async def api_admin_mass_extend_handler(request: web.Request) -> web.Response:
    ctx, err_resp = await require_admin_user(request)
    if err_resp is not None or ctx is None:
        return err_resp  # type: ignore[return-value]

    payload, parse_err = await parse_json_model(request, MassExtendPayload)
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
        return safe_admin_error_response(exc)

    return web.json_response({
        "ok": True,
        **res,
        "message": f"Продление +{payload.days} дн. запущено для {res['target_count']} польз.",
    })


async def api_admin_finances_handler(request: web.Request) -> web.Response:
    ctx, err_resp = await require_admin_user(request)
    if err_resp is not None or ctx is None:
        return err_resp  # type: ignore[return-value]

    kind = request.query.get("kind", "orders").strip()
    if kind not in ("orders", "subscriptions", "topups", "gateway"):
        kind = "orders"
    status = request.query.get("status", "").strip()
    query_str = request.query.get("q", "").strip()
    attention_only = request.query.get("attention_only", "").strip().lower() in ("1", "true", "yes")
    offset = parse_bounded_int_query(request, "offset", default=0, min_value=0, max_value=MAX_INT32)
    limit = parse_bounded_int_query(request, "limit", default=25, min_value=1, max_value=100)

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
    ctx, err_resp = await require_admin_user(request)
    if err_resp is not None or ctx is None:
        return err_resp  # type: ignore[return-value]

    action = request.query.get("action", "").strip()
    target_type = request.query.get("target_type", "").strip()
    query_str = request.query.get("q", "").strip()
    offset = parse_bounded_int_query(request, "offset", default=0, min_value=0, max_value=MAX_INT32)
    limit = parse_bounded_int_query(request, "limit", default=40, min_value=1, max_value=100)

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
    ctx, err_resp = await require_admin_user(request)
    if err_resp is not None or ctx is None:
        return err_resp  # type: ignore[return-value]

    async with session_scope() as session:
        val = await get_system_setting(session, "payments_maintenance_enabled", "false")
        enabled = str(val or "").strip().lower() in ("true", "1", "yes")

    return web.json_response({"ok": True, "enabled": enabled})


async def api_admin_payments_maintenance_post_handler(request: web.Request) -> web.Response:
    """Toggle payments maintenance mode."""
    ctx, err_resp = await require_admin_user(request)
    if err_resp is not None or ctx is None:
        return err_resp  # type: ignore[return-value]

    payload, parse_err = await parse_json_model(request, PaymentsMaintenancePayload)
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
    ctx, err_resp = await require_admin_user(request)
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
    })


async def api_admin_order_ack_review_handler(request: web.Request) -> web.Response:
    """Clear requires_manual_review flag on an order after admin review."""
    ctx, err_resp = await require_admin_user(request)
    if err_resp is not None or ctx is None:
        return err_resp  # type: ignore[return-value]

    raw_order_id = (request.match_info.get("order_id") or "").strip()
    try:
        order_uuid = UUID(raw_order_id)
    except ValueError:
        return web.json_response({"ok": False, "error": "Некорректный ID заказа"}, status=400)

    async with session_scope() as session:
        order = await session.scalar(
            select(Order).where(Order.id == order_uuid).with_for_update()
        )
        if order is None:
            return web.json_response({"ok": False, "error": "Заказ не найден"}, status=404)

        meta = dict(order.metadata_ or {})
        was_flagged = bool(meta.get("requires_manual_review") or meta.get("manual_review"))
        meta.pop("requires_manual_review", None)
        meta.pop("manual_review", None)
        meta["manual_review_acked_by"] = ctx.telegram_id
        meta["manual_review_acked_at"] = now_utc().isoformat()
        order.metadata_ = meta
        flag_modified(order, "metadata_")

        await AuditService.log_action(
            session,
            admin_id=ctx.telegram_id,
            action=AdminAuditAction.PAYMENT_DISPUTE_RESOLVED,
            target_type="order",
            target_id=order.user_id,
            details={
                "order_id": str(order.id),
                "action": "ack_manual_review",
                "was_flagged": was_flagged,
            },
        )

    return web.json_response({
        "ok": True,
        "order_id": str(order_uuid),
        "message": "Проверка подтверждена",
    })
