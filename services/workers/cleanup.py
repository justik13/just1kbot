import asyncio
import logging
import time
from datetime import timedelta
from typing import Any

from cachetools import TTLCache
from sqlalchemy import and_, delete, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from config.constants import (
    VPN_ACCESS_GRACE_HOURS,
)
from database.connection import session_scope
from database.models import (
    DeviceRegistrationEvent,
    Order,
    User,
    VlessSubscription,
    WebhookInbox,
)
from database.repositories.audit_repo import clear_audit_logs
from services.subscription import SubscriptionService
from utils.datetime_helpers import now_utc

logger = logging.getLogger(__name__)

PENDING_RETRY_INTERVAL = 3600
CLEANUP_START_DELAY = 60.0
CLEANUP_LOOP_INTERVAL = 3600.0
RECONCILE_LOOP_INTERVAL = 60.0
OLD_RECORDS_INTERVAL = 86400.0

AUDIT_LOG_RETENTION_DAYS = 180
WEBHOOK_INBOX_RETENTION_DAYS = 30

_last_heavy_cleanup: float | None = None
_last_old_cleanup: float = 0.0
_pending_order_backoff_cache: TTLCache[Any, float] = TTLCache(maxsize=2000, ttl=300.0)


async def cleanup_expired_subscriptions_loop(
    shutdown_event: asyncio.Event | None = None,
):
    global _last_old_cleanup, _last_heavy_cleanup

    event = shutdown_event or asyncio.Event()

    try:
        await asyncio.wait_for(
            event.wait(),
            timeout=CLEANUP_START_DELAY,
        )
        logger.info("Cleanup worker stopped during start delay (shutdown)")
        return
    except asyncio.TimeoutError:
        pass

    while not event.is_set():
        try:
            now = time.monotonic()
            if _last_heavy_cleanup is None or (now - _last_heavy_cleanup) >= CLEANUP_LOOP_INTERVAL:
                await _cleanup_expired_vless_network_grace()
                await _sweep_vless_pending_revocations()
                await _apply_due_white_internet_downgrades()
                _last_heavy_cleanup = now

            await _reconcile_stale_pending_orders()

            if now - _last_old_cleanup > OLD_RECORDS_INTERVAL:
                await _cleanup_old_records()
                _last_old_cleanup = now

        except asyncio.CancelledError:
            logger.info("Cleanup worker cancelled")
            break
        except Exception as e:
            logger.error(
                "Error in cleanup worker: %s",
                e,
                exc_info=True,
            )

        try:
            await asyncio.wait_for(
                event.wait(),
                timeout=RECONCILE_LOOP_INTERVAL,
            )
            break
        except asyncio.TimeoutError:
            continue

    logger.info("Cleanup worker stopped gracefully")





async def _apply_due_white_internet_downgrades() -> None:
    """Apply scheduled White Internet device limit downgrades when pending_from is reached."""
    from services.white_internet_service import WhiteInternetService

    try:
        async with session_scope() as session:
            applied = await WhiteInternetService.apply_due_wi_downgrades(session, now=now_utc())
            if applied:
                logger.info("Applied %d scheduled White Internet device downgrades", applied)
    except Exception as exc:
        logger.warning("Failed to apply scheduled White Internet downgrades: %s", exc)


