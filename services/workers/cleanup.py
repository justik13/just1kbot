import asyncio
import logging
import time
from datetime import timedelta
from typing import Any

from aiogram import Bot
from aiogram.exceptions import TelegramForbiddenError
from bot.keyboards.notifications import get_devices_deleted_keyboard
from bot.texts.runtime.notifications import NOTIFY_DEVICES_DELETED
from cachetools import TTLCache
from sqlalchemy import delete, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from config.constants import (
    AMNEZIA_PROTOCOLS,
    AdminAuditAction,
    GRACE_PERIOD_HOURS,
    VPN_ACCESS_GRACE_HOURS,
)
from database.connection import session_scope
from database.models import (
    AdminOperationIdempotency,
    APIOperation,
    BroadcastProgress,
    HubMessage,
    Order,
    Server,
    User,
    VlessSubscription,
    VPNProfile,
    WebhookInbox,
)
from database.repositories.audit_repo import clear_audit_logs
from services.amnezia_client import AmneziaClient
from services.profile_deletion_service import ProfileDeletionService
from services.subscription import SubscriptionService
from utils.datetime_helpers import now_utc

logger = logging.getLogger(__name__)

_unmanaged_peers_log_cache: TTLCache[tuple[int, str], float] = TTLCache(maxsize=5000, ttl=3600.0)
_unmanaged_peers_summary_last_logged: float | None = None

MAX_PENDING_ATTEMPTS = 10
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


def _safe_log_value(value, limit=64):
    text = str(value or "unknown")
    sanitized = "".join(character if character.isprintable() else "?" for character in text)
    return sanitized[:limit]


async def cleanup_dangling_peers_loop(
    bot_or_shutdown: Bot | asyncio.Event | None = None,
    shutdown_event: asyncio.Event | None = None,
):
    global _last_old_cleanup, _last_heavy_cleanup

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
            now = time.monotonic()
            if now - _last_heavy_cleanup >= CLEANUP_LOOP_INTERVAL:
                await _cleanup_stuck_profiles()
                await _cleanup_expired_profiles_grace(bot)
                await _cleanup_expired_vless_network_grace()
                await _sweep_vless_pending_revocations()
                await _cleanup_dangling_peers()
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
                or_(
                    select(VPNProfile.id).where(VPNProfile.user_id == User.id).exists(),
                    select(VlessSubscription.id).where(
                        VlessSubscription.user_id == User.id,
                        VlessSubscription.is_active.is_(True),
                    ).exists(),
                ),
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

                await SubscriptionService.sync_access_state(session, user)

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


async def _cleanup_expired_vless_network_grace() -> None:
    """Deactivate VLESS access when 4-hour network grace expires (before 24h retention delete)."""
    current_time = now_utc()
    threshold = current_time - timedelta(hours=VPN_ACCESS_GRACE_HOURS)

    async with session_scope() as session:
        stmt = (
            select(User.id)
            .join(VlessSubscription, VlessSubscription.user_id == User.id)
            .where(
                User.is_deleted.is_(False),
                User.subscription_end.is_not(None),
                User.subscription_end < threshold,
                VlessSubscription.is_active.is_(True),
            )
            .order_by(User.subscription_end.asc())
            .limit(50)
        )
        result = await session.execute(stmt)
        user_ids = [row[0] for row in result.all()]

    for user_id in user_ids:
        try:
            async with session_scope() as session:
                user = await session.get(User, user_id, with_for_update=True)
                if user and user.subscription_end and user.subscription_end < threshold:
                    from utils.datetime_helpers import is_permanent_subscription

                    if is_permanent_subscription(user.subscription_end):
                        continue
                    await SubscriptionService.sync_access_state(session, user)
                    logger.info("VLESS 4h network grace deactivation applied for user_id=%s", user_id)
        except Exception as e:
            logger.warning("VLESS network grace deactivation failed for user_id=%s: %s", user_id, e)


async def _sweep_vless_pending_revocations() -> None:
    """Retry deprovisioning for any pending revoked VLESS UUIDs and converge out-of-sync subscriptions."""
    from database.repositories import vless_subscription_repo
    from services.vless_subscription_service import VlessSubscriptionService

    try:
        # 1. Retry deprovisioning of old revoked UUIDs
        async with session_scope() as session:
            subs = await vless_subscription_repo.get_subscriptions_with_pending_revocations(session, limit=20)
            servers = await VlessSubscriptionService.get_configured_vless_servers(session)
            targets = VlessSubscriptionService._extract_node_targets(servers)
            if not targets:
                return

            sub_items = [
                (s.id, list(getattr(s, "pending_revoked_uuids", None) or []), getattr(s, "version", 1) or 1)
                for s in subs
            ] if subs else []

        for sub_id, rev_uuids, ver in sub_items:
            for rev_uuid in rev_uuids:
                try:
                    res = await VlessSubscriptionService._execute_deprovision_on_nodes(targets, rev_uuid, version=ver)
                    if all(res.values()) and len(res) == len(targets):
                        async with session_scope() as session:
                            await vless_subscription_repo.pop_pending_revoked_uuid(session, sub_id, rev_uuid)
                            logger.info("Durable deprovision cleared revoked UUID for sub_id=%s", sub_id)
                except Exception as exc:
                    masked = f"{rev_uuid[:8]}***" if rev_uuid else "unknown"
                    logger.warning("Retry deprovision failed for sub_id=%s UUID %s: %s", sub_id, masked, exc)

        # 2. Converge out-of-sync subscriptions (e.g. node was offline during ban or grace deactivation)
        async with session_scope() as session:
            out_of_sync_stmt = (
                select(VlessSubscription.user_id)
                .where(
                    or_(
                        VlessSubscription.last_synced_at.is_(None),
                        VlessSubscription.last_synced_at < VlessSubscription.updated_at,
                    )
                )
                .order_by(VlessSubscription.updated_at.asc())
                .limit(20)
            )
            out_of_sync_users = list((await session.execute(out_of_sync_stmt)).scalars().all())

        for uid in out_of_sync_users:
            try:
                async with session_scope() as session:
                    await VlessSubscriptionService.sync_user_to_nodes(session, uid)
            except Exception as e:
                logger.warning("Out-of-sync VLESS reconciliation failed for user_id=%s: %s", uid, e)
    except Exception as e:
        logger.warning("Error in _sweep_vless_pending_revocations: %s", e)


