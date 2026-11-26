"""Admin HTTP handlers for overview, webhooks, broadcasts, mass operations, finances, and audit logs."""

from __future__ import annotations

import logging
from typing import Any

from aiohttp import web
from sqlalchemy import func, select

import bot.miniapp.handlers_admin as _ha
from bot.miniapp.schemas import (
    BroadcastPreviewPayload,
    BroadcastStartPayload,
    MassBonusPayload,
    MassExtendPayload,
    PaymentsMaintenancePayload,
)
from config.enums import AdminAuditAction, ServerHealthState, WebhookInboxStatus
from database.models import BroadcastProgress, WebhookInbox

logger = logging.getLogger(__name__)


async def api_admin_broadcasts_list_handler(request: web.Request) -> web.Response:
    ctx, err_resp = _ha.require_admin_user(request)
    if err_resp is not None or ctx is None:
        return err_resp  # type: ignore[return-value]

    limit = _ha._parse_bounded_int_query(request, "limit", default=15, min_value=1, max_value=50)
    offset = _ha._parse_bounded_int_query(request, "offset", default=0, min_value=0, max_value=_ha._MAX_INT32)

    async with _ha.session_scope() as session:
        items, total = await _ha.broadcast_service.list_broadcasts(session, limit=limit, offset=offset)
    return web.json_response({
        "ok": True,
        "broadcasts": items,
        "total": total,
        "offset": offset,
        "limit": limit,
    })


async def api_admin_broadcast_preview_handler(request: web.Request) -> web.Response:
    ctx, err_resp = _ha.require_admin_user(request)
    if err_resp is not None or ctx is None:
        return err_resp  # type: ignore[return-value]

    payload, parse_err = await _ha._parse_json_model(request, BroadcastPreviewPayload)
    if parse_err is not None or payload is None:
        return parse_err  # type: ignore[return-value]

    try:
        async with _ha.session_scope() as session:
            reach = await _ha.broadcast_service.count_broadcast_audience(
                session,
                payload.target_audience,
                admin_id=ctx.telegram_id,
                include_bot_blocked=False,
            )
            total_with_blocked = await _ha.broadcast_service.count_broadcast_audience(
                session,
                payload.target_audience,
                admin_id=ctx.telegram_id,
                include_bot_blocked=True,
            )
            label = await _ha.broadcast_service.resolve_audience_label(
                session, payload.target_audience
            )
    except ValueError as exc:
        return _ha._safe_admin_error_response(exc)

    return web.json_response({
        "ok": True,
        "target_audience": payload.target_audience,
        "label": label,
        "reachable_count": reach,
        "total_count": total_with_blocked,
    })


async def api_admin_broadcast_start_handler(request: web.Request) -> web.Response:
    ctx, err_resp = _ha.require_admin_user(request)
    if err_resp is not None or ctx is None:
        return err_resp  # type: ignore[return-value]

    payload, parse_err = await _ha._parse_json_model(request, BroadcastStartPayload)
    if parse_err is not None or payload is None:
        return parse_err  # type: ignore[return-value]

    try:
        item = await _ha.broadcast_service.create_and_start_broadcast(
            request.app.get("bot"),
            admin_id=ctx.telegram_id,
            target_audience=payload.target_audience,
            text=payload.text,
            media_url=payload.media_url,
        )
    except _ha.broadcast_service.BroadcastConflictError as exc:
        return _ha._safe_admin_error_response(exc, status=409)
    except ValueError as exc:
        return _ha._safe_admin_error_response(exc, status=400)

    return web.json_response({"ok": True, "broadcast": item, "message": "Рассылка запущена"})