async def _cleanup_expired_vless_network_grace() -> None:
    """Deactivate VLESS access when network grace window (4 hours) expires or under financial hold."""
    current_time = now_utc()
    threshold = current_time - timedelta(hours=VPN_ACCESS_GRACE_HOURS)

    for _batch in range(10):
        async with session_scope() as session:
            stmt = (
                select(User.id)
                .join(VlessSubscription, VlessSubscription.user_id == User.id)
                .where(
                    User.is_deleted.is_(False),
                    or_(
                        and_(
                            VlessSubscription.expires_at.is_not(None),
                            VlessSubscription.expires_at < threshold,
                        ),
                        User.financial_hold.is_(True),
                    ),
                    VlessSubscription.is_active.is_(True),
                )
                .order_by(VlessSubscription.expires_at.asc().nullslast())
                .limit(50)
            )
            result = await session.execute(stmt)
            user_ids = [row[0] for row in result.all()]

        if not user_ids:
            break

        for user_id in user_ids:
            try:
                async with session_scope() as session:
                    user = await session.get(User, user_id, with_for_update=True)
                    if not user or user.is_deleted:
                        continue
                    from database.repositories import vless_subscription_repo
                    from utils.datetime_helpers import is_permanent_subscription

                    vless_sub = await vless_subscription_repo.get_subscription_by_user_id(session, user.id)
                    sub_end = vless_sub.expires_at if vless_sub else None
                    if not user.financial_hold and sub_end and is_permanent_subscription(sub_end):
                        continue
                    if not user.financial_hold and (not sub_end or sub_end >= threshold):
                        continue
                    await SubscriptionService._sync_access_state(session, user, vless_sub=vless_sub)
                    logger.info("VLESS network grace/hold deactivation applied for user_id=%s", user_id)
            except Exception as e:
                logger.warning("VLESS network grace deactivation failed for user_id=%s: %s", user_id, e)

        if len(user_ids) < 50:
            break


async def _sweep_vless_pending_revocations() -> None:
    """Retry deprovisioning for any pending revoked VLESS UUIDs and converge out-of-sync subscriptions."""
    from database.repositories import vless_subscription_repo
    from services.vless_subscription_service import VlessSubscriptionService

    try:
        # 1. Retry deprovisioning of old revoked UUIDs
        async with session_scope() as session:
            subs = await vless_subscription_repo.get_subscriptions_with_pending_revocations(session, limit=20)
            servers = await VlessSubscriptionService.get_revocation_target_servers(session)
            targets = VlessSubscriptionService._extract_node_targets(servers)
            exit_nodes = await VlessSubscriptionService.get_all_exit_nodes_for_revocation(session)
            sub_items = [
                (s.id, list(getattr(s, "pending_revoked_uuids", None) or []), getattr(s, "version", 1) or 1)
                for s in subs
            ] if subs else []

        if targets:
            for sub_id, rev_uuids, ver in sub_items:
                all_cleared = True
                for rev_uuid in rev_uuids:
                    cleared_this = False
                    try:
                        expanded = VlessSubscriptionService._expand_revocation_uuids(rev_uuid, exit_nodes)
                        results: dict[int, bool] = {}
                        for u in expanded:
                            res = await VlessSubscriptionService._execute_deprovision_on_nodes(targets, u, version=ver)
                            for s_id, ok in res.items():
                                results[s_id] = results.get(s_id, True) and ok
                        if (
                            servers
                            and len(targets) == len(servers)
                            and all(results.values())
                            and len(results) == len(targets)
                        ):
                            async with session_scope() as session:
                                await vless_subscription_repo.pop_pending_revoked_uuid(session, sub_id, rev_uuid)
                                logger.info("Durable deprovision cleared revoked UUID for sub_id=%s", sub_id)
                            cleared_this = True
                    except Exception as exc:
                        masked = f"{rev_uuid[:8]}***" if rev_uuid else "unknown"
                        logger.warning("Retry deprovision failed for sub_id=%s UUID %s: %s", sub_id, masked, exc)
                    if not cleared_this:
                        all_cleared = False
                if not all_cleared:
                    async with session_scope() as session:
                        sub_to_touch = await session.get(VlessSubscription, sub_id)
                        if sub_to_touch:
                            sub_to_touch.updated_at = now_utc()
                            await session.flush()

        # 2. Converge out-of-sync subscriptions (e.g. node was offline during ban or grace deactivation)
        async with session_scope() as session:
            out_of_sync_stmt = (
                select(VlessSubscription.user_id)
                .where(
                    or_(
                        VlessSubscription.last_synced_version.is_(None),
                        VlessSubscription.last_synced_version < VlessSubscription.version,
                    )
                )
                .order_by(VlessSubscription.updated_at.asc())
                .limit(20)
            )
            out_of_sync_users = list((await session.execute(out_of_sync_stmt)).scalars().all())

        for uid in out_of_sync_users:
            try:
                async with session_scope() as session:
                    res = await VlessSubscriptionService.sync_user_to_nodes(session, uid)
                    if not (res and all(res.values())):
                        sub = await vless_subscription_repo.get_subscription_by_user_id(session, uid)
                        if sub:
                            sub.updated_at = now_utc()
                            await session.flush()
            except Exception as e:
                logger.warning("Out-of-sync VLESS reconciliation failed for user_id=%s: %s", uid, e)
                try:
                    async with session_scope() as session:
                        sub = await vless_subscription_repo.get_subscription_by_user_id(session, uid)
                        if sub:
                            sub.updated_at = now_utc()
                            await session.flush()
                except Exception as inner_exc:
                    logger.debug("Failed to touch sub.updated_at for user_id=%s: %s", uid, inner_exc)

        # 3. Converge VLESS servers whose epoch drift occurred while monitor wasn't active
        async with session_scope() as session:
            vless_servers = await VlessSubscriptionService.get_configured_vless_servers(session)
            unsynced_server_ids = [
                s.id for s in vless_servers
                if s.xray_instance_epoch and (s.extra_data or {}).get("vless_synced_epoch") != s.xray_instance_epoch
            ]

        for srv_id in unsynced_server_ids:
            try:
                await VlessSubscriptionService.sync_all_active_to_server(srv_id)
            except Exception as e:
                logger.warning("VLESS server epoch convergence failed for server %s: %s", srv_id, e)
    except Exception as e:
        logger.warning("Error in _sweep_vless_pending_revocations: %s", e)


