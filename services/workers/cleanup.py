import asyncio
import logging
import time
from datetime import timedelta

from sqlalchemy import delete, select, update

from database.connection import session_scope
from database.models import (
    BroadcastProgress,
    HubMessage,
    PendingAPIDeletion,
    Server,
    User,
    VPNProfile,
)
from database.repositories.audit_repo import clear_audit_logs
from services.amnezia_client import AmneziaClient
from services.profile_deletion_service import ProfileDeletionService
from utils.datetime_helpers import now_utc
from bot.constants import GRACE_PERIOD_HOURS

logger = logging.getLogger("BackgroundWorker")

MAX_PENDING_ATTEMPTS = 10
PENDING_RETRY_INTERVAL = 3600
CLEANUP_START_DELAY = 60.0
CLEANUP_LOOP_INTERVAL = 900.0
OLD_RECORDS_INTERVAL = 86400.0

AUDIT_LOG_RETENTION_DAYS = 180

_last_old_cleanup: float = 0.0

QUARANTINE_ERROR_PREFIX = "QUARANTINED_UNSAFE_DELETE_REASON"

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
        character if character.isprintable() else "?"
        for character in text
    )
    return sanitized[:limit]


async def cleanup_dangling_peers_loop(shutdown_event: asyncio.Event):
    global _last_old_cleanup

    try:
        await asyncio.wait_for(
            shutdown_event.wait(), timeout=CLEANUP_START_DELAY,
        )
        logger.info(
            "Cleanup worker stopped during start delay (shutdown)"
        )
        return
    except asyncio.TimeoutError:
        pass

    while not shutdown_event.is_set():
        try:
            await _cleanup_expired_profiles_grace()
            await _cleanup_dangling_peers()
            await _process_pending_deletions()

            now = time.monotonic()
            if now - _last_old_cleanup > OLD_RECORDS_INTERVAL:
                await _cleanup_old_records()
                _last_old_cleanup = now

        except asyncio.CancelledError:
            logger.info("Cleanup worker cancelled")
            break
        except Exception as e:
            logger.error(
                "Критическая ошибка в цикле очистки: %s",
                e, exc_info=True,
            )
            if shutdown_event.is_set():
                break
            try:
                await asyncio.wait_for(
                    shutdown_event.wait(),
                    timeout=CLEANUP_LOOP_INTERVAL,
                )
                break
            except asyncio.TimeoutError:
                continue

    logger.info("Cleanup worker stopped gracefully")


