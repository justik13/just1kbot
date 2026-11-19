"""Reconciliation worker ensuring White Internet subscriptions stay synced across all active WI ru_hubs."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
import logging

from aiogram import Bot
from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker
from sqlalchemy.orm.attributes import flag_modified

from config.constants import VLESS_PROTOCOL, WHITE_INTERNET_CAPABILITY, XRAY_PROTOCOL
from config.enums import (
    ServerHealthState,
    ServerLifecycleStatus,
    WhiteInternetProvisioningStatus,
    WhiteInternetStatus,
)
from database.connection import session_scope
from database.models import Server, WhiteInternetSubscription
from database.repositories import user_devices_repo, white_internet_repo
from services.xray_node_client import SyncResult, XrayNodeClient
from utils.datetime_helpers import now_utc
from utils.telegram import safe_send_message

logger = logging.getLogger(__name__)

RECONCILIATION_INTERVAL_SECONDS = 30.0
BATCH_SIZE = 100
DEFAULT_RECONCILIATION_CONCURRENCY = 5


class WhiteInternetReconciliationWorker:
    """Worker keeping White Internet subscriptions in sync across all active WI ru_hubs."""

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
            logger.warning("Failed to notify user_id=%d for WI ready: %s", user_id, exc)

    @staticmethod
    def _expected_inbound_tags(expected_xhttp: list) -> set[str]:
        tags: set[str] = set()
        for loc in expected_xhttp or []:
            if isinstance(loc, dict) and loc.get("inbound_tag"):
                tags.add(str(loc["inbound_tag"]))
            elif isinstance(loc, dict):
                code = loc.get("location_code") or loc.get("code")
                if code:
                    tags.add(f"just1k-wl-inbound-{code}")
            elif isinstance(loc, str) and loc:
                tags.add(f"just1k-wl-inbound-{loc}")
        if not tags:
            tags.add("just1k-wl-default")
        return tags

    async def _reconcile_sub_on_server(
        self,
        server_id: int,
        api_url: str,
        api_key: str,
        target_epoch: str,
        expected_xhttp: list,
        task: dict,
        all_server_ids: set[int],
        sf,
    ) -> bool:
        sub_id = task["sub_id"]
        sub_uuid = task["uuid"]
        user_id = task["user_id"]
        desired_active = task["desired_active"]
        target_version = task["target_version"]
        expected_inbound_tags = self._expected_inbound_tags(expected_xhttp)

        notify_user_id: int | None = None
        async with self._get_sub_lock(sub_id):
            async with self._semaphore:
                try:
                    resp = await self.client.sync_client(
                        api_url,
                        api_key,
                        sub_uuid,
                        is_active=desired_active,
                        version=target_version,
                        expected_node_epoch=target_epoch,
                        idempotency_key=f"reconcile:{sub_id}:{server_id}:{target_epoch}:{target_version}:{desired_active}",
                        service="white_internet",
                    )

                    dev_uuids: list[str] = []
                    async with sf() as dev_session:
                        dev_uuids = await user_devices_repo.get_active_device_uuids(dev_session, user_id)
                    for d_uuid in dev_uuids:
                        if d_uuid and d_uuid != sub_uuid:
                            try:
                                await self.client.sync_client(
                                    api_url,
                                    api_key,
                                    d_uuid,
                                    is_active=desired_active,
                                    version=target_version,
                                    expected_node_epoch=target_epoch,
                                    service="white_internet",
                                )
                            except Exception as dev_exc:
                                logger.debug("WI reconcile device %s sync note: %s", d_uuid, dev_exc)

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
                            if verified_inbounds and not expected_inbound_tags.issubset(verified_inbounds):
                                missing = expected_inbound_tags - verified_inbounds
                                logger.warning(
                                    "WI inbound coverage incomplete for sub_id=%d on server %d: missing %s",
                                    sub_id,
                                    server_id,
                                    missing,
                                )
                                sub.provisioning_status = WhiteInternetProvisioningStatus.PENDING_UPDATE
                                sub.last_sync_error = f"missing_inbounds:{','.join(sorted(missing))}"
                                sub.last_synced_at = now_utc()
                                await lock_session.commit()
                                return False

                            if verified_epoch != target_epoch:
                                sub.provisioning_status = WhiteInternetProvisioningStatus.PENDING_UPDATE
                                sub.last_sync_error = "epoch_drift_detected"
                                sub.last_synced_at = now_utc()
                                await lock_session.commit()
                                return False

                            node_state = dict(sub.node_sync_state or {}) if isinstance(sub.node_sync_state, dict) else {}
                            node_state[str(server_id)] = {
                                "version": target_version,
                                "epoch": verified_epoch,
                                "active": desired_active,
                                "synced_at": now_utc().isoformat(),
                            }
                            sub.node_sync_state = node_state
                            flag_modified(sub, "node_sync_state")

                            all_synced = all(
                                (node_state.get(str(sid)) or {}).get("version") == sub.desired_version
                                and (node_state.get(str(sid)) or {}).get("active") is desired_active
                                for sid in all_server_ids
                            )
                            if all_synced:
                                sub.actual_version = target_version
                                sub.provisioning_status = (
                                    WhiteInternetProvisioningStatus.ACTIVE
                                    if desired_active
                                    else WhiteInternetProvisioningStatus.SYNCED_INACTIVE
                                )
                                sub.last_sync_error = None
                                if sub.status == WhiteInternetStatus.PENDING and desired_active:
                                    sub.status = WhiteInternetStatus.ACTIVE
                                    sub.status_reason = None
                                    notify_user_id = sub.user_id

                            sub.last_synced_at = now_utc()
                            await lock_session.commit()
                            return True

                        if sync_result == SyncResult.ALREADY_NEWER:
                            if inv_ok and inv_data and "inventory" in inv_data:
                                client_inv = inv_data["inventory"].get(sub_uuid)
                                if client_inv:
                                    observed_state = client_inv.get("observed_state")
                                    expected_state = "active" if desired_active else "disabled"
                                    if observed_state == expected_state:
                                        node_state = dict(sub.node_sync_state or {}) if isinstance(sub.node_sync_state, dict) else {}
                                        node_state[str(server_id)] = {
                                            "version": target_version,
                                            "epoch": target_epoch,
                                            "active": desired_active,
                                            "synced_at": now_utc().isoformat(),
                                        }
                                        sub.node_sync_state = node_state
                                        flag_modified(sub, "node_sync_state")

                                        all_synced = all(
                                            (node_state.get(str(sid)) or {}).get("version") == sub.desired_version
                                            and (node_state.get(str(sid)) or {}).get("active") is desired_active
                                            for sid in all_server_ids
                                        )
                                        if all_synced:
                                            sub.actual_version = max(sub.actual_version or 0, target_version)
                                            sub.provisioning_status = (
                                                WhiteInternetProvisioningStatus.ACTIVE
                                                if desired_active
                                                else WhiteInternetProvisioningStatus.SYNCED_INACTIVE
                                            )
                                            sub.last_sync_error = None
                                            if sub.status == WhiteInternetStatus.PENDING and desired_active:
                                                sub.status = WhiteInternetStatus.ACTIVE
                                                sub.status_reason = None
                                                notify_user_id = sub.user_id

                                        sub.last_synced_at = now_utc()
                                        await lock_session.commit()
                                        return True

                            sub.desired_version = max(sub.desired_version, target_version) + 1
                            sub.provisioning_status = WhiteInternetProvisioningStatus.PENDING_UPDATE
                            sub.last_sync_error = f"node_already_newer_inventory_mismatch_{err_msg}"
                            sub.last_synced_at = now_utc()
                            await lock_session.commit()
                            return False

                        if sync_result == SyncResult.FENCED:
                            sub.desired_version = max(sub.desired_version, target_version) + 1
                            sub.provisioning_status = WhiteInternetProvisioningStatus.PENDING_UPDATE
                            sub.last_sync_error = err_msg or "sync_fenced"
                            sub.last_synced_at = now_utc()
                            await lock_session.commit()
                            return False

                        sub.provisioning_status = WhiteInternetProvisioningStatus.PENDING_UPDATE
                        sub.last_sync_error = err_msg or "sync_failed"
                        sub.last_synced_at = now_utc()
                        await lock_session.commit()
                        return False
                finally:
                    if notify_user_id is not None:
                        try:
                            async with sf() as notify_session:
                                await self._notify_sub_ready(notify_session, notify_user_id)
                        except Exception as exc:
                            logger.warning("Failed to notify user %s for WI sub %d: %s", notify_user_id, sub_id, exc)

    async def _reconcile_pending_revocations(
        self,
        active_servers: list[tuple[int, str, str, str, list]],
        sf,
    ) -> int:
        if not active_servers:
            return 0
        revoked_count = 0
        async with sf() as sess:
            stmt = (
                select(WhiteInternetSubscription)
                .where(WhiteInternetSubscription.pending_revoked_uuids.is_not(None))
                .order_by(WhiteInternetSubscription.id.asc())
                .limit(BATCH_SIZE)
            )
            subs = (await sess.execute(stmt)).scalars().all()
            work = [
                (s.id, [u for u in (s.pending_revoked_uuids or []) if isinstance(u, str) and u.strip()])
                for s in subs
                if isinstance(s.pending_revoked_uuids, list) and s.pending_revoked_uuids
            ]

        for sub_id, uuids in work:
            for old_uuid in uuids:
                results = []
                for _sid, api_url, api_key, target_epoch, _ in active_servers:
                    try:
                        async with self._semaphore:
                            resp = await self.client.sync_client(
                                api_url,
                                api_key,
                                old_uuid,
                                is_active=False,
                                expected_node_epoch=target_epoch,
                                service="white_internet",
                            )
                            results.append(resp.result in (SyncResult.APPLIED, SyncResult.ALREADY_NEWER))
                    except Exception:
                        results.append(False)
                if len(results) == len(active_servers) and all(results):
                    async with sf() as sess:
                        await white_internet_repo.pop_pending_revoked_uuid(sess, sub_id, old_uuid)
                        await sess.commit()
                    revoked_count += 1
        return revoked_count

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
            servers = (await sess.execute(stmt_servers)).scalars().all()

            active_server_targets: list[tuple[int, str, str, str, list]] = []
            for s in servers:
                if WHITE_INTERNET_CAPABILITY not in (s.capabilities or []):
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
                        logger.debug("Failed initializing epoch for WI server %d: %s", s.id, e)
                if epoch:
                    active_server_targets.append((
                        s.id,
                        s.api_url,
                        s.api_key,
                        epoch,
                        (s.extra_data or {}).get("xhttp_locations", []),
                    ))

        if not active_server_targets:
            return 0

        all_server_ids = {t[0] for t in active_server_targets}

        async with sf() as sess:
            stmt_subs = (
                select(WhiteInternetSubscription)
                .where(
                    or_(
                        WhiteInternetSubscription.actual_version != WhiteInternetSubscription.desired_version,
                        WhiteInternetSubscription.provisioning_status.in_([
                            WhiteInternetProvisioningStatus.PENDING_CREATE,
                            WhiteInternetProvisioningStatus.PENDING_UPDATE,
                            WhiteInternetProvisioningStatus.PENDING_DELETE,
                        ]),
                        WhiteInternetSubscription.status.in_([
                            WhiteInternetStatus.PENDING,
                            WhiteInternetStatus.ACTIVE,
                        ]),
                        and_(
                            WhiteInternetSubscription.status == WhiteInternetStatus.EXHAUSTED,
                            WhiteInternetSubscription.expires_at <= now,
                        ),
                    )
                )
                .order_by(WhiteInternetSubscription.id.asc())
                .limit(BATCH_SIZE)
            )
            subs = (await sess.execute(stmt_subs)).scalars().all()

            sub_tasks: list[dict] = []
            for sub in subs:
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
                sub_tasks.append({
                    "sub_id": sub.id,
                    "user_id": sub.user_id,
                    "uuid": sub.uuid,
                    "desired_active": desired_active,
                    "target_version": sub.desired_version,
                    "node_sync_state": dict(sub.node_sync_state or {}) if isinstance(sub.node_sync_state, dict) else {},
                    "actual_version": sub.actual_version,
                })

        synced_count = 0
        for server_id, api_url, api_key, target_epoch, xhttp_locs in active_server_targets:
            per_server_tasks = []
            for task in sub_tasks:
                n_state = task["node_sync_state"].get(str(server_id)) or {}
                if (
                    n_state.get("version") != task["target_version"]
                    or n_state.get("epoch") != target_epoch
                    or n_state.get("active") is not task["desired_active"]
                    or task["actual_version"] != task["target_version"]
                ):
                    per_server_tasks.append(
                        self._reconcile_sub_on_server(
                            server_id,
                            api_url,
                            api_key,
                            target_epoch,
                            xhttp_locs,
                            task,
                            all_server_ids,
                            sf,
                        )
                    )
            if per_server_tasks:
                results = await asyncio.gather(*per_server_tasks, return_exceptions=True)
                for r in results:
                    if isinstance(r, Exception):
                        logger.error("Error in WI reconciliation task: %s", r, exc_info=r)
                    elif r is True:
                        synced_count += 1

        synced_count += await self._reconcile_pending_revocations(active_server_targets, sf)
        return synced_count


async def white_internet_reconciliation_loop(
    bot: Bot | None = None,
    shutdown_event: asyncio.Event | None = None,
):
    """Background loop running WI reconciliation cycles across all active WI ru_hubs."""
    event = shutdown_event or asyncio.Event()
    worker = WhiteInternetReconciliationWorker(bot=bot)

    logger.info("White Internet reconciliation worker started.")

    try:
        while not event.is_set():
            try:
                synced = await worker.run_reconciliation_cycle()
                if synced > 0:
                    logger.info("Reconciled %d White Internet items.", synced)
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
