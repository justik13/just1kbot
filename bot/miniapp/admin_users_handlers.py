"""Admin HTTP handlers for user search, details, devices, resync, and token rotation."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from typing import Any
from urllib.parse import unquote

from aiohttp import web
from sqlalchemy import delete, func, or_, select

from bot.miniapp.admin_common import (
    MAX_INT32,
    MAX_INT64,
    parse_bounded_int_query,
    parse_json_model,
    parse_positive_int,
    queue_user_notification,
    queue_wi_inline_sync,
    queue_wi_revoke_uuids,
    safe_admin_error_response,
)
from bot.miniapp.auth import require_admin_user
from bot.miniapp.handlers_user import _build_backup_sub_url, _build_sub_url
from bot.miniapp.schemas import (
    AdminDirectMessagePayload,
    AdminUserOptionalNotifyPayload,
)
from config.constants import VPN_ACCESS_GRACE_HOURS
from config.enums import (
    AdminAuditAction,
    WhiteInternetProvisioningStatus,
    WhiteInternetStatus,
)
from database.connection import session_scope
from database.models import (
    AccountLedgerEntry,
    AuditLog,
    DeviceRegistrationEvent,
    Order,
    User,
    UserDevice,
    VlessSubscription,
    WhiteInternetSubscription,
)
from database.repositories import (
    user_devices_repo,
    users_repo,
    vless_subscription_repo,
    white_internet_repo,
)
from database.repositories.account_ledger_repo import (
    get_account_balance,
    get_account_balances_batch,
    get_account_history,
)
from database.repositories.white_internet_repo import get_subscription_by_user_id
from services import admin_tools_service
from services.audit_service import AuditService
from services.order_service import acquire_user_account_lock
from services.subscription import SubscriptionService
from services.vless_subscription_service import VlessSubscriptionService
from utils.datetime_helpers import now_utc


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


async def api_admin_users_handler(request: web.Request) -> web.Response:
    """Return user statistics and paginated user list with balances and subscriptions."""
    ctx, err_resp = await require_admin_user(request)
    if err_resp is not None or ctx is None:
        return err_resp  # type: ignore[return-value]

    q = (request.query.get("q") or "").strip()
    flt = (request.query.get("filter") or "all").strip()
    limit = parse_bounded_int_query(request, "limit", default=10, min_value=1, max_value=50)
    offset = parse_bounded_int_query(request, "offset", default=0, min_value=0, max_value=MAX_INT32)

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
            if q.isdigit() and int(q) <= MAX_INT64:
                q_num = int(q)
                id_conds = [User.telegram_id == q_num]
                if 0 < q_num <= MAX_INT32:
                    id_conds.append(User.id == q_num)
                stmt = stmt.where(or_(*id_conds))
            else:
                effective_q = q.lstrip("@") or q
                escaped_q = effective_q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
                pattern = f"%{escaped_q}%"
                stmt = stmt.where(
                    or_(
                        User.username.ilike(pattern, escape="\\"),
                        User.first_name.ilike(pattern, escape="\\"),
                        User.id.in_(
                            select(UserDevice.user_id).where(UserDevice.hwid.ilike(pattern, escape="\\"))
                        ),
                        User.id.in_(
                            select(VlessSubscription.user_id).where(
                                or_(
                                    VlessSubscription.uuid.ilike(pattern, escape="\\"),
                                    VlessSubscription.token.ilike(pattern, escape="\\"),
                                )
                            )
                        ),
                        User.id.in_(
                            select(WhiteInternetSubscription.user_id).where(
                                or_(
                                    WhiteInternetSubscription.uuid.ilike(pattern, escape="\\"),
                                    WhiteInternetSubscription.token.ilike(pattern, escape="\\"),
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


async def api_admin_user_details_handler(request: web.Request) -> web.Response:
    ctx, err_resp = await require_admin_user(request)
    if err_resp is not None or ctx is None:
        return err_resp  # type: ignore[return-value]

    target_user_id, id_err = parse_positive_int(request, "user_id", label="user id")
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
        recent_orders_breakdown = await admin_tools_service.build_orders_payment_breakdown(
            session, recent_orders_rows
        )
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
            "server_now": now.isoformat(),
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
                "standard_grace_hours": VPN_ACCESS_GRACE_HOURS,
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
                        **recent_orders_breakdown[o.id],
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


async def _revoke_wi_device_uuids(
    session,
    wi_sub: WhiteInternetSubscription | None,
    uuids: list[str],
    *,
    context: str,
    reason: str,
) -> None:
    """Bump WI subscription version and queue revocation + inline sync for removed device UUIDs."""
    if wi_sub is None or not uuids:
        return
    wi_sub = await white_internet_repo.get_subscription_with_lock(session, wi_sub.id) or wi_sub
    await white_internet_repo.add_pending_revocation_uuids(session, wi_sub.id, uuids)
    wi_sub.desired_version = (wi_sub.desired_version or 1) + 1
    if wi_sub.status in (WhiteInternetStatus.ACTIVE, WhiteInternetStatus.PENDING):
        wi_sub.provisioning_status = WhiteInternetProvisioningStatus.PENDING_UPDATE
    await session.flush()
    await queue_wi_revoke_uuids(session, wi_sub, uuids, context=context)
    if wi_sub.status in (WhiteInternetStatus.ACTIVE, WhiteInternetStatus.PENDING):
        await queue_wi_inline_sync(session, wi_sub, reason)


async def api_admin_user_devices_reset_handler(request: web.Request) -> web.Response:
    ctx, err_resp = await require_admin_user(request)
    if err_resp is not None or ctx is None:
        return err_resp  # type: ignore[return-value]

    target_user_id, id_err = parse_positive_int(request, "user_id", label="user id")
    if id_err is not None or target_user_id is None:
        return id_err  # type: ignore[return-value]

    notify_user = False
    raw_text = await request.text()
    if raw_text.strip():
        opt_payload, parse_err = await parse_json_model(request, AdminUserOptionalNotifyPayload)
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

        await _revoke_wi_device_uuids(
            session,
            wi_sub,
            dev_uuids,
            context="admin_devices_reset",
            reason="devices_reset",
        )
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
            queue_user_notification(
                session,
                request.app.get("bot"),
                user_id=user.id,
                telegram_id=user.telegram_id,
                text="Привязки устройств к вашей подписке сброшены администратором. Подключите устройства заново.",
            )

    return web.json_response({"ok": True, "message": "Привязки устройств сброшены"})


async def api_admin_user_device_delete_handler(request: web.Request) -> web.Response:
    """Delete a single bound HWID device for a user and queue deprovisioning/sync."""
    ctx, err_resp = await require_admin_user(request)
    if err_resp is not None or ctx is None:
        return err_resp  # type: ignore[return-value]

    target_user_id, id_err = parse_positive_int(request, "user_id", label="user id")
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
                await vless_subscription_repo.add_pending_revocation_uuids(
                    session, vless_sub.id, [deleted_uuid]
                )
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
            await _revoke_wi_device_uuids(
                session,
                wi_sub,
                [deleted_uuid],
                context="admin_device_delete",
                reason="device_delete",
            )

        await AuditService.log_action(
            session,
            admin_id=ctx.telegram_id,
            action=AdminAuditAction.VLESS_HWID_RESET,
            target_type="user",
            target_id=user.id,
            details={"action": "delete_device", "hwid": hwid},
        )

    return web.json_response({"ok": True, "hwid": hwid, "message": "Устройство удалено"})


async def api_admin_user_token_rotate_handler(request: web.Request) -> web.Response:
    ctx, err_resp = await require_admin_user(request)
    if err_resp is not None or ctx is None:
        return err_resp  # type: ignore[return-value]

    target_user_id, id_err = parse_positive_int(request, "user_id", label="user id")
    if id_err is not None or target_user_id is None:
        return id_err  # type: ignore[return-value]

    notify_user = False
    raw_text = await request.text()
    if raw_text.strip():
        opt_payload, parse_err = await parse_json_model(request, AdminUserOptionalNotifyPayload)
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
                    wi_sub.provisioning_status = WhiteInternetProvisioningStatus.PENDING_UPDATE
                await session.flush()
                await queue_wi_revoke_uuids(
                    session, wi_sub, wi_rev_uuids, context="admin_token_rotate"
                )

        await session.execute(
            delete(UserDevice).where(UserDevice.user_id == user.id)
        )
        if vless_sub is not None:
            VlessSubscriptionService.ensure_synced_background(user.id, session=session)
        if wi_sub is not None:
            await queue_wi_inline_sync(session, wi_sub, "token_rotate")
        await AuditService.log_action(
            session,
            admin_id=ctx.telegram_id,
            action=AdminAuditAction.VLESS_TOKEN_RESET,
            target_type="user",
            target_id=user.id,
        )
        if notify_user:
            queue_user_notification(
                session,
                request.app.get("bot"),
                user_id=user.id,
                telegram_id=user.telegram_id,
                text="Ссылка вашей подписки перевыпущена администратором. Откройте приложение для обновления.",
            )

    return web.json_response({
        "ok": True,
        "subscription_url": _build_sub_url(new_token) if new_token else None,
        "subscription_backup_url": _build_backup_sub_url(new_token) if new_token else None,
        "message": "Токен подписки перевыпущен, старая ссылка отключена",
    })


async def api_admin_user_message_handler(request: web.Request) -> web.Response:
    ctx, err_resp = await require_admin_user(request)
    if err_resp is not None or ctx is None:
        return err_resp  # type: ignore[return-value]

    user_id, id_err = parse_positive_int(request, "user_id", label="user id")
    if id_err is not None or user_id is None:
        return id_err  # type: ignore[return-value]

    payload, parse_err = await parse_json_model(request, AdminDirectMessagePayload)
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
        return safe_admin_error_response(exc, status=404)
    except ValueError as exc:
        return safe_admin_error_response(exc)

    return web.json_response({"ok": True, **res, "message": "Сообщение отправлено"})
