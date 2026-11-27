"""Business logic orchestration for White Internet subscriptions, quotas, and client integration."""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import secrets
import urllib.parse
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bot import texts
from config.constants import (
    CANONICAL_XHTTP_PROFILE,
    DEFAULT_WHITE_INTERNET_PATH,
    WHITE_INTERNET_BASE_DURATION_DAYS,
    WHITE_INTERNET_BASE_PRICE_RUB,
    WHITE_INTERNET_BASE_TRAFFIC_BYTES,
    WHITE_INTERNET_EXTRA_DEVICE_PRICE_RUB,
    WHITE_INTERNET_MAX_DEVICE_LIMIT,
    WHITE_INTERNET_MAX_EXPIRY_DAYS,
    WHITE_INTERNET_MAX_QUOTA_BYTES,
    WHITE_INTERNET_TLS_FINGERPRINT,
    WHITE_INTERNET_TOPUP_PACKS,
    VLESS_PROTOCOL,
    XRAY_PROTOCOL,
)
from config.enums import (
    WhiteInternetProvisioningStatus,
    WhiteInternetStatus,
)
from database.models import Server, WhiteInternetSubscription
from database.repositories import servers_repo, white_internet_repo
from database.repositories.account_ledger_repo import lock_checkout_user
from services.xray_node_client import SyncResult, XrayNodeClient, _sanitize_url
from utils.admin import is_admin
from utils.datetime_helpers import now_utc

logger = logging.getLogger(__name__)

_BACKGROUND_TASKS: set[asyncio.Task] = set()


_effective_device_limit = white_internet_repo._effective_device_limit


def get_white_internet_tier_price(device_limit: int, base_price: Decimal | None = None) -> Decimal:
    """Calculate White Internet monthly renewal/subscription price based on device slots.

    1 device  = base_price (50 GiB base)
    2 devices = base_price + 200 RUB (100 GiB base)
    3 devices = base_price + 400 RUB (150 GiB base)
    """
    limit = max(1, min(int(device_limit), WHITE_INTERNET_MAX_DEVICE_LIMIT))
    base = base_price if base_price is not None else WHITE_INTERNET_BASE_PRICE_RUB
    return base + Decimal(limit - 1) * WHITE_INTERNET_EXTRA_DEVICE_PRICE_RUB


async def _deprovision_old_node_safe(
    api_url: str, api_key: str, client_uuid: str, version: int
) -> None:
    try:
        async with XrayNodeClient() as client:
            await client.sync_client(
                api_url,
                api_key,
                client_uuid=client_uuid,
                is_active=False,
                version=version,
                service="white_internet",
            )
    except Exception as exc:
        logger.warning("Failed to deprovision old hub node %s: %s", _sanitize_url(api_url), exc)


def _dispatch_deprovision(
    server: Server | None,
    client_uuid: str,
    version: int,
    *,
    session: AsyncSession | None = None,
    context: str = "",
) -> None:
    if not server or not server.api_url or not server.api_key:
        return
    if getattr(server, "protocol", None) not in (XRAY_PROTOCOL, VLESS_PROTOCOL):
        logger.warning(
            "Refusing cross-protocol deprovision dispatch (%s): server %s is not xray",
            context,
            getattr(server, "id", "?"),
        )
        return

    async def _runner() -> None:
        await _deprovision_old_node_safe(
            server.api_url,
            server.api_key,
            client_uuid=client_uuid,
            version=version,
        )

    if session is not None and hasattr(session, "info") and isinstance(session.info, dict):
        from database.connection import queue_post_commit_task

        queue_post_commit_task(session, _runner)
        return

    try:
        task = asyncio.create_task(_runner())
        _BACKGROUND_TASKS.add(task)
        task.add_done_callback(_BACKGROUND_TASKS.discard)
    except Exception as exc:
        logger.warning("Failed to schedule deprovision task (%s): %s", context, exc)


def _normalize_base_path(path: str | None) -> str:
    cleaned = (path or "").strip().rstrip("/")
    if cleaned.endswith("/default"):
        cleaned = cleaned[:-8].rstrip("/")
    if not cleaned:
        cleaned = DEFAULT_WHITE_INTERNET_PATH.rstrip("/")
    if not cleaned.startswith("/"):
        cleaned = "/" + cleaned
    return cleaned


