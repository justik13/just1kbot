"""Reconciliation worker ensuring White Internet subscriptions stay synced across all active WI ru_hubs."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
import logging
import re
import time
from typing import Any

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
FAILED_DRIFT_RETRY_SECONDS = 120.0
BATCH_SIZE = 100
DEFAULT_RECONCILIATION_CONCURRENCY = 5

_NODE_WIDE_BATCH_FAILURE_PREFIXES = (
    "Network failure:",
    "Unexpected failure:",
    "HTTP 5",
    "Node epoch is unavailable",
    "No managed Xray inbounds",
    "Client store corrupted",
)


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
        self._last_drift_check_by_server: dict[int, tuple[float, str]] = {}
        self._drift_fail_backoff: dict[int, tuple[float, str]] = {}

    @staticmethod
    def _get_inventory_entry(inv_ok: bool, inv_data: Any, client_uuid: str) -> dict | None:
        """Look up a client UUID in /v1/clients/inventory response case-insensitively."""
        if not inv_ok or not isinstance(inv_data, dict):
            return None
        inventory = inv_data.get("inventory")
        if not isinstance(inventory, dict):
            return None
        target = str(client_uuid or "").strip().lower()
        if not target:
            return None
        direct = inventory.get(client_uuid)
        if isinstance(direct, dict):
            return direct
        direct_low = inventory.get(target)
        if isinstance(direct_low, dict):
            return direct_low
        for k, v in inventory.items():
            if str(k).strip().lower() == target and isinstance(v, dict):
                return v
        return None

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
                                    d_inv = self._get_inventory_entry(d_inv_ok, d_inv_data, d_uuid)
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
                                    disp_inv = self._get_inventory_entry(disp_inv_ok, disp_inv_data, disp_uuid)
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
                            client_inv = self._get_inventory_entry(inv_ok, inv_data, sub_uuid)
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
                                client_inv = self._get_inventory_entry(inv_ok, inv_data, old_uuid)
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
            pending_pairs: list[tuple[int, int]] = []
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
                    pending_pairs.append((task["sub_id"], server_id))
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
                for (done_sub_id, done_server_id), r in zip(pending_pairs, results, strict=True):
                    if isinstance(r, Exception):
                        logger.error("Error in WI reconciliation task: %s", r, exc_info=r)
                    elif r is True:
                        reconciled_pairs.add((done_sub_id, done_server_id))
                        synced_count += 1

        non_decom_epochs = {
            t[0]: t[3] for t in active_server_targets if t[0] not in decommissioning_server_ids
        }
        now_mono = time.monotonic()
        force_full_drift = (
            self._last_inventory_drift_check_mono is not None
            and (now_mono - self._last_inventory_drift_check_mono) >= INVENTORY_DRIFT_INTERVAL_SECONDS
        )
        due_server_ids: set[int] = set()
        for sid, ep in non_decom_epochs.items():
            backoff = self._drift_fail_backoff.get(sid)
            if backoff is not None and backoff[1] == ep and now_mono < backoff[0]:
                continue
            last_ok = self._last_drift_check_by_server.get(sid)
            if (
                force_full_drift
                or last_ok is None
                or last_ok[1] != ep
                or (now_mono - last_ok[0]) >= INVENTORY_DRIFT_INTERVAL_SECONDS
            ):
                due_server_ids.add(sid)

        if due_server_ids:
            synced_count += await self._reconcile_inventory_drift(
                active_server_targets,
                decommissioning_server_ids,
                active_only_server_ids,
                server_epochs,
                sf,
                reconciled_pairs=reconciled_pairs,
                target_server_ids=due_server_ids,
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
        sub_ids: set[int] | None = None,
    ) -> tuple[dict[int, dict], dict[str, int]]:
        """Return (sub_tasks_by_id, expected_active_uuid -> sub_id) from DB."""
        if sub_ids is not None and not sub_ids:
            return {}, {}
        now = now_utc()
        async with sf() as sess:
            stmt = (
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
            )
            if sub_ids is not None:
                stmt = stmt.where(WhiteInternetSubscription.id.in_(sorted(sub_ids)))
            stmt = stmt.order_by(WhiteInternetSubscription.id.asc())
            active_rows = (await sess.execute(stmt)).scalars().all()
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
                expected_uuid_to_sub_id[str(d_uuid).strip().lower()] = sub.id
        return tasks_by_sub_id, expected_uuid_to_sub_id

    async def _load_stale_wi_uuid_db_state(
        self,
        sf,
        stale_uuids: list[str],
    ) -> dict[str, tuple[int, int, int, str]]:
        """Map stale WI UUIDs (sub.uuid or device_uuid) to (sub_id, desired_version, actual_version, status)."""
        clean = sorted({str(u).strip().lower() for u in stale_uuids if str(u).strip()})
        if not clean:
            return {}
        from database.models import UserDevice

        result: dict[str, tuple[int, int, int, str]] = {}
        async with sf() as sess:
            direct_rows = (
                await sess.execute(
                    select(WhiteInternetSubscription)
                    .where(func.lower(WhiteInternetSubscription.uuid).in_(clean))
                    .order_by(WhiteInternetSubscription.id.desc())
                )
            ).scalars().all()
            for sub in direct_rows:
                k = str(sub.uuid or "").strip().lower()
                if k and k not in result:
                    result[k] = (
                        sub.id,
                        int(sub.desired_version or 1),
                        int(sub.actual_version or 0),
                        str(sub.status or ""),
                    )

            remaining = [u for u in clean if u not in result]
            if remaining:
                dev_rows = (
                    await sess.execute(
                        select(UserDevice.device_uuid, WhiteInternetSubscription)
                        .join(
                            WhiteInternetSubscription,
                            WhiteInternetSubscription.user_id == UserDevice.user_id,
                        )
                        .where(func.lower(UserDevice.device_uuid).in_(remaining))
                        .order_by(WhiteInternetSubscription.id.desc())
                    )
                ).all()
                for dev_uuid, sub in dev_rows:
                    k = str(dev_uuid or "").strip().lower()
                    if k and k not in result:
                        result[k] = (
                            sub.id,
                            int(sub.desired_version or 1),
                            int(sub.actual_version or 0),
                            str(sub.status or ""),
                        )
        return result

    async def _disable_stale_uuids_on_server(
        self,
        server_id: int,
        api_url: str,
        api_key: str,
        target_epoch: str,
        stale_uuids: list[str],
        inv_versions_by_uuid: dict[str, int],
        sf,
    ) -> int:
        """Disable stale active WI UUIDs with safe monotonic versioning and epoch/node failure guards."""
        stale_db_info = await self._load_stale_wi_uuid_db_state(sf, stale_uuids)
        version_by_uuid: dict[str, int] = {}
        for u in stale_uuids:
            inv_ver = inv_versions_by_uuid.get(u, 0)
            db_info = stale_db_info.get(u)
            if db_info is not None:
                _sid, db_desired, db_actual, _st = db_info
                # If node already synced db_desired, same version with opposite state triggers already_newer.
                base_ver = db_desired + 1 if db_actual >= db_desired else db_desired
                version_by_uuid[u] = max(base_ver, inv_ver + 1 if inv_ver > 0 else 1)
            else:
                # Never blindly push v=1 for orphans because initial WI clients live at v>=1.
                version_by_uuid[u] = (inv_ver + 1) if inv_ver > 0 else 2

        batch_payload = [
            {
                "uuid": u,
                "is_active": False,
                "desired_state": "disabled",
                "version": version_by_uuid[u],
                "expected_node_epoch": target_epoch,
                "service": "white_internet",
            }
            for u in stale_uuids
        ]
        batch_supported, batch_succeeded, batch_failed = await self.client.batch_sync_clients(
            api_url, api_key, batch_payload
        )
        healed = 0
        disabled_versions: dict[str, int] = {}
        fallback_stale: list[str]
        if batch_supported:
            for u in batch_succeeded:
                disabled_versions[u] = version_by_uuid.get(u, 2)
                healed += 1
            if not batch_succeeded and batch_failed:
                if all(err.startswith(_NODE_WIDE_BATCH_FAILURE_PREFIXES) for err in batch_failed.values()):
                    logger.warning(
                        "Aborting WI stale cleanup on server %d due to node-wide batch failure: %s",
                        server_id,
                        next(iter(batch_failed.values())),
                    )
                    return healed
                if any("fenced" in err.lower() or "epoch" in err.lower() for err in batch_failed.values()):
                    await self._refresh_server_epoch(server_id, api_url, api_key, sf)
                    return healed
            fallback_stale = [u for u in stale_uuids if u in batch_failed]
        else:
            batch_failed = {}
            fallback_stale = list(stale_uuids)

        for stale_uuid in fallback_stale:
            try:
                send_ver = version_by_uuid.get(stale_uuid, 2)
                err_hint = batch_failed.get(stale_uuid, "")
                ver_match = re.search(r"\bversion=(\d+)", err_hint)
                if ver_match:
                    send_ver = max(send_ver, int(ver_match.group(1)) + 1)

                async with self._semaphore:
                    resp = await self.client.sync_client(
                        api_url,
                        api_key,
                        stale_uuid,
                        is_active=False,
                        version=send_ver,
                        expected_node_epoch=target_epoch,
                        service="white_internet",
                    )
                    res = resp.result if hasattr(resp, "result") else resp[0]
                    applied_ver = send_ver
                    if res == SyncResult.FENCED:
                        await self._refresh_server_epoch(server_id, api_url, api_key, sf)
                        break
                    if res == SyncResult.ALREADY_NEWER:
                        raw_data = getattr(resp, "raw_data", None) or {}
                        raw_state = raw_data.get("state")
                        if raw_state == "disabled" and not raw_data.get("tombstone"):
                            res = SyncResult.APPLIED
                        else:
                            raw_v = raw_data.get("version")
                            if not isinstance(raw_v, int):
                                st_inv_ok, st_inv_data, _ = await self.client.get_inventory(
                                    api_url, api_key, client_ids=[stale_uuid], service="white_internet"
                                )
                                st_inv = self._get_inventory_entry(st_inv_ok, st_inv_data, stale_uuid)
                                if st_inv_ok and (st_inv is None or st_inv.get("observed_state") == "disabled"):
                                    res = SyncResult.APPLIED
                                else:
                                    raw_v = (st_inv or {}).get("version")
                            if res != SyncResult.APPLIED:
                                retry_ver = max(send_ver, int(raw_v) if isinstance(raw_v, int) else send_ver) + 1
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
                                if res == SyncResult.FENCED:
                                    await self._refresh_server_epoch(server_id, api_url, api_key, sf)
                                    break
                                applied_ver = retry_ver
                    if res == SyncResult.APPLIED:
                        disabled_versions[stale_uuid] = applied_ver
                        healed += 1
            except Exception as exc:
                logger.warning(
                    "Failed disabling stale WI UUID %s on server %d: %s",
                    stale_uuid[:8],
                    server_id,
                    exc,
                )

        sub_bumps: dict[int, int] = {}
        inactive_statuses = (
            WhiteInternetStatus.DISABLED.value,
            WhiteInternetStatus.EXPIRED.value,
            WhiteInternetStatus.EXHAUSTED.value,
        )
        for u, applied_ver in disabled_versions.items():
            db_info = stale_db_info.get(u)
            if db_info is None:
                continue
            sid_db, db_desired, _db_actual, db_status = db_info
            if db_status in inactive_statuses and applied_ver > db_desired:
                sub_bumps[sid_db] = max(sub_bumps.get(sid_db, 0), applied_ver)

        if sub_bumps:
            try:
                async with sf() as sess:
                    for sid_db, new_ver in sorted(sub_bumps.items()):
                        sub = await white_internet_repo.get_subscription_with_lock(sess, sid_db)
                        if (
                            sub is not None
                            and sub.status in (
                                WhiteInternetStatus.DISABLED,
                                WhiteInternetStatus.EXPIRED,
                                WhiteInternetStatus.EXHAUSTED,
                            )
                            and (sub.desired_version or 1) < new_ver
                        ):
                            sub.desired_version = new_ver
                    await sess.commit()
            except Exception as bump_exc:
                logger.warning("Failed syncing desired_version after stale WI disable: %s", bump_exc)

        return healed

    async def _reconcile_inventory_drift(
        self,
        active_server_targets: list[tuple[int, str, str, str, list]],
        decommissioning_server_ids: set[int],
        active_only_server_ids: set[int],
        server_epochs: dict[int, str],
        sf,
        *,
        reconciled_pairs: set[tuple[int, int]] | None = None,
        target_server_ids: set[int] | None = None,
    ) -> int:
        """Detect missing active WI UUIDs and stale node-side WI UUIDs across active ru_hubs."""
        non_decom_targets = [t for t in active_server_targets if t[0] not in decommissioning_server_ids]
        if not non_decom_targets:
            return 0
        due_targets = (
            [t for t in non_decom_targets if t[0] in target_server_ids]
            if target_server_ids is not None
            else non_decom_targets
        )
        if not due_targets:
            return 0

        _, expected_uuid_to_sub_id = await self._collect_active_wi_inventory_truth(sf)
        expected_active_uuids = set(expected_uuid_to_sub_id.keys())
        seen_pairs = reconciled_pairs or set()
        healed_count = 0
        checked_epochs: dict[int, str] = {}
        all_due_ok = True

        for server_id, api_url, api_key, target_epoch, xhttp_locs in due_targets:
            try:
                inv_ok, inv_data, inv_err = await self.client.get_inventory(
                    api_url, api_key, service="white_internet"
                )
            except Exception as exc:
                logger.debug("WI inventory drift check skipped for server %d: %s", server_id, exc)
                self._drift_fail_backoff[server_id] = (time.monotonic() + FAILED_DRIFT_RETRY_SECONDS, target_epoch)
                all_due_ok = False
                continue
            if not inv_ok or not isinstance(inv_data, dict) or not isinstance(inv_data.get("inventory"), dict):
                if inv_err:
                    logger.debug("WI inventory drift check failed on server %d: %s", server_id, inv_err)
                self._drift_fail_backoff[server_id] = (time.monotonic() + FAILED_DRIFT_RETRY_SECONDS, target_epoch)
                all_due_ok = False
                continue

            now_done = time.monotonic()
            checked_epochs[server_id] = target_epoch
            self._last_drift_check_by_server[server_id] = (now_done, target_epoch)
            self._drift_fail_backoff.pop(server_id, None)

            inventory = inv_data["inventory"]
            expected_inbounds = self._expected_inbound_tags(xhttp_locs)
            node_active_uuids: set[str] = set()
            node_healthy_uuids: set[str] = set()
            inv_versions_by_uuid: dict[str, int] = {}
            for raw_cid, info in inventory.items():
                if not isinstance(info, dict):
                    continue
                cid = str(raw_cid).strip().lower()
                raw_info_ver = info.get("version")
                if isinstance(raw_info_ver, int) and not isinstance(raw_info_ver, bool):
                    inv_versions_by_uuid[cid] = raw_info_ver
                obs = info.get("observed_state")
                if obs in ("active", "partial"):
                    node_active_uuids.add(cid)
                inb_map = info.get("inbounds") or {}
                present_tags = {tag for tag, present in inb_map.items() if present} if isinstance(inb_map, dict) else set()
                inbounds_ok = not expected_inbounds or expected_inbounds.issubset(present_tags)
                if obs == "active" and info.get("all_inbounds_matched", True) is not False and inbounds_ok:
                    node_healthy_uuids.add(cid)

            missing_uuids = expected_active_uuids - node_healthy_uuids
            candidate_missing_sub_ids = {
                expected_uuid_to_sub_id[u]
                for u in missing_uuids
                if (expected_uuid_to_sub_id[u], server_id) not in seen_pairs
            }
            if candidate_missing_sub_ids:
                # Re-verify live DB status/version immediately before repairing to avoid re-activating freshly disabled subs.
                live_tasks_by_sub_id, _ = await self._collect_active_wi_inventory_truth(
                    sf, sub_ids=candidate_missing_sub_ids
                )
                live_missing_sub_ids = sorted(
                    sid_sub for sid_sub in candidate_missing_sub_ids if sid_sub in live_tasks_by_sub_id
                )
                if live_missing_sub_ids:
                    logger.info(
                        "Detected %d WI missing/partial UUIDs (%d live subs) on server %d; resyncing.",
                        len(missing_uuids),
                        len(live_missing_sub_ids),
                        server_id,
                    )
                    repair_tasks = [
                        self._reconcile_sub_on_server(
                            server_id,
                            api_url,
                            api_key,
                            target_epoch,
                            xhttp_locs,
                            live_tasks_by_sub_id[sid_sub],
                            active_only_server_ids,
                            sf,
                            server_epochs=server_epochs,
                            bypass_idempotency=True,
                        )
                        for sid_sub in live_missing_sub_ids
                    ]
                    repair_results = await asyncio.gather(*repair_tasks, return_exceptions=True)
                    for res in repair_results:
                        if isinstance(res, Exception):
                            logger.error("Error repairing missing WI inventory on server %d: %s", server_id, res, exc_info=res)
                        elif res is True:
                            healed_count += 1

            candidate_stale = sorted(node_active_uuids - expected_active_uuids)
            if candidate_stale:
                # Refresh active inventory truth per server right after get_inventory to close cross-hub TOCTOU.
                _, fresh_map = await self._collect_active_wi_inventory_truth(sf)
                confirmed_expected_uuids = set(fresh_map.keys())
                stale_uuids = [u for u in candidate_stale if u not in confirmed_expected_uuids]
                if stale_uuids:
                    logger.info(
                        "Disabling %d stale active WI UUIDs on server %d.",
                        len(stale_uuids),
                        server_id,
                    )
                    healed_count += await self._disable_stale_uuids_on_server(
                        server_id,
                        api_url,
                        api_key,
                        target_epoch,
                        stale_uuids,
                        inv_versions_by_uuid,
                        sf,
                    )

        if checked_epochs:
            non_decom_ids = {t[0] for t in non_decom_targets}
            merged_epochs = {
                sid: ep for sid, ep in self._last_checked_server_epochs.items() if sid in non_decom_ids
            }
            merged_epochs.update(checked_epochs)
            self._last_checked_server_epochs = merged_epochs
            expected_all_epochs = {t[0]: t[3] for t in non_decom_targets}
            if all_due_ok and merged_epochs == expected_all_epochs:
                self._last_inventory_drift_check_mono = time.monotonic()

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