BATCH_DELETE_CHUNK_SIZE = 500
MAX_BATCH_DELETE_ROUNDS = 100


async def _batch_delete_matching(
    model,
    *where_clauses,
    session: AsyncSession | None = None,
    batch_size: int = BATCH_DELETE_CHUNK_SIZE,
    max_rounds: int = MAX_BATCH_DELETE_ROUNDS,
) -> int:
    """Delete rows matching where_clauses in bounded primary-key batches with skip_locked.

    When session is None, each batch executes and commits in its own short-lived session_scope() transaction,
    immediately releasing row-level locks in PostgreSQL.
    """
    if session is not None:
        if not hasattr(model, "id"):
            stmt = delete(model).where(*where_clauses)
            res = await session.execute(stmt)
            await session.flush()
            return int(res.rowcount or 0)

        total_deleted = 0
        for _ in range(max_rounds):
            id_stmt = (
                select(model.id)
                .where(*where_clauses)
                .limit(batch_size)
                .with_for_update(skip_locked=True)
            )
            res = await session.execute(id_stmt)
            ids = list(res.scalars().all())
            if not ids:
                break
            del_stmt = delete(model).where(model.id.in_(ids))
            del_res = await session.execute(del_stmt)
            await session.flush()
            total_deleted += int(del_res.rowcount or 0)
            if len(ids) < batch_size:
                break
            await asyncio.sleep(0.01)
        return total_deleted

    if not hasattr(model, "id"):
        async with session_scope() as sess:
            stmt = delete(model).where(*where_clauses)
            res = await sess.execute(stmt)
            return int(res.rowcount or 0)

    total_deleted = 0
    for _ in range(max_rounds):
        async with session_scope() as sess:
            id_stmt = (
                select(model.id)
                .where(*where_clauses)
                .limit(batch_size)
                .with_for_update(skip_locked=True)
            )
            res = await sess.execute(id_stmt)
            ids = list(res.scalars().all())
            if not ids:
                break
            del_stmt = delete(model).where(model.id.in_(ids))
            del_res = await sess.execute(del_stmt)
            total_deleted += int(del_res.rowcount or 0)
            if len(ids) < batch_size:
                break
        await asyncio.sleep(0.01)
    return total_deleted


