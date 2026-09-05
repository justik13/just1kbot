import asyncio
import logging
import time
from datetime import timedelta

from aiogram import Bot
from aiogram.exceptions import TelegramForbiddenError
from bot.keyboards.notifications import get_devices_deleted_keyboard
from bot.texts.runtime.notifications import NOTIFY_DEVICES_DELETED
from cachetools import TTLCache
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from config.constants import (
    AMNEZIA_PROTOCOL,
    AdminAuditAction,
    GRACE_PERIOD_HOURS,
    PAYMENT_EXPIRATION_HOURS,
)
from database.connection import session_scope
from database.models import (
    APIOperation,
    BroadcastProgress,
    HubMessage,
    Payment,
    PaymentEvent,
    PaymentProviderOperation,
    Server,
    User,
    VPNProfile,
    WebhookInbox,
)
from database.repositories.audit_repo import clear_audit_logs
from services.amnezia_client import AmneziaClient
from services.profile_deletion_service import ProfileDeletionService
from services.subscription import SubscriptionService
from services.yookassa_service import YooKassaService
from utils.datetime_helpers import now_utc

logger = logging.getLogger("BackgroundWorker")

_unmanaged_peers_log_cache: TTLCache[tuple[int, str], float] = TTLCache(maxsize=5000, ttl=3600.0)
_unmanaged_peers_summary_last_logged: float | None = None

MAX_PENDING_ATTEMPTS = 10
PENDING_RETRY_INTERVAL = 3600
CLEANUP_START_DELAY = 60.0
CLEANUP_LOOP_INTERVAL = 900.0
OLD_RECORDS_INTERVAL = 86400.0
# Auto-expire throughput: each daily pass drains the pending-expiry backlog
# within a wall-clock budget (and at most MAX_BATCHES batches ≈ 400
# verifications), so a large backlog shrinks every day without one unbounded
# or stalled run.
EXPIRE_VERIFY_BATCH_SIZE = 20
EXPIRE_MAX_BATCHES_PER_PASS = 20
EXPIRE_VERIFY_PARALLELISM = 5
EXPIRE_TIME_BUDGET_SECONDS = 120.0

AUDIT_LOG_RETENTION_DAYS = 180
WEBHOOK_INBOX_RETENTION_DAYS = 30
WHITE_INTERNET_TRAFFIC_EVENTS_RETENTION_DAYS = 30

_last_old_cleanup: float = 0.0


def _safe_log_value(value, limit=64):
    text = str(value or "unknown")
    sanitized = "".join(character if character.isprintable() else "?" for character in text)
    return sanitized[:limit]


async def cleanup_dangling_peers_loop(
    bot_or_shutdown: Bot | asyncio.Event | None = None,
    shutdown_event: asyncio.Event | None = None,
):
    global _last_old_cleanup

    if isinstance(bot_or_shutdown, asyncio.Event):
        event = bot_or_shutdown
        bot = None
    else:
        bot = bot_or_shutdown
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
            await _cleanup_stuck_profiles()
            await _cleanup_expired_profiles_grace(bot)
            await _cleanup_dangling_peers()

            now = time.monotonic()
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
                timeout=CLEANUP_LOOP_INTERVAL,
            )
            break
        except asyncio.TimeoutError:
            continue

    logger.info("Cleanup worker stopped gracefully")


