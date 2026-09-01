"""Reconciliation worker ensuring desired subscription state matches Xray Origin runtime."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
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

    def __init__(
        self,
        bot: Bot | None = None,
        node_client: XrayNodeClient | None = None,
        session_factory=None,
    ):
        self.bot = bot
        self.client = node_client or XrayNodeClient()
        self.session_factory = session_factory or session_scope

    async def run_reconciliation_cycle(self, session: AsyncSession | None = None) -> int:
        now = now_utc()
        sf = self.session_factory
        if session is not None:
            @asynccontextmanager
            async def _session_ctx():
                yield session

            sf = _session_ctx

        async with sf() as sess:
            stmt_servers = (
                select(Server)
                .where(
                    Server.api_url.is_not(None),
                    Server.is_active.is_(True),
                    Server.health_state.in_([ServerHealthState.ONLINE, ServerHealthState.WAITING_CONFIRMATION]),
                )
                .order_by(Server.id.asc())
            )
            res_servers = await sess.execute(stmt_servers)
            servers = res_servers.scalars().all()
            server_list = [
                (s.id, s.name, s.api_url, s.api_key, s.xray_instance_epoch, s.xray_instance_boot_id, s.xray_instance_starttime)
                for s in servers
                if "xray_origin" in (s.capabilities or []) and s.api_url and s.api_key
            ]

        # Check health outside DB transaction
        active_server_targets: list[tuple[int, str, str, str]] = []  # (id, api_url, api_key, epoch)
        for server_id, _name, api_url, api_key, cur_epoch, cur_boot_id, cur_starttime in server_list:
            is_healthy, node_epoch, health_data = await self.client.check_health(api_url, api_key)
            if is_healthy and node_epoch and health_data:
                boot_id = health_data.get("boot_id")
                starttime = health_data.get("starttime")
                target_node_epoch = cur_epoch
                if (
                    node_epoch != cur_epoch
                    or boot_id != cur_boot_id
                    or starttime != cur_starttime
                ):
                    async with sf() as sess:
                        cas_ok, updated = await servers_repo.update_server_xray_epoch_cas(
                            sess,
                            server_id,
                            expected_boot_id=cur_boot_id,
                            expected_starttime=cur_starttime,
                            new_epoch=node_epoch,
                            new_boot_id=boot_id,
                            new_starttime=starttime,
                        )
                        if cas_ok and updated:
                            target_node_epoch = node_epoch
                        else:
                            # CAS Fencing: reload fresh state from DB to get the true winner's epoch
                            fresh_server = await sess.scalar(select(Server).where(Server.id == server_id))
                            target_node_epoch = fresh_server.xray_instance_epoch if fresh_server else None

                active_server_targets.append((server_id, api_url, api_key, target_node_epoch or node_epoch))

        synced_count = 0
        for server_id, api_url, api_key, target_epoch in active_server_targets:
            if not target_epoch:
                continue

            sync_tasks: list[dict] = []
            async with sf() as sess:
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
                res_subs = await sess.execute(stmt_subs)
                pending_subs = res_subs.scalars().all()

                for sub in pending_subs:
                    if sub.expires_at <= now and sub.status in (
                        WhiteInternetStatus.PENDING,
                        WhiteInternetStatus.ACTIVE,
                        WhiteInternetStatus.EXHAUSTED,
                    ):
                        await white_internet_repo.expire_subscription_atomic(sess, sub.id)

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

            # Remote network I/O outside DB transaction
            for task in sync_tasks:
                sub_id = task["sub_id"]
                sub_uuid = task["uuid"]
                desired_active = task["desired_active"]
                target_version = task["target_version"]

                success, err_msg = await self.client.sync_client(
                    api_url,
                    api_key,
                    sub_uuid,
                    is_active=desired_active,
                    version=target_version,
                )

                # Persist result in short transaction under lock
                async with sf() as sess:
                    sub = await white_internet_repo.get_subscription_with_lock(sess, sub_id)
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
            synced = await worker.run_reconciliation_cycle()
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
