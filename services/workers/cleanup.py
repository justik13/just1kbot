import asyncio
import logging
import time
from datetime import timedelta
from typing import Any

from aiogram import Bot
from aiogram.exceptions import TelegramForbiddenError
from cachetools import TTLCache
from sqlalchemy import and_, cast, delete, or_, select, String, update
from sqlalchemy.ext.asyncio import AsyncSession
from config.constants import (
    AdminAuditAction,
    GRACE_PERIOD_HOURS,
    VPN_ACCESS_GRACE_HOURS,
)
from database.connection import session_scope
from database.models import (
    AdminOperationIdempotency,
    BroadcastProgress,
    HubMessage,
    Order,
    User,
    VlessSubscription,
    WebhookInbox,
)
from database.repositories.audit_repo import clear_audit_logs
from bot.keyboards.notifications import get_devices_deleted_keyboard
from bot.texts.runtime.notifications import NOTIFY_DEVICES_DELETED
from services.subscription import SubscriptionService
from utils.datetime_helpers import now_utc

logger = logging.getLogger(__name__)

PENDING_RETRY_INTERVAL = 3600
CLEANUP_START_DELAY = 60.0
CLEANUP_LOOP_INTERVAL = 900.0
RECONCILE_LOOP_INTERVAL = 60.0
OLD_RECORDS_INTERVAL = 86400.0
# Auto-expire throughput: each daily pass drains the pending-expiry backlog
# within a wall-clock budget (and at most MAX_BATCHES batches ≈ 400
# verifications), so a large backlog shrinks every day without one unbounded
# or stalled run.

AUDIT_LOG_RETENTION_DAYS = 180
WEBHOOK_INBOX_RETENTION_DAYS = 30

_last_heavy_cleanup: float = 0.0
_last_old_cleanup: float = 0.0
_pending_order_backoff_cache: TTLCache[Any, float] = TTLCache(maxsize=2000, ttl=300.0)


async def cleanup_expired_subscriptions_loop(
    bot_or_shutdown: Bot | asyncio.Event | None = None,
    shutdown_event: asyncio.Event | None = None,
):
    global _last_old_cleanup, _last_heavy_cleanup

    bot: Bot | None = bot_or_shutdown if isinstance(bot_or_shutdown, Bot) else None
    if isinstance(bot_or_shutdown, asyncio.Event):
        event = bot_or_shutdown
    else:
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
            if now - _last_heavy_cleanup >= CLEANUP_LOOP_INTERVAL:
                await _cleanup_expired_vless_network_grace()
                await _cleanup_expired_vless_retention_grace(bot)
                await _sweep_vless_pending_revocations()
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





async def _cleanup_expired_vless_network_grace() -> None:
    """Deactivate VLESS access when network grace window (4 hours) expires or under financial hold."""
    current_time = now_utc()
    threshold = current_time - timedelta(hours=VPN_ACCESS_GRACE_HOURS)

    async with session_scope() as session:
        stmt = (
            select(User.id)
            .join(VlessSubscription, VlessSubscription.user_id == User.id)
            .where(
                User.is_deleted.is_(False),
                or_(
                    and_(
                        User.subscription_end.is_not(None),
                        User.subscription_end < threshold,
                    ),
                    User.financial_hold.is_(True),
                ),
                VlessSubscription.is_active.is_(True),
            )
            .order_by(User.subscription_end.asc().nullslast())
            .limit(50)
        )
        result = await session.execute(stmt)
        user_ids = [row[0] for row in result.all()]

    for user_id in user_ids:
        try:
            async with session_scope() as session:
                user = await session.get(User, user_id, with_for_update=True)
                if not user or user.is_deleted:
                    continue
                from utils.datetime_helpers import is_permanent_subscription

                if user.subscription_end and is_permanent_subscription(user.subscription_end):
                    continue
                if not user.financial_hold and (not user.subscription_end or user.subscription_end >= threshold):
                    continue
                await SubscriptionService.sync_access_state(session, user)
                logger.info("VLESS network grace/hold deactivation applied for user_id=%s", user_id)
        except Exception as e:
            logger.warning("VLESS network grace deactivation failed for user_id=%s: %s", user_id, e)