async def api_admin_broadcast_cancel_handler(request: web.Request) -> web.Response:
    ctx, err_resp = _ha.require_admin_user(request)
    if err_resp is not None or ctx is None:
        return err_resp  # type: ignore[return-value]

    broadcast_id, id_err = _ha._parse_positive_int(request, "broadcast_id")
    if id_err is not None or broadcast_id is None:
        return id_err  # type: ignore[return-value]

    async with _ha.session_scope() as session:
        item = await _ha.broadcast_service.cancel_broadcast(session, broadcast_id, ctx.telegram_id)
    if item is None:
        return web.json_response({"ok": False, "error": "Рассылка не найдена"}, status=404)
    _ha.broadcast_service.cancel_broadcast_task(broadcast_id)
    return web.json_response({"ok": True, "broadcast": item, "message": "Рассылка остановлена"})


async def api_admin_mass_bonus_handler(request: web.Request) -> web.Response:
    ctx, err_resp = _ha.require_admin_user(request)
    if err_resp is not None or ctx is None:
        return err_resp  # type: ignore[return-value]

    payload, parse_err = await _ha._parse_json_model(request, MassBonusPayload)
    if parse_err is not None or payload is None:
        return parse_err  # type: ignore[return-value]

    try:
        res = await _ha.admin_tools_service.start_mass_bonus(
            request.app.get("bot"),
            admin_id=ctx.telegram_id,
            target_audience=payload.target_audience,
            amount_rub=payload.amount_rub,
            reason=payload.reason,
            notify_users=payload.notify_users,
        )
    except ValueError as exc:
        return _ha._safe_admin_error_response(exc)

    return web.json_response({
        "ok": True,
        **res,
        "message": f"Начисление +{payload.amount_rub} ₽ запущено для {res['target_count']} польз.",
    })


async def api_admin_mass_extend_handler(request: web.Request) -> web.Response:
    ctx, err_resp = _ha.require_admin_user(request)
    if err_resp is not None or ctx is None:
        return err_resp  # type: ignore[return-value]

    payload, parse_err = await _ha._parse_json_model(request, MassExtendPayload)
    if parse_err is not None or payload is None:
        return parse_err  # type: ignore[return-value]

    try:
        res = await _ha.admin_tools_service.start_mass_extend(
            request.app.get("bot"),
            admin_id=ctx.telegram_id,
            service_scope=payload.service_scope,
            target_audience=payload.target_audience,
            days=payload.days,
            reason=payload.reason,
            notify_users=payload.notify_users,
        )
    except ValueError as exc:
        return _ha._safe_admin_error_response(exc)

    return web.json_response({
        "ok": True,
        **res,
        "message": f"Продление +{payload.days} дн. запущено для {res['target_count']} польз.",
    })


