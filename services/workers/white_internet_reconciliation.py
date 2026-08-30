"""Reconciliation worker ensuring desired subscription state matches Xray Origin runtime."""

from __future__ import annotations

import asyncio
import logging

from aiogram import Bot
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from config.enums import WhiteInternetProvisioningStatus, WhiteInternetStatus
from database.connection import session_scope
from database.models import Server, WhiteInternetSubscription
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
        """
        Execute one reconciliation cycle:
        1. Discover active Origin servers and check their health/node epoch.
        2. Detect epoch drift (Xray restart) and update server.xray_instance_epoch.
        3. Identify subscriptions where:
           actual_version != desired_version OR last_reconciled_node_epoch != server.xray_instance_epoch
        4. Reconcile each subscription idempotently via sync_client().
        """
        now = now_utc()

        # 1. Fetch servers with capability 'xray_origin'
        stmt_servers = select(Server).where(Server.api_url.is_not(None))
        res_servers = await session.execute(stmt_servers)
        servers = res_servers.scalars().all()

        server_map: dict[int, Server] = {}
        for s in servers:
            caps = s.capabilities or []
            if "xray_origin" in caps or "xray" in (s.protocol or "").lower():
                server_map[s.id] = s

        # If no servers explicitly tagged, include all active servers
        if not server_map and servers:
            server_map = {s.id: s for s in servers}

        # Check health and update node epoch for each server
        for _server_id, server in server_map.items():
            if not server.api_url or not server.api_key:

                continue
            is_healthy, current_epoch, _ = await self.client.check_health(server.api_url, server.api_key)
            if is_healthy and current_epoch and current_epoch != server.xray_instance_epoch:
                logger.info(
                    "Detected new Xray epoch for server %d (%s): %s (was %s). Triggering reconciliation.",
                    server.id,
                    server.name,
                    current_epoch,
                    server.xray_instance_epoch,
                )
                server.xray_instance_epoch = current_epoch
                await session.flush()

        # 2. Query subscriptions needing reconciliation
        synced_count = 0
        for server_id, server in server_map.items():
            if not server.xray_instance_epoch:
                continue

            stmt_subs = (
                select(WhiteInternetSubscription)
                .where(
                    WhiteInternetSubscription.origin_node_id == server_id,
                    or_(
                        WhiteInternetSubscription.actual_version != WhiteInternetSubscription.desired_version,
                        WhiteInternetSubscription.last_reconciled_node_epoch != server.xray_instance_epoch,
                        WhiteInternetSubscription.last_reconciled_node_epoch.is_(None),
                    ),
                )
                .limit(BATCH_SIZE)
            )
            res_subs = await session.execute(stmt_subs)
            pending_subs = res_subs.scalars().all()

            for sub_meta in pending_subs:
                # Lock row under transaction
                sub = await white_internet_repo.get_subscription_with_lock(session, sub_meta.id)
                if sub is None:
                    continue

                # Re-verify condition under lock
                if (
                    sub.actual_version == sub.desired_version
                    and sub.last_reconciled_node_epoch == server.xray_instance_epoch
                ):
                    continue

                desired_active = (
                    sub.status == WhiteInternetStatus.ACTIVE
                    and sub.expires_at > now
                )

                target_version = sub.desired_version
                target_epoch = server.xray_instance_epoch

                success, err_msg = await self.client.sync_client(
                    server.api_url, server.api_key, sub.uuid, is_active=desired_active
                )

                if success:
                    # Stale-write check
                    if sub.desired_version == target_version:
                        sub.actual_version = target_version
                        sub.last_reconciled_node_epoch = target_epoch
                        sub.provisioning_status = (
                            WhiteInternetProvisioningStatus.ACTIVE
                            if desired_active
                            else WhiteInternetProvisioningStatus.ACTIVE
                        )
                        sub.last_synced_at = now
                        sub.last_sync_error = None
                        synced_count += 1
                else:
                    sub.provisioning_status = WhiteInternetProvisioningStatus.FAILED
                    sub.last_sync_error = err_msg
                    sub.last_synced_at = now
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