async def _cleanup_expired_profiles_grace(bot: Bot | None = None):
    current_time = now_utc()
    threshold = current_time - timedelta(hours=GRACE_PERIOD_HOURS)

    async with session_scope() as session:
        stmt = (
            select(User.id)
            .where(
                User.is_deleted.is_(False),
                User.subscription_end.is_not(None),
                (User.subscription_end < threshold) | User.financial_hold,
            )
            .order_by(User.subscription_end.asc())
            .limit(50)
        )
        result = await session.execute(stmt)
        user_ids = [row[0] for row in result.all()]

    if not user_ids:
        return

    deleted_users_count = 0
    deleted_profiles_count = 0

    for user_id in user_ids:
        try:
            async with session_scope() as session:
                user_stmt = select(User).where(User.id == user_id).with_for_update()
                user_result = await session.execute(user_stmt)
                user = user_result.scalar_one_or_none()

                if user is None:
                    continue
                if user.is_deleted:
                    continue
                if user.subscription_end is None:
                    continue
                from utils.datetime_helpers import is_permanent_subscription

                if is_permanent_subscription(user.subscription_end):
                    continue
                if not user.financial_hold and user.subscription_end >= threshold:
                    continue

                if user.financial_hold and user.subscription_end >= threshold:
                    # Active subscription under financial hold (dispute/chargeback):
                    # disable access via desired-state sync, NEVER delete paid devices.
                    # Deletion is reserved for actually expired subscriptions.
                    await SubscriptionService.sync_access_state(session, user)
                    logger.info(
                        "Grace cleanup disabled (not deleted) profiles for user %s "
                        "with active subscription under financial_hold",
                        _safe_log_value(user.id),
                    )
                    continue

                profiles_stmt = select(VPNProfile).where(
                    VPNProfile.user_id == user.id,
                )
                profiles_result = await session.execute(profiles_stmt)
                profiles = list(profiles_result.scalars().all())

                if not profiles:
                    continue

                deleted = await ProfileDeletionService.delete_profiles_list(
                    session,
                    profiles,
                    reason="grace_delete",
                    background=True,
                )

                if deleted > 0:
                    deleted_users_count += 1
                    deleted_profiles_count += deleted
                    from services.audit_service import AuditService

                    await AuditService.log_action(
                        session,
                        admin_id=0,
                        action=AdminAuditAction.CLEANUP_DEVICE_DELETE,
                        target_type="user",
                        target_id=user.id,
                        details={
                            "profiles_deleted": deleted,
                            "reason": "grace_delete",
                        },
                    )
                    logger.info(
                        "Grace cleanup: removed %s expired profiles "
                        "for user_id=%s (subscription_end=%s)",
                        deleted,
                        user_id,
                        user.subscription_end,
                    )

                    # Уведомить пользователя об удалении устройств
                    if bot:
                        try:
                            await bot.send_message(
                                user.telegram_id,
                                NOTIFY_DEVICES_DELETED,
                                reply_markup=get_devices_deleted_keyboard(),
                                parse_mode="HTML",
                            )
                        except TelegramForbiddenError:
                            user.is_bot_blocked = True
                            logger.info(
                                "User %s blocked the bot (grace cleanup notification)",
                                user.telegram_id,
                            )

                        except Exception as e:
                            logger.warning(
                                "Failed to send grace cleanup notification to user %s: %s",
                                user.telegram_id,
                                e,
                            )

        except Exception as e:
            logger.error(
                "Grace cleanup failed for user_id=%s: %s",
                user_id,
                e,
                exc_info=True,
            )

    if deleted_users_count > 0:
        logger.info(
            "Grace cleanup completed: %s users, %s profiles removed",
            deleted_users_count,
            deleted_profiles_count,
        )


