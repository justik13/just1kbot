"""Reconciliation worker ensuring desired subscription state matches Xray ru_hub runtime."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
import hashlib
import logging
import time

from aiogram import Bot
from sqlalchemy import and_, nulls_first, or_, select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from bot.texts.runtime.alerts import (
    ALERT_WHITE_INTERNET_HUB_EXHAUSTED,
    ALERT_WHITE_INTERNET_HUB_RESTORED,
)
from config.constants import VLESS_PROTOCOL, WHITE_INTERNET_CAPABILITY, XRAY_PROTOCOL
from config.enums import (
    ServerHealthState,
    ServerLifecycleStatus,
    WhiteInternetProvisioningStatus,
    WhiteInternetStatus,
)
from config.settings import get_settings
from database.connection import session_scope
from database.models import Server, WhiteInternetSubscription
from database.repositories import servers_repo, white_internet_repo
from services.xray_node_client import SyncResult, XrayNodeClient
from utils.datetime_helpers import now_utc
from utils.telegram import safe_send_message

logger = logging.getLogger(__name__)

RECONCILIATION_INTERVAL_SECONDS = 30.0
BATCH_SIZE = 50


DEFAULT_RECONCILIATION_CONCURRENCY = 5


class WhiteInternetReconciliationWorker:
    """Worker keeping White Internet subscriptions in sync with Xray nodes."""

    def __init__(
        self,
        bot: Bot | None = None,
        node_client: XrayNodeClient | None = None,
        session_factory=None,
        max_concurrency: int = DEFAULT_RECONCILIATION_CONCURRENCY,
    ):
        self.bot = bot
        self.client = node_client or XrayNodeClient()
        self.session_factory = session_factory or session_scope
        self.max_concurrency = max_concurrency
        self._semaphore = asyncio.Semaphore(self.max_concurrency)
        self._sub_locks: dict[int, asyncio.Lock] = {}
        self._hub_exhausted_alerted: bool = False
        self._hub_exhausted_last_sent: float | None = None

    async def _send_hub_exhausted_alert(self, sub_id: int) -> None:
        if not self.bot:
            return
        text = ALERT_WHITE_INTERNET_HUB_EXHAUSTED.format(sub_id=sub_id)
        for aid in get_settings().ADMIN_IDS:
            try:
                await safe_send_message(
                    self.bot, chat_id=aid, text=text, parse_mode="HTML"
                )
            except Exception as e:
                logger.warning("Failed to send hub exhausted alert to admin %s: %s", aid, e)

    async def _send_hub_restored_alert(self) -> None:
        if not self.bot:
            return
        text = ALERT_WHITE_INTERNET_HUB_RESTORED
        for aid in get_settings().ADMIN_IDS:
            try:
                await safe_send_message(
                    self.bot, chat_id=aid, text=text, parse_mode="HTML"
                )
            except Exception as e:
                logger.warning("Failed to send hub restored alert to admin %s: %s", aid, e)

    def _get_sub_lock(self, sub_id: int) -> asyncio.Lock:
        lock = self._sub_locks.get(sub_id)
        if lock is None:
            if len(self._sub_locks) > 1000:
                self._sub_locks = {k: v for k, v in self._sub_locks.items() if v.locked()}
            lock = asyncio.Lock()
            self._sub_locks[sub_id] = lock
        return lock

    async def _notify_sub_ready(self, session: AsyncSession, user_id: int) -> None:
        if self.bot is None:
            return
        try:
            from database.models import User
            user = await session.get(User, user_id)
            if not user or not user.telegram_id or user.is_bot_blocked:
                return
            from bot import texts
            await safe_send_message(
                self.bot,
                chat_id=user.telegram_id,
                text=texts.WL_AUTO_PUSH_READY,
                parse_mode="HTML",
                raise_forbidden=False,
            )
        except Exception as exc:
            logger.warning("Failed to send auto-push for user_id=%d: %s", user_id, exc)

    async def _reconcile_single_subscription(
        self,
        server_id: int,
        api_url: str,
        api_key: str,
        target_epoch: str,
        task: dict,
        sf,
    ) -> bool:
        sub_id = task["sub_id"]
        sub_uuid = task["uuid"]
        desired_active = task["desired_active"]
        target_version = task["target_version"]
        expected_xhttp = task.get("expected_xhttp_locations") or []
        expected_inbound_tags = set()
        for loc in expected_xhttp:
            if isinstance(loc, dict) and loc.get("inbound_tag"):
                expected_inbound_tags.add(str(loc["inbound_tag"]))
            elif isinstance(loc, dict):
                code = loc.get("location_code") or loc.get("code")
                if code:
                    expected_inbound_tags.add(f"just1k-wl-inbound-{code}")
            elif isinstance(loc, str) and loc:
                expected_inbound_tags.add(f"just1k-wl-inbound-{loc}")
        if not expected_inbound_tags:
            expected_inbound_tags.add("just1k-wl-default")

        notify_user_id: int | None = None
        async with self._get_sub_lock(sub_id):
            async with self._semaphore:
                try:
                    # 1. External network mutation on Xray node (no ORM session held during HTTP I/O)
                    resp = await self.client.sync_client(
                        api_url,
                        api_key,
                        sub_uuid,
                        is_active=desired_active,
                        version=target_version,
                        expected_node_epoch=target_epoch,
                        idempotency_key=f"reconcile:{sub_id}:{target_epoch}:{target_version}:{desired_active}",
                    )

                    sync_result = resp.result if hasattr(resp, "result") else resp[0]
                    err_msg = resp.error if hasattr(resp, "error") else resp[1]
                    verified_epoch = getattr(resp, "verified_epoch", None) or target_epoch
                    verified_inbounds = set(getattr(resp, "verified_inbounds", None) or [])

                    inv_data = None
                    inv_ok = False
                    if sync_result == SyncResult.ALREADY_NEWER:
                        inv_ok, inv_data, _ = await self.client.get_inventory(
                            api_url, api_key, client_ids=[sub_uuid], service="white_internet"
                        )

                    async with sf() as lock_session:
                        sub = await white_internet_repo.get_subscription_with_lock(lock_session, sub_id)
                        if sub is None:
                            return False

                        if sync_result == SyncResult.APPLIED and sub.desired_version == target_version:
                            # Postcondition verification: check all required inbounds were verified
                            if verified_inbounds and not expected_inbound_tags.issubset(verified_inbounds):
                                missing = expected_inbound_tags - verified_inbounds
                                logger.warning(
                                    "Inbound coverage incomplete for sub_id=%d on server %d: missing %s (verified=%s). Keeping PENDING_UPDATE.",
                                    sub_id,
                                    server_id,
                                    missing,
                                    verified_inbounds,
                                )
                                sub.provisioning_status = WhiteInternetProvisioningStatus.PENDING_UPDATE
                                sub.last_sync_error = f"missing_inbounds:{','.join(sorted(missing))}"
                                sub.last_synced_at = now_utc()
                                await lock_session.commit()
                                return False

                            if verified_epoch != target_epoch:
                                logger.warning(
                                    "Epoch drift detected after mutation for sub_id=%d: verified=%s != target=%s. Keeping PENDING_UPDATE.",
                                    sub_id,
                                    verified_epoch,
                                    target_epoch,
                                )
                                sub.provisioning_status = WhiteInternetProvisioningStatus.PENDING_UPDATE
                                sub.last_sync_error = "epoch_drift_detected"
                                sub.last_synced_at = now_utc()
                                await lock_session.commit()
                                return False

                            sub.actual_version = target_version
                            sub.last_reconciled_node_epoch = verified_epoch
                            transitioned_to_active = False
                            if sub.status == WhiteInternetStatus.PENDING and desired_active:
                                sub.status = WhiteInternetStatus.ACTIVE
                                sub.status_reason = None
                                transitioned_to_active = True
                            sub.provisioning_status = (
                                WhiteInternetProvisioningStatus.ACTIVE
                                if desired_active
                                else WhiteInternetProvisioningStatus.SYNCED_INACTIVE
                            )
                            sub.last_synced_at = now_utc()
                            sub.last_sync_error = None
                            await lock_session.commit()
                            if transitioned_to_active:
                                notify_user_id = sub.user_id
                            return True
                        elif sync_result == SyncResult.ALREADY_NEWER:
                            # Runtime inventory was already fetched before opening lock_session
                            if inv_ok and inv_data and "inventory" in inv_data:
                                client_inv = inv_data["inventory"].get(sub_uuid)
                                if client_inv:
                                    observed_state = client_inv.get("observed_state")
                                    expected_state = "active" if desired_active else "disabled"
                                    if observed_state == expected_state:
                                        logger.info(
                                            "Runtime inventory verified for sub_id=%d on server %d: observed=%s matches desired=%s. Marking synced.",
                                            sub_id,
                                            server_id,
                                            observed_state,
                                            desired_active,
                                        )
                                        sub.actual_version = max(sub.actual_version or 0, target_version)
                                        sub.last_reconciled_node_epoch = target_epoch
                                        transitioned_to_active = False
                                        if sub.status == WhiteInternetStatus.PENDING and desired_active:
                                            sub.status = WhiteInternetStatus.ACTIVE
                                            sub.status_reason = None
                                            transitioned_to_active = True
                                        sub.provisioning_status = (
                                            WhiteInternetProvisioningStatus.ACTIVE
                                            if desired_active
                                            else WhiteInternetProvisioningStatus.SYNCED_INACTIVE
                                        )
                                        sub.last_synced_at = now_utc()
                                        sub.last_sync_error = None
                                        await lock_session.commit()
                                        if transitioned_to_active:
                                            notify_user_id = sub.user_id
                                        return True

                            # Observed state does not match desired state: force convergence by bumping desired_version
                            sub.desired_version = max(sub.desired_version, target_version) + 1
                            sub.provisioning_status = WhiteInternetProvisioningStatus.PENDING_UPDATE
                            sub.last_sync_error = f"node_already_newer_inventory_mismatch_{err_msg}"
                            sub.last_synced_at = now_utc()
                            logger.warning(
                                "Node reported already_newer but observed state did not match for sub_id=%d on server %d. Bumping desired_version to %d.",
                                sub_id,
                                server_id,
                                sub.desired_version,
                            )
                            await lock_session.commit()
                            return False
                        elif sync_result == SyncResult.FENCED:
                            sub.desired_version = max(sub.desired_version, target_version) + 1
                            sub.provisioning_status = WhiteInternetProvisioningStatus.PENDING_UPDATE
                            sub.last_sync_error = err_msg or "sync_fenced"
                            sub.last_synced_at = now_utc()
                            logger.warning(
                                "Sync fenced for sub_id=%d on server %d (target_version=%d). Bumped desired_version to %d: %s",
                                sub_id,
                                server_id,
                                target_version,
                                sub.desired_version,
                                err_msg,
                            )
                            await lock_session.commit()
                            return False
                        elif sync_result == SyncResult.FAILED:
                            sub.provisioning_status = WhiteInternetProvisioningStatus.PENDING_UPDATE
                            sub.last_sync_error = err_msg or "sync_failed"
                            sub.last_synced_at = now_utc()
                            logger.error(
                                "Sync failed for sub_id=%d on server %d: %s",
                                sub_id,
                                server_id,
                                err_msg,
                            )
                            await lock_session.commit()
                            return False
                        else:
                            sub.provisioning_status = WhiteInternetProvisioningStatus.PENDING_UPDATE
                            sub.last_synced_at = now_utc()
                            logger.warning(
                                "Reconciliation detected version drift during sync for sub_id=%d on server %d (desired=%d != target=%d): result=%s error=%s",
                                sub_id,
                                server_id,
                                sub.desired_version,
                                target_version,
                                sync_result,
                                err_msg,
                            )
                            await lock_session.commit()
                            return False
                finally:
                    if notify_user_id is not None:
                        try:
                            async with sf() as notify_session:
                                await self._notify_sub_ready(notify_session, notify_user_id)
                        except Exception as exc:
                            logger.warning(
                                "Failed to notify user %s for sub %d after advisory unlock: %s",
                                notify_user_id,
                                sub_id,
                                exc,
                            )

    async def run_reconciliation_cycle(self, session: AsyncSession | None = None) -> int:
        now = now_utc()
        sf = self.session_factory
        if session is not None:
            bind = getattr(session, "bind", None)
            if not bind and hasattr(session, "sync_session"):
                bind = getattr(session.sync_session, "bind", None)
            if isinstance(bind, AsyncEngine):
                task_maker = async_sessionmaker(bind, expire_on_commit=False)

                @asynccontextmanager
                async def _task_session_ctx():
                    async with task_maker() as s:
                        yield s

                sf = _task_session_ctx
            else:
                @asynccontextmanager
                async def _session_ctx():
                    yield session

                sf = _session_ctx

        async with sf() as sess:
            stmt_servers = (
                select(Server)
                .where(
                    Server.protocol.in_([XRAY_PROTOCOL, VLESS_PROTOCOL]),
                    Server.api_url.is_not(None),
                    Server.api_key.is_not(None),
                    Server.is_active.is_(True),
                    Server.lifecycle_status.in_([
                        ServerLifecycleStatus.ACTIVE,
                        ServerLifecycleStatus.DECOMMISSIONING,
                    ]),
                    Server.health_state.in_([ServerHealthState.ONLINE, ServerHealthState.WAITING_CONFIRMATION]),
                )
                .order_by(Server.id.asc())
            )
            res_servers = await sess.execute(stmt_servers)
            servers = res_servers.scalars().all()

            active_server_targets = []
            for s in servers:
                if not (
                    getattr(s, "protocol", None) in (XRAY_PROTOCOL, VLESS_PROTOCOL)
                    or (getattr(s, "protocol", None) is None and WHITE_INTERNET_CAPABILITY in (s.capabilities or []))
                ) or WHITE_INTERNET_CAPABILITY not in (s.capabilities or []):
                    continue
                epoch = s.xray_instance_epoch
                if not epoch:
                    try:
                        is_h, node_ep, h_data = await self.client.check_health(s.api_url, s.api_key)
                        if is_h and node_ep:
                            epoch = node_ep
                            s.xray_instance_epoch = node_ep
                            if h_data:
                                s.xray_instance_boot_id = h_data.get("boot_id")
                                s.xray_instance_starttime = h_data.get("starttime")
                            await sess.commit()
                    except Exception as e:
                        logger.debug("Failed initializing epoch for server %d: %s", s.id, e)
                if epoch:
                    active_server_targets.append((
                        s.id,
                        s.api_url,
                        s.api_key,
                        epoch,
                        (s.extra_data or {}).get("xhttp_locations", []),
                    ))

        synced_count = 0
        for server_id, api_url, api_key, target_epoch, xhttp_locs in active_server_targets:
            if not target_epoch:
                continue

            sync_tasks: list[dict] = []
            async with sf() as sess:
                stmt_subs = (
                    select(WhiteInternetSubscription)
                    .where(
                        WhiteInternetSubscription.hub_server_id == server_id,
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
                            and_(
                                WhiteInternetSubscription.status.in_([
                                    WhiteInternetStatus.PENDING,
                                    WhiteInternetStatus.ACTIVE,
                                    WhiteInternetStatus.EXHAUSTED,
                                ]),
                                WhiteInternetSubscription.expires_at <= now,
                            ),
                        ),
                    )
                    .order_by(
                        nulls_first(WhiteInternetSubscription.last_synced_at.asc()),
                        WhiteInternetSubscription.id.asc(),
                    )
                    .limit(BATCH_SIZE)
                )
                res_subs = await sess.execute(stmt_subs)
                pending_subs = res_subs.scalars().all()

                for sub in pending_subs:
                    if sub.expires_at is not None and sub.expires_at <= now and sub.status in (
                        WhiteInternetStatus.PENDING,
                        WhiteInternetStatus.ACTIVE,
                        WhiteInternetStatus.EXHAUSTED,
                    ):
                        await white_internet_repo.expire_subscription_atomic(sess, sub.id)
                        await sess.commit()

                    desired_active = (
                        sub.status in (WhiteInternetStatus.PENDING, WhiteInternetStatus.ACTIVE)
                        and (sub.expires_at is None or sub.expires_at > now)
                    )
                    sync_tasks.append({
                        "sub_id": sub.id,
                        "uuid": sub.uuid,
                        "desired_active": desired_active,
                        "target_version": sub.desired_version,
                        "expected_xhttp_locations": xhttp_locs,
                    })

            # Bounded concurrent execution outside DB transaction
            if sync_tasks:
                tasks = [
                    self._reconcile_single_subscription(
                        server_id, api_url, api_key, target_epoch, task, sf
                    )
                    for task in sync_tasks
                ]
                results = await asyncio.gather(*tasks, return_exceptions=True)
                for r in results:
                    if isinstance(r, Exception):
                        logger.error("Error in parallel reconciliation task: %s", r, exc_info=r)
                    elif r is True:
                        synced_count += 1

        synced_count += await self._finalize_hard_deletes(sf)
        synced_count += await self._sweep_orphan_cleanups(sf)
        synced_count += await self._sweep_unallocated_subscriptions(sf)

        return synced_count

    async def _sweep_orphan_cleanups(self, sf) -> int:
        """Converge former hub nodes to disabled for migrated subscriptions.

        Drains the durable orphan-cleanup outbox written by renew migrations in
        the same transaction. Fire-and-forget dispatches are only accelerators;
        this sweep is the guarantee, so restarts can never orphan credentials.
        """
        swept = 0
        try:
            async with sf() as sess:
                stmt = (
                    select(white_internet_repo.WhiteInternetOrphanCleanup.id)
                    .where(
                        white_internet_repo.WhiteInternetOrphanCleanup.status == "pending",
                        white_internet_repo.WhiteInternetOrphanCleanup.attempts < 20,
                    )
                    .order_by(white_internet_repo.WhiteInternetOrphanCleanup.id.asc())
                    .limit(BATCH_SIZE)
                )
                cleanup_ids = list((await sess.execute(stmt)).scalars().all())
        except Exception as exc:
            logger.debug("Orphan sweep listing failed: %s", exc)
            return 0
        for cleanup_id in cleanup_ids:
            async with sf() as sess:
                try:
                    row = await sess.get(
                        white_internet_repo.WhiteInternetOrphanCleanup,
                        cleanup_id,
                        with_for_update=True,
                    )
                    if row is None or row.status != "pending":
                        continue
                    server = (
                        await sess.get(Server, row.server_id)
                        if row.server_id is not None
                        else None
                    )
                    if server is None:
                        # Hub server row is gone (node decommissioned): the credential
                        # dies with the node, nothing left to converge.
                        await white_internet_repo.mark_orphan_cleanup_done(sess, row.id)
                        await sess.commit()
                        swept += 1
                        continue

                    # Defense against A -> B -> A ping-pong migration:
                    # If this client is currently active/pending on this hub node,
                    # the orphan cleanup is stale and must not deactivate the active user!
                    reassigned_sub = await sess.scalar(
                        select(WhiteInternetSubscription.id).where(
                            WhiteInternetSubscription.uuid == row.client_uuid,
                            WhiteInternetSubscription.hub_server_id == row.server_id,
                            WhiteInternetSubscription.status.in_([
                                WhiteInternetStatus.ACTIVE,
                                WhiteInternetStatus.PENDING,
                                WhiteInternetStatus.EXHAUSTED,
                            ]),
                        )
                    )
                    if isinstance(reassigned_sub, (int, str)):
                        cred_hash = hashlib.sha256(row.client_uuid.encode()).hexdigest()[:12]
                        logger.info(
                            "Orphan cleanup %d cancelled: credential sha256:%s... is currently active/placed on hub %d (sub_id=%s).",
                            row.id,
                            cred_hash,
                            row.server_id,
                            reassigned_sub,
                        )
                        await white_internet_repo.mark_orphan_cleanup_done(sess, row.id)
                        await sess.commit()
                        swept += 1
                        continue
                    server_proto = getattr(server, "protocol", None)
                    if not server_proto:
                        caps = getattr(server, "capabilities", None) or []
                        server_proto = XRAY_PROTOCOL if WHITE_INTERNET_CAPABILITY in caps else None
                    if (
                        server_proto not in (XRAY_PROTOCOL, VLESS_PROTOCOL)
                        or not server.is_active
                        or not server.api_url
                        or not server.api_key
                    ):
                        # Not converged yet and not safe to call: leave pending
                        # without burning attempts; retried on later cycles.
                        continue
                    async with self._semaphore:
                        resp = await self.client.sync_client(
                            server.api_url,
                            server.api_key,
                            client_uuid=row.client_uuid,
                            is_active=False,
                            version=row.desired_version,
                            expected_node_epoch=server.xray_instance_epoch,
                            idempotency_key=(
                                f"orphan:{row.id}:{row.client_uuid}:{row.desired_version}"
                            ),
                        )
                    sync_result = resp.result if hasattr(resp, "result") else resp[0]
                    err_msg = resp.error if hasattr(resp, "error") else resp[1]
                    if sync_result == SyncResult.APPLIED:
                        await white_internet_repo.mark_orphan_cleanup_done(sess, row.id)
                        await sess.commit()
                        swept += 1
                        cred_hash = hashlib.sha256(row.client_uuid.encode()).hexdigest()[:12]
                        logger.info(
                            "Swept orphan credential id=%d sha256:%s... on former hub %d.",
                            row.id,
                            cred_hash,
                            server.id,
                        )
                    elif sync_result == SyncResult.ALREADY_NEWER:
                        inv_ok, inv_data, _ = await self.client.get_inventory(
                            server.api_url,
                            server.api_key,
                            client_ids=[row.client_uuid],
                            service="white_internet",
                        )
                        observed = None
                        if inv_ok and inv_data and "inventory" in inv_data:
                            observed = (inv_data["inventory"].get(row.client_uuid) or {}).get(
                                "observed_state"
                            )
                        if observed == "disabled":
                            await white_internet_repo.mark_orphan_cleanup_done(sess, row.id)
                            await sess.commit()
                            swept += 1
                        else:
                            await white_internet_repo.mark_orphan_cleanup_failed(
                                sess, row.id, f"already_newer_unconfirmed:{observed}"
                            )
                            await sess.commit()
                    else:
                        await white_internet_repo.mark_orphan_cleanup_failed(
                            sess, row.id, err_msg or str(sync_result)
                        )
                        await sess.commit()
                except Exception as exc:
                    logger.warning(
                        "Orphan sweep failed for cleanup %d: %s", cleanup_id, exc
                    )
        return swept

    async def _finalize_hard_deletes(self, sf) -> int:
        """Delete reset rows only after the node confirmed the client disabled.

        Two-phase trial reset (see WhiteInternetService.reset_user_trial):
        rows stay DISABLED+PENDING_DELETE until the node converges to
        SYNCED_INACTIVE, then the row is removed so a fresh trial can be issued.
        Deleting earlier would orphan an active credential on the node.
        """
        finalized = 0
        try:
            async with sf() as sess:
                stmt = (
                    select(WhiteInternetSubscription.id)
                    .where(
                        WhiteInternetSubscription.pending_hard_delete.is_(True),
                        WhiteInternetSubscription.status == WhiteInternetStatus.DISABLED,
                    )
                    .order_by(WhiteInternetSubscription.id.asc())
                    .limit(BATCH_SIZE)
                )
                sub_ids = list((await sess.execute(stmt)).scalars().all())
        except Exception as exc:
            logger.debug("Hard-delete finalizer listing failed: %s", exc)
            return 0
        for sub_id in sub_ids:
            async with sf() as sess:
                try:
                    if await white_internet_repo.finalize_hard_delete_subscription(sess, sub_id):
                        await sess.commit()
                        finalized += 1
                        logger.info(
                            "Finalized two-phase trial reset: deleted subscription %d "
                            "after node-confirmed disable.",
                            sub_id,
                        )
                except Exception as exc:
                    logger.warning(
                        "Hard-delete finalizer failed for subscription %d: %s", sub_id, exc
                    )
        return finalized

    async def _sweep_unallocated_subscriptions(self, sf) -> int:
        """Allocate available ru_hub nodes to active/pending subscriptions with hub_server_id IS NULL."""
        allocated = 0
        try:
            async with sf() as sess:
                stmt = (
                    select(WhiteInternetSubscription.id)
                    .where(
                        WhiteInternetSubscription.hub_server_id.is_(None),
                        WhiteInternetSubscription.status.in_([
                            WhiteInternetStatus.PENDING,
                            WhiteInternetStatus.ACTIVE,
                            WhiteInternetStatus.EXHAUSTED,
                        ]),
                    )
                    .order_by(WhiteInternetSubscription.id.asc())
                    .limit(BATCH_SIZE)
                )
                res = await sess.execute(stmt)
                unallocated_ids = list(res.scalars().all())
        except Exception as exc:
            logger.debug("Unallocated subscription sweep query failed: %s", exc)
            return 0

        for sub_id in unallocated_ids:
            async with self._get_sub_lock(sub_id):
                async with self._semaphore:
                    async with sf() as sess:
                        try:
                            hub = await servers_repo.allocate_wi_hub_atomic(sess)
                            if hub is None:
                                logger.warning("No healthy hub server available for unallocated sub_id=%d", sub_id)
                                if self.bot:
                                    now_m = time.monotonic()
                                    if not self._hub_exhausted_alerted or (
                                        self._hub_exhausted_last_sent
                                        and (now_m - self._hub_exhausted_last_sent) >= 3600.0
                                    ):
                                        self._hub_exhausted_alerted = True
                                        self._hub_exhausted_last_sent = now_m
                                        asyncio.create_task(self._send_hub_exhausted_alert(sub_id))
                                break

                            sub = await sess.get(WhiteInternetSubscription, sub_id, with_for_update=True)
                            if sub is None or sub.hub_server_id is not None:
                                continue
                            if sub.status not in (
                                WhiteInternetStatus.PENDING,
                                WhiteInternetStatus.ACTIVE,
                                WhiteInternetStatus.EXHAUSTED,
                            ):
                                continue

                            sub.hub_server_id = hub.id
                            sub.desired_version += 1
                            sub.provisioning_status = WhiteInternetProvisioningStatus.PENDING_UPDATE
                            sub.last_reconciled_node_epoch = None
                            await sess.commit()
                            allocated += 1
                            if self._hub_exhausted_alerted and self.bot:
                                self._hub_exhausted_alerted = False
                                self._hub_exhausted_last_sent = None
                                asyncio.create_task(self._send_hub_restored_alert())
                        except Exception as exc:
                            logger.error("Failed to allocate hub node for sub_id=%d: %s", sub_id, exc)
                            await sess.rollback()
        return allocated


async def white_internet_reconciliation_loop(
    bot: Bot | None = None,
    shutdown_event: asyncio.Event | None = None,
):
    """Background loop running reconciliation cycles."""
    event = shutdown_event or asyncio.Event()
    worker = WhiteInternetReconciliationWorker(bot=bot)

    logger.info("White Internet reconciliation worker started.")

    try:
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
    finally:
        if worker.client is not None:
            try:
                await worker.client.close()
            except Exception as exc:
                logger.debug("Error closing XrayNodeClient in reconciliation worker: %s", exc)
        logger.info("White Internet reconciliation worker stopped.")
