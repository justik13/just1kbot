"""Transactional persistence boundary for White Internet subscriptions."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
import secrets
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified

from config.constants import (
    WHITE_INTERNET_BASE_DURATION_DAYS,
    WHITE_INTERNET_BASE_PRICE_RUB,
    WHITE_INTERNET_BASE_TRAFFIC_BYTES,
    WHITE_INTERNET_EXTRA_DEVICE_TRAFFIC_BYTES,
    WHITE_INTERNET_MAX_DEVICE_LIMIT,
    WHITE_INTERNET_MAX_EXPIRY_DAYS,
    WHITE_INTERNET_MAX_QUOTA_BYTES,
)
from config.enums import (
    WhiteInternetProvisioningStatus,
    WhiteInternetStatus,
)
from database.models import (
    WhiteInternetSubscription,
)
from utils.datetime_helpers import now_utc


class WhiteInternetError(RuntimeError):
    """Base exception for White Internet domain errors."""


class WhiteInternetQuotaCapExceededError(WhiteInternetError):
    """Raised when an extra quota purchase would exceed the maximum accumulation limit."""


class WhiteInternetDeviceLimitExceededError(WhiteInternetError):
    """Raised when device limit would exceed maximum allowed limit (3)."""


class WhiteInternetResetCooldownError(WhiteInternetError):
    """Raised when device reset is requested before cooldown expires."""

    def __init__(self, message: str, remaining_seconds: int = 0):
        super().__init__(message)
        self.remaining_seconds = remaining_seconds


class WhiteInternetRenewalHorizonExceededError(WhiteInternetError):
    """Raised when renewal would push expiration beyond 60 days maximum horizon."""


def _effective_device_limit(sub: WhiteInternetSubscription | None) -> int:
    if sub is None:
        return 1
    val = getattr(sub, "device_limit", 1)
    if isinstance(val, int):
        return max(1, val)
    return 1


class WhiteInternetSubscriptionNotFoundError(WhiteInternetError):
    """Raised when a subscription is not found."""


class WhiteInternetInactiveSubscriptionError(WhiteInternetError):
    """Raised when an operation requires a live subscription."""


async def get_subscription_by_token(
    session: AsyncSession, token: str
) -> WhiteInternetSubscription | None:
    return (
        await session.execute(
            select(WhiteInternetSubscription).where(WhiteInternetSubscription.token == token)
        )
    ).scalar_one_or_none()



async def get_subscription_by_user_id(
    session: AsyncSession, user_id: int
) -> WhiteInternetSubscription | None:
    if not isinstance(user_id, int) or user_id < 1 or user_id > 2_147_483_647:
        return None
    stmt = (
        select(WhiteInternetSubscription)
        .where(WhiteInternetSubscription.user_id == user_id)
        .order_by(WhiteInternetSubscription.id.desc())
        .limit(1)
    )
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


async def get_subscription_by_id(
    session: AsyncSession, subscription_id: int
) -> WhiteInternetSubscription | None:
    if not isinstance(subscription_id, int) or subscription_id < 1 or subscription_id > 2_147_483_647:
        return None
    return await session.scalar(
        select(WhiteInternetSubscription).where(WhiteInternetSubscription.id == subscription_id)
    )


async def get_subscription_with_lock(
    session: AsyncSession, subscription_id: int
) -> WhiteInternetSubscription | None:
    if not isinstance(subscription_id, int) or subscription_id < 1 or subscription_id > 2_147_483_647:
        return None
    stmt = (
        select(WhiteInternetSubscription)
        .where(WhiteInternetSubscription.id == subscription_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def get_available_quota_bytes(
    session: AsyncSession, subscription_id: int, now: datetime | None = None
) -> int:
    sub = await get_subscription_by_id(session, subscription_id)
    if sub is None:
        return 0
    total_quota = (sub.base_traffic_bytes or 0) + (sub.extra_traffic_bytes or 0)
    used_quota = max(0, (sub.traffic_used_bytes or 0) - (sub.traffic_overage_bytes or 0))
    return max(0, total_quota - used_quota)


async def expire_subscription_atomic(
    session: AsyncSession,
    subscription_id: int,
    *,
    reason: str = "subscription_expired",
    now: datetime | None = None,
) -> WhiteInternetSubscription:
    sub = await get_subscription_with_lock(session, subscription_id)
    if sub is None:
        raise WhiteInternetSubscriptionNotFoundError(f"Subscription {subscription_id} not found")
    if sub.status not in (WhiteInternetStatus.EXPIRED, WhiteInternetStatus.DISABLED):
        sub.status = WhiteInternetStatus.EXPIRED
        sub.status_reason = reason
        sub.desired_version += 1
        sub.provisioning_status = WhiteInternetProvisioningStatus.PENDING_DELETE
    await session.flush()
    return sub


async def create_white_internet_subscription(
    session: AsyncSession,
    *,
    user_id: int,
    token: str,
    uuid: str,
    duration_days: int = WHITE_INTERNET_BASE_DURATION_DAYS,
    base_bytes: int | None = None,
    device_limit: int = 1,
) -> WhiteInternetSubscription:
    now = now_utc()
    expires_at = now + timedelta(days=duration_days)
    clamped_devices = max(1, min(WHITE_INTERNET_MAX_DEVICE_LIMIT, int(device_limit)))
    effective_base_bytes = (
        base_bytes
        if base_bytes is not None
        else clamped_devices * WHITE_INTERNET_BASE_TRAFFIC_BYTES
    )
    subscription = WhiteInternetSubscription(
        user_id=user_id,
        token=token,
        uuid=uuid,
        status=WhiteInternetStatus.PENDING,
        status_reason=None,
        started_at=now,
        expires_at=expires_at,
        base_traffic_bytes=effective_base_bytes,
        extra_traffic_bytes=0,
        device_limit=clamped_devices,
        notified_3d=False,
        traffic_used_bytes=0,
        traffic_uplink_bytes=0,
        traffic_downlink_bytes=0,
        traffic_overage_bytes=0,
        node_traffic_snapshots={},
        node_sync_state={},
        pending_revoked_uuids=[],
        provisioning_status=WhiteInternetProvisioningStatus.PENDING_CREATE,
        desired_version=1,
        actual_version=0,
    )
    session.add(subscription)
    await session.flush()
    await session.refresh(subscription)
    return subscription


async def renew_subscription_atomic(
    session: AsyncSession,
    *,
    subscription_id: int,
    price_rub: Decimal = WHITE_INTERNET_BASE_PRICE_RUB,
    duration_days: int = WHITE_INTERNET_BASE_DURATION_DAYS,
    base_bytes: int | None = None,
    target_device_limit: int | None = None,
    max_expiry_days: int = WHITE_INTERNET_MAX_EXPIRY_DAYS,
    now: datetime | None = None,
) -> WhiteInternetSubscription:
    now = now or now_utc()
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    sub = await get_subscription_with_lock(session, subscription_id)
    if sub is None:
        raise WhiteInternetSubscriptionNotFoundError(f"Subscription {subscription_id} not found")
    if sub.status in (WhiteInternetStatus.DISABLED, WhiteInternetStatus.PENDING):
        raise WhiteInternetInactiveSubscriptionError("Subscription is not eligible for renewal")

    sub_expires_at = sub.expires_at
    if sub_expires_at is not None and sub_expires_at.tzinfo is None:
        sub_expires_at = sub_expires_at.replace(tzinfo=timezone.utc)
    base_time = sub_expires_at if (sub_expires_at and sub_expires_at > now) else now
    new_expires_at = base_time + timedelta(days=duration_days)
    if new_expires_at > now + timedelta(days=max_expiry_days):
        raise WhiteInternetRenewalHorizonExceededError(
            f"Renewal exceeds maximum horizon of {max_expiry_days} days"
        )

    if target_device_limit is not None:
        effective_devices = max(1, min(WHITE_INTERNET_MAX_DEVICE_LIMIT, int(target_device_limit)))
        sub.device_limit = effective_devices
        sub.pending_device_limit = None
        sub.pending_from = None
    elif sub.pending_device_limit is not None:
        effective_devices = max(1, min(WHITE_INTERNET_MAX_DEVICE_LIMIT, int(sub.pending_device_limit)))
        sub.device_limit = effective_devices
        sub.pending_device_limit = None
        sub.pending_from = None
    else:
        effective_devices = _effective_device_limit(sub)
    new_base_bytes = (
        base_bytes
        if base_bytes is not None
        else (effective_devices * WHITE_INTERNET_BASE_TRAFFIC_BYTES)
    )

    used_quota = max(0, (sub.traffic_used_bytes or 0) - (sub.traffic_overage_bytes or 0))
    total_left = max(
        0,
        ((sub.base_traffic_bytes or 0) + (sub.extra_traffic_bytes or 0))
        - used_quota,
    )
    extra_rollover = min(sub.extra_traffic_bytes or 0, total_left)
    is_grace_valid = (now <= (sub_expires_at + timedelta(days=7))) if sub_expires_at else True
    max_extra_allowed = max(0, effective_devices * WHITE_INTERNET_MAX_QUOTA_BYTES)
    new_extra = min(extra_rollover, max_extra_allowed) if is_grace_valid else 0

    if not sub.token:
        sub.token = secrets.token_hex(32)
    sub.base_traffic_bytes = new_base_bytes
    sub.extra_traffic_bytes = new_extra
    sub.expires_at = new_expires_at
    sub.status = WhiteInternetStatus.ACTIVE
    sub.status_reason = None
    sub.desired_version = (sub.desired_version or 1) + 1
    sub.provisioning_status = WhiteInternetProvisioningStatus.PENDING_UPDATE
    sub.notified_3d = False
    sub.notified_1d = False
    sub.notified_2h = False
    sub.notified_expired = False
    sub.notified_90p = False

    # Reset period usage counters; preserve per-node delta baselines in node_traffic_snapshots
    sub.traffic_used_bytes = 0
    sub.traffic_uplink_bytes = 0
    sub.traffic_downlink_bytes = 0
    sub.traffic_overage_bytes = 0

    await session.flush()
    await session.refresh(sub)
    return sub


async def add_device_slot_atomic(
    session: AsyncSession,
    *,
    subscription_id: int,
    extra_bytes: int = WHITE_INTERNET_EXTRA_DEVICE_TRAFFIC_BYTES,
    max_devices: int = WHITE_INTERNET_MAX_DEVICE_LIMIT,
    max_quota_bytes: int = WHITE_INTERNET_MAX_QUOTA_BYTES,
) -> WhiteInternetSubscription:
    """Atomically adds a device slot and extra traffic to a subscription under row-level lock."""
    sub = await get_subscription_with_lock(session, subscription_id)
    if sub is None:
        raise WhiteInternetSubscriptionNotFoundError(f"Subscription {subscription_id} not found")
    if sub.status in (WhiteInternetStatus.DISABLED, WhiteInternetStatus.PENDING):
        raise WhiteInternetInactiveSubscriptionError("Subscription is not eligible for device slot upgrade")
    now = now_utc()
    if sub.status == WhiteInternetStatus.EXPIRED or (sub.expires_at and sub.expires_at <= now):
        raise WhiteInternetInactiveSubscriptionError("Cannot upgrade an expired subscription")

    current_limit = _effective_device_limit(sub)
    if current_limit >= max_devices:
        raise WhiteInternetDeviceLimitExceededError(
            f"Cannot exceed maximum limit of {max_devices} devices."
        )

    new_device_limit = current_limit + 1
    new_base_bytes = (sub.base_traffic_bytes or 0) + extra_bytes
    max_base_allowed = new_device_limit * max_quota_bytes
    if new_base_bytes > max_base_allowed:
        raise WhiteInternetQuotaCapExceededError(
            f"Base quota ({new_base_bytes} bytes) exceeds maximum allowed limit ({max_base_allowed} bytes)"
        )
    sub.device_limit = new_device_limit
    sub.base_traffic_bytes = new_base_bytes
    if sub.pending_device_limit is not None and sub.pending_device_limit >= new_device_limit:
        sub.pending_device_limit = None
        sub.pending_from = None

    if sub.status == WhiteInternetStatus.EXHAUSTED:
        sub.status = WhiteInternetStatus.ACTIVE
        sub.status_reason = None
        sub.desired_version = (sub.desired_version or 1) + 1
        sub.provisioning_status = WhiteInternetProvisioningStatus.PENDING_UPDATE
    elif sub.status == WhiteInternetStatus.ACTIVE:
        sub.desired_version = (sub.desired_version or 1) + 1
        sub.provisioning_status = WhiteInternetProvisioningStatus.PENDING_UPDATE

    await session.flush()
    await session.refresh(sub)
    return sub


async def schedule_downgrade_atomic(
    session: AsyncSession,
    *,
    subscription_id: int,
    target_device_limit: int,
) -> WhiteInternetSubscription:
    """Schedule a deferred device-limit downgrade for the end of the current period."""
    sub = await get_subscription_with_lock(session, subscription_id)
    if sub is None:
        raise WhiteInternetSubscriptionNotFoundError(f"Subscription {subscription_id} not found")
    if sub.status in (WhiteInternetStatus.DISABLED, WhiteInternetStatus.PENDING, WhiteInternetStatus.EXPIRED):
        raise WhiteInternetInactiveSubscriptionError("Subscription is not active")
    current_limit = _effective_device_limit(sub)
    target = int(target_device_limit)
    if target < 1 or target >= current_limit:
        raise WhiteInternetError(
            f"Target device limit must be between 1 and {current_limit - 1}"
        )
    sub.pending_device_limit = target
    sub.pending_from = sub.expires_at
    await session.flush()
    await session.refresh(sub)
    return sub


async def cancel_downgrade_atomic(
    session: AsyncSession,
    *,
    subscription_id: int,
) -> WhiteInternetSubscription:
    """Cancel a previously scheduled deferred device-limit downgrade."""
    sub = await get_subscription_with_lock(session, subscription_id)
    if sub is None:
        raise WhiteInternetSubscriptionNotFoundError(f"Subscription {subscription_id} not found")
    sub.pending_device_limit = None
    sub.pending_from = None
    await session.flush()
    await session.refresh(sub)
    return sub


async def apply_due_downgrades(
    session: AsyncSession,
    *,
    now: datetime | None = None,
) -> int:
    """Apply deferred White Internet downgrades whose pending_from timestamp has arrived."""
    now = now or now_utc()
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    stmt = (
        select(WhiteInternetSubscription)
        .where(
            WhiteInternetSubscription.pending_device_limit.is_not(None),
            WhiteInternetSubscription.pending_from.is_not(None),
            WhiteInternetSubscription.pending_from <= now,
        )
        .with_for_update(skip_locked=True)
    )
    rows = (await session.scalars(stmt)).all()
    applied = 0
    for sub in rows:
        target = int(sub.pending_device_limit)
        sub.device_limit = max(1, min(WHITE_INTERNET_MAX_DEVICE_LIMIT, target))
        sub.base_traffic_bytes = sub.device_limit * WHITE_INTERNET_BASE_TRAFFIC_BYTES
        sub.pending_device_limit = None
        sub.pending_from = None
        total_quota = (sub.base_traffic_bytes or 0) + (sub.extra_traffic_bytes or 0)
        used = max(0, (sub.traffic_used_bytes or 0) - (sub.traffic_overage_bytes or 0))
        if sub.status == WhiteInternetStatus.ACTIVE:
            if total_quota > 0 and used >= total_quota:
                sub.status = WhiteInternetStatus.EXHAUSTED
                sub.status_reason = "quota_exhausted"
                sub.provisioning_status = WhiteInternetProvisioningStatus.PENDING_DELETE
            else:
                sub.provisioning_status = WhiteInternetProvisioningStatus.PENDING_UPDATE
            sub.desired_version = (sub.desired_version or 1) + 1
        applied += 1
    if applied:
        await session.flush()
    return applied


async def topup_quota_atomic(
    session: AsyncSession,
    *,
    subscription_id: int,
    pack_gb: int,
    price_rub: Decimal,
) -> int:
    now = now_utc()
    sub = await get_subscription_with_lock(session, subscription_id)
    if sub is None:
        raise WhiteInternetSubscriptionNotFoundError(f"Subscription {subscription_id} not found")
    if sub.status in (WhiteInternetStatus.PENDING, WhiteInternetStatus.DISABLED):
        raise WhiteInternetInactiveSubscriptionError("Subscription is not eligible for top-up")
    if sub.status == WhiteInternetStatus.EXPIRED or (sub.expires_at and sub.expires_at <= now):
        raise WhiteInternetInactiveSubscriptionError("Cannot top up an expired subscription")

    pack_bytes = pack_gb * 1024 * 1024 * 1024
    effective_devices = _effective_device_limit(sub)
    max_extra_allowed = effective_devices * WHITE_INTERNET_MAX_QUOTA_BYTES
    if (sub.extra_traffic_bytes or 0) + pack_bytes > max_extra_allowed:
        max_extra_gb = effective_devices * (WHITE_INTERNET_MAX_QUOTA_BYTES // (1024**3))
        raise WhiteInternetQuotaCapExceededError(
            f"Adding {pack_gb} GiB would exceed the maximum extra traffic cap of {max_extra_gb} GiB."
        )

    sub.extra_traffic_bytes = (sub.extra_traffic_bytes or 0) + pack_bytes
    if sub.status == WhiteInternetStatus.EXHAUSTED:
        sub.status = WhiteInternetStatus.ACTIVE
        sub.status_reason = None
        sub.desired_version += 1
        sub.provisioning_status = WhiteInternetProvisioningStatus.PENDING_UPDATE
        await clear_active_pending_revocations(session, sub)

    total_quota = (sub.base_traffic_bytes or 0) + (sub.extra_traffic_bytes or 0)
    used = max(0, (sub.traffic_used_bytes or 0) - (sub.traffic_overage_bytes or 0))
    if total_quota > 0 and used < 0.90 * total_quota:
        sub.notified_90p = False

    await session.flush()
    return pack_bytes


async def add_pending_revocation_uuids(
    session: AsyncSession,
    subscription_id: int,
    uuids: list[str],
) -> None:
    """Append device UUIDs to pending_revoked_uuids for multi-hub WI deprovisioning."""
    clean_uuids = [str(u).strip().lower() for u in uuids if isinstance(u, str) and u.strip()]
    if not clean_uuids:
        return
    sub = await get_subscription_with_lock(session, subscription_id)
    if sub is None:
        return
    pending = list(getattr(sub, "pending_revoked_uuids", None) or [])
    changed = False
    for u in clean_uuids:
        if u not in pending:
            pending.append(u)
            changed = True
    if changed:
        sub.pending_revoked_uuids = pending
        flag_modified(sub, "pending_revoked_uuids")
        await session.flush()


async def pop_pending_revoked_uuid(
    session: AsyncSession,
    sub_id: int,
    target_uuid: str,
) -> None:
    """Remove a successfully deprovisioned UUID from pending_revoked_uuids under row lock."""
    clean_uuid = str(target_uuid or "").strip().lower()
    if not clean_uuid:
        return
    sub = await get_subscription_with_lock(session, sub_id)
    if sub is None:
        return
    pending = list(getattr(sub, "pending_revoked_uuids", None) or [])
    if not pending:
        return
    new_pending = [
        u for u in pending if str(u).strip().lower() != clean_uuid and u != target_uuid
    ]
    if len(new_pending) != len(pending):
        sub.pending_revoked_uuids = new_pending
        flag_modified(sub, "pending_revoked_uuids")
        await session.flush()


async def clear_active_pending_revocations(
    session: AsyncSession,
    sub: WhiteInternetSubscription,
) -> None:
    """Remove sub.uuid and assigned WI device UUIDs from pending_revoked_uuids on reactivation."""
    pending = list(getattr(sub, "pending_revoked_uuids", None) or [])
    if not pending:
        return
    live_uuids: set[str] = set()
    if getattr(sub, "uuid", None):
        live_uuids.add(str(sub.uuid).strip().lower())
    if getattr(sub, "user_id", None) is not None:
        from database.models import UserDevice

        dev_uuids = (
            await session.scalars(
                select(UserDevice.device_uuid).where(
                    UserDevice.user_id == sub.user_id,
                    UserDevice.is_wi_assigned.is_(True),
                )
            )
        ).all()
        for du in dev_uuids:
            if du:
                live_uuids.add(str(du).strip().lower())
    if not live_uuids:
        return
    new_pending = [u for u in pending if str(u).strip().lower() not in live_uuids]
    if len(new_pending) != len(pending):
        sub.pending_revoked_uuids = new_pending
        flag_modified(sub, "pending_revoked_uuids")
        await session.flush()


async def collect_displaced_wi_device_uuids(
    session: AsyncSession,
    user_id: int,
    new_device_limit: int,
) -> list[str]:
    """Unassign excess WI device slots beyond new_device_limit and return displaced UUIDs."""
    from database.models import UserDevice

    keep_limit = max(0, int(new_device_limit))
    assigned = list(
        (
            await session.scalars(
                select(UserDevice)
                .where(
                    UserDevice.user_id == user_id,
                    UserDevice.is_wi_assigned.is_(True),
                )
                .order_by(UserDevice.created_at.asc(), UserDevice.id.asc())
            )
        ).all()
    )
    if len(assigned) <= keep_limit:
        return []
    displaced_devs = assigned[keep_limit:]
    displaced_uuids: list[str] = []
    for dev in displaced_devs:
        dev.is_wi_assigned = False
        if dev.device_uuid:
            displaced_uuids.append(str(dev.device_uuid).strip().lower())
    await session.flush()
    return displaced_uuids


async def record_wi_traffic_atomic(
    session: AsyncSession,
    subscription_id: int,
    server_id: int,
    client_uuid: str,
    raw_uplink: int,
    raw_downlink: int,
    current_epoch: str | None,
) -> tuple[int, bool]:
    """Record per-(server, device_uuid) WI traffic delta atomically.

    Returns (total_delta_bytes, newly_exhausted).
    """
    sub = await get_subscription_with_lock(session, subscription_id)
    if sub is None:
        return 0, False

    snapshots = dict(getattr(sub, "node_traffic_snapshots", None) or {})
    snap_key = f"{server_id}:{client_uuid}"
    prev = snapshots.get(snap_key)
    str_epoch = str(current_epoch or "")
    current_up = max(0, int(raw_uplink or 0))
    current_down = max(0, int(raw_downlink or 0))

    if not isinstance(prev, dict):
        # First observation of this device/server pair: count current counters unless this is a
        # legacy subscription with existing usage and no snapshots recorded yet.
        is_legacy_untracked = (not snapshots) and ((sub.traffic_used_bytes or 0) > 0)
        delta_up = 0 if is_legacy_untracked else current_up
        delta_down = 0 if is_legacy_untracked else current_down
    elif str(prev.get("epoch") or "") != str_epoch:
        delta_up = current_up
        delta_down = current_down
    else:
        prev_up = int(prev.get("up", 0) or 0)
        prev_down = int(prev.get("down", 0) or 0)
        delta_up = (current_up - prev_up) if current_up >= prev_up else current_up
        delta_down = (current_down - prev_down) if current_down >= prev_down else current_down

    delta_up = max(0, delta_up)
    delta_down = max(0, delta_down)
    # Quota is metered strictly by downlink (Egress from CDN to client)
    consumed_delta = delta_down
    any_delta = delta_up + delta_down

    if not isinstance(prev, dict) or any_delta > 0 or str(prev.get("epoch") or "") != str_epoch:
        snapshots[snap_key] = {
            "epoch": str_epoch,
            "up": current_up,
            "down": current_down,
            "updated_at": int(now_utc().timestamp()),
        }
        sub.node_traffic_snapshots = snapshots
        flag_modified(sub, "node_traffic_snapshots")

    newly_exhausted = False
    if any_delta > 0:
        total_quota = (sub.base_traffic_bytes or 0) + (sub.extra_traffic_bytes or 0)
        used_before = max(0, (sub.traffic_used_bytes or 0) - (sub.traffic_overage_bytes or 0))
        available_before = max(0, total_quota - used_before)

        if sub.status in (
            WhiteInternetStatus.DISABLED,
            WhiteInternetStatus.EXPIRED,
            WhiteInternetStatus.EXHAUSTED,
        ):
            overage = consumed_delta
        else:
            overage = max(0, consumed_delta - available_before)

        sub.traffic_uplink_bytes = (sub.traffic_uplink_bytes or 0) + delta_up
        sub.traffic_downlink_bytes = (sub.traffic_downlink_bytes or 0) + delta_down
        sub.traffic_used_bytes = (sub.traffic_used_bytes or 0) + consumed_delta
        sub.traffic_overage_bytes = (sub.traffic_overage_bytes or 0) + overage

        used_quota = max(0, (sub.traffic_used_bytes or 0) - (sub.traffic_overage_bytes or 0))
        if sub.status == WhiteInternetStatus.ACTIVE and total_quota > 0 and used_quota >= total_quota:
            sub.status = WhiteInternetStatus.EXHAUSTED
            sub.status_reason = "quota_exhausted"
            sub.desired_version += 1
            sub.provisioning_status = WhiteInternetProvisioningStatus.PENDING_DELETE
            newly_exhausted = True

    await session.flush()
    return consumed_delta, newly_exhausted


async def reset_subscription_token_atomic(
    session: AsyncSession,
    subscription_id: int,
    *,
    new_token: str | None = None,
    reset_hwids: bool = True,
) -> str:
    """Atomically regenerates the subscription token (and optionally rotates UUID) under row-level lock.

    Returns the new token string.
    """
    sub = await get_subscription_with_lock(session, subscription_id)
    if sub is None:
        raise WhiteInternetSubscriptionNotFoundError(f"Subscription {subscription_id} not found")

    token = new_token or secrets.token_hex(32)
    sub.token = token
    if reset_hwids:
        old_uuid = sub.uuid
        if old_uuid:
            pending = list(getattr(sub, "pending_revoked_uuids", None) or [])
            clean_old = str(old_uuid).strip().lower()
            if clean_old and clean_old not in pending:
                pending.append(clean_old)
                sub.pending_revoked_uuids = pending
                flag_modified(sub, "pending_revoked_uuids")
        sub.uuid = str(uuid4())
        sub.desired_version = (sub.desired_version or 1) + 1
        if sub.status == WhiteInternetStatus.ACTIVE:
            sub.provisioning_status = WhiteInternetProvisioningStatus.PENDING_UPDATE
    await session.flush()
    return token


async def reset_traffic_used_atomic(
    session: AsyncSession,
    subscription_id: int,
) -> WhiteInternetSubscription:
    """Zeroes traffic usage counters for ACTIVE or EXHAUSTED subscription under row lock.

    State Guard: rejects DISABLED, PENDING_DELETE, or EXPIRED subscriptions.
    Preserves node_traffic_snapshots to prevent worker delta desynchronization.
    """
    sub = await get_subscription_with_lock(session, subscription_id)
    if sub is None:
        raise WhiteInternetSubscriptionNotFoundError(f"Subscription {subscription_id} not found")
    if sub.status not in (WhiteInternetStatus.ACTIVE, WhiteInternetStatus.EXHAUSTED):
        raise WhiteInternetInactiveSubscriptionError(
            f"Cannot reset traffic for subscription in {sub.status} state"
        )

    sub.traffic_used_bytes = 0
    sub.traffic_overage_bytes = 0
    sub.traffic_uplink_bytes = 0
    sub.traffic_downlink_bytes = 0
    sub.notified_90p = False
    # Preserves node_traffic_snapshots so per-node Xray counters remain monotonic

    if sub.status == WhiteInternetStatus.EXHAUSTED:
        sub.status = WhiteInternetStatus.ACTIVE
        sub.status_reason = None
        sub.desired_version += 1
        sub.provisioning_status = WhiteInternetProvisioningStatus.PENDING_UPDATE
        await clear_active_pending_revocations(session, sub)

    await session.flush()
    return sub


async def add_extra_traffic_atomic(
    session: AsyncSession,
    subscription_id: int,
    extra_bytes: int,
) -> WhiteInternetSubscription:
    """Adds bonus/extra traffic bytes to subscription quota under row lock."""
    if extra_bytes <= 0:
        raise WhiteInternetError("Extra traffic bytes must be positive")
    sub = await get_subscription_with_lock(session, subscription_id)
    if sub is None:
        raise WhiteInternetSubscriptionNotFoundError(f"Subscription {subscription_id} not found")
    if sub.status not in (
        WhiteInternetStatus.ACTIVE,
        WhiteInternetStatus.EXHAUSTED,
        WhiteInternetStatus.PENDING,
    ):
        raise WhiteInternetInactiveSubscriptionError(
            f"Cannot add traffic to subscription in {sub.status} state"
        )

    new_extra = (sub.extra_traffic_bytes or 0) + extra_bytes
    effective_devices = _effective_device_limit(sub)
    max_extra_allowed = effective_devices * WHITE_INTERNET_MAX_QUOTA_BYTES
    if new_extra > max_extra_allowed:
        raise WhiteInternetQuotaCapExceededError(
            f"Extra traffic ({new_extra} bytes) exceeds maximum allowed limit ({max_extra_allowed} bytes)"
        )
    sub.extra_traffic_bytes = new_extra
    total_quota = (sub.base_traffic_bytes or 0) + sub.extra_traffic_bytes
    used = max(0, (sub.traffic_used_bytes or 0) - (sub.traffic_overage_bytes or 0))

    if sub.status == WhiteInternetStatus.EXHAUSTED and total_quota > used:
        sub.status = WhiteInternetStatus.ACTIVE
        sub.status_reason = None
        sub.desired_version += 1
        sub.provisioning_status = WhiteInternetProvisioningStatus.PENDING_UPDATE
        await clear_active_pending_revocations(session, sub)
    elif sub.status == WhiteInternetStatus.ACTIVE and total_quota > 0 and used >= total_quota:
        sub.status = WhiteInternetStatus.EXHAUSTED
        sub.status_reason = "quota_exhausted"
        sub.desired_version += 1
        sub.provisioning_status = WhiteInternetProvisioningStatus.PENDING_DELETE

    if total_quota > 0 and used < 0.90 * total_quota:
        sub.notified_90p = False

    await session.flush()
    return sub


async def set_base_traffic_quota_atomic(
    session: AsyncSession,
    subscription_id: int,
    base_bytes: int,
) -> WhiteInternetSubscription:
    """Updates base traffic quota under row lock."""
    if base_bytes <= 0:
        raise WhiteInternetError("Base traffic quota must be positive")
    sub = await get_subscription_with_lock(session, subscription_id)
    if sub is None:
        raise WhiteInternetSubscriptionNotFoundError(f"Subscription {subscription_id} not found")
    if sub.status not in (
        WhiteInternetStatus.ACTIVE,
        WhiteInternetStatus.EXHAUSTED,
        WhiteInternetStatus.PENDING,
    ):
        raise WhiteInternetInactiveSubscriptionError(
            f"Cannot change quota for subscription in {sub.status} state"
        )

    effective_devices = _effective_device_limit(sub)
    max_base_allowed = effective_devices * WHITE_INTERNET_MAX_QUOTA_BYTES
    if base_bytes > max_base_allowed:
        raise WhiteInternetQuotaCapExceededError(
            f"Base quota ({base_bytes} bytes) exceeds maximum allowed limit ({max_base_allowed} bytes)"
        )

    total_quota = base_bytes + (sub.extra_traffic_bytes or 0)

    sub.base_traffic_bytes = base_bytes
    used = max(0, (sub.traffic_used_bytes or 0) - (sub.traffic_overage_bytes or 0))

    if sub.status == WhiteInternetStatus.EXHAUSTED and total_quota > used:
        sub.status = WhiteInternetStatus.ACTIVE
        sub.status_reason = None
        sub.desired_version += 1
        sub.provisioning_status = WhiteInternetProvisioningStatus.PENDING_UPDATE
        await clear_active_pending_revocations(session, sub)
    elif sub.status == WhiteInternetStatus.ACTIVE and total_quota > 0 and used >= total_quota:
        sub.status = WhiteInternetStatus.EXHAUSTED
        sub.status_reason = "quota_exhausted"
        sub.desired_version += 1
        sub.provisioning_status = WhiteInternetProvisioningStatus.PENDING_DELETE

    if total_quota > 0 and used < 0.90 * total_quota:
        sub.notified_90p = False

    await session.flush()
    return sub


async def extend_subscription_atomic(
    session: AsyncSession,
    subscription_id: int,
    days: int,
    *,
    now: datetime | None = None,
) -> WhiteInternetSubscription:
    """Atomically extend the expiration date of a White Internet subscription under row lock.

    Contract:
    - Active subscriptions: Adds days to the active end date without resetting mid-cycle
      traffic consumption.
    - Expired/Exhausted/Pending subscriptions: Reactivates the subscription to ACTIVE, resets
      cycle usage counters (traffic_used_bytes=0, etc.) for the new period, and schedules
      node provisioning (PENDING_UPDATE).
    - Disabled subscriptions: Explicitly rejected with WhiteInternetInactiveSubscriptionError.
    """
    if not isinstance(subscription_id, int) or subscription_id < 1 or subscription_id > 2_147_483_647:
        raise WhiteInternetSubscriptionNotFoundError(f"Invalid subscription id {subscription_id}")
    if not isinstance(days, int) or days < 1:
        raise ValueError(f"Invalid days to extend: {days}. Must be an integer >= 1.")

    sub = await get_subscription_with_lock(session, subscription_id)
    if sub is None:
        raise WhiteInternetSubscriptionNotFoundError(f"Subscription {subscription_id} not found")

    if sub.status == WhiteInternetStatus.DISABLED:
        raise WhiteInternetInactiveSubscriptionError("Subscription is disabled")

    current_time = now or now_utc()
    if current_time.tzinfo is None:
        current_time = current_time.replace(tzinfo=timezone.utc)

    old_expires_at = sub.expires_at
    if old_expires_at is not None and old_expires_at.tzinfo is None:
        old_expires_at = old_expires_at.replace(tzinfo=timezone.utc)

    is_expired_by_time = old_expires_at is not None and old_expires_at <= current_time
    is_expired_cycle = is_expired_by_time or sub.status == WhiteInternetStatus.EXPIRED
    is_new_cycle = is_expired_cycle or sub.status in (
        WhiteInternetStatus.EXHAUSTED,
        WhiteInternetStatus.PENDING,
    )

    from utils.datetime_helpers import calculate_extension_end

    sub.expires_at = calculate_extension_end(sub.expires_at, days, now=current_time)
    sub.desired_version = (sub.desired_version or 1) + 1
    sub.provisioning_status = WhiteInternetProvisioningStatus.PENDING_UPDATE
    if is_new_cycle:
        await clear_active_pending_revocations(session, sub)
        if is_expired_cycle or sub.status == WhiteInternetStatus.EXHAUSTED:
            effective_devices = _effective_device_limit(sub)
            used_quota = max(0, (sub.traffic_used_bytes or 0) - (sub.traffic_overage_bytes or 0))
            total_left = max(
                0,
                ((sub.base_traffic_bytes or 0) + (sub.extra_traffic_bytes or 0)) - used_quota,
            )
            extra_rollover = min(sub.extra_traffic_bytes or 0, total_left)
            is_grace_valid = (
                (current_time <= (old_expires_at + timedelta(days=7)))
                if old_expires_at
                else True
            )
            max_extra_allowed = max(0, effective_devices * WHITE_INTERNET_MAX_QUOTA_BYTES)
            sub.extra_traffic_bytes = min(extra_rollover, max_extra_allowed) if is_grace_valid else 0
        sub.traffic_used_bytes = 0
        sub.traffic_overage_bytes = 0
        sub.traffic_uplink_bytes = 0
        sub.traffic_downlink_bytes = 0
        sub.notified_90p = False
        sub.status = WhiteInternetStatus.ACTIVE
        sub.status_reason = None

    sub.notified_3d = False
    sub.notified_1d = False
    sub.notified_2h = False
    sub.notified_expired = False

    await session.flush()
    return sub