async def _cleanup_old_records():
    current_time = now_utc()

    deleted_logs = await clear_audit_logs(
        older_than_days=AUDIT_LOG_RETENTION_DAYS,
    )

    # Prune old settled webhook inbox records in per-batch committed transactions.
    # "dead" is no longer written since the inbox worker was removed in PR #277,
    # but rows written before that purge still exist and must be reclaimed, so the
    # retention predicate keeps covering both settled statuses.
    threshold_webhooks = current_time - timedelta(days=WEBHOOK_INBOX_RETENTION_DAYS)
    webhooks_deleted = await _batch_delete_matching(
        WebhookInbox,
        WebhookInbox.status.in_(["succeeded", "dead"]),
        WebhookInbox.received_at < threshold_webhooks,
    )

    threshold_device_events = current_time - timedelta(hours=24)
    device_events_deleted = await _batch_delete_matching(
        DeviceRegistrationEvent,
        DeviceRegistrationEvent.created_at < threshold_device_events,
    )

    if deleted_logs > 0 or webhooks_deleted > 0 or device_events_deleted > 0:
        logger.info(
            "Cleanup: %s old audit logs, %s old webhooks, %s old device events deleted",
            deleted_logs,
            webhooks_deleted,
            device_events_deleted,
        )


async def _send_reconciliation_admin_alert(
    order_id: Any,
    user_id: int | None,
    amount_rub: Any,
    reason: str,
    external_id: str | None = None,
) -> None:
    """Send a one-time admin alert for a payment mismatch or partial refund."""
    try:
        from aiogram import Bot
        from config.settings import get_settings
        from utils.telegram import safe, safe_send_message

        settings = get_settings()
        admin_ids = getattr(settings, "ADMIN_IDS", None) or []
        bot_token = getattr(settings, "BOT_TOKEN", None)
        if not admin_ids or not bot_token:
            logger.warning(
                "Cannot send reconciliation admin alert for order %s: missing ADMIN_IDS or BOT_TOKEN",
                order_id,
            )
            return

        text = (
            "⚠️ <b>Требуется ручная проверка платежа (reconciliation)</b>\n"
            f"Заказ: <code>{safe(str(order_id))}</code>\n"
            f"Пользователь ID: <code>{safe(str(user_id))}</code>\n"
            f"Сумма: <b>{safe(str(amount_rub))} ₽</b>\n"
            f"Платеж: <code>{safe(external_id)}</code>\n"
            f"Причина: <code>{safe(reason)}</code>"
        )
        async with Bot(token=bot_token) as alert_bot:
            for aid in admin_ids:
                try:
                    await safe_send_message(alert_bot, aid, text, parse_mode="HTML")
                except Exception as exc:
                    logger.warning(
                        "Failed to send reconciliation alert to admin %s for order %s: %s",
                        aid,
                        order_id,
                        exc,
                    )
    except Exception as exc:
        logger.warning(
            "Failed to prepare reconciliation admin alert for order %s: %s",
            order_id,
            exc,
        )


def _flag_order_manual_review(
    order: Order,
    *,
    reason: str,
    is_partial_refund: bool,
    external_id: str | None,
    extra_meta: dict[str, Any] | None = None,
) -> tuple[Any, int | None, Any, str, str | None] | None:
    """Mark order metadata for manual review and return alert payload once."""
    order_meta = dict(order.metadata_ or {})
    order_meta["requires_manual_review"] = True
    order_meta["manual_review"] = True
    order_meta["payment_mismatch_reason"] = reason
    if is_partial_refund:
        order_meta["is_partial_refund"] = True
    if extra_meta:
        order_meta.update(extra_meta)
    alert_payload: tuple[Any, int | None, Any, str, str | None] | None = None
    if not order_meta.get("mismatch_alert_sent"):
        order_meta["mismatch_alert_sent"] = True
        alert_payload = (order.id, order.user_id, order.amount_rub, reason, external_id)
    order.metadata_ = order_meta
    return alert_payload


