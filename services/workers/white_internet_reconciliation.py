"""Reconciliation worker ensuring White Internet subscriptions stay synced across all active WI ru_hubs."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
import logging
import time

from aiogram import Bot
from sqlalchemy import and_, func, or_, select
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
from database.models import Server, User, WhiteInternetSubscription
from database.repositories import user_devices_repo, white_internet_repo
from services.xray_node_client import SyncResult, XrayNodeClient
from utils.datetime_helpers import now_utc
from utils.telegram import safe_send_message

logger = logging.getLogger(__name__)

RECONCILIATION_INTERVAL_SECONDS = 30.0
INVENTORY_DRIFT_INTERVAL_SECONDS = 600.0
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
        self._last_inventory_drift_check_mono: float | None = None
        self._last_checked_server_epochs: dict[int, str] = {}

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

    async def _refresh_server_epoch(
        self,
        server_id: int,
        api_url: str,
        api_key: str,
        sf,
    ) -> str | None:
        """Re-fetch /v1/health on SyncResult.FENCED and update server.xray_instance_epoch in DB."""
        try:
            is_h, node_ep, h_data = await self.client.check_health(api_url, api_key)
            if is_h and node_ep:
                async with sf() as sess:
                    srv = await sess.get(Server, server_id)
                    if srv is not None:
                        srv.xray_instance_epoch = str(node_ep)
                        if isinstance(h_data, dict):
                            if h_data.get("boot_id"):
                                srv.xray_instance_boot_id = str(h_data["boot_id"])
                            if h_data.get("starttime") is not None:
                                srv.xray_instance_starttime = int(h_data["starttime"])
                        await sess.commit()
                return str(node_ep)
        except Exception as exc:
            logger.debug("Failed refreshing epoch for WI server %d after FENCED: %s", server_id, exc)
        return None

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
        server_epochs: dict[int, str] | None = None,
        bypass_idempotency: bool = False,
    ) -> bool:
        sub_id = task["sub_id"]
        sub_uuid = task["uuid"]
        user_id = task["user_id"]
        desired_active = task["desired_active"]
        target_version = task["target_version"]
        wi_device_limit = max(1, int(task.get("device_limit") or 1))
        expected_inbound_tags = self._expected_inbound_tags(expected_xhttp)

        notify_user_id: int | None = None
        async with self._get_sub_lock(sub_id):
            async with self._semaphore:
                try:
                    idempotency_key = (
                        None
                        if bypass_idempotency
                        else f"reconcile:{sub_id}:{server_id}:{target_epoch}:{target_version}:{desired_active}"
                    )
                    resp = await self.client.sync_client(
                        api_url,
                        api_key,
                        sub_uuid,
                        is_active=desired_active,
                        version=target_version,
                        expected_node_epoch=target_epoch,
                        idempotency_key=idempotency_key,
                        service="white_internet",
                    )

                    async with sf() as dev_session:
                        devs = await user_devices_repo.list_user_devices(
                            dev_session, user_id, wi_device_limit=wi_device_limit
                        )
                    assigned_dev_uuids = [
                        str(d["device_uuid"])
                        for d in devs
                        if d.get("is_bi_assigned") and d.get("device_uuid")
                    ]
                    displaced_dev_uuids = [
                        str(d["device_uuid"])
                        for d in devs
                        if not d.get("is_bi_assigned") and d.get("device_uuid")
                    ]
                    dev_sync_failed = False
                    dev_version_conflict = False
                    dev_fenced = False
                    max_node_ver = target_version

                    for d_uuid in assigned_dev_uuids:
                        if d_uuid and d_uuid != sub_uuid:
                            try:
                                d_resp = await self.client.sync_client(
                                    api_url,
                                    api_key,
                                    d_uuid,
                                    is_active=desired_active,
                                    version=target_version,
                                    expected_node_epoch=target_epoch,
                                    service="white_internet",
                                )
                                d_res = d_resp.result if hasattr(d_resp, "result") else d_resp[0]
                                if d_res == SyncResult.ALREADY_NEWER:
                                    d_inv_ok, d_inv_data, _ = await self.client.get_inventory(
                                        api_url, api_key, client_ids=[d_uuid], service="white_internet"
                                    )
                                    d_inv = (
                                        (d_inv_data or {}).get("inventory", {}).get(d_uuid)
                                        if d_inv_ok and isinstance(d_inv_data, dict)
                                        else None
                                    )
                                    expected_d_state = "active" if desired_active else "disabled"
                                    d_ver_inbounds = set(getattr(d_resp, "verified_inbounds", None) or [])
                                    d_ver_epoch = getattr(d_resp, "verified_epoch", None) or target_epoch
                                    d_inbounds_ok = not d_ver_inbounds or expected_inbound_tags.issubset(d_ver_inbounds)
                                    d_epoch_ok = d_ver_epoch == target_epoch
                                    if not (
                                        d_inv
                                        and d_inv.get("observed_state") == expected_d_state
                                        and d_inbounds_ok
                                        and d_epoch_ok
                                    ):
                                        dev_sync_failed = True
                                        dev_version_conflict = True
                                        raw_v = (getattr(d_resp, "raw_data", None) or {}).get("version") or (
                                            (d_inv or {}).get("version")
                                        )
                                        if isinstance(raw_v, int) and raw_v > max_node_ver:
                                            max_node_ver = raw_v
                                elif d_res == SyncResult.FENCED:
                                    dev_sync_failed = True
                                    dev_fenced = True
                                elif d_res != SyncResult.APPLIED:
                                    dev_sync_failed = True
                            except Exception as dev_exc:
                                dev_sync_failed = True
                                logger.warning("WI reconcile device %s sync failed: %s", d_uuid, dev_exc)
                    for disp_uuid in displaced_dev_uuids:
                        if disp_uuid and disp_uuid != sub_uuid:
                            try:
                                disp_resp = await self.client.sync_client(
                                    api_url,
                                    api_key,
                                    disp_uuid,
                                    is_active=False,
                                    version=target_version,
                                    expected_node_epoch=target_epoch,
                                    service="white_internet",
                                )
                                disp_res = disp_resp.result if hasattr(disp_resp, "result") else disp_resp[0]
                                if disp_res == SyncResult.ALREADY_NEWER:
                                    disp_inv_ok, disp_inv_data, _ = await self.client.get_inventory(
                                        api_url, api_key, client_ids=[disp_uuid], service="white_internet"
                                    )
                                    disp_inv = (
                                        (disp_inv_data or {}).get("inventory", {}).get(disp_uuid)
                                        if disp_inv_ok and isinstance(disp_inv_data, dict)
                                        else None
                                    )
                                    if not (
                                        disp_inv_ok
                                        and (disp_inv is None or disp_inv.get("observed_state") == "disabled")
                                    ):
                                        dev_sync_failed = True
                                        dev_version_conflict = True
                                        raw_v = (getattr(disp_resp, "raw_data", None) or {}).get("version") or (
                                            (disp_inv or {}).get("version")
                                        )
                                        if isinstance(raw_v, int) and raw_v > max_node_ver:
                                            max_node_ver = raw_v
                                elif disp_res == SyncResult.FENCED:
                                    dev_sync_failed = True
                                    dev_fenced = True
                                elif disp_res != SyncResult.APPLIED:
                                    dev_sync_failed = True
                            except Exception as disp_exc:
                                dev_sync_failed = True
                                logger.warning("WI reconcile displaced device %s deactivation failed: %s", disp_uuid, disp_exc)

                    if dev_sync_failed:
                        if dev_fenced:
                            await self._refresh_server_epoch(server_id, api_url, api_key, sf)
                        if dev_version_conflict:
                            async with sf() as lock_session:
                                sub = await white_internet_repo.get_subscription_with_lock(lock_session, sub_id)
                                if sub is not None:
                                    sub.desired_version = max(sub.desired_version or 1, target_version, max_node_ver) + 1
                                    sub.provisioning_status = WhiteInternetProvisioningStatus.PENDING_UPDATE
                                    sub.last_sync_error = "device_already_newer_inventory_mismatch"
                                    sub.last_synced_at = now_utc()
                                    await lock_session.commit()
                        return False

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
                    elif sync_result == SyncResult.FENCED:
                        await self._refresh_server_epoch(server_id, api_url, api_key, sf)

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
                                and (node_state.get(str(sid)) or {}).get("epoch")
                                == (server_epochs[sid] if server_epochs and sid in server_epochs else target_epoch)
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
                            client_inv = None
                            if inv_ok and inv_data and "inventory" in inv_data:
                                client_inv = inv_data["inventory"].get(sub_uuid)
                                if client_inv:
                                    observed_state = client_inv.get("observed_state")
                                    expected_state = "active" if desired_active else "disabled"
                                    inbounds_ok = not verified_inbounds or expected_inbound_tags.issubset(verified_inbounds)
                                    epoch_ok = verified_epoch == target_epoch
                                    if observed_state == expected_state and inbounds_ok and epoch_ok:
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
                                            and (node_state.get(str(sid)) or {}).get("epoch")
                                            == (
                                                server_epochs[sid]
                                                if server_epochs and sid in server_epochs
                                                else target_epoch
                                            )
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

                            node_ver = (getattr(resp, "raw_data", None) or {}).get("version") or (
                                (client_inv or {}).get("version")
                            )
                            base_ver = max(
                                sub.desired_version or 1,
                                target_version,
                                int(node_ver) if isinstance(node_ver, int) else 0,
                            )
                            sub.desired_version = base_ver + 1
                            sub.provisioning_status = WhiteInternetProvisioningStatus.PENDING_UPDATE
                            sub.last_sync_error = f"node_already_newer_inventory_mismatch_{err_msg}"
                            sub.last_synced_at = now_utc()
                            await lock_session.commit()
                            return False

                        if sync_result == SyncResult.FENCED:
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
        expected_hub_count: int | None = None,
    ) -> int:
        if not active_servers:
            return 0
        required_hub_count = (
            expected_hub_count if expected_hub_count is not None else len(active_servers)
        )
        revoked_count = 0
        async with sf() as sess:
            stmt = (
                select(WhiteInternetSubscription)
                .where(
                    WhiteInternetSubscription.pending_revoked_uuids.is_not(None),
                    func.jsonb_array_length(WhiteInternetSubscription.pending_revoked_uuids) > 0,
                )
                .order_by(WhiteInternetSubscription.id.asc())
                .limit(BATCH_SIZE)
            )
            subs = (await sess.execute(stmt)).scalars().all()
            wi_limit_by_user: dict[int, int] = {}
            for s in subs:
                uid_val = getattr(s, "user_id", None)
                if uid_val is None:
                    continue
                if s.status in (WhiteInternetStatus.PENDING, WhiteInternetStatus.ACTIVE):
                    lim = max(1, int(getattr(s, "device_limit", 1) or 1))
                    wi_limit_by_user[int(uid_val)] = max(wi_limit_by_user.get(int(uid_val), 0), lim)
            assigned_devs_by_user, _ = await user_devices_repo.partition_bi_device_uuids_by_users(
                sess, wi_limit_by_user
            )
            active_devs_by_user = {
                uid_int: set(uuids) for uid_int, uuids in assigned_devs_by_user.items()
            }
            work = [
                (
                    s.id,
                    s.status,
                    str(getattr(s, "uuid", "") or "").strip().lower(),
                    active_devs_by_user.get(int(s.user_id), set()),
                    int(getattr(s, "desired_version", 1) or 1),
                    [u for u in (s.pending_revoked_uuids or []) if isinstance(u, str) and u.strip()],
                )
                for s in subs
                if isinstance(s.pending_revoked_uuids, list) and s.pending_revoked_uuids
            ]

        for sub_id, sub_status, active_sub_uuid, active_dev_uuids, sub_ver, uuids in work:
            cur_ver = sub_ver
            for old_uuid in uuids:
                clean_old = old_uuid.strip().lower()
                is_still_live_uuid = (
                    sub_status in (
                        WhiteInternetStatus.PENDING,
                        WhiteInternetStatus.ACTIVE,
                    )
                    and clean_old == active_sub_uuid
                ) or (
                    sub_status in (WhiteInternetStatus.PENDING, WhiteInternetStatus.ACTIVE)
                    and clean_old in active_dev_uuids
                )
                if is_still_live_uuid:
                    logger.info(
                        "Skipping stale pending revocation of active WI UUID %s on sub %d (status=%s)",
                        clean_old[:8],
                        sub_id,
                        sub_status,
                    )
                    async with sf() as sess:
                        await white_internet_repo.pop_pending_revoked_uuid(sess, sub_id, old_uuid)
                        await sess.commit()
                    continue
                results = []
                bump_version_to: int | None = None
                for _sid, api_url, api_key, target_epoch, _ in active_servers:
                    try:
                        async with self._semaphore:
                            resp = await self.client.sync_client(
                                api_url,
                                api_key,
                                old_uuid,
                                is_active=False,
                                version=cur_ver,
                                expected_node_epoch=target_epoch,
                                service="white_internet",
                            )
                            res = resp.result if hasattr(resp, "result") else resp[0]
                            if res == SyncResult.APPLIED:
                                results.append(True)
                            elif res == SyncResult.ALREADY_NEWER:
                                inv_ok, inv_data, _ = await self.client.get_inventory(
                                    api_url, api_key, client_ids=[old_uuid], service="white_internet"
                                )
                                client_inv = (
                                    (inv_data or {}).get("inventory", {}).get(old_uuid)
                                    if inv_ok and isinstance(inv_data, dict)
                                    else None
                                )
                                if inv_ok and (client_inv is None or client_inv.get("observed_state") == "disabled"):
                                    results.append(True)
                                else:
                                    raw_v = (getattr(resp, "raw_data", None) or {}).get("version") or (
                                        (client_inv or {}).get("version")
                                    )
                                    retry_ver = max(cur_ver, int(raw_v) if isinstance(raw_v, int) else 0) + 1
                                    retry_resp = await self.client.sync_client(
                                        api_url,
                                        api_key,
                                        old_uuid,
                                        is_active=False,
                                        version=retry_ver,
                                        expected_node_epoch=target_epoch,
                                        service="white_internet",
                                    )
                                    retry_res = retry_resp.result if hasattr(retry_resp, "result") else retry_resp[0]
                                    if retry_res == SyncResult.APPLIED:
                                        results.append(True)
                                    else:
                                        results.append(False)
                                    bump_version_to = max(bump_version_to or 0, retry_ver)
                            elif res == SyncResult.FENCED:
                                await self._refresh_server_epoch(_sid, api_url, api_key, sf)
                                results.append(False)
                            else:
                                results.append(False)
                    except Exception:
                        results.append(False)
                if bump_version_to is not None:
                    async with sf() as sess:
                        sub = await white_internet_repo.get_subscription_with_lock(sess, sub_id)
                        if sub is not None and (sub.desired_version or 1) < bump_version_to:
                            sub.desired_version = bump_version_to
                            await sess.commit()
                    cur_ver = max(cur_ver, bump_version_to)
                if required_hub_count > 0 and len(results) == required_hub_count and all(results):
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
                )
                .order_by(Server.id.asc())
            )
            servers = (await sess.execute(stmt_servers)).scalars().all()

            active_server_targets: list[tuple[int, str, str, str, list]] = []
            decommissioning_server_ids: set[int] = set()
            total_active_wi_hubs = 0
            for s in servers:
                if WHITE_INTERNET_CAPABILITY not in (s.capabilities or []):
                    continue
                total_active_wi_hubs += 1
                health_state = getattr(s, "health_state", None) or ServerHealthState.ONLINE
                if health_state not in (ServerHealthState.ONLINE, ServerHealthState.WAITING_CONFIRMATION):
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
                    if str(getattr(s, "lifecycle_status", "") or "").upper() == ServerLifecycleStatus.DECOMMISSIONING.value:
                        decommissioning_server_ids.add(s.id)
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
        active_only_server_ids = all_server_ids - decommissioning_server_ids
        server_epochs = {t[0]: t[3] for t in active_server_targets}

        async with sf() as sess:
            stmt_urgent = (
                select(WhiteInternetSubscription, User.financial_hold, User.is_deleted)
                .join(User, User.id == WhiteInternetSubscription.user_id)
                .where(
                    or_(
                        WhiteInternetSubscription.actual_version != WhiteInternetSubscription.desired_version,
                        WhiteInternetSubscription.provisioning_status.in_([
                            WhiteInternetProvisioningStatus.PENDING_CREATE,
                            WhiteInternetProvisioningStatus.PENDING_UPDATE,
                            WhiteInternetProvisioningStatus.PENDING_DELETE,
                        ]),
                        WhiteInternetSubscription.status == WhiteInternetStatus.PENDING,
                        and_(
                            WhiteInternetSubscription.status.in_([
                                WhiteInternetStatus.ACTIVE,
                                WhiteInternetStatus.EXHAUSTED,
                            ]),
                            WhiteInternetSubscription.expires_at <= now,
                        ),
                        and_(
                            WhiteInternetSubscription.status == WhiteInternetStatus.ACTIVE,
                            or_(User.financial_hold.is_(True), User.is_deleted.is_(True)),
                        ),
                    )
                )
                .order_by(WhiteInternetSubscription.id.asc())
                .limit(BATCH_SIZE)
            )
            urgent_rows = (await sess.execute(stmt_urgent)).all()
            seen_sub_ids = {sub.id for sub, _, _ in urgent_rows}

            remaining_limit = max(0, BATCH_SIZE - len(urgent_rows))
            active_rows = []
            if remaining_limit > 0:
                stmt_active = (
                    select(WhiteInternetSubscription, User.financial_hold, User.is_deleted)
                    .join(User, User.id == WhiteInternetSubscription.user_id)
                    .where(WhiteInternetSubscription.status == WhiteInternetStatus.ACTIVE)
                )
                if seen_sub_ids:
                    stmt_active = stmt_active.where(
                        WhiteInternetSubscription.id.not_in(seen_sub_ids)
                    )
                stmt_active = stmt_active.order_by(
                    WhiteInternetSubscription.last_synced_at.asc().nullsfirst(),
                    WhiteInternetSubscription.id.asc(),
                ).limit(remaining_limit)
                active_rows = (await sess.execute(stmt_active)).all()

            subs_with_flags = [*urgent_rows, *active_rows]

            sub_tasks: list[dict] = []
            for sub, is_hold, is_deleted in subs_with_flags:
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
                    and not bool(is_hold)
                    and not bool(is_deleted)
                )
                sub_tasks.append({
                    "sub_id": sub.id,
                    "user_id": sub.user_id,
                    "uuid": sub.uuid,
                    "device_limit": max(1, int(getattr(sub, "device_limit", 1) or 1)),
                    "desired_active": desired_active,
                    "target_version": sub.desired_version,
                    "node_sync_state": dict(sub.node_sync_state or {}) if isinstance(sub.node_sync_state, dict) else {},
                    "actual_version": sub.actual_version,
                })

        synced_count = 0
        reconciled_pairs: set[tuple[int, int]] = set()
        for server_id, api_url, api_key, target_epoch, xhttp_locs in active_server_targets:
            per_server_tasks = []
            is_decommissioning = server_id in decommissioning_server_ids
            for task in sub_tasks:
                if is_decommissioning and task["desired_active"]:
                    continue
                task_server_ids = active_only_server_ids if task["desired_active"] else all_server_ids
                n_state = task["node_sync_state"].get(str(server_id)) or {}
                if (
                    n_state.get("version") != task["target_version"]
                    or n_state.get("epoch") != target_epoch
                    or n_state.get("active") is not task["desired_active"]
                    or task["actual_version"] != task["target_version"]
                ):
                    reconciled_pairs.add((task["sub_id"], server_id))
                    per_server_tasks.append(
                        self._reconcile_sub_on_server(
                            server_id,
                            api_url,
                            api_key,
                            target_epoch,
                            xhttp_locs,
                            task,
                            task_server_ids,
                            sf,
                            server_epochs=server_epochs,
                        )
                    )
            if per_server_tasks:
                results = await asyncio.gather(*per_server_tasks, return_exceptions=True)
                for r in results:
                    if isinstance(r, Exception):
                        logger.error("Error in WI reconciliation task: %s", r, exc_info=r)
                    elif r is True:
                        synced_count += 1

        non_decom_epochs = {
            t[0]: t[3] for t in active_server_targets if t[0] not in decommissioning_server_ids
        }
        now_mono = time.monotonic()
        should_check_drift = bool(non_decom_epochs) and (
            self._last_inventory_drift_check_mono is None
            or (now_mono - self._last_inventory_drift_check_mono) >= INVENTORY_DRIFT_INTERVAL_SECONDS
            or non_decom_epochs != self._last_checked_server_epochs
        )
        if should_check_drift:
            synced_count += await self._reconcile_inventory_drift(
                active_server_targets,
                decommissioning_server_ids,
                active_only_server_ids,
                server_epochs,
                sf,
                reconciled_pairs=reconciled_pairs,
            )
        synced_count += await self._reconcile_pending_revocations(
            active_server_targets,
            sf,
            expected_hub_count=total_active_wi_hubs,
        )
        return synced_count

    async def _collect_active_wi_inventory_truth(
        self,
        sf,
    ) -> tuple[dict[int, dict], dict[str, int]]:
        """Return (sub_tasks_by_id, expected_active_uuid -> sub_id) from DB."""
        now = now_utc()
        async with sf() as sess:
            active_rows = (
                await sess.execute(
                    select(WhiteInternetSubscription)
                    .join(User, User.id == WhiteInternetSubscription.user_id)
                    .where(
                        WhiteInternetSubscription.status.in_([
                            WhiteInternetStatus.PENDING,
                            WhiteInternetStatus.ACTIVE,
                        ]),
                        or_(
                            WhiteInternetSubscription.expires_at.is_(None),
                            WhiteInternetSubscription.expires_at > now,
                        ),
                        User.financial_hold.is_(False),
                        User.is_deleted.is_(False),
                    )
                    .order_by(WhiteInternetSubscription.id.asc())
                )
            ).scalars().all()
            wi_limit_by_user: dict[int, int] = {}
            for sub in active_rows:
                uid_val = getattr(sub, "user_id", None)
                if uid_val is None:
                    continue
                lim = max(1, int(getattr(sub, "device_limit", 1) or 1))
                wi_limit_by_user[int(uid_val)] = max(wi_limit_by_user.get(int(uid_val), 0), lim)
            devs_by_user, _ = await user_devices_repo.partition_bi_device_uuids_by_users(
                sess, wi_limit_by_user
            )

        tasks_by_sub_id: dict[int, dict] = {}
        expected_uuid_to_sub_id: dict[str, int] = {}
        for sub in active_rows:
            dev_limit = max(1, int(getattr(sub, "device_limit", 1) or 1))
            tasks_by_sub_id[sub.id] = {
                "sub_id": sub.id,
                "user_id": sub.user_id,
                "uuid": sub.uuid,
                "device_limit": dev_limit,
                "desired_active": True,
                "target_version": sub.desired_version,
                "node_sync_state": dict(sub.node_sync_state or {}) if isinstance(sub.node_sync_state, dict) else {},
                "actual_version": sub.actual_version,
            }
            if sub.uuid:
                expected_uuid_to_sub_id[str(sub.uuid).strip().lower()] = sub.id
            for d_uuid in devs_by_user.get(int(sub.user_id), [])[:dev_limit]:
                expected_uuid_to_sub_id[d_uuid] = sub.id
        return tasks_by_sub_id, expected_uuid_to_sub_id

    async def _reconcile_inventory_drift(
        self,
        active_server_targets: list[tuple[int, str, str, str, list]],
        decommissioning_server_ids: set[int],
        active_only_server_ids: set[int],
        server_epochs: dict[int, str],
        sf,
        *,
        reconciled_pairs: set[tuple[int, int]] | None = None,
    ) -> int:
        """Detect missing active WI UUIDs and stale node-side WI UUIDs across active ru_hubs."""
        non_decom_targets = [t for t in active_server_targets if t[0] not in decommissioning_server_ids]
        if not non_decom_targets:
            return 0

        tasks_by_sub_id, expected_uuid_to_sub_id = await self._collect_active_wi_inventory_truth(sf)
        expected_active_uuids = set(expected_uuid_to_sub_id.keys())
        seen_pairs = reconciled_pairs or set()
        healed_count = 0
        confirmed_expected_uuids: set[str] | None = None
        any_inv_ok = False
        checked_epochs: dict[int, str] = {}

        for server_id, api_url, api_key, target_epoch, xhttp_locs in non_decom_targets:
            try:
                inv_ok, inv_data, inv_err = await self.client.get_inventory(
                    api_url, api_key, service="white_internet"
                )
            except Exception as exc:
                logger.debug("WI inventory drift check skipped for server %d: %s", server_id, exc)
                continue
            if not inv_ok or not isinstance(inv_data, dict) or not isinstance(inv_data.get("inventory"), dict):
                if inv_err:
                    logger.debug("WI inventory drift check failed on server %d: %s", server_id, inv_err)
                continue

            any_inv_ok = True
            checked_epochs[server_id] = target_epoch
            inventory = inv_data["inventory"]
            expected_inbounds = self._expected_inbound_tags(xhttp_locs)
            node_active_uuids: set[str] = set()
            node_healthy_uuids: set[str] = set()
            for raw_cid, info in inventory.items():
                if not isinstance(info, dict):
                    continue
                cid = str(raw_cid).strip().lower()
                obs = info.get("observed_state")
                if obs in ("active", "partial"):
                    node_active_uuids.add(cid)
                inb_map = info.get("inbounds") or {}
                present_tags = {tag for tag, present in inb_map.items() if present} if isinstance(inb_map, dict) else set()
                inbounds_ok = not expected_inbounds or expected_inbounds.issubset(present_tags)
                if obs == "active" and info.get("all_inbounds_matched", True) is not False and inbounds_ok:
                    node_healthy_uuids.add(cid)

            missing_uuids = expected_active_uuids - node_healthy_uuids
            missing_sub_ids = {
                expected_uuid_to_sub_id[u]
                for u in missing_uuids
                if (expected_uuid_to_sub_id[u], server_id) not in seen_pairs
            }
            if missing_sub_ids:
                logger.info(
                    "Detected %d WI missing/partial UUIDs (%d subs) on server %d; resyncing.",
                    len(missing_uuids),
                    len(missing_sub_ids),
                    server_id,
                )
                repair_tasks = [
                    self._reconcile_sub_on_server(
                        server_id,
                        api_url,
                        api_key,
                        target_epoch,
                        xhttp_locs,
                        tasks_by_sub_id[sid_sub],
                        active_only_server_ids,
                        sf,
                        server_epochs=server_epochs,
                        bypass_idempotency=True,
                    )
                    for sid_sub in sorted(missing_sub_ids)
                ]
                repair_results = await asyncio.gather(*repair_tasks, return_exceptions=True)
                for res in repair_results:
                    if isinstance(res, Exception):
                        logger.error("Error repairing missing WI inventory on server %d: %s", server_id, res, exc_info=res)
                    elif res is True:
                        healed_count += 1

            candidate_stale = sorted(node_active_uuids - expected_active_uuids)
            if candidate_stale:
                if confirmed_expected_uuids is None:
                    _, fresh_map = await self._collect_active_wi_inventory_truth(sf)
                    confirmed_expected_uuids = set(fresh_map.keys())
                stale_uuids = [u for u in candidate_stale if u not in confirmed_expected_uuids]
                if stale_uuids:
                    logger.info(
                        "Disabling %d stale active WI UUIDs on server %d.",
                        len(stale_uuids),
                        server_id,
                    )
                    batch_payload = [
                        {
                            "uuid": u,
                            "is_active": False,
                            "desired_state": "disabled",
                            "version": 1,
                            "expected_node_epoch": target_epoch,
                            "service": "white_internet",
                        }
                        for u in stale_uuids
                    ]
                    batch_supported, batch_succeeded, batch_failed = await self.client.batch_sync_clients(
                        api_url, api_key, batch_payload
                    )
                    if batch_supported:
                        healed_count += len(batch_succeeded)
                        fallback_stale = [u for u in stale_uuids if u in batch_failed]
                    else:
                        fallback_stale = stale_uuids
                    for stale_uuid in fallback_stale:
                        try:
                            async with self._semaphore:
                                resp = await self.client.sync_client(
                                    api_url,
                                    api_key,
                                    stale_uuid,
                                    is_active=False,
                                    version=1,
                                    expected_node_epoch=target_epoch,
                                    service="white_internet",
                                )
                                res = resp.result if hasattr(resp, "result") else resp[0]
                                if res == SyncResult.ALREADY_NEWER:
                                    raw_v = (getattr(resp, "raw_data", None) or {}).get("version")
                                    retry_ver = (int(raw_v) if isinstance(raw_v, int) else 1) + 1
                                    retry_resp = await self.client.sync_client(
                                        api_url,
                                        api_key,
                                        stale_uuid,
                                        is_active=False,
                                        version=retry_ver,
                                        expected_node_epoch=target_epoch,
                                        service="white_internet",
                                    )
                                    res = retry_resp.result if hasattr(retry_resp, "result") else retry_resp[0]
                                if res == SyncResult.APPLIED:
                                    healed_count += 1
                        except Exception as exc:
                            logger.warning(
                                "Failed disabling stale WI UUID %s on server %d: %s",
                                stale_uuid[:8],
                                server_id,
                                exc,
                            )

        if any_inv_ok:
            self._last_inventory_drift_check_mono = time.monotonic()
            self._last_checked_server_epochs = checked_epochs

        return healed_count


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
