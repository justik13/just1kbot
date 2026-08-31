"""Reconciliation worker ensuring desired subscription state matches Xray Origin runtime."""

from __future__ import annotations

import asyncio
import logging

from aiogram import Bot
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from config.enums import ServerHealthState, WhiteInternetProvisioningStatus, WhiteInternetStatus
from database.connection import session_scope
from database.models import Server, WhiteInternetSubscription
from database.repositories import servers_repo, white_internet_repo
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

        # Only explicitly provisioned, active and healthy Xray Origin nodes are eligible.
        # Disabled or problematic nodes are skipped from reconciliation.
        stmt_servers = (
            select(Server)
            .where(
                Server.api_url.is_not(None),
                Server.is_active.is_(True),
                Server.health_state.in_([ServerHealthState.ONLINE, ServerHealthState.WAITING_CONFIRMATION]),
            )
            .order_by(Server.id.asc())
        )
        res_servers = await session.execute(stmt_servers)
        servers = res_servers.scalars().all()
        server_map = {
            server.id: server
            for server in servers
            if "xray_origin" in (server.capabilities or [])
        }

        # Refresh node generation before selecting subscriptions. The epoch is
        # the runtime truth for the Xray process, not the Python agent process.
        for server in list(server_map.values()):
            if not server.api_url or not server.api_key:
                continue
            is_healthy, current_epoch, health_data = await self.client.check_health(
                server.api_url, server.api_key
            )
            if is_healthy and current_epoch and health_data:
                boot_id = health_data.get("boot_id")
                starttime = health_data.get("starttime")
                if (
                    current_epoch != server.xray_instance_epoch
                    or boot_id != server.xray_instance_boot_id
                    or starttime != server.xray_instance_starttime
                ):
                    cas_ok, updated_server = await servers_repo.update_server_xray_epoch_cas(
                        session,
                        server.id,
                        expected_boot_id=server.xray_instance_boot_id,
                        expected_starttime=server.xray_instance_starttime,
                        new_epoch=current_epoch,
                        new_boot_id=boot_id,
                        new_starttime=starttime,
                    )
                    if cas_ok and updated_server:
                        server_map[server.id] = updated_server
                        logger.info(
                            "Updated Xray generation for server %d (%s): epoch=%s, boot_id=%s, starttime=%s",
                            server.id,
                            server.name,
                            current_epoch,
                            boot_id,
                            starttime,
                        )

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
                        WhiteInternetSubscription.provisioning_status.in_([
                            WhiteInternetProvisioningStatus.PENDING_CREATE,
                            WhiteInternetProvisioningStatus.PENDING_UPDATE,
                            WhiteInternetProvisioningStatus.PENDING_DELETE,
                        ]),
                    ),
                )
                .order_by(WhiteInternetSubscription.id.asc())
                .limit(BATCH_SIZE)
            )
            res_subs = await session.execute(stmt_subs)
            pending_subs = res_subs.scalars().all()

            # Phase 1: Collect candidates and expire overdue subscriptions atomically
            sync_tasks: list[dict] = []
            for sub in pending_subs:
                if sub.expires_at <= now and sub.status in (
                    WhiteInternetStatus.PENDING,
                    WhiteInternetStatus.ACTIVE,
                    WhiteInternetStatus.EXHAUSTED,
                ):
                    sub.status = WhiteInternetStatus.EXPIRED
                    sub.status_reason = "subscription_expired"
                    sub.desired_version += 1
                    await white_internet_repo.expire_subscription_atomic(session, sub.id, now=now)

                desired_active = (
                    sub.status in (WhiteInternetStatus.PENDING, WhiteInternetStatus.ACTIVE)
                    and sub.expires_at > now
                )
                sync_tasks.append({
                    "sub_id": sub.id,
                    "uuid": sub.uuid,
                    "desired_active": desired_active,
                    "target_version": sub.desired_version,
                })

            # Phase 2 & 3: Perform remote network I/O with Xray, then CAS write under brief lock
            for task in sync_tasks:
                sub_id = task["sub_id"]
                sub_uuid = task["uuid"]
                desired_active = task["desired_active"]
                target_version = task["target_version"]

                # 2. Perform external network I/O with Xray node
                success, err_msg = await self.client.sync_client(
                    server.api_url,
                    server.api_key,
                    sub_uuid,
                    is_active=desired_active,
                )

                # 3. Post-sync atomic state persistence under brief row lock
                sub = await white_internet_repo.get_subscription_with_lock(session, sub_id)
                if sub is None:
                    continue

                if success and sub.desired_version == target_version:
                    sub.actual_version = target_version
                    sub.last_reconciled_node_epoch = target_epoch
                    if sub.status == WhiteInternetStatus.PENDING and desired_active:
                        sub.status = WhiteInternetStatus.ACTIVE
                        sub.status_reason = None
                    sub.provisioning_status = (
                        WhiteInternetProvisioningStatus.ACTIVE
                        if desired_active
                        else WhiteInternetProvisioningStatus.SYNCED_INACTIVE
                    )
                    sub.last_synced_at = now_utc()
                    sub.last_sync_error = None
                    synced_count += 1
                elif not success:
                    sub.provisioning_status = WhiteInternetProvisioningStatus.FAILED
                    sub.last_sync_error = err_msg
                    sub.last_synced_at = now_utc()
                    logger.error(
                        "Failed to reconcile White Internet sub_id=%d on server %d: %s",
                        sub_id,
                        server_id,
                        err_msg,
                    )
                else:
                    sub.provisioning_status = WhiteInternetProvisioningStatus.PENDING_UPDATE
                    logger.warning(
                        "Reconciliation detected version drift during sync for sub_id=%d on server %d",
                        sub_id,
                        server_id,
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