async def _reconcile_stale_pending_orders() -> None:
    """Safety net: reconcile pending orders whose webhooks were dropped or missed.

    Decoupled transaction & network boundary:
    1. Read candidate order identifiers in a fast query without holding database locks.
    2. Query external gateway HTTP outside DB transactions.
    3. Settle or cancel each order in its own isolated transaction boundary.
    4. Expire ancient pending orders (> 24 hours old).
    5. Reconcile recently canceled superseded_by_new_order YooKassa orders.
    """
    from config.enums import OrderStatus
    from integrations.payment_gateways.factory import get_payment_gateway
    from services.order_service import OrderService, acquire_user_account_lock

    now = now_utc()
    window_start = now - timedelta(hours=24)
    stale_threshold = now - timedelta(minutes=3)

    # 1. Fetch candidate order tuples in a fast read query (no lock held during external HTTP)
    candidates: list[tuple[Any, int | None, str, str]] = []
    try:
        cached_ids = list(_pending_order_backoff_cache.keys())
        async with session_scope() as session:
            stmt = (
                select(Order.id, Order.user_id, Order.external_id, Order.payment_method, Order.metadata_)
                .where(
                    Order.status == "pending",
                    Order.external_id.is_not(None),
                    Order.external_id != "",
                    Order.created_at >= window_start,
                    Order.created_at <= stale_threshold,
                )
            )
            if cached_ids:
                stmt = stmt.where(Order.id.notin_(cached_ids))
            stmt = stmt.order_by(Order.created_at.asc()).limit(20)
            res = await session.execute(stmt)
            candidates = [
                (row[0], row[1], row[2], row[3])
                for row in res.all()
                if row[0] not in _pending_order_backoff_cache
                and not (
                    isinstance(row[4], dict)
                    and (row[4].get("underpaid") or row[4].get("requires_manual_review"))
                )
            ]
    except Exception as exc:
        logger.error("Failed to query stale pending order candidates: %s", exc)
        return

    # 2. Check each candidate with the payment gateway outside DB transactions,
    # then settle/cancel in an isolated transaction per order.
    for order_id, user_id, external_id, payment_method in candidates:
        try:
            gateway = get_payment_gateway(payment_method)
            status_res = await gateway.check_payment_status(external_id)
            is_refunded = bool(getattr(status_res, "is_refunded", False))
            is_partial_refund = bool(getattr(status_res, "is_partial_refund", False))
            mismatch_reason = getattr(status_res, "mismatch_reason", None)
            needs_manual_review = is_partial_refund or bool(mismatch_reason)

            if not (status_res.is_paid or status_res.is_canceled or is_refunded or needs_manual_review):
                _pending_order_backoff_cache[order_id] = time.monotonic()
                if status_res.status_str == "not_found":
                    logger.warning(
                        "Reconciliation payment not found in gateway for order %s (external_id=%s)",
                        order_id,
                        external_id,
                    )
                continue

            alert_payload: tuple[Any, int | None, Any, str, str | None] | None = None
            # Isolated transaction for this specific order (canonical lock order: advisory -> User -> Order)
            async with session_scope() as order_session:
                if user_id is not None:
                    await acquire_user_account_lock(order_session, user_id)
                    await order_session.scalar(
                        select(User.id).where(User.id == user_id).with_for_update()
                    )
                order = await order_session.scalar(
                    select(Order).where(Order.id == order_id).with_for_update()
                )
                if not order or order.status != "pending":
                    continue
                if isinstance(order.metadata_, dict) and (
                    order.metadata_.get("underpaid") or order.metadata_.get("requires_manual_review")
                ):
                    _pending_order_backoff_cache[order_id] = time.monotonic()
                    continue

                if needs_manual_review:
                    reason_text = mismatch_reason or (
                        "partial_refund" if is_partial_refund else "payment_status_mismatch"
                    )
                    alert_payload = _flag_order_manual_review(
                        order,
                        reason=reason_text,
                        is_partial_refund=is_partial_refund,
                        external_id=external_id,
                    )
                    _pending_order_backoff_cache[order_id] = time.monotonic()
                    logger.warning(
                        "Reconciliation flagged pending order %s for manual review (reason=%s, is_partial_refund=%s)",
                        order.id,
                        reason_text,
                        is_partial_refund,
                    )
                elif status_res.is_paid:
                    paid_order = await OrderService.mark_order_paid(
                        order_session,
                        order.id,
                        external_id=external_id,
                        paid_amount_rub=status_res.amount_rub,
                    )
                    if paid_order and paid_order.status == "paid":
                        _pending_order_backoff_cache.pop(order_id, None)
                        logger.info(
                            "Reconciliation settled stale pending order %s (amount=%s rub)",
                            order.id,
                            order.amount_rub,
                        )
                    else:
                        _pending_order_backoff_cache[order_id] = time.monotonic()
                        logger.info(
                            "Reconciliation deferred stale pending order %s (withheld or underpaid)",
                            order.id,
                        )
                elif status_res.is_canceled:
                    OrderService.mark_order_canceled(
                        order,
                        reason=status_res.cancellation_reason or "gateway_canceled",
                    )
                    await OrderService.refund_reserved_mixed_debit(order_session, order)
                    _pending_order_backoff_cache.pop(order_id, None)
                    logger.info(
                        "Reconciliation canceled stale pending order %s (reason=%s)",
                        order.id,
                        status_res.cancellation_reason,
                    )
                elif is_refunded:
                    await OrderService.refund_reserved_mixed_debit(order_session, order)
                    order.status = OrderStatus.REFUNDED
                    order.refunded_at = now_utc()
                    order_meta = dict(order.metadata_ or {})
                    order_meta["refunded_during_pending"] = True
                    order.metadata_ = order_meta
                    _pending_order_backoff_cache.pop(order_id, None)
                    logger.info(
                        "Reconciliation marked pending order %s as refunded",
                        order.id,
                    )

            if alert_payload is not None:
                await _send_reconciliation_admin_alert(*alert_payload)
        except Exception as exc:
            _pending_order_backoff_cache[order_id] = time.monotonic()
            logger.warning(
                "Reconciliation check failed for order %s: %s",
                order_id,
                exc,
            )

    # 3. Expire orphaned pending orders without external_id (> 30 min old)
    # and ancient pending orders (> 24 hours old) after checking gateway if external_id exists.
    try:
        ambiguous_threshold = now - timedelta(minutes=30)
        async with session_scope() as session:
            expired_stmt = (
                select(Order.id, Order.user_id, Order.external_id, Order.payment_method, Order.metadata_)
                .where(
                    Order.status == "pending",
                    or_(
                        Order.created_at < window_start,
                        and_(
                            or_(Order.external_id.is_(None), Order.external_id == ""),
                            Order.created_at < ambiguous_threshold,
                        ),
                    ),
                )
                .order_by(Order.created_at.asc())
                .limit(50)
            )
            expired_candidates = [
                (row[0], row[1], row[2], row[3])
                for row in (await session.execute(expired_stmt)).all()
                if not (
                    isinstance(row[4], dict)
                    and (row[4].get("underpaid") or row[4].get("requires_manual_review"))
                )
            ]

        for exp_id, exp_user_id, exp_ext_id, exp_pm in expired_candidates:
            paid_status = None
            refunded_status = None
            manual_review_info: tuple[str, bool] | None = None
            if exp_ext_id:
                try:
                    gateway = get_payment_gateway(exp_pm)
                    status_res = await gateway.check_payment_status(exp_ext_id)
                    exp_is_partial = bool(getattr(status_res, "is_partial_refund", False))
                    exp_mismatch = getattr(status_res, "mismatch_reason", None)
                    if exp_is_partial or exp_mismatch:
                        manual_review_info = (
                            exp_mismatch or ("partial_refund" if exp_is_partial else "payment_status_mismatch"),
                            exp_is_partial,
                        )
                    elif status_res.is_paid:
                        paid_status = status_res
                    elif getattr(status_res, "is_refunded", False):
                        refunded_status = status_res
                except Exception as gw_exc:
                    logger.warning(
                        "Gateway check before expiry failed for order %s: %s; skipping expiry",
                        exp_id,
                        gw_exc,
                    )
                    continue

            exp_alert_payload: tuple[Any, int | None, Any, str, str | None] | None = None
            async with session_scope() as order_session:
                if exp_user_id is not None:
                    await acquire_user_account_lock(order_session, exp_user_id)
                    await order_session.scalar(
                        select(User.id).where(User.id == exp_user_id).with_for_update()
                    )
                exp_order = await order_session.scalar(
                    select(Order).where(Order.id == exp_id).with_for_update(skip_locked=True)
                )
                if not exp_order or exp_order.status != "pending":
                    continue
                if isinstance(exp_order.metadata_, dict) and (
                    exp_order.metadata_.get("underpaid") or exp_order.metadata_.get("requires_manual_review")
                ):
                    continue
                if manual_review_info is not None:
                    reason_text, is_partial = manual_review_info
                    exp_alert_payload = _flag_order_manual_review(
                        exp_order,
                        reason=reason_text,
                        is_partial_refund=is_partial,
                        external_id=exp_ext_id,
                    )
                    logger.warning(
                        "Reconciliation flagged 24h+ pending order %s for manual review (reason=%s)",
                        exp_order.id,
                        reason_text,
                    )
                elif paid_status is not None:
                    await OrderService.mark_order_paid(
                        order_session,
                        exp_order.id,
                        external_id=exp_ext_id,
                        paid_amount_rub=paid_status.amount_rub,
                    )
                    logger.info("Reconciliation settled 24h+ pending order %s before expiry", exp_order.id)
                elif refunded_status is not None:
                    await OrderService.refund_reserved_mixed_debit(order_session, exp_order)
                    exp_order.status = OrderStatus.REFUNDED
                    exp_order.refunded_at = now_utc()
                    order_meta = dict(exp_order.metadata_ or {})
                    order_meta["refunded_during_pending"] = True
                    exp_order.metadata_ = order_meta
                    logger.info("Reconciliation marked 24h+ pending order %s as refunded before expiry", exp_order.id)
                else:
                    reason = "ambiguous_creation_timeout" if not exp_order.external_id else "order_expired_24h"
                    OrderService.mark_order_canceled(exp_order, reason=reason)
                    await OrderService.refund_reserved_mixed_debit(order_session, exp_order)
                    logger.info(
                        "Reconciliation expired pending order %s (reason=%s)",
                        exp_order.id,
                        reason,
                    )

            if exp_alert_payload is not None:
                await _send_reconciliation_admin_alert(*exp_alert_payload)
    except Exception as exc:
        logger.error("Error expiring ancient pending orders: %s", exc)

    # 4. Reconcile recently canceled `superseded_by_new_order` YooKassa orders
    # so a payment completed on an old open tab with a lost webhook is still credited.
    try:
        superseded_window_start = now - timedelta(hours=2)
        cached_ids = list(_pending_order_backoff_cache.keys())
        async with session_scope() as session:
            sup_stmt = (
                select(Order.id, Order.user_id, Order.external_id, Order.payment_method, Order.metadata_)
                .where(
                    Order.status == "canceled",
                    Order.payment_method == "yookassa",
                    Order.external_id.is_not(None),
                    Order.external_id != "",
                    Order.created_at >= superseded_window_start,
                    Order.metadata_["cancellation_reason"].as_string() == "superseded_by_new_order",
                )
            )
            if cached_ids:
                sup_stmt = sup_stmt.where(Order.id.notin_(cached_ids))
            sup_stmt = sup_stmt.order_by(Order.created_at.asc()).limit(20)
            sup_candidates = [
                (row[0], row[1], row[2], row[3])
                for row in (await session.execute(sup_stmt)).all()
                if row[0] not in _pending_order_backoff_cache
                and not (
                    isinstance(row[4], dict)
                    and (
                        row[4].get("superseded_reconciled")
                        or row[4].get("underpaid")
                        or row[4].get("requires_manual_review")
                    )
                )
            ]

        for sup_id, sup_user_id, sup_ext_id, sup_pm in sup_candidates:
            try:
                gateway = get_payment_gateway(sup_pm)
                status_res = await gateway.check_payment_status(sup_ext_id)
                is_refunded = bool(getattr(status_res, "is_refunded", False))
                is_partial_refund = bool(getattr(status_res, "is_partial_refund", False))
                mismatch_reason = getattr(status_res, "mismatch_reason", None)
                needs_manual_review = is_partial_refund or bool(mismatch_reason)
                is_not_found = status_res.status_str == "not_found"

                if not (
                    status_res.is_paid
                    or status_res.is_canceled
                    or is_refunded
                    or needs_manual_review
                    or is_not_found
                ):
                    _pending_order_backoff_cache[sup_id] = time.monotonic()
                    continue

                sup_alert_payload: tuple[Any, int | None, Any, str, str | None] | None = None
                async with session_scope() as order_session:
                    if sup_user_id is not None:
                        await acquire_user_account_lock(order_session, sup_user_id)
                        await order_session.scalar(
                            select(User.id).where(User.id == sup_user_id).with_for_update()
                        )
                    sup_order = await order_session.scalar(
                        select(Order).where(Order.id == sup_id).with_for_update(skip_locked=True)
                    )
                    if not sup_order or sup_order.status != "canceled":
                        continue
                    sup_meta = sup_order.metadata_ if isinstance(sup_order.metadata_, dict) else {}
                    if (
                        sup_meta.get("cancellation_reason") != "superseded_by_new_order"
                        or sup_meta.get("superseded_reconciled")
                        or sup_meta.get("underpaid")
                        or sup_meta.get("requires_manual_review")
                    ):
                        continue

                    if needs_manual_review:
                        reason_text = mismatch_reason or (
                            "partial_refund" if is_partial_refund else "payment_status_mismatch"
                        )
                        sup_alert_payload = _flag_order_manual_review(
                            sup_order,
                            reason=reason_text,
                            is_partial_refund=is_partial_refund,
                            external_id=sup_ext_id,
                            extra_meta={"superseded_reconciled": True},
                        )
                        _pending_order_backoff_cache.pop(sup_id, None)
                        logger.warning(
                            "Reconciliation flagged superseded order %s for manual review (reason=%s)",
                            sup_order.id,
                            reason_text,
                        )
                    elif status_res.is_paid:
                        paid_order = await OrderService.mark_order_paid(
                            order_session,
                            sup_order.id,
                            external_id=sup_ext_id,
                            paid_amount_rub=status_res.amount_rub,
                        )
                        if paid_order and paid_order.status == "paid":
                            _pending_order_backoff_cache.pop(sup_id, None)
                            logger.info(
                                "Reconciliation revived paid superseded order %s (amount=%s rub)",
                                sup_order.id,
                                sup_order.amount_rub,
                            )
                        else:
                            _pending_order_backoff_cache[sup_id] = time.monotonic()
                            logger.info(
                                "Reconciliation deferred paid superseded order %s (withheld or underpaid)",
                                sup_order.id,
                            )
                    elif status_res.is_canceled or is_not_found:
                        order_meta = dict(sup_meta)
                        order_meta["superseded_reconciled"] = True
                        sup_order.metadata_ = order_meta
                        _pending_order_backoff_cache.pop(sup_id, None)
                    elif is_refunded:
                        order_meta = dict(sup_meta)
                        order_meta["superseded_reconciled"] = True
                        order_meta["refunded_after_supersede"] = True
                        sup_order.metadata_ = order_meta
                        _pending_order_backoff_cache.pop(sup_id, None)

                if sup_alert_payload is not None:
                    await _send_reconciliation_admin_alert(*sup_alert_payload)
            except Exception as sup_exc:
                _pending_order_backoff_cache[sup_id] = time.monotonic()
                logger.warning(
                    "Superseded order reconciliation check failed for order %s: %s",
                    sup_id,
                    sup_exc,
                )
    except Exception as exc:
        logger.error("Error reconciling superseded canceled orders: %s", exc)