async def _cleanup_stuck_profiles():
    # Cleanup dangling pending_create, create_cleanup_pending, and deleting profiles.
    # Only clean up profiles that do NOT have an active APIOperation in flight.
    from sqlalchemy import func
    from sqlalchemy import update as sa_update

    async with session_scope() as session:
        cutoff_time = now_utc() - timedelta(hours=1)

        stuck_profiles = (
            (
                await session.execute(
                    select(VPNProfile)
                    .where(
                        VPNProfile.provisioning_status.in_(
                            ["pending_create", "create_cleanup_pending", "deleting"]
                        ),
                        VPNProfile.created_at < cutoff_time,
                    )
                    .limit(100)
                    .with_for_update(skip_locked=True)
                )
            )
            .scalars()
            .all()
        )

        for profile in stuck_profiles:
            active_op_res = await session.execute(
                select(APIOperation.id)
                .where(
                    APIOperation.profile_id == profile.id,
                    APIOperation.status.in_(["pending", "processing", "retry"]),
                )
                .limit(1)
            )
            if active_op_res.scalar_one_or_none() is not None:
                logger.debug(
                    "Skipping profile %s cleanup: APIOperation is still active", profile.id
                )
                continue

            peer_id = profile.peer_id
            create_op = None
            if not peer_id:
                create_op_res = await session.execute(
                    select(APIOperation)
                    .where(
                        APIOperation.profile_id == profile.id,
                        APIOperation.operation_type == "create_peer",
                    )
                    .order_by(APIOperation.id.desc())
                    .limit(1)
                )
                create_op = create_op_res.scalar_one_or_none()
                if create_op is not None:
                    peer_id = getattr(create_op, "peer_id", None)

            if peer_id:
                profile.peer_id = peer_id
                try:
                    from services.api_operations_queue import (
                        ensure_delete_operation,
                        resolve_profile_endpoint_snapshot,
                    )

                    (
                        server_id,
                        server_name,
                        api_url,
                        api_key,
                    ) = await resolve_profile_endpoint_snapshot(session, profile)
                    await ensure_delete_operation(
                        session,
                        idempotency_key=f"delete-peer:{profile.id}:{peer_id}",
                        server_id=server_id,
                        profile_id=profile.id,
                        server_name_snapshot=server_name,
                        api_url_snapshot=api_url,
                        api_key_snapshot=api_key,
                        peer_id=peer_id,
                        client_name=profile.client_name,
                        audit_reason="stuck_cleanup_worker",
                    )
                    profile.provisioning_status = "deleting"
                except Exception as exc:
                    logger.warning(
                        "Failed to queue delete_peer operation during stuck profile cleanup: %s",
                        exc,
                    )
                    profile.provisioning_status = "create_cleanup_pending"
            elif profile.provisioning_status in {"create_cleanup_pending", "deleting"}:
                # Peer ID unknown: requeue create_peer for reconciliation by client_name on Amnezia
                if create_op and create_op.status in {"dead", "cancelled"}:
                    from database.models import Server

                    server = await session.get(Server, profile.server_id)
                    if server and server.is_active:
                        await session.execute(
                            sa_update(APIOperation)
                            .where(APIOperation.id == create_op.id)
                            .values(
                                status="retry",
                                attempts=0,
                                next_attempt_at=func.now(),
                                completed_at=None,
                                locked_at=None,
                                locked_by=None,
                                updated_at=func.now(),
                                last_error_code="stuck_cleanup_requeued",
                                last_error="Requeued by stuck profile cleanup worker for peer reconciliation",
                            )
                        )
                        logger.info(
                            "Requeued create_peer op %s for profile %s reconciliation",
                            create_op.id,
                            profile.id,
                        )
                elif not create_op and profile.provisioning_status == "create_cleanup_pending":
                    # Recreate the durable reconciliation command instead of
                    # deleting a state that explicitly means a peer may exist.
                    try:
                        from services.api_operations_queue import (
                            enqueue_api_operation,
                            resolve_profile_endpoint_snapshot,
                        )

                        (
                            server_id,
                            server_name,
                            api_url,
                            api_key,
                        ) = await resolve_profile_endpoint_snapshot(session, profile)
                        await enqueue_api_operation(
                            session,
                            operation_type="create_peer",
                            idempotency_key=f"create-peer:{profile.id}:v{profile.desired_version}",
                            server_id=server_id,
                            profile_id=profile.id,
                            server_name_snapshot=server_name,
                            api_url_snapshot=api_url,
                            api_key_snapshot=api_key,
                            client_name=profile.client_name,
                            payload={"desired_version": profile.desired_version},
                        )
                        logger.info(
                            "Recreated missing CREATE reconciliation op for profile %s",
                            profile.id,
                        )
                    except Exception as exc:
                        logger.warning(
                            "Failed to recreate CREATE reconciliation op for profile %s: %s",
                            profile.id,
                            type(exc).__name__,
                        )
                elif not create_op and profile.provisioning_status == "deleting":
                    # No operation ever existed and no peer_id; safe to delete local tombstone
                    await session.delete(profile)
                    logger.info(
                        "Deleted orphaned tombstone profile %s without operations", profile.id
                    )
            else:
                # pending_create where attempts == 0: safe to fail closed without side effects
                profile.provisioning_status = "create_failed"
                profile.last_sync_error = "Creation timed out by cleanup worker"
                logger.info(
                    "Marked unattempted pending_create profile %s as create_failed", profile.id
                )