async def _cleanup_stuck_profiles():
    # Cleanup dangling pending_create, create_cleanup_pending, deleting, and delete_failed profiles.
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
                            ["pending_create", "create_cleanup_pending", "deleting", "delete_failed"]
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
                from database.models import Server

                server = await session.get(Server, profile.server_id)
                if server and getattr(server, "is_active", True) is False:
                    logger.info(
                        "Skipping delete_peer queue for profile %s: server %s is inactive",
                        profile.id,
                        profile.server_id,
                    )
                    continue

                from config.enums import ServerHealthState

                if server and getattr(server, "health_state", None) in (
                    ServerHealthState.AUTO_DISABLED,
                    ServerHealthState.MANUAL_DISABLED,
                    ServerHealthState.PROBLEM,
                ):
                    logger.info(
                        "Skipping delete_peer queue for profile %s: server %s is in %s",
                        profile.id,
                        profile.server_id,
                        server.health_state,
                    )
                    continue

                # For delete_failed profiles, avoid infinite retry loops when operation reached dead status.
                # Require at least 6 hours cooldown after the dead operation before cleanup revives it.
                if profile.provisioning_status == "delete_failed":
                    dead_op_res = await session.execute(
                        select(APIOperation)
                        .where(
                            APIOperation.profile_id == profile.id,
                            APIOperation.operation_type == "delete_peer",
                            APIOperation.status.in_(["dead", "cancelled"]),
                        )
                        .order_by(APIOperation.id.desc())
                        .limit(1)
                    )
                    dead_op = dead_op_res.scalar_one_or_none()
                    if dead_op:
                        op_time = dead_op.completed_at or dead_op.updated_at
                        if op_time and op_time > now_utc() - timedelta(hours=6):
                            logger.debug(
                                "Skipping delete_failed profile %s retry: dead operation %s in cooldown",
                                profile.id,
                                dead_op.id,
                            )
                            continue

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
                    if profile.provisioning_status != "delete_failed":
                        profile.provisioning_status = "create_cleanup_pending"
            elif profile.provisioning_status in {"create_cleanup_pending", "deleting", "delete_failed"}:
                # Peer ID unknown: requeue create_peer for reconciliation by client_name on Amnezia
                if create_op and create_op.status in {"dead", "cancelled"}:
                    from database.models import Server

                    server = await session.get(Server, profile.server_id)
                    if server and getattr(server, "is_active", True):
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
                    from database.models import Server

                    server = await session.get(Server, profile.server_id)
                    if server and getattr(server, "is_active", True) is False:
                        logger.info(
                            "Skipping CREATE reconciliation op for profile %s: server %s is inactive",
                            profile.id,
                            profile.server_id,
                        )
                        continue

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
                            payload={
                                "desired_version": profile.desired_version,
                                "protocol": server.protocol if server else None,
                            },
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
                elif not create_op and profile.provisioning_status in {"deleting", "delete_failed"}:
                    # No operation ever existed and no peer_id; safe to delete local tombstone
                    await session.delete(profile)
                    logger.info(
                        "Deleted orphaned tombstone profile %s without operations (status=%s)",
                        profile.id,
                        profile.provisioning_status,
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
                Server.protocol.in_(AMNEZIA_PROTOCOLS),
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

    threshold_idempotency = current_time - timedelta(days=7)
    await _batch_delete_matching(
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
    ):
        logger.info(
            "Cleanup: %s old broadcasts, %s old audit logs, "
            "%s old hub_messages, %s old webhooks deleted",
            broadcasts_deleted,
            deleted_logs,
            hub_deleted,
            webhooks_deleted,
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

            if not (
                status_res.is_paid
                or status_res.is_canceled
                or getattr(status_res, "is_refunded", False)
            ):
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
                elif getattr(status_res, "is_refunded", False):
                    OrderService.mark_order_canceled(
                        order,
                        reason="gateway_refunded",
                    )
                    _pending_order_backoff_cache.pop(order_id, None)
                    logger.info(
                        "Reconciliation canceled stale pending order %s (refunded in gateway)",
                        order.id,
                    )
        except Exception as exc:
            _pending_order_backoff_cache[order_id] = time.monotonic()
            logger.warning(
                "Reconciliation check failed for order %s: %s",
                order_id,
                exc,
            )

    # 3. Expire ancient pending orders (> 24 hours old) in a bounded transaction
    try:
        async with session_scope() as session:
            expired_stmt = (
                select(Order)
                .where(
                    Order.status == "pending",
                    Order.created_at < window_start,
                )
                .order_by(Order.created_at.asc())
                .limit(50)
                .with_for_update(skip_locked=True)
            )
            expired_res = await session.execute(expired_stmt)
            expired_orders = list(expired_res.scalars().all())
            for exp_order in expired_orders:
                OrderService.mark_order_canceled(exp_order, reason="order_expired_24h")
                logger.info(
                    "Reconciliation expired ancient pending order %s",
                    exp_order.id,
                )
    except Exception as exc:
        logger.error("Error expiring ancient pending orders: %s", exc)