async def _cleanup_expired_vless_retention_grace(bot: Bot | None = None) -> None:
    """Perform 24h grace retention cleanup for expired VLESS subscriptions (clear active HWIDs, notify user)."""
    current_time = now_utc()
    threshold = current_time - timedelta(hours=GRACE_PERIOD_HOURS)

    async with session_scope() as session:
        stmt = (
            select(User.id)
            .join(VlessSubscription, VlessSubscription.user_id == User.id)
            .where(
                User.is_deleted.is_(False),
                User.subscription_end.is_not(None),
                User.subscription_end < threshold,
                or_(
                    VlessSubscription.is_active.is_(True),
                    and_(
                        VlessSubscription.active_hwids.is_not(None),
                        cast(VlessSubscription.active_hwids, String) != "{}",
                    ),
                ),
            )
            .order_by(User.subscription_end.asc())
            .limit(50)
        )
        result = await session.execute(stmt)
        user_ids = [row[0] for row in result.all()]

    if not user_ids:
        return

    from database.repositories import vless_subscription_repo
    from services.audit_service import AuditService
    from services.vless_subscription_service import VlessSubscriptionService

    for user_id in user_ids:
        notify_tg_id = None
        try:
            async with session_scope() as session:
                user = await session.get(User, user_id, with_for_update=True)
                if not user or user.is_deleted or not user.subscription_end:
                    continue

                from utils.datetime_helpers import is_permanent_subscription

                if is_permanent_subscription(user.subscription_end):
                    continue
                if user.subscription_end >= threshold:
                    continue

                vless_sub = await vless_subscription_repo.get_subscription_by_user_id(session, user.id)
                had_vless_devices = bool(vless_sub and (vless_sub.active_hwids or vless_sub.is_active))

                await SubscriptionService.sync_access_state(session, user)

                if not vless_sub or not had_vless_devices:
                    continue

                cleared = await vless_subscription_repo.clear_active_hwids(session, vless_sub.id)
                vless_sub.is_active = False
                await session.flush()

                VlessSubscriptionService.ensure_synced_background(user.id, is_active=False, session=session)

                notify_tg_id = user.telegram_id
                await AuditService.log_action(
                    session,
                    admin_id=0,
                    action=AdminAuditAction.CLEANUP_DEVICE_DELETE,
                    target_type="user",
                    target_id=user.id,
                    details={
                        "hwids_cleared": cleared,
                        "vless_deactivated": True,
                        "reason": "grace_expiry",
                    },
                )
                logger.info(
                    "VLESS 24h retention grace cleanup: cleared HWIDs for user_id=%s without UUID rotation",
                    user_id,
                )

            if notify_tg_id and bot:
                try:
                    await bot.send_message(
                        notify_tg_id,
                        NOTIFY_DEVICES_DELETED,
                        reply_markup=get_devices_deleted_keyboard(),
                        parse_mode="HTML",
                    )
                except TelegramForbiddenError:
                    async with session_scope() as blocked_session:
                        b_user = await blocked_session.get(User, user_id)
                        if b_user:
                            b_user.is_bot_blocked = True
                except Exception as e:
                    logger.warning("Failed to notify user %s on device deletion: %s", user_id, e)

        except Exception as e:
            logger.warning("VLESS 24h retention grace cleanup failed for user_id=%s: %s", user_id, e)


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
                        res = await VlessSubscriptionService._execute_deprovision_on_nodes(targets, rev_uuid, version=ver)
                        if (
                            servers
                            and len(targets) == len(servers)
                            and all(res.values())
                            and len(res) == len(targets)
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

    # Mark stuck in_progress broadcasts as stopped (short atomic transaction)
    threshold_stuck = current_time - timedelta(hours=2)
    async with session_scope() as session:
        stmt_stuck = (
            update(BroadcastProgress)
            .where(BroadcastProgress.status == "in_progress")
            .where(BroadcastProgress.updated_at < threshold_stuck)
            .values(status="stopped")
        )
        await session.execute(stmt_stuck)

    threshold_broadcasts = current_time - timedelta(days=7)
    broadcasts_deleted = await _batch_delete_matching(
        BroadcastProgress,
        BroadcastProgress.status.in_(["completed", "stopped"]),
        BroadcastProgress.updated_at < threshold_broadcasts,
    )

    deleted_logs = await clear_audit_logs(
        older_than_days=AUDIT_LOG_RETENTION_DAYS,
    )

    threshold_hub = current_time - timedelta(days=1)
    hub_deleted = await _batch_delete_matching(
        HubMessage,
        HubMessage.created_at < threshold_hub,
    )

    threshold_idempotency = current_time - timedelta(days=7)
    idempotency_deleted = await _batch_delete_matching(
        AdminOperationIdempotency,
        AdminOperationIdempotency.created_at < threshold_idempotency,
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

    if (
        broadcasts_deleted > 0
        or deleted_logs > 0
        or hub_deleted > 0
        or webhooks_deleted > 0
        or idempotency_deleted > 0
    ):
        logger.info(
            "Cleanup: %s old broadcasts, %s old audit logs, "
            "%s old hub_messages, %s old webhooks deleted, %s old idempotency records deleted",
            broadcasts_deleted,
            deleted_logs,
            hub_deleted,
            webhooks_deleted,
            idempotency_deleted,
        )


async def _reconcile_stale_pending_orders() -> None:
    """Safety net: reconcile pending orders whose webhooks were dropped or missed.

    Decoupled transaction & network boundary:
    1. Read candidate order identifiers in a fast query without holding database locks.
    2. Query external gateway HTTP outside DB transactions.
    3. Settle or cancel each order in its own isolated transaction boundary.
    4. Expire ancient pending orders (> 24 hours old).
    """
    from integrations.payment_gateways.factory import get_payment_gateway
    from services.order_service import OrderService

    now = now_utc()
    window_start = now - timedelta(hours=24)
    stale_threshold = now - timedelta(minutes=3)

    # 1. Fetch candidate order tuples in a fast read query (no lock held during external HTTP)
    candidates: list[tuple[Any, str, str]] = []
    try:
        cached_ids = list(_pending_order_backoff_cache.keys())
        async with session_scope() as session:
            stmt = (
                select(Order.id, Order.external_id, Order.payment_method)
                .where(
                    Order.status == "pending",
                    Order.external_id.is_not(None),
                    Order.created_at >= window_start,
                    Order.created_at <= stale_threshold,
                )
            )
            if cached_ids:
                stmt = stmt.where(Order.id.notin_(cached_ids))
            stmt = stmt.order_by(Order.created_at.asc()).limit(20)
            res = await session.execute(stmt)
            candidates = [
                (row[0], row[1], row[2])
                for row in res.all()
                if row[0] not in _pending_order_backoff_cache
            ]
    except Exception as exc:
        logger.error("Failed to query stale pending order candidates: %s", exc)
        return

    # 2. Check each candidate with the payment gateway outside DB transactions,
    # then settle/cancel in an isolated transaction per order.
    for order_id, external_id, payment_method in candidates:
        try:
            gateway = get_payment_gateway(payment_method)
            status_res = await gateway.check_payment_status(external_id)

            if not (status_res.is_paid or status_res.is_canceled):
                _pending_order_backoff_cache[order_id] = time.monotonic()
                if status_res.status_str == "not_found":
                    logger.warning(
                        "Reconciliation payment not found in gateway for order %s (external_id=%s)",
                        order_id,
                        external_id,
                    )
                continue

            # Isolated transaction for this specific order
            async with session_scope() as order_session:
                order = await order_session.scalar(
                    select(Order).where(Order.id == order_id).with_for_update()
                )
                if not order or order.status != "pending":
                    continue

                if status_res.is_paid:
                    paid_order = await OrderService.mark_order_paid(
                        order_session,
                        order.id,
                        external_id=external_id,
                        paid_amount_rub=status_res.amount_rub,
                    )
                    if paid_order:
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
                    _pending_order_backoff_cache.pop(order_id, None)
                    logger.info(
                        "Reconciliation canceled stale pending order %s (reason=%s)",
                        order.id,
                        status_res.cancellation_reason,
                    )
        except Exception as exc:
            _pending_order_backoff_cache[order_id] = time.monotonic()
            logger.warning(
                "Reconciliation check failed for order %s: %s",
                order_id,
                exc,
            )

    # 3. Expire orphaned pending orders without external_id (> 30 min old)
    # and ancient pending orders (> 24 hours old) in a bounded transaction
    try:
        ambiguous_threshold = now - timedelta(minutes=30)
        async with session_scope() as session:
            expired_stmt = (
                select(Order)
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
                .with_for_update(skip_locked=True)
            )
            expired_res = await session.execute(expired_stmt)
            expired_orders = list(expired_res.scalars().all())
            for exp_order in expired_orders:
                reason = "ambiguous_creation_timeout" if exp_order.external_id is None else "order_expired_24h"
                OrderService.mark_order_canceled(exp_order, reason=reason)
                logger.info(
                    "Reconciliation expired pending order %s (reason=%s)",
                    exp_order.id,
                    reason,
                )
    except Exception as exc:
        logger.error("Error expiring ancient pending orders: %s", exc)

