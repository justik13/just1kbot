from bot import texts
import asyncio
import logging
import time
from datetime import timedelta

from aiogram import Bot
from aiogram.exceptions import TelegramForbiddenError
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from database.connection import session_scope
from database.models import (
    BroadcastProgress,
    HubMessage,
    Payment,
    Server,
    User,
    VPNProfile,
    WebhookInbox,
)
from database.repositories.audit_repo import clear_audit_logs
from services.amnezia_client import AmneziaClient
from services.profile_deletion_service import ProfileDeletionService
from utils.datetime_helpers import now_utc
from bot.constants import GRACE_PERIOD_HOURS

from cachetools import TTLCache

logger = logging.getLogger("BackgroundWorker")

_unmanaged_peers_log_cache: TTLCache[tuple[int, str], float] = TTLCache(
    maxsize=5000, ttl=3600.0
)
_unmanaged_peers_summary_last_logged: float | None = None

MAX_PENDING_ATTEMPTS = 10
PENDING_RETRY_INTERVAL = 3600
CLEANUP_START_DELAY = 60.0
CLEANUP_LOOP_INTERVAL = 900.0
OLD_RECORDS_INTERVAL = 86400.0

AUDIT_LOG_RETENTION_DAYS = 180
WEBHOOK_INBOX_RETENTION_DAYS = 30

_last_old_cleanup: float = 0.0



EXECUTABLE_DELETE_REASONS = frozenset(
    {
        "create_device_rollback_failed",
        "device_delete_api_failed",
        "ban_delete",
        "chargeback_delete",
        "grace_delete",
        "server_delete",
    }
)


def _is_executable_pending_deletion(reason: str | None) -> bool:
    """Allow only deletion reasons produced by confirmed bot workflows."""
    return reason in EXECUTABLE_DELETE_REASONS


def _safe_log_value(value, limit=64):
    text = str(value or "unknown")
    sanitized = "".join(
        character if character.isprintable() else "?" for character in text
    )
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
                texts.RUNTIME_SERVICES_WORKERS_CLEANUP_L90_1,
                e,
                exc_info=True,
            )
            if event.is_set():
                break
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
                        action="CLEANUP_DEVICE_DELETE",
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
                            builder = InlineKeyboardBuilder()
                            builder.button(
                                text="🛒 Купить подписку",
                                callback_data="menu_buy",
                            )
                            builder.button(
                                text="✅ Прочитано",
                                callback_data="dismiss_notification",
                            )
                            builder.adjust(1)

                            await bot.send_message(
                                user.telegram_id,
                                texts.UI_SERVICES_WORKERS_CLEANUP_L184_1,
                                reply_markup=builder.as_markup(),
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
    # P1-2: Cleanup dangling pending_create profiles
    async with session_scope() as session:
        cutoff_time = now_utc() - timedelta(hours=1)
        stuck_profiles = (
            await session.execute(
                select(VPNProfile)
                .where(
                    VPNProfile.provisioning_status.in_(["pending_create", "create_cleanup_pending"]),
                    VPNProfile.created_at < cutoff_time
                )
                .with_for_update(skip_locked=True)
            )
        ).scalars().all()

        for profile in stuck_profiles:
            profile.provisioning_status = "create_failed"
            profile.last_sync_error = "Creation timed out by cleanup worker"
            logger.info("Marked stuck pending_create profile %s as create_failed", profile.id)

async def _cleanup_dangling_peers():
    servers_data = []
    db_server_peers = set()

    async with session_scope() as session:
        servers_result = await session.execute(
            select(Server).where(Server.is_active.is_(True))
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
        client = AmneziaClient(
            server_info["api_url"],
            server_info["api_key"],
        )
        try:
            api_clients_list = await client.get_all_clients()
            if api_clients_list is None:
                return server_info, []
            return server_info, api_clients_list
        except Exception as e:
            logger.error(
                texts.RUNTIME_SERVICES_WORKERS_CLEANUP_L255_1,
                server_info["name"],
                e,
            )
            return server_info, []

    tasks = [_fetch_api_peers(s) for s in servers_data]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    unmanaged_count = 0

    for result in results:
        if isinstance(result, Exception):
            continue

        server_info, api_clients_list = result
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
            if last_logged is None or now_ts - last_logged >= 3600.0:  # Log at most once per hour per peer
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
    session: AsyncSession,
    model,
    *where_clauses,
    batch_size: int = BATCH_DELETE_CHUNK_SIZE,
    max_rounds: int = MAX_BATCH_DELETE_ROUNDS,
    commit_per_batch: bool = True,
) -> int:
    """Delete rows matching where_clauses in bounded primary-key batches with skip_locked, committing per batch to release locks immediately."""
    if not hasattr(model, "id"):
        stmt = delete(model).where(*where_clauses)
        res = await session.execute(stmt)
        if commit_per_batch:
            await session.commit()
        else:
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
        if commit_per_batch:
            await session.commit()
        else:
            await session.flush()
        total_deleted += int(del_res.rowcount or 0)
        if len(ids) < batch_size:
            break
        await asyncio.sleep(0.01)
    return total_deleted




async def _cleanup_old_records():
    async with session_scope() as session:
        current_time = now_utc()

        # Mark stuck in_progress broadcasts as stopped
        threshold_stuck = current_time - timedelta(hours=2)
        stmt_stuck = (
            update(BroadcastProgress)
            .where(BroadcastProgress.status == "in_progress")
            .where(BroadcastProgress.updated_at < threshold_stuck)
            .values(status="stopped")
        )
        await session.execute(stmt_stuck)

        threshold_broadcasts = current_time - timedelta(days=7)
        broadcasts_deleted = await _batch_delete_matching(
            session,
            BroadcastProgress,
            BroadcastProgress.status.in_(["completed", "stopped"]),
            BroadcastProgress.updated_at < threshold_broadcasts,
        )

        deleted_logs = await clear_audit_logs(
            session,
            older_than_days=AUDIT_LOG_RETENTION_DAYS,
        )

        threshold_hub = current_time - timedelta(days=1)
        hub_deleted = await _batch_delete_matching(
            session,
            HubMessage,
            HubMessage.created_at < threshold_hub,
        )

        # Auto-expire abandoned pending payments older than 48 hours
        threshold_payments = current_time - timedelta(hours=48)
        stmt_payments = (
            update(Payment)
            .where(
                Payment.provider_status == "pending",
                Payment.created_at < threshold_payments,
            )
            .values(
                provider_status="canceled",
                fulfillment_status="failed",
                reconciliation_status="ok",
                manual_review_reason="auto_expired_abandoned_pending_48h",
            )
        )
        result_payments = await session.execute(stmt_payments)
        payments_expired = result_payments.rowcount

        # Prune old succeeded/dead webhook inbox records
        threshold_webhooks = current_time - timedelta(days=WEBHOOK_INBOX_RETENTION_DAYS)
        webhooks_deleted = await _batch_delete_matching(
            session,
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