async def _cleanup_dangling_peers():
    servers_data = []
    db_server_peers = set()

    async with session_scope() as session:
        servers_result = await session.execute(
            select(Server).where(
                Server.is_active.is_(True),
                Server.protocol == AMNEZIA_PROTOCOL,
            )
        )
        servers = servers_result.scalars().all()

        result = await session.execute(select(VPNProfile.server_id, VPNProfile.peer_id))
        db_server_peers = {
            (row[0], row[1]) for row in result.all() if row[0] is not None and row[1]
        }

        servers_data = [
            {
                "api_url": s.api_url,
                "api_key": s.api_key,
                "name": s.name,
                "id": s.id,
            }
            for s in servers
            if s.api_url and s.api_key
        ]

    if not servers_data:
        return

    async def _fetch_api_peers(server_info):
        from services.slots_cache import get_server_generation

        client = AmneziaClient(
            server_info["api_url"],
            server_info["api_key"],
        )
        gen = get_server_generation(server_info["id"])
        try:
            api_clients_list = await client.get_all_clients()
            t_done = time.monotonic()
            if api_clients_list is None:
                return server_info, None, t_done, gen
            return server_info, api_clients_list, t_done, gen
        except Exception as e:
            t_done = time.monotonic()
            logger.error(
                "Failed to fetch clients from server %s: %s",
                server_info["name"],
                e,
            )
            return server_info, None, t_done, gen

    tasks = [_fetch_api_peers(s) for s in servers_data]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    unmanaged_count = 0

    for result in results:
        if isinstance(result, Exception) or result is None:
            continue

        server_info, api_clients_list, t_done, gen = result
        from services.slots_cache import get_server_generation

        if gen != get_server_generation(server_info["id"]):
            logger.info(
                "Skipping cleanup for server %s due to configuration generation change",
                server_info["name"],
            )
            continue

        if api_clients_list is not None:
            from services.slots_cache import update_cached_peer_count

            update_cached_peer_count(
                server_info["id"], len(api_clients_list), timestamp=t_done, generation=gen
            )
        if not api_clients_list:
            continue

        for api_client in api_clients_list:
            client_id = api_client.id
            client_name = api_client.clientName or api_client.name

            if not client_name or not client_name.startswith("tg_"):
                continue

            if (server_info["id"], client_id) in db_server_peers:
                continue

            peer_exists_in_db = False
            try:
                async with session_scope() as session:
                    fresh_result = await session.execute(
                        select(VPNProfile.id).where(
                            VPNProfile.server_id == server_info["id"],
                            VPNProfile.peer_id == client_id,
                        )
                    )
                    peer_exists_in_db = fresh_result.first() is not None
            except Exception as e:
                logger.error(
                    "Double-check failed for server_id=%s, peer=%s..., error_kind=%s",
                    server_info["id"],
                    _safe_log_value(client_id, 16),
                    type(e).__name__,
                )
                continue

            if peer_exists_in_db:
                continue

            peer_key = (server_info["id"], client_id)
            now_ts = time.monotonic()
            last_logged = _unmanaged_peers_log_cache.get(peer_key)
            if (
                last_logged is None or now_ts - last_logged >= 3600.0
            ):  # Log at most once per hour per peer
                _unmanaged_peers_log_cache[peer_key] = now_ts
                logger.warning(
                    "Unmanaged VPN peer detected: server_id=%s, "
                    "server=%s, peer=%s..., client=%s; "
                    "automatic deletion disabled",
                    server_info["id"],
                    _safe_log_value(server_info["name"]),
                    _safe_log_value(client_id, 16),
                    _safe_log_value(client_name),
                )
            unmanaged_count += 1

    global _unmanaged_peers_summary_last_logged
    now_ts = time.monotonic()
    if unmanaged_count and (
        _unmanaged_peers_summary_last_logged is None
        or now_ts - _unmanaged_peers_summary_last_logged >= 3600.0
    ):
        _unmanaged_peers_summary_last_logged = now_ts
        logger.warning(
            "Unmanaged VPN peers detected: %s; automatic deletion disabled",
            unmanaged_count,
        )


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

    # Auto-expire abandoned pending payments older than PAYMENT_EXPIRATION_HOURS.
    # This is a provider-verified LOCAL cancellation (not a provider-side
    # cancel): the local row is moved to canceled only after GET
    # /payments/{id} still reports pending. If the provider later confirms a
    # paid payment, apply_provider_transition treats it as
    # canceled_to_succeeded → manual_review — the expected reconciliation
    # path, never a silent credit. Missing external_id or any transport error
    # skips the row this cycle (fail-closed).
    # Verifications run in bounded parallel batches: 20 sequential GETs with a
    # 15s timeout could stall the once-a-day old-records pass for ~5 minutes.
    threshold_payments = current_time - timedelta(hours=PAYMENT_EXPIRATION_HOURS)
    payments_expired = 0
    last_id = 0

    # Bounded drain: up to EXPIRE_MAX_BATCHES_PER_PASS batches per daily pass,
    # each batch committed separately, so a large backlog shrinks every day
    # without one unbounded run.
    async def _verify(row: tuple[int, str], semaphore: asyncio.Semaphore):
        payment_id, external_id = row
        async with semaphore:
            result = await YooKassaService.get_payment_result(external_id)
        return payment_id, result

    expire_deadline = time.monotonic() + EXPIRE_TIME_BUDGET_SECONDS
    for _batch in range(EXPIRE_MAX_BATCHES_PER_PASS):
        if time.monotonic() >= expire_deadline:
            logger.warning(
                "Auto-expire time budget (%.0fs) spent; remaining backlog continues next pass",
                EXPIRE_TIME_BUDGET_SECONDS,
            )
            break
        async with session_scope() as session:
            pending_rows = (
                await session.execute(
                    select(Payment.id, Payment.external_id)
                    .where(
                        Payment.provider_status == "pending",
                        Payment.created_at < threshold_payments,
                        Payment.id > last_id,
                    )
                    .order_by(Payment.id)
                    .limit(EXPIRE_VERIFY_BATCH_SIZE)
                )
            ).all()
        if not pending_rows:
            break
        pending_ids = [pid for pid, _ext in pending_rows]
        if not pending_ids:
            break
        last_id = pending_ids[-1]

        verifiable = [(pid, ext) for pid, ext in pending_rows if ext]
        for payment_id, _external_id in pending_rows:
            if not _external_id:
                logger.warning(
                    "Auto-expire skipped for payment %s: no external_id, provider verification impossible",
                    payment_id,
                )

        cancellable_ids: list[int] = []
        if verifiable:
            semaphore = asyncio.Semaphore(EXPIRE_VERIFY_PARALLELISM)

            verify_results = await asyncio.gather(
                *(_verify(row, semaphore) for row in verifiable),
                return_exceptions=True,
            )
        else:
            verify_results = []
        for item in verify_results:
            if isinstance(item, Exception) or not isinstance(item, tuple):
                logger.warning("Auto-expire verification task failed unexpectedly: %r", item)
                continue
            payment_id, result = item
            if not result.ok:
                logger.warning(
                    "Auto-expire skipped for payment %s: provider verification failed (%s)",
                    payment_id,
                    result.error_kind.value if result.error_kind else "unknown",
                )
                continue
            observed = str((result.value or {}).get("status") or "unknown")
            if observed == "pending":
                cancellable_ids.append(payment_id)
            else:
                logger.info(
                    "Auto-expire skipped for payment %s: provider status %s (left for stale-topup settlement)",
                    payment_id,
                    observed,
                )

        if cancellable_ids:
            async with session_scope() as session:
                # Terminal-boundary semantics for the whole payment lifecycle,
                # mirroring apply_provider_transition's `canceled` branch:
                # close the checkout, hide the UI, drop the payment URL.
                # RETURNING is race-safety: a webhook flipping pending→succeeded
                # between the provider GET and this UPDATE yields no row for
                # that payment, and the queue cancellation + audit event below
                # then apply ONLY to payments actually transitioned here.
                result_payments = await session.execute(
                    update(Payment)
                    .where(
                        Payment.id.in_(cancellable_ids),
                        Payment.provider_status == "pending",
                    )
                    .values(
                        provider_status="canceled",
                        fulfillment_status="failed",
                        reconciliation_status="ok",
                        checkout_status="abandoned",
                        ui_visible=False,
                        payment_url=None,
                        manual_review_reason="auto_expired_abandoned_pending_48h",
                    )
                    .returning(Payment.id)
                )
                expired_ids = [row[0] for row in result_payments.all()]
                payments_expired += len(expired_ids)
                if expired_ids:
                    # Queue synchronization: cancel still-queued provider
                    # operations so the pipeline never pushes a payment URL for
                    # (or re-reconciles) a locally expired checkout. In-flight
                    # (processing) operations are left alone - their finalizers
                    # handle the canceled state via the state machine.
                    await session.execute(
                        update(PaymentProviderOperation)
                        .where(
                            PaymentProviderOperation.payment_id.in_(expired_ids),
                            PaymentProviderOperation.status.in_(("pending", "retry")),
                        )
                        .values(
                            status="cancelled",
                            completed_at=now_utc(),
                            last_error_code="payment_locally_expired",
                        )
                    )
                    # Immutable audit trail: one event per ACTUALLY expired
                    # payment (not per GET-verified candidate).
                    for pid in expired_ids:
                        session.add(
                            PaymentEvent(
                                payment_id=pid,
                                event_type="payment_locally_expired",
                                provider_status="canceled",
                                reason="auto_expired_abandoned_pending_48h",
                                source="cleanup_worker",
                            )
                        )

    # Prune old succeeded/dead webhook inbox records in per-batch committed transactions
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
        or payments_expired > 0
        or webhooks_deleted > 0
    ):
        logger.info(
            "Cleanup: %s old broadcasts, %s old audit logs, "
            "%s old hub_messages deleted, %s abandoned pending payments expired, "
            "%s old webhooks deleted",
            broadcasts_deleted,
            deleted_logs,
            hub_deleted,
            payments_expired,
            webhooks_deleted,
        )
