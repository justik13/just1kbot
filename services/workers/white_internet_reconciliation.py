"""Reconciliation worker ensuring desired subscription state matches Xray Origin runtime."""

from __future__ import annotations

import asyncio
import logging

from aiogram import Bot
from sqlalchemy import or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from config.enums import WhiteInternetProvisioningStatus, WhiteInternetStatus
from database.connection import session_scope
from database.models import Server, WhiteInternetQuotaGrant, WhiteInternetSubscription
from database.repositories import white_internet_repo
from services.xray_node_client import XrayNodeClient
from utils.datetime_helpers import now_utc

logger = logging.getLogger("WhiteInternetReconciliation")

RECONCILIATION_INTERVAL_SECONDS = 15.0
BATCH_SIZE = 50


class WhiteInternetReconciliationWorker:
    """Worker keeping White Internet subscriptions in sync with Xray nodes."""

    def __init__(self, bot: Bot | None = None, node_client: XrayNodeClient | None = None):
        self.bot = bot
        self.client = node_client or XrayNodeClient()

    async def run_reconciliation_cycle(self, session: AsyncSession) -> int:
        now = now_utc()

        # Only explicitly provisioned Xray Origin nodes are eligible. Never
        # fall back to an arbitrary Amnezia/other server.
        stmt_servers = select(Server).where(Server.api_url.is_not(None)).order_by(Server.id.asc())
        res_servers = await session.execute(stmt_servers)
        servers = res_servers.scalars().all()
        server_map = {
            server.id: server
            for server in servers
            if "xray_origin" in (server.capabilities or [])
        }

        # Refresh node generation before selecting subscriptions. The epoch is
        # the runtime truth for the Xray process, not the Python agent process.
        for server in server_map.values():
            if not server.api_url or not server.api_key:
                continue
            is_healthy, current_epoch, _ = await self.client.check_health(
                server.api_url, server.api_key
            )
            if is_healthy and current_epoch:
                if current_epoch != server.xray_instance_epoch:
                    logger.info(
                        "Detected new Xray epoch for server %d (%s): %s (was %s).",
                        server.id,
                        server.name,
                        current_epoch,
                        server.xray_instance_epoch,
                    )
                    server.xray_instance_epoch = current_epoch
                    await session.flush()

        synced_count = 0
        for server_id, server in server_map.items():
            target_epoch = server.xray_instance_epoch
            if not target_epoch:
                continue

            stmt_subs = (
                select(WhiteInternetSubscription)
                .where(
                    WhiteInternetSubscription.origin_node_id == server_id,
                    or_(
                        WhiteInternetSubscription.actual_version
                        != WhiteInternetSubscription.desired_version,
                        WhiteInternetSubscription.last_reconciled_node_epoch
                        != target_epoch,
                        WhiteInternetSubscription.last_reconciled_node_epoch.is_(None),
                        WhiteInternetSubscription.provisioning_status
                        != WhiteInternetProvisioningStatus.ACTIVE,
                    ),
                )
                .order_by(WhiteInternetSubscription.id.asc())
                .limit(BATCH_SIZE)
            )
            res_subs = await session.execute(stmt_subs)
            pending_subs = res_subs.scalars().all()

            for sub_meta in pending_subs:
                sub = await white_internet_repo.get_subscription_with_lock(session, sub_meta.id)
                if sub is None:
                    continue

                # Expiration is a DB state transition, not merely an HTTP
                # presentation concern. Close all remaining grants atomically.
                if sub.expires_at <= now and sub.status in (
                    WhiteInternetStatus.PENDING,
                    WhiteInternetStatus.ACTIVE,
                    WhiteInternetStatus.EXHAUSTED,
                ):
                    sub.status = WhiteInternetStatus.EXPIRED
                    sub.status_reason = "subscription_expired"
                    sub.desired_version += 1
                    await session.execute(
                        update(WhiteInternetQuotaGrant)
                        .where(
                            WhiteInternetQuotaGrant.subscription_id == sub.id,
                            WhiteInternetQuotaGrant.bytes_remaining > 0,
                        )
                        .values(bytes_remaining=0)
                    )

                # PENDING means paid but not yet provisioned. It therefore has
                # an active desired runtime state while within its paid period.
                desired_active = (
                    sub.status in (WhiteInternetStatus.PENDING, WhiteInternetStatus.ACTIVE)
                    and sub.expires_at > now
                )

                target_version = sub.desired_version
                success, err_msg = await self.client.sync_client(
                    server.api_url,
                    server.api_key,
                    sub.uuid,
                    is_active=desired_active,
                )

                if success:
                    # The Xray call may have raced with a purchase/topup/expiry.
                    # Never commit an obsolete version or epoch as current.
                    current_epoch = server.xray_instance_epoch
                    if sub.desired_version == target_version and current_epoch == target_epoch:
                        sub.actual_version = target_version
                        sub.last_reconciled_node_epoch = target_epoch
                        if sub.status == WhiteInternetStatus.PENDING and desired_active:
                            sub.status = WhiteInternetStatus.ACTIVE
                            sub.status_reason = None
                        sub.provisioning_status = WhiteInternetProvisioningStatus.ACTIVE
                        sub.last_synced_at = now_utc()
                        sub.last_sync_error = None
                        synced_count += 1
                else:
                    sub.provisioning_status = WhiteInternetProvisioningStatus.FAILED
                    sub.last_sync_error = err_msg
                    sub.last_synced_at = now_utc()
                    logger.error(
                        "Failed to reconcile White Internet sub_id=%d on server %d: %s",
                        sub.id,
                        server_id,
                        err_msg,
                    )

                await session.flush()

        return synced_count


async def white_internet_reconciliation_loop(
    bot: Bot | None = None,
    shutdown_event: asyncio.Event | None = None,
):
    """Background loop running reconciliation cycles."""
    event = shutdown_event or asyncio.Event()
    worker = WhiteInternetReconciliationWorker(bot=bot)

    logger.info("White Internet reconciliation worker started.")

    while not event.is_set():
        try:
            async with session_scope() as session:
                synced = await worker.run_reconciliation_cycle(session)
                if synced > 0:
                    logger.info("Reconciled %d White Internet subscriptions.", synced)
        except Exception as exc:
            logger.error("Unhandled error in White Internet reconciliation cycle: %s", exc, exc_info=True)

        try:
            await asyncio.wait_for(event.wait(), timeout=RECONCILIATION_INTERVAL_SECONDS)
            break
        except asyncio.TimeoutError:
            pass

    logger.info("White Internet reconciliation worker stopped.")