async def _cleanup_expired_profiles_grace():
    current_time = now_utc()
    threshold = current_time - timedelta(hours=GRACE_PERIOD_HOURS)

    async with session_scope() as session:
        stmt = (
            select(User.id)
            .where(
                User.is_deleted == False,
                User.subscription_end != None,
                User.subscription_end < threshold,
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
                user_stmt = (
                    select(User)
                    .where(User.id == user_id)
                    .with_for_update()
                )
                user_result = await session.execute(user_stmt)
                user = user_result.scalar_one_or_none()

                if user is None:
                    continue
                if user.is_deleted:
                    continue
                if user.subscription_end is None:
                    continue
                if user.subscription_end.year >= 2100:
                    continue
                if user.subscription_end >= threshold:
                    continue

                profiles_stmt = select(VPNProfile).where(
                    VPNProfile.user_id == user.id,
                )
                profiles_result = await session.execute(
                    profiles_stmt
                )
                profiles = list(
                    profiles_result.scalars().all()
                )

                if not profiles:
                    continue

                deleted = (
                    await ProfileDeletionService.delete_profiles_list(
                        session,
                        profiles,
                        reason="grace_delete",
                        background=True,
                    )
                )

                if deleted > 0:
                    deleted_users_count += 1
                    deleted_profiles_count += deleted
                    logger.info(
                        "Grace cleanup: removed %s expired profiles "
                        "for user_id=%s (subscription_end=%s)",
                        deleted, user_id, user.subscription_end,
                    )

                    # Уведомить пользователя об удалении устройств
                    try:
                        from services.workers.heartbeat import get_bot_ref
                        bot = get_bot_ref()
                        if bot:
                            await bot.send_message(
                                user.telegram_id,
                                "⚠️ Ваши устройства были удалены из-за истечения подписки. "
                                "Продлите доступ, чтобы создать новые.",
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
                            user.telegram_id, e,
                        )

        except Exception as e:
            logger.error(
                "Grace cleanup failed for user_id=%s: %s",
                user_id, e, exc_info=True,
            )

    if deleted_users_count > 0:
        logger.info(
            "Grace cleanup completed: %s users, %s profiles removed",
            deleted_users_count, deleted_profiles_count,
        )


async def _cleanup_dangling_peers():
    servers_data = []
    db_server_peers = set()

    async with session_scope() as session:
        servers_result = await session.execute(select(Server))
        servers = servers_result.scalars().all()

        result = await session.execute(
            select(VPNProfile.server_id, VPNProfile.peer_id)
        )
        db_server_peers = {
            (row[0], row[1])
            for row in result.all()
            if row[0] is not None and row[1]
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
            server_info["api_url"], server_info["api_key"],
        )
        try:
            api_clients_list = await client.get_all_clients()
            if api_clients_list is None:
                return server_info, []
            return server_info, api_clients_list
        except Exception as e:
            logger.error(
                "Ошибка получения списка пиров на %s: %s",
                server_info["name"], e,
            )
            return server_info, []

    tasks = [_fetch_api_peers(s) for s in servers_data]
    results = await asyncio.gather(
        *tasks, return_exceptions=True
    )

    unmanaged_count = 0

    for result in results:
        if isinstance(result, Exception):
            continue

        server_info, api_clients_list = result
        if not api_clients_list:
            continue

        for api_client in api_clients_list:
            client_id = api_client.id
            client_name = (
                api_client.clientName or api_client.name
            )

            if not client_name or not client_name.startswith("tg_"):
                continue

            if (server_info["id"], client_id) in db_server_peers:
                continue

            peer_exists_in_db = False
            try:
                async with session_scope() as session:
                    fresh_result = await session.execute(
                        select(VPNProfile.id).where(
                            VPNProfile.server_id
                            == server_info["id"],
                            VPNProfile.peer_id == client_id,
                        )
                    )
                    peer_exists_in_db = (
                        fresh_result.first() is not None
                    )
            except Exception as e:
                logger.error(
                    "Double-check failed for server_id=%s, "
                    "peer=%s..., error_kind=%s",
                    server_info["id"],
                    _safe_log_value(client_id, 16),
                    type(e).__name__,
                )
                continue

            if peer_exists_in_db:
                continue

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

    if unmanaged_count:
        logger.warning(
            "Unmanaged VPN peers detected: %s; "
            "automatic deletion disabled",
            unmanaged_count,
        )


async def _process_pending_deletions():
    """Legacy queue is quarantine/report-only after durable migration."""
    async with session_scope() as session:
        rows = (
            await session.execute(
                select(PendingAPIDeletion).where(
                    PendingAPIDeletion.attempts >= 0
                )
            )
        ).scalars().all()
        if not rows:
            return
        for row in rows:
            row.attempts = -1
            row.last_attempt_at = now_utc()
            row.last_error = f"{QUARANTINE_ERROR_PREFIX}: legacy worker disabled"
        logger.warning(
            "Quarantined %s legacy pending deletions; "
            "no API writes performed",
            len(rows),
        )

async def _cleanup_old_records():
    async with session_scope() as session:
        current_time = now_utc()

        threshold_broadcasts = current_time - timedelta(days=7)
        stmt_broadcasts = (
            delete(BroadcastProgress)
            .where(
                BroadcastProgress.status.in_(
                    ["completed", "stopped"]
                )
            )
            .where(
                BroadcastProgress.updated_at
                < threshold_broadcasts
            )
        )
        result_broadcasts = await session.execute(
            stmt_broadcasts
        )
        broadcasts_deleted = result_broadcasts.rowcount

        deleted_logs = await clear_audit_logs(
            session,
            older_than_days=AUDIT_LOG_RETENTION_DAYS,
        )

        threshold_hub = current_time - timedelta(days=1)
        stmt_hub = delete(HubMessage).where(
            HubMessage.created_at < threshold_hub
        )
        result_hub = await session.execute(stmt_hub)
        hub_deleted = result_hub.rowcount

        if (
            broadcasts_deleted > 0
            or deleted_logs > 0
            or hub_deleted > 0
        ):
            logger.info(
                "Cleanup: %s old broadcasts, %s old audit logs, "
                "%s old hub_messages deleted",
                broadcasts_deleted, deleted_logs, hub_deleted,
            )