async def api_admin_finances_handler(request: web.Request) -> web.Response:
    ctx, err_resp = _ha.require_admin_user(request)
    if err_resp is not None or ctx is None:
        return err_resp  # type: ignore[return-value]

    kind = request.query.get("kind", "orders").strip()
    if kind not in ("orders", "subscriptions", "topups", "gateway"):
        kind = "orders"
    status = request.query.get("status", "").strip()
    query_str = request.query.get("q", "").strip()
    attention_only = request.query.get("attention_only", "").strip().lower() in ("1", "true", "yes")
    offset = _ha._parse_bounded_int_query(request, "offset", default=0, min_value=0, max_value=_ha._MAX_INT32)
    limit = _ha._parse_bounded_int_query(request, "limit", default=25, min_value=1, max_value=100)

    async with _ha.session_scope() as session:
        data = await _ha.admin_tools_service.list_finances(
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
    ctx, err_resp = _ha.require_admin_user(request)
    if err_resp is not None or ctx is None:
        return err_resp  # type: ignore[return-value]

    action = request.query.get("action", "").strip()
    target_type = request.query.get("target_type", "").strip()
    query_str = request.query.get("q", "").strip()
    offset = _ha._parse_bounded_int_query(request, "offset", default=0, min_value=0, max_value=_ha._MAX_INT32)
    limit = _ha._parse_bounded_int_query(request, "limit", default=40, min_value=1, max_value=100)

    async with _ha.session_scope() as session:
        data = await _ha.admin_tools_service.list_audit_logs(
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
    ctx, err_resp = _ha.require_admin_user(request)
    if err_resp is not None or ctx is None:
        return err_resp  # type: ignore[return-value]

    async with _ha.session_scope() as session:
        val = await _ha.get_system_setting(session, "payments_maintenance_enabled", "false")
        enabled = str(val or "").strip().lower() in ("true", "1", "yes")

    return web.json_response({"ok": True, "enabled": enabled})


async def api_admin_payments_maintenance_post_handler(request: web.Request) -> web.Response:
    """Toggle payments maintenance mode."""
    ctx, err_resp = _ha.require_admin_user(request)
    if err_resp is not None or ctx is None:
        return err_resp  # type: ignore[return-value]

    payload, parse_err = await _ha._parse_json_model(request, PaymentsMaintenancePayload)
    if parse_err is not None or payload is None:
        return parse_err  # type: ignore[return-value]

    new_val = "true" if payload.enabled else "false"
    async with _ha.session_scope() as session:
        await _ha.set_system_setting(
            session,
            key="payments_maintenance_enabled",
            value=new_val,
            updated_by=ctx.telegram_id,
        )
        await _ha.AuditService.log_action(
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
    ctx, err_resp = _ha.require_admin_user(request)
    if err_resp is not None or ctx is None:
        return err_resp  # type: ignore[return-value]

    async with _ha.session_scope() as session:
        nodes = await _ha.admin_nodes_service.list_nodes(session)
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

        maint_val = await _ha.get_system_setting(session, "payments_maintenance_enabled", "false")
        payments_maintenance_enabled = str(maint_val or "").strip().lower() in ("true", "1", "yes")

        finances_summary = await _ha.admin_tools_service.get_overview_finances_summary(session)
        user_counts = await _ha.users_repo.get_user_filter_counts(session)

        active_bp = await session.scalar(
            select(BroadcastProgress)
            .where(BroadcastProgress.status == "in_progress")
            .order_by(BroadcastProgress.id.desc())
            .limit(1)
        )
        active_broadcast = _ha.broadcast_service.serialize_broadcast(active_bp) if active_bp is not None else None

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
        "workers": _ha.get_worker_health_snapshot(),
        "webhooks": {
            "pending": pending_wh,
            "dead": dead_wh,
        },
    })


async def api_admin_webhooks_handler(request: web.Request) -> web.Response:
    """Return latest 30 pending/retry/dead rows from WebhookInbox."""
    ctx, err_resp = _ha.require_admin_user(request)
    if err_resp is not None or ctx is None:
        return err_resp  # type: ignore[return-value]

    async with _ha.session_scope() as session:
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


async def _transition_webhook_status(
    request: web.Request,
    *,
    target_status: WebhookInboxStatus,
    audit_action: AdminAuditAction,
    message: str,
) -> web.Response:
    ctx, err_resp = _ha.require_admin_user(request)
    if err_resp is not None or ctx is None:
        return err_resp  # type: ignore[return-value]

    webhook_id, id_err = _ha._parse_positive_int(request, "webhook_id", label="webhook id")
    if id_err is not None or webhook_id is None:
        return id_err  # type: ignore[return-value]

    allowed_from = {WebhookInboxStatus.DEAD.value, WebhookInboxStatus.RETRY.value}
    async with _ha.session_scope() as session:
        row = await session.scalar(
            select(WebhookInbox).where(WebhookInbox.id == webhook_id).with_for_update()
        )
        if row is None:
            return web.json_response({"ok": False, "error": "Вебхук не найден"}, status=404)
        prev_status = str(row.status)
        if prev_status not in allowed_from:
            return web.json_response(
                {"ok": False, "error": "Операция доступна только для статусов dead или retry"},
                status=400,
            )
        row.status = target_status.value
        await _ha.AuditService.log_action(
            session,
            admin_id=ctx.telegram_id,
            action=audit_action,
            target_type="webhook_inbox",
            target_id=row.id,
            details={
                "event_key": row.event_key,
                "provider": row.provider,
                "from_status": prev_status,
                "to_status": target_status.value,
            },
        )

    return web.json_response({
        "ok": True,
        "id": webhook_id,
        "status": target_status.value,
        "message": message,
    })


async def api_admin_webhook_retry_handler(request: web.Request) -> web.Response:
    """Synchronously reprocess a dead/retry WebhookInbox event."""
    ctx, err_resp = _ha.require_admin_user(request)
    if err_resp is not None or ctx is None:
        return err_resp  # type: ignore[return-value]

    webhook_id, id_err = _ha._parse_positive_int(request, "webhook_id", label="webhook id")
    if id_err is not None or webhook_id is None:
        return id_err  # type: ignore[return-value]

    from bot.handlers.webhook import (
        _dispatch_yookassa_notifications,
        reprocess_webhook_inbox_row,
    )

    bot = request.app.get("bot") if hasattr(request, "app") else None
    allowed_from = {WebhookInboxStatus.DEAD.value, WebhookInboxStatus.RETRY.value}
    notify_ctx: dict[str, Any] | None = None
    response_status = 200
    response_payload: dict[str, Any] = {}

    async with _ha.session_scope() as session:
        row = await session.scalar(
            select(WebhookInbox).where(WebhookInbox.id == webhook_id).with_for_update()
        )
        if row is None:
            return web.json_response({"ok": False, "error": "Вебхук не найден"}, status=404)
        prev_status = str(row.status)
        if prev_status not in allowed_from:
            return web.json_response(
                {"ok": False, "error": "Операция доступна только для статусов dead или retry"},
                status=400,
            )

        try:
            succeeded, final_status, error_reason, notify_ctx = await reprocess_webhook_inbox_row(
                session, bot, row
            )
        except ValueError:
            return web.json_response(
                {"ok": False, "error": "Некорректный или пустой payload вебхука"},
                status=400,
            )

        audit_details: dict[str, Any] = {
            "event_key": row.event_key,
            "provider": row.provider,
            "from_status": prev_status,
            "to_status": final_status,
            "outcome": "succeeded" if succeeded else "dead",
        }
        if error_reason:
            audit_details["error"] = error_reason[:300]

        await _ha.AuditService.log_action(
            session,
            admin_id=ctx.telegram_id,
            action=AdminAuditAction.PAYMENT_QUEUE_MANUAL_RETRY,
            target_type="webhook_inbox",
            target_id=row.id,
            details=audit_details,
        )

        if succeeded:
            response_status = 200
            response_payload = {
                "ok": True,
                "id": webhook_id,
                "status": final_status,
                "message": "Вебхук повторно обработан",
            }
        elif error_reason in ("order_not_found", "amount_mismatch"):
            response_status = 400
            err_msg = (
                "Обнаружено расхождение суммы платежа; вебхук оставлен в статусе dead"
                if error_reason == "amount_mismatch"
                else "Заказ для вебхука не найден или не готов к обработке"
            )
            response_payload = {
                "ok": False,
                "id": webhook_id,
                "status": final_status,
                "error": err_msg,
            }
        else:
            response_status = 502
            response_payload = {
                "ok": False,
                "id": webhook_id,
                "status": final_status,
                "error": "Не удалось повторно обработать вебхук",
            }

    if notify_ctx is not None and bot is not None:
        await _dispatch_yookassa_notifications(bot, **notify_ctx)

    return web.json_response(response_payload, status=response_status)


async def api_admin_webhook_dismiss_handler(request: web.Request) -> web.Response:
    """Mark a dead/retry WebhookInbox event as succeeded (dismissed) with audit log."""
    return await _transition_webhook_status(
        request,
        target_status=WebhookInboxStatus.SUCCEEDED,
        audit_action=AdminAuditAction.PAYMENT_DISPUTE_RESOLVED,
        message="Вебхук скрыт",
    )