class WhiteInternetService:
    """Service managing the White Internet product lifecycle."""

    @staticmethod
    async def select_hub_node(session: AsyncSession) -> Server:
        hubs = await servers_repo.get_wi_capable_ru_hubs(session)
        if not hubs:
            raise RuntimeError(
                "No healthy server with white_internet capability and available capacity is available."
            )
        return hubs[0]

    @staticmethod
    async def get_hub_nodes(session: AsyncSession) -> list[Server]:
        return await servers_repo.get_wi_capable_ru_hubs(session)

    @staticmethod
    def calculate_wi_add_slot_quote(
        expires_at: datetime | None, now: datetime | None = None
    ) -> tuple[Decimal, int]:
        """Calculate mid-term device slot add-on (price_rub, gb_added)."""
        if not expires_at:
            return WHITE_INTERNET_EXTRA_DEVICE_PRICE_RUB, 50
        now = now or now_utc()
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        exp = expires_at if expires_at.tzinfo is not None else expires_at.replace(tzinfo=timezone.utc)
        days_left = max(0.0, (exp - now).total_seconds() / 86400.0)
        gb_added = max(0, round(50 * days_left / 30.0))
        if days_left > 0 and gb_added == 0:
            gb_added = 1
        return Decimal(50 + gb_added * 4), gb_added

    @staticmethod
    def calculate_wi_add_slot_price(
        expires_at: datetime | None, now: datetime | None = None
    ) -> Decimal:
        """Calculate mid-term device slot add-on price: 50 + round(50 * days_left / 30) * 4."""
        price, _ = WhiteInternetService.calculate_wi_add_slot_quote(expires_at, now=now)
        return price

    @classmethod
    async def purchase_subscription(
        cls,
        session: AsyncSession,
        user_id: int,
        device_limit: int = 1,
        duration_days: int = WHITE_INTERNET_BASE_DURATION_DAYS,
    ):
        user = await lock_checkout_user(session, user_id)
        if user is None:
            return False, texts.WL_USER_NOT_FOUND, None
        existing = await white_internet_repo.get_subscription_by_user_id(session, user_id)
        now = now_utc()
        if existing is not None:
            if (
                getattr(existing, "provisioning_status", None)
                == WhiteInternetProvisioningStatus.PENDING_DELETE
            ):
                return False, texts.WL_DEACTIVATION_PENDING, existing
            elif existing.status == WhiteInternetStatus.DISABLED:
                # Re-activate disabled subscription in-place to preserve legacy /sub/wl/{token}
                pass
            elif existing.status in (
                WhiteInternetStatus.PENDING,
                WhiteInternetStatus.ACTIVE,
                WhiteInternetStatus.EXHAUSTED,
            ) and existing.expires_at and existing.expires_at > now:
                return False, texts.WL_ALREADY_ACTIVE, existing
            else:
                # Existing expired subscription routes to renew_subscription to preserve UUID and token
                if not existing.token:
                    existing.token = secrets.token_hex(32)
                return await cls.renew_subscription(
                    session,
                    user_id,
                    target_device_limit=device_limit,
                    duration_days=duration_days,
                )

        # Pre-Debit Validation: verify mandatory tariff quota BEFORE touching ledger
        if not WHITE_INTERNET_BASE_TRAFFIC_BYTES or WHITE_INTERNET_BASE_TRAFFIC_BYTES <= 0:
            raise ValueError(
                "White Internet tariff missing mandatory base_quota_bytes"
            )

        hub_nodes = await cls.get_hub_nodes(session)
        if not hub_nodes:
            logger.warning("No available hub node for white internet purchase")
            return False, texts.WL_NO_SERVERS_AVAILABLE, None

        clamped_devices = max(1, min(WHITE_INTERNET_MAX_DEVICE_LIMIT, int(device_limit)))
        base_bytes = clamped_devices * WHITE_INTERNET_BASE_TRAFFIC_BYTES

        if existing is not None:
            sub = await white_internet_repo.get_subscription_with_lock(session, existing.id) or existing
            if not sub.token:
                sub.token = secrets.token_hex(32)
            if not sub.uuid:
                sub.uuid = str(uuid.uuid4())
            sub.status = WhiteInternetStatus.PENDING
            sub.status_reason = None
            sub.started_at = now
            sub.expires_at = now + timedelta(days=duration_days)
            sub.base_traffic_bytes = base_bytes
            sub.extra_traffic_bytes = 0
            sub.device_limit = clamped_devices
            sub.pending_device_limit = None
            sub.pending_from = None
            sub.notified_3d = False
            sub.notified_1d = False
            sub.notified_2h = False
            sub.notified_expired = False
            sub.notified_90p = False
            sub.traffic_used_bytes = 0
            sub.traffic_uplink_bytes = 0
            sub.traffic_downlink_bytes = 0
            sub.traffic_overage_bytes = 0
            sub.desired_version = (existing.desired_version or 1) + 1
            sub.provisioning_status = WhiteInternetProvisioningStatus.PENDING_UPDATE
            await white_internet_repo.clear_active_pending_revocations(session, sub)
        else:
            sub = await white_internet_repo.create_white_internet_subscription(
                session,
                user_id=user.id,
                token=secrets.token_hex(32),
                uuid=str(uuid.uuid4()),
                duration_days=duration_days,
                base_bytes=base_bytes,
                device_limit=clamped_devices,
            )
        await session.flush()

        from database.connection import queue_post_commit_task

        sub_id = sub.id
        sub_ver = int(sub.desired_version or 1)
        target_hubs = [(h.id, h.api_url, h.api_key, h.xray_instance_epoch) for h in hub_nodes if h.api_url and h.api_key]

        async def _post_commit_sync() -> None:
            from database.connection import session_scope
            async with session_scope() as bg_sess:
                bg_sub = await white_internet_repo.get_subscription_by_id(bg_sess, sub_id)
                if bg_sub is not None:
                    await cls._try_inline_sync_multi(
                        bg_sess, bg_sub, target_hubs, idempotency_key=f"purchase:{sub_id}:{sub_ver}:True"
                    )

        queue_post_commit_task(session, _post_commit_sync)
        logger.info(
            "White Internet subscription purchased: user_id=%s, sub_id=%s, days=%s, devices=%s",
            user.id,
            sub.id,
            duration_days,
            clamped_devices,
        )
        return True, texts.WL_BUY_SUCCESS, sub

    @classmethod
    async def renew_subscription(
        cls,
        session: AsyncSession,
        user_id: int,
        target_device_limit: int | None = None,
        duration_days: int = WHITE_INTERNET_BASE_DURATION_DAYS,
    ):
        user = await lock_checkout_user(session, user_id)
        if user is None:
            return False, texts.WL_USER_NOT_FOUND, None
        sub = await white_internet_repo.get_subscription_by_user_id(session, user_id)
        if sub is None:
            return False, texts.WL_SUB_NOT_FOUND, None
        if sub.status == WhiteInternetStatus.DISABLED:
            return False, texts.WL_SUB_DISABLED, None
        if sub.status == WhiteInternetStatus.PENDING:
            return False, texts.WL_SUB_NOT_READY, None

        if (
            getattr(sub, "provisioning_status", None)
            == WhiteInternetProvisioningStatus.PENDING_DELETE
        ):
            return False, texts.WL_DEACTIVATION_PENDING, None

        # Pre-Debit Validation: verify mandatory tariff quota BEFORE touching ledger
        if not WHITE_INTERNET_BASE_TRAFFIC_BYTES or WHITE_INTERNET_BASE_TRAFFIC_BYTES <= 0:
            raise ValueError(
                "White Internet tariff missing mandatory base_quota_bytes"
            )

        hub_nodes = await cls.get_hub_nodes(session)
        if not hub_nodes:
            logger.warning("No available hub node for white internet renewal")
            return False, texts.WL_NO_SERVERS_AVAILABLE, None

        now = now_utc()
        base_time = sub.expires_at if sub.expires_at and sub.expires_at > now else now
        if base_time + timedelta(days=duration_days) > now + timedelta(
            days=WHITE_INTERNET_MAX_EXPIRY_DAYS
        ):
            return False, texts.WL_RENEWAL_HORIZON_EXCEEDED, None

        if target_device_limit is not None:
            sub_device_limit = max(1, min(WHITE_INTERNET_MAX_DEVICE_LIMIT, int(target_device_limit)))
        elif sub.pending_device_limit is not None:
            sub_device_limit = sub.pending_device_limit
        else:
            sub_device_limit = max(1, getattr(sub, "device_limit", 1) or 1)
        tier_price = get_white_internet_tier_price(sub_device_limit)
        tier_base_bytes = sub_device_limit * WHITE_INTERNET_BASE_TRAFFIC_BYTES

        renewed = await white_internet_repo.renew_subscription_atomic(
            session,
            subscription_id=sub.id,
            price_rub=tier_price,
            duration_days=duration_days,
            base_bytes=tier_base_bytes,
            target_device_limit=target_device_limit,
        )

        await session.flush()

        logger.info(
            "White Internet subscription renewed: user_id=%s, sub_id=%s, days=%s",
            user.id,
            renewed.id,
            duration_days,
        )
        return True, texts.WL_RENEW_SUCCESS, renewed

    @classmethod
    async def purchase_device_slot(
        cls,
        session: AsyncSession,
        user_id: int,
        actor_telegram_id: int | None = None,
        extra_bytes: int | None = None,
    ) -> tuple[bool, str, WhiteInternetSubscription | None]:
        user = await lock_checkout_user(session, user_id)
        if user is None:
            return False, texts.WL_USER_NOT_FOUND, None

        is_owner = bool(actor_telegram_id is not None and user.telegram_id == actor_telegram_id)
        is_admin_actor = bool(actor_telegram_id is not None and is_admin(actor_telegram_id))
        if not (is_owner or is_admin_actor):
            return False, texts.WL_ADMIN_ONLY_ALERT, None

        sub = await white_internet_repo.get_subscription_by_user_id(session, user_id)
        if sub is None:
            return False, texts.WL_NO_SUB, None
        if sub.user_id != user.id:
            return False, texts.ERROR_ACCESS_DENIED, None
        now = now_utc()
        if sub.status in (WhiteInternetStatus.PENDING, WhiteInternetStatus.DISABLED):
            return False, texts.WL_SUB_NOT_READY, None
        if sub.status == WhiteInternetStatus.EXPIRED or (sub.expires_at and sub.expires_at <= now):
            return False, texts.WL_SUB_EXPIRED, None

        current_limit = _effective_device_limit(sub)
        if current_limit >= WHITE_INTERNET_MAX_DEVICE_LIMIT:
            return False, texts.WL_DEVICE_LIMIT_MAX_REACHED, None

        hub_nodes = await cls.get_hub_nodes(session)
        if not hub_nodes:
            logger.warning("No healthy hub node available for device slot")
            return False, texts.WL_NO_SERVERS_AVAILABLE, None

        price, gb_added = cls.calculate_wi_add_slot_quote(sub.expires_at, now=now)
        added_bytes = extra_bytes if extra_bytes is not None else gb_added * (1024**3)

        updated_sub = await white_internet_repo.add_device_slot_atomic(
            session,
            subscription_id=sub.id,
            extra_bytes=added_bytes,
        )
        await session.flush()

        logger.info(
            "White Internet device slot purchased: user_id=%s, sub_id=%s, new_limit=%s",
            user.id,
            updated_sub.id,
            updated_sub.device_limit,
        )
        return True, texts.WL_ADD_DEVICE_SUCCESS.format(limit=updated_sub.device_limit), updated_sub

    @classmethod
    async def topup_quota(
        cls,
        session: AsyncSession,
        user_id: int,
        pack_gb: int,
        actor_telegram_id: int | None = None,
    ):
        if pack_gb not in WHITE_INTERNET_TOPUP_PACKS:
            return False, texts.WL_INVALID_TOPUP_PACK.format(gb=pack_gb), None
        pack_price = WHITE_INTERNET_TOPUP_PACKS[pack_gb]
        user = await lock_checkout_user(session, user_id)
        if user is None:
            return False, texts.WL_USER_NOT_FOUND, None

        is_owner = bool(actor_telegram_id is not None and user.telegram_id == actor_telegram_id)
        is_admin_actor = bool(actor_telegram_id is not None and is_admin(actor_telegram_id))
        if not (is_owner or is_admin_actor):
            return False, texts.WL_ADMIN_ONLY_ALERT, None

        sub = await white_internet_repo.get_subscription_by_user_id(session, user_id)
        if sub is None:
            return False, texts.WL_NO_SUB, None
        if sub.user_id != user.id:
            return False, texts.ERROR_ACCESS_DENIED, None
        now = now_utc()
        if sub.status in (WhiteInternetStatus.PENDING, WhiteInternetStatus.DISABLED):
            return False, texts.WL_SUB_NOT_READY, None
        if sub.status == WhiteInternetStatus.EXPIRED or (sub.expires_at and sub.expires_at <= now):
            return False, texts.WL_SUB_EXPIRED, None

        hub_nodes = await cls.get_hub_nodes(session)
        if not hub_nodes:
            logger.warning("No healthy hub node available for topup")
            return False, texts.WL_NO_SERVERS_AVAILABLE, None

        pack_bytes = pack_gb * 1024 * 1024 * 1024
        effective_devices = _effective_device_limit(sub)
        max_extra_allowed = effective_devices * WHITE_INTERNET_MAX_QUOTA_BYTES
        if (sub.extra_traffic_bytes or 0) + pack_bytes > max_extra_allowed:
            current_available = await white_internet_repo.get_available_quota_bytes(
                session, sub.id, now
            )
            return (
                False,
                texts.WL_CAP_EXCEEDED.format(gb=pack_gb, available=current_available // (1024**3)),
                None,
            )

        grant = await white_internet_repo.topup_quota_atomic(
            session,
            subscription_id=sub.id,
            pack_gb=pack_gb,
            price_rub=pack_price,
        )

        await session.flush()


        logger.info(
            "White Internet traffic topped up: user_id=%s, sub_id=%s, pack_gb=%s",
            user.id,
            sub.id,
            pack_gb,
        )
        return True, texts.WL_TOPUP_SUCCESS.format(gb=pack_gb), grant

    @classmethod
    async def schedule_wi_downgrade(
        cls,
        session: AsyncSession,
        user_id: int,
        target_device_limit: int,
    ) -> tuple[bool, str, WhiteInternetSubscription | None]:
        """Schedule a deferred device limit downgrade for the end of the current billing period."""
        sub = await white_internet_repo.get_subscription_by_user_id(session, user_id)
        if sub is None:
            return False, texts.WL_NO_SUB, None
        try:
            updated = await white_internet_repo.schedule_downgrade_atomic(
                session,
                subscription_id=sub.id,
                target_device_limit=target_device_limit,
            )
            await session.flush()
            date_str = updated.pending_from.strftime("%d.%m.%Y") if updated.pending_from else ""
            return (
                True,
                f"Лимит устройств будет снижен до {updated.pending_device_limit} с {date_str}.",
                updated,
            )
        except white_internet_repo.WhiteInternetError as exc:
            return False, str(exc), None

    @classmethod
    async def cancel_wi_downgrade(
        cls,
        session: AsyncSession,
        user_id: int,
    ) -> tuple[bool, str, WhiteInternetSubscription | None]:
        """Cancel a scheduled deferred device limit downgrade."""
        sub = await white_internet_repo.get_subscription_by_user_id(session, user_id)
        if sub is None:
            return False, texts.WL_NO_SUB, None
        updated = await white_internet_repo.cancel_downgrade_atomic(
            session,
            subscription_id=sub.id,
        )
        await session.flush()
        return True, "Отложенное снижение лимита отменено.", updated

    @classmethod
    async def apply_due_wi_downgrades(
        cls,
        session: AsyncSession,
        now: datetime | None = None,
    ) -> int:
        """Apply all deferred White Internet downgrades whose pending_from has arrived."""
        return await white_internet_repo.apply_due_downgrades(session, now=now)

    @classmethod
    async def _try_inline_sync(
        cls,
        session: AsyncSession,
        sub: WhiteInternetSubscription,
        hub_node: Server,
        idempotency_key: str,
    ) -> bool:
        """Attempt best-effort synchronous activation on hub node (timeout=4.0s).

        Fail-closed like the reconciliation worker: mark ACTIVE only when the
        node confirms the expected epoch and full inbound coverage; verify
        ALREADY_NEWER against runtime inventory. Anything unconfirmed stays
        PENDING_CREATE for the background worker to converge.
        """
        expected_inbound_tags: set[str] = set()
        extra_data = hub_node.extra_data or {}
        endpoint_entries = extra_data.get("xhttp_locations") or []
        for entry in endpoint_entries:
            if isinstance(entry, dict):
                inbound_tag = entry.get("inbound_tag")
                if inbound_tag:
                    expected_inbound_tags.add(str(inbound_tag))
                    continue
                code = entry.get("location_code") or entry.get("code")
            elif isinstance(entry, str):
                code = entry
            else:
                code = None
            if code:
                expected_inbound_tags.add(f"just1k-wl-inbound-{code}")
        if not expected_inbound_tags:
            expected_inbound_tags.add("just1k-wl-default")
        target_version = sub.desired_version or 1
        try:
            async with XrayNodeClient(timeout=4.0) as xray_client:
                resp = await xray_client.sync_client(
                    hub_node.api_url,
                    hub_node.api_key,
                    client_uuid=sub.uuid,
                    is_active=True,
                    version=target_version,
                    expected_node_epoch=hub_node.xray_instance_epoch,
                    idempotency_key=idempotency_key,
                    service="white_internet",
                )
                sync_res = resp.result if hasattr(resp, "result") else resp[0]
                verified_epoch = (
                    getattr(resp, "verified_epoch", None) or hub_node.xray_instance_epoch
                )
                verified_inbounds = set(getattr(resp, "verified_inbounds", None) or [])
                epoch_ok = verified_epoch == hub_node.xray_instance_epoch
                inbounds_ok = (
                    not verified_inbounds
                    or expected_inbound_tags.issubset(verified_inbounds)
                )
                confirmed = False
                if sync_res == SyncResult.APPLIED and epoch_ok and inbounds_ok:
                    confirmed = True
                elif sync_res == SyncResult.ALREADY_NEWER and epoch_ok:
                    inv_ok, inv_data, _ = await xray_client.get_inventory(
                        hub_node.api_url,
                        hub_node.api_key,
                        client_ids=[sub.uuid],
                        service="white_internet",
                    )
                    observed = None
                    if inv_ok and inv_data and "inventory" in inv_data:
                        observed = (inv_data["inventory"].get(sub.uuid) or {}).get(
                            "observed_state"
                        )
                    confirmed = inv_ok and observed == "active" and inbounds_ok
                if confirmed:
                    from database.repositories import user_devices_repo

                    dev_uuids = await user_devices_repo.get_active_device_uuids(session, sub.user_id)
                    for d_uuid in dev_uuids:
                        if d_uuid and d_uuid != sub.uuid:
                            try:
                                await xray_client.sync_client(
                                    hub_node.api_url,
                                    hub_node.api_key,
                                    client_uuid=d_uuid,
                                    is_active=True,
                                    version=target_version,
                                    expected_node_epoch=hub_node.xray_instance_epoch,
                                    service="white_internet",
                                )
                            except Exception as dev_exc:
                                logger.warning("Inline WI device sync failed for %s: %s", d_uuid, dev_exc)
                    sub.actual_version = target_version
                    if (sub.desired_version or 1) == target_version:
                        sub.status = WhiteInternetStatus.ACTIVE
                        sub.provisioning_status = WhiteInternetProvisioningStatus.ACTIVE
                    sub.last_reconciled_node_epoch = verified_epoch
                    sub.last_synced_at = now_utc()
                    await session.flush()
                    return True
                else:
                    logger.warning(
                        "Inline sync unconfirmed (result=%s epoch_ok=%s inbounds_ok=%s), "
                        "leaving subscription %d PENDING_CREATE for worker",
                        sync_res,
                        epoch_ok,
                        inbounds_ok,
                        sub.id,
                    )
        except Exception as exc:
            logger.warning(
                "Synchronous activation fallback to background worker: %s", exc
            )
        return False

    @classmethod
    async def _try_inline_sync_multi(
        cls,
        session: AsyncSession,
        sub: WhiteInternetSubscription,
        target_hubs: list[tuple[int, str, str, str | None]],
        *,
        idempotency_key: str,
    ) -> bool:
        """Best-effort inline sync across all active WI ru_hub nodes."""
        if not target_hubs:
            return False
        from database.repositories import user_devices_repo
        from sqlalchemy.orm.attributes import flag_modified

        wi_limit = _effective_device_limit(sub)
        devices = await user_devices_repo.list_user_devices(
            session, sub.user_id, wi_device_limit=wi_limit
        )
        assigned_dev_uuids = [
            str(d["device_uuid"])
            for d in devices
            if d.get("is_bi_assigned") and d.get("device_uuid")
        ]
        displaced_dev_uuids = [
            str(d["device_uuid"])
            for d in devices
            if not d.get("is_bi_assigned") and d.get("device_uuid")
        ]
        all_uuids = list(dict.fromkeys([sub.uuid, *assigned_dev_uuids]))
        target_version = sub.desired_version or 1
        node_sync_state = dict(getattr(sub, "node_sync_state", None) or {})
        all_ok = True

        try:
            async with XrayNodeClient(timeout=4.0) as xray_client:
                for srv_id, api_url, api_key, node_epoch in target_hubs:
                    hub_ok = True
                    for c_uuid in all_uuids:
                        try:
                            resp = await xray_client.sync_client(
                                api_url,
                                api_key,
                                client_uuid=c_uuid,
                                is_active=True,
                                version=target_version,
                                expected_node_epoch=node_epoch,
                                idempotency_key=f"{idempotency_key}:{srv_id}:{c_uuid}",
                                service="white_internet",
                            )
                            sync_res = resp.result if hasattr(resp, "result") else resp[0]
                            if sync_res == SyncResult.ALREADY_NEWER:
                                inv_ok, inv_data, _ = await xray_client.get_inventory(
                                    api_url, api_key, client_ids=[c_uuid], service="white_internet"
                                )
                                client_inv = (inv_data or {}).get("inventory", {}).get(c_uuid) if inv_ok else None
                                if not client_inv or client_inv.get("observed_state") != "active":
                                    hub_ok = False
                            elif sync_res != SyncResult.APPLIED:
                                hub_ok = False
                        except Exception as exc:
                            logger.warning("Inline WI sync failed for %s on hub %s: %s", c_uuid, srv_id, exc)
                            hub_ok = False
                    for disp_uuid in displaced_dev_uuids:
                        if disp_uuid and disp_uuid != sub.uuid:
                            try:
                                await xray_client.sync_client(
                                    api_url,
                                    api_key,
                                    client_uuid=disp_uuid,
                                    is_active=False,
                                    version=target_version,
                                    expected_node_epoch=node_epoch,
                                    service="white_internet",
                                )
                            except Exception as exc:
                                logger.debug("Inline WI deprovision failed for displaced %s: %s", disp_uuid, exc)
                    if hub_ok:
                        node_sync_state[str(srv_id)] = {
                            "version": target_version,
                            "epoch": node_epoch or "",
                            "active": True,
                        }
                    else:
                        all_ok = False
        except Exception as exc:
            logger.warning("Inline multi-hub WI sync error: %s", exc)
            all_ok = False

        sub.node_sync_state = node_sync_state
        flag_modified(sub, "node_sync_state")
        if all_ok:
            sub.actual_version = target_version
            if (sub.desired_version or 1) == target_version:
                sub.status = WhiteInternetStatus.ACTIVE
                sub.provisioning_status = WhiteInternetProvisioningStatus.ACTIVE
            sub.last_synced_at = now_utc()
        await session.flush()
        return all_ok

    @classmethod
    async def deactivate_user_subscriptions(
        cls,
        session: AsyncSession,
        user_id: int,
        reason: str = "deactivated",
        *,
        target_subscription_id: int | None = None,
    ) -> list[WhiteInternetSubscription]:
        """Deactivate and deprovision White Internet subscriptions across ALL WI ru_hub nodes."""
        stmt = (
            select(WhiteInternetSubscription)
            .where(
                WhiteInternetSubscription.user_id == user_id,
                WhiteInternetSubscription.status != WhiteInternetStatus.DISABLED,
            )
            .with_for_update()
        )
        if target_subscription_id is not None:
            stmt = stmt.where(WhiteInternetSubscription.id == target_subscription_id)
        res = await session.execute(stmt)
        subs = list(res.scalars().all())

        deactivated: list[WhiteInternetSubscription] = []
        for sub in subs:
            sub.status = WhiteInternetStatus.DISABLED
            sub.status_reason = reason
            sub.desired_version += 1
            sub.provisioning_status = WhiteInternetProvisioningStatus.PENDING_DELETE
            deactivated.append(sub)

        await session.flush()

        from database.models import UserDevice

        dev_uuids = (
            await session.scalars(
                select(UserDevice.device_uuid).where(
                    UserDevice.user_id == user_id,
                    UserDevice.device_uuid.is_not(None),
                )
            )
        ).all()
        hubs = await servers_repo.get_wi_capable_ru_hubs(session)
        for sub in deactivated:
            for hub_server in hubs:
                for target_uuid in {sub.uuid, *[str(u) for u in dev_uuids if u]}:
                    _dispatch_deprovision(
                        hub_server,
                        client_uuid=target_uuid,
                        version=sub.desired_version,
                        context=f"deactivate sub {sub.id}",
                        session=session,
                    )

        logger.info(
            "White Internet subscriptions deactivated: user_id=%s, count=%s, reason=%s",
            user_id,
            len(deactivated),
            reason,
        )
        return deactivated

    @classmethod
    async def extend_subscription(
        cls,
        session: AsyncSession,
        user_id: int,
        days: int,
    ) -> tuple[bool, str, WhiteInternetSubscription | None]:
        """Extend the expiration date of a user's White Internet subscription."""
        sub = await white_internet_repo.get_subscription_by_user_id(session, user_id)
        if sub is None:
            return False, texts.ADMIN_WI_SUB_NOT_FOUND, None

        try:
            sub = await white_internet_repo.extend_subscription_atomic(
                session, sub.id, days
            )
        except white_internet_repo.WhiteInternetError as exc:
            return False, str(exc), None

        hub_nodes = await cls.get_hub_nodes(session)
        if hub_nodes:
            from database.connection import queue_post_commit_task

            sub_id = sub.id
            ver = sub.desired_version
            target_hubs = [(h.id, h.api_url, h.api_key, h.xray_instance_epoch) for h in hub_nodes if h.api_url and h.api_key]

            async def _post_extend_sync() -> None:
                from database.connection import session_scope
                async with session_scope() as bg_sess:
                    bg_sub = await white_internet_repo.get_subscription_by_id(bg_sess, sub_id)
                    if bg_sub is not None:
                        await cls._try_inline_sync_multi(
                            bg_sess,
                            bg_sub,
                            target_hubs,
                            idempotency_key=f"admin_extend:{sub_id}:{ver}:{days}",
                        )

            queue_post_commit_task(session, _post_extend_sync)

        return True, "ok", sub

    @staticmethod
    def _ensure_country_flag(name: str, code: str = "") -> str:
        """Prepend country flag emoji if missing based on ISO code or common country name."""
        clean_name = (name or "").strip()
        if not clean_name:
            return clean_name
        known_flags = (
            "🇷🇺", "🇩🇪", "🇸🇪", "🇪🇪", "🇫🇮", "🇳🇱", "🇵🇱",
            "🇫🇷", "🇬🇧", "🇺🇸", "🇹🇷", "🇰🇿", "🇦🇹", "🇨🇭",
        )
        if any(f in clean_name for f in known_flags):
            return clean_name

        c = (code or "").strip().lower()
        lookup = f"{c} {clean_name.lower()}"
        flag = ""
        country_keyword_flags = getattr(texts, "WL_COUNTRY_KEYWORD_FLAGS", {})
        for kw, fl in country_keyword_flags.items():
            if kw in lookup:
                flag = fl
                break

        if not flag:
            if c in ("de", "germany") or "germany" in lookup:
                flag = "🇩🇪"
            elif c in ("se", "sweden") or "sweden" in lookup:
                flag = "🇸🇪"
            elif c in ("ee", "estonia") or "estonia" in lookup:
                flag = "🇪🇪"
            elif c in ("fi", "finland") or "finland" in lookup:
                flag = "🇫🇮"
            elif c in ("nl", "netherlands") or "netherlands" in lookup:
                flag = "🇳🇱"
            elif c in ("pl", "poland") or "poland" in lookup:
                flag = "🇵🇱"
            elif c in ("fr", "france") or "france" in lookup:
                flag = "🇫🇷"
            elif c in ("gb", "uk", "united_kingdom") or "britain" in lookup:
                flag = "🇬🇧"
            elif c in ("us", "usa") or "usa" in lookup:
                flag = "🇺🇸"
            elif c in ("ru", "russia") or "russia" in lookup:
                flag = "🇷🇺"
            elif c in ("tr", "turkey") or "turkey" in lookup:
                flag = "🇹🇷"
            elif c in ("kz", "kazakhstan") or "kazakhstan" in lookup:
                flag = "🇰🇿"
            elif c in ("at", "austria") or "austria" in lookup:
                flag = "🇦🇹"
            elif c in ("ch", "switzerland") or "switzerland" in lookup:
                flag = "🇨🇭"

        if flag and not clean_name.startswith(flag):
            return f"{flag} {clean_name}"
        return clean_name

    @staticmethod
    def _format_vless_tag(tag_name: str, badge: str | None) -> str:
        quoted_tag = urllib.parse.quote(tag_name)
        if badge and isinstance(badge, str) and badge.strip() and badge.strip().lower() != "none":
            clean_badge = badge.strip()[:30]
            b64_badge = base64.b64encode(clean_badge.encode("utf-8")).decode("ascii")
            return f"{quoted_tag}?serverDescription={b64_badge}"
        return quoted_tag

    @staticmethod
    def generate_xhttp_links(
        subscription: WhiteInternetSubscription,
        cdn_domain: str,
        port: int = 443,
        path: str = DEFAULT_WHITE_INTERNET_PATH,
        relays: list[dict] | None = None,
        include_direct_hub: bool = True,
        direct_hub_tag: str | None = None,
        direct_hub_badge: str | None = None,
        default_relay_badge: str | None = None,
        relay_names: dict[str, str] | None = None,
        relay_badges: dict[str, str] | None = None,
        cdn_type: str = "cdn_yandex",
        direct_hub_path: str | None = None,
        client_uuid: str | None = None,
        *,
        # Backward compatibility aliases
        include_origin: bool | None = None,
        origin_tag: str | None = None,
        origin_badge: str | None = None,
        origin_path: str | None = None,
    ) -> list[str]:
        if include_origin is not None:
            include_direct_hub = include_origin
        effective_tag = direct_hub_tag or origin_tag
        effective_badge = direct_hub_badge or origin_badge
        effective_path = direct_hub_path or origin_path

        if str(cdn_type or "").strip().lower() == "cdn_turboflare":
            extra_dict = {
                "mode": CANONICAL_XHTTP_PROFILE["mode"],
                "uplinkHTTPMethod": "POST",
                "uplinkDataPlacement": "body",
                "uplinkDataKey": "",
                "scMaxEachPostBytes": 65536,
                "scMaxConcurrentPosts": 2,
                "scMinPostsIntervalMs": 10,
                "xPaddingObfsMode": CANONICAL_XHTTP_PROFILE["xPaddingObfsMode"],
                "xPaddingKey": CANONICAL_XHTTP_PROFILE["xPaddingKey"],
                "xPaddingHeader": CANONICAL_XHTTP_PROFILE["xPaddingHeader"],
                "xPaddingMethod": CANONICAL_XHTTP_PROFILE["xPaddingMethod"],
                "xPaddingPlacement": "header",
                "xPaddingBytes": CANONICAL_XHTTP_PROFILE.get("xPaddingBytes", "100-1000"),
                "xmux": CANONICAL_XHTTP_PROFILE["xmux"],
            }
        else:
            extra_dict = {
                "mode": CANONICAL_XHTTP_PROFILE["mode"],
                "uplinkHTTPMethod": CANONICAL_XHTTP_PROFILE["uplinkHTTPMethod"],
                "uplinkDataPlacement": CANONICAL_XHTTP_PROFILE["uplinkDataPlacement"],
                "uplinkDataKey": CANONICAL_XHTTP_PROFILE["uplinkDataKey"],
                "scMaxEachPostBytes": CANONICAL_XHTTP_PROFILE["scMaxEachPostBytes"],
                "scMaxConcurrentPosts": CANONICAL_XHTTP_PROFILE["scMaxConcurrentPosts"],
                "scMinPostsIntervalMs": CANONICAL_XHTTP_PROFILE["scMinPostsIntervalMs"],
                "xPaddingObfsMode": CANONICAL_XHTTP_PROFILE["xPaddingObfsMode"],
                "xPaddingKey": CANONICAL_XHTTP_PROFILE["xPaddingKey"],
                "xPaddingHeader": CANONICAL_XHTTP_PROFILE["xPaddingHeader"],
                "xPaddingMethod": CANONICAL_XHTTP_PROFILE["xPaddingMethod"],
                "xPaddingPlacement": CANONICAL_XHTTP_PROFILE["xPaddingPlacement"],
                "xPaddingBytes": CANONICAL_XHTTP_PROFILE.get("xPaddingBytes", "100-1000"),
                "xmux": CANONICAL_XHTTP_PROFILE["xmux"],
            }
        extra_param = urllib.parse.quote(json.dumps(extra_dict, separators=(",", ":")))
        fp = CANONICAL_XHTTP_PROFILE.get("fp", WHITE_INTERNET_TLS_FINGERPRINT)
        base = _normalize_base_path(path)
        eff_uuid = client_uuid or subscription.uuid

        links: list[str] = []

        if include_direct_hub:
            hub_tag_str = (
                effective_tag.strip()
                if effective_tag and isinstance(effective_tag, str) and effective_tag.strip()
                else texts.WL_VLESS_TAG
            )
            hub_tag_str = WhiteInternetService._ensure_country_flag(hub_tag_str, "ru")
            if effective_badge and isinstance(effective_badge, str) and effective_badge.strip().lower() == "none":
                final_badge = None
            elif effective_badge and isinstance(effective_badge, str) and effective_badge.strip():
                final_badge = effective_badge.strip()
            else:
                final_badge = None
            hub_tag_formatted = WhiteInternetService._format_vless_tag(hub_tag_str, final_badge)
            standalone_path = effective_path or (
                f"{base}/turboflare" if str(cdn_type or "").strip().lower() == "cdn_turboflare" else f"{base}/default"
            )
            hub_link = f"vless://{eff_uuid}@{cdn_domain}:{port}?encryption=none&security=tls&sni={cdn_domain}&alpn=h2&fp={fp}&type=xhttp&path={urllib.parse.quote(standalone_path, safe='')}&mode=packet-up&mux=false&extra={extra_param}#{hub_tag_formatted}"
            links.append(hub_link)

        if relays and isinstance(relays, list):
            for r in relays:
                if not isinstance(r, dict):
                    continue
                relay_code = r.get("code") or r.get("name") or "default"
                r_path = r.get("path") or f"{base}/{relay_code}"
                custom_name = relay_names.get(relay_code) if (relay_names and isinstance(relay_names, dict)) else None
                r_name = (
                    custom_name.strip()
                    if custom_name and isinstance(custom_name, str) and custom_name.strip()
                    else (r.get("name") or texts.WL_VLESS_TAG)
                )
                r_name = WhiteInternetService._ensure_country_flag(r_name, relay_code)

                if relay_badges and relay_code in relay_badges and isinstance(relay_badges[relay_code], str):
                    val = relay_badges[relay_code].strip()
                    r_badge = None if (not val or val.lower() == "none") else val
                elif r.get("badge") and str(r.get("badge")).strip():
                    val = str(r.get("badge")).strip()
                    r_badge = None if val.lower() == "none" else val
                elif r.get("server_description") and str(r.get("server_description")).strip():
                    val = str(r.get("server_description")).strip()
                    r_badge = None if val.lower() == "none" else val
                elif default_relay_badge and isinstance(default_relay_badge, str) and default_relay_badge.strip():
                    val = default_relay_badge.strip()
                    r_badge = None if val.lower() == "none" else val
                else:
                    r_badge = None

                r_tag = WhiteInternetService._format_vless_tag(r_name, r_badge)
                link = f"vless://{eff_uuid}@{cdn_domain}:{port}?encryption=none&security=tls&sni={cdn_domain}&alpn=h2&fp={fp}&type=xhttp&path={urllib.parse.quote(r_path, safe='')}&mode=packet-up&mux=false&extra={extra_param}#{r_tag}"
                links.append(link)

        return links

    # Backward compatibility alias
    generate_vless_links = generate_xhttp_links
