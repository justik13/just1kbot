"""Transactional persistence boundary for White Internet subscriptions."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from config.constants import (
    WHITE_INTERNET_BASE_DURATION_DAYS,
    WHITE_INTERNET_BASE_PRICE_RUB,
    WHITE_INTERNET_BASE_TRAFFIC_BYTES,
    WHITE_INTERNET_DEVICE_RESET_COOLDOWN_SECONDS,
    WHITE_INTERNET_EXTRA_DEVICE_TRAFFIC_BYTES,
    WHITE_INTERNET_HWID_TTL_HOURS,
    WHITE_INTERNET_MAX_DEVICE_LIMIT,
    WHITE_INTERNET_MAX_EXPIRY_DAYS,
    WHITE_INTERNET_MAX_QUOTA_BYTES,
    WHITE_INTERNET_SERVICE_TYPE,
)
from config.enums import (
    TariffQuoteOperation,
    TariffQuoteStatus,
    WhiteInternetProvisioningStatus,
    WhiteInternetStatus,
)
from database.models import (
    TariffQuote,
    User,
    WhiteInternetOrphanCleanup,
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
    return (await session.execute(stmt)).scalar_one_or_none()


async def has_user_any_subscription(
    session: AsyncSession, user_id: int
) -> bool:
    """Check if user has ever had any White Internet subscription (trial or regular)."""
    if not isinstance(user_id, int) or user_id < 1 or user_id > 2_147_483_647:
        return False
    stmt = (
        select(WhiteInternetSubscription.id)
        .where(WhiteInternetSubscription.user_id == user_id)
        .limit(1)
    )
    return await session.scalar(stmt) is not None


async def has_ever_activated_trial(session: AsyncSession, user_id: int) -> bool:
    """Check if user has ever consumed a White Internet trial quote (with last_trial_reset_at guard)."""
    if not isinstance(user_id, int) or user_id < 1 or user_id > 2_147_483_647:
        return False
    user = await session.get(User, user_id)
    stmt = (
        select(TariffQuote.id)
        .where(
            TariffQuote.user_id == user_id,
            TariffQuote.service_type == WHITE_INTERNET_SERVICE_TYPE,
            TariffQuote.operation_type == TariffQuoteOperation.TRIAL,
            TariffQuote.status == TariffQuoteStatus.CONSUMED,
        )
    )
    if user and user.last_trial_reset_at is not None:
        stmt = stmt.where(TariffQuote.created_at > user.last_trial_reset_at)
    return (await session.scalar(stmt.limit(1))) is not None


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
    origin_node_id: int,
    token: str,
    uuid: str,
    quote_id: int,
    price_rub: Decimal = WHITE_INTERNET_BASE_PRICE_RUB,
    duration_days: int = WHITE_INTERNET_BASE_DURATION_DAYS,
    base_bytes: int = WHITE_INTERNET_BASE_TRAFFIC_BYTES,
    is_trial: bool = False,
) -> WhiteInternetSubscription:
    now = now_utc()
    expires_at = now + timedelta(days=duration_days)
    subscription = WhiteInternetSubscription(
        user_id=user_id,
        origin_node_id=origin_node_id,
        token=token,
        uuid=uuid,
        status=WhiteInternetStatus.PENDING,
        status_reason=None,
        started_at=now,
        expires_at=expires_at,
        base_traffic_bytes=base_bytes,
        extra_traffic_bytes=0,
        is_trial=is_trial,
        traffic_used_bytes=0,
        traffic_uplink_bytes=0,
        traffic_downlink_bytes=0,
        traffic_overage_bytes=0,
        last_uplink_snapshot=0,
        last_downlink_snapshot=0,
        traffic_stats_epoch=None,
        provisioning_status=WhiteInternetProvisioningStatus.PENDING_CREATE,
        desired_version=1,
        actual_version=0,
        last_reconciled_node_epoch=None,
    )
    session.add(subscription)
    await session.flush()
    await cancel_pending_orphan_cleanups_for_client(session, origin_node_id, uuid)
    await session.refresh(subscription)
    return subscription


async def renew_subscription_atomic(
    session: AsyncSession,
    *,
    subscription_id: int,
    quote_id: int,
    price_rub: Decimal = WHITE_INTERNET_BASE_PRICE_RUB,
    duration_days: int = WHITE_INTERNET_BASE_DURATION_DAYS,
    base_bytes: int | None = None,
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

    effective_devices = max(1, getattr(sub, "device_limit", 1) or 1)
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

    if getattr(sub, "is_trial", False):
        new_extra = 0
    else:
        is_grace_valid = (now <= (sub_expires_at + timedelta(days=7))) if sub_expires_at else True
        max_extra_allowed = max(0, WHITE_INTERNET_MAX_QUOTA_BYTES - new_base_bytes)
        new_extra = min(extra_rollover, max_extra_allowed) if is_grace_valid else 0

    sub.is_trial = False
    sub.base_traffic_bytes = new_base_bytes
    sub.extra_traffic_bytes = new_extra
    sub.expires_at = new_expires_at
    sub.status = WhiteInternetStatus.ACTIVE
    sub.status_reason = None
    sub.desired_version += 1
    sub.provisioning_status = WhiteInternetProvisioningStatus.PENDING_UPDATE

    # Reset period usage counters; DO NOT reset last_uplink_snapshot / last_downlink_snapshot / last_device_reset_at!
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

    current_limit = max(1, getattr(sub, "device_limit", 1) or 1)
    if current_limit >= max_devices:
        raise WhiteInternetDeviceLimitExceededError(
            f"Cannot exceed maximum limit of {max_devices} devices."
        )

    total_accumulated = (sub.base_traffic_bytes or 0) + (sub.extra_traffic_bytes or 0) + extra_bytes
    if total_accumulated > max_quota_bytes:
        raise WhiteInternetQuotaCapExceededError(
            f"Adding device slot would exceed maximum quota cap of {max_quota_bytes} bytes."
        )

    sub.device_limit = current_limit + 1
    sub.extra_traffic_bytes = (sub.extra_traffic_bytes or 0) + extra_bytes

    if sub.status == WhiteInternetStatus.EXHAUSTED:
        sub.status = WhiteInternetStatus.ACTIVE
        sub.status_reason = None
        sub.desired_version += 1
        sub.provisioning_status = WhiteInternetProvisioningStatus.PENDING_UPDATE

    await session.flush()
    await session.refresh(sub)
    return sub


async def topup_quota_atomic(
    session: AsyncSession,
    *,
    subscription_id: int,
    quote_id: int,
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
    total_accumulated = (sub.base_traffic_bytes or 0) + (sub.extra_traffic_bytes or 0) + pack_bytes
    if total_accumulated > WHITE_INTERNET_MAX_QUOTA_BYTES:
        raise WhiteInternetQuotaCapExceededError(
            f"Adding {pack_gb} GiB would exceed the 150 GiB maximum accumulation cap."
        )

    sub.extra_traffic_bytes = (sub.extra_traffic_bytes or 0) + pack_bytes
    if sub.status == WhiteInternetStatus.EXHAUSTED:
        sub.status = WhiteInternetStatus.ACTIVE
        sub.status_reason = None
        sub.desired_version += 1
        sub.provisioning_status = WhiteInternetProvisioningStatus.PENDING_UPDATE

    await session.flush()
    return pack_bytes


async def deduct_traffic_atomic(
    session: AsyncSession,
    *,
    subscription_id: int,
    delta_bytes: int,
    delta_uplink: int = 0,
    delta_downlink: int = 0,
    now: datetime | None = None,
) -> tuple[int, bool, int]:
    if delta_bytes <= 0:
        return 0, False, 0
    now = now or now_utc()
    sub = await get_subscription_with_lock(session, subscription_id)
    if sub is None:
        raise WhiteInternetSubscriptionNotFoundError(f"Subscription {subscription_id} not found")

    total_quota = (sub.base_traffic_bytes or 0) + (sub.extra_traffic_bytes or 0)
    used_before = max(0, (sub.traffic_used_bytes or 0) - (sub.traffic_overage_bytes or 0))
    available_before = max(0, total_quota - used_before)

    if sub.status in (WhiteInternetStatus.DISABLED, WhiteInternetStatus.EXPIRED):
        overage = delta_bytes
        consumed = 0
    else:
        consumed = min(delta_bytes, available_before)
        overage = max(0, delta_bytes - available_before)

    sub.traffic_used_bytes = (sub.traffic_used_bytes or 0) + delta_bytes
    sub.traffic_overage_bytes = (sub.traffic_overage_bytes or 0) + overage
    sub.traffic_uplink_bytes = (sub.traffic_uplink_bytes or 0) + max(0, delta_uplink)
    sub.traffic_downlink_bytes = (sub.traffic_downlink_bytes or 0) + max(0, delta_downlink)

    used_after = max(0, (sub.traffic_used_bytes or 0) - (sub.traffic_overage_bytes or 0))
    available_after = max(0, total_quota - used_after)
    became_exhausted = False
    if available_after == 0 and sub.status == WhiteInternetStatus.ACTIVE:
        sub.status = WhiteInternetStatus.EXHAUSTED
        sub.status_reason = "quota_exhausted"
        sub.desired_version += 1
        sub.provisioning_status = WhiteInternetProvisioningStatus.PENDING_UPDATE
        became_exhausted = True

    await session.flush()
    return consumed, became_exhausted, overage


async def record_and_deduct_traffic_atomic(
    session: AsyncSession,
    subscription_id: int,
    node_epoch: int | str,
    snapshot_uplink_after: int,
    snapshot_downlink_after: int,
    snapshot_uplink_before: int = 0,
    snapshot_downlink_before: int = 0,
    *,
    node_boot_id: str | None = None,
    node_starttime: int | None = None,
    now: datetime | None = None,
) -> tuple[int, bool, int, None]:
    now = now or now_utc()
    str_node_epoch = str(node_epoch)

    sub = await get_subscription_with_lock(session, subscription_id)
    if sub is None:
        raise WhiteInternetSubscriptionNotFoundError(f"Subscription {subscription_id} not found")

    effective_before_up = (
        snapshot_uplink_before if snapshot_uplink_before > 0 else (sub.last_uplink_snapshot or 0)
    )
    effective_before_down = (
        snapshot_downlink_before
        if snapshot_downlink_before > 0
        else (sub.last_downlink_snapshot or 0)
    )

    delta_uplink = snapshot_uplink_after - effective_before_up
    delta_downlink = snapshot_downlink_after - effective_before_down

    if delta_uplink < 0 or delta_downlink < 0:
        delta_uplink = max(0, snapshot_uplink_after)
        delta_downlink = max(0, snapshot_downlink_after)

    total_delta = delta_uplink + delta_downlink
    total_quota = (sub.base_traffic_bytes or 0) + (sub.extra_traffic_bytes or 0)
    used_before = max(0, (sub.traffic_used_bytes or 0) - (sub.traffic_overage_bytes or 0))
    available_before = max(0, total_quota - used_before)

    if total_delta <= 0:
        return 0, False, available_before, None

    if sub.status in (WhiteInternetStatus.DISABLED, WhiteInternetStatus.EXPIRED):
        overage = total_delta
    else:
        overage = max(0, total_delta - available_before)

    sub.traffic_used_bytes = (sub.traffic_used_bytes or 0) + total_delta
    sub.traffic_overage_bytes = (sub.traffic_overage_bytes or 0) + overage
    sub.traffic_uplink_bytes = (sub.traffic_uplink_bytes or 0) + delta_uplink
    sub.traffic_downlink_bytes = (sub.traffic_downlink_bytes or 0) + delta_downlink
    sub.last_uplink_snapshot = snapshot_uplink_after
    sub.last_downlink_snapshot = snapshot_downlink_after
    sub.traffic_stats_epoch = str_node_epoch

    used_after = max(0, (sub.traffic_used_bytes or 0) - (sub.traffic_overage_bytes or 0))
    available_after = max(0, total_quota - used_after)
    became_exhausted = False
    if available_after == 0 and sub.status == WhiteInternetStatus.ACTIVE:
        sub.status = WhiteInternetStatus.EXHAUSTED
        sub.status_reason = "quota_exhausted"
        sub.desired_version += 1
        sub.provisioning_status = WhiteInternetProvisioningStatus.PENDING_UPDATE
        became_exhausted = True

    await session.flush()
    return total_delta, became_exhausted, available_after, None


async def finalize_hard_delete_subscription(
    session: AsyncSession,
    subscription_id: int,
) -> bool:
    """Hard-delete a reset subscription only after node-confirmed disable.

    Returns True when the row was deleted. Returns False (fail-closed, row kept)
    unless ALL hold: pending_hard_delete flag, DISABLED status, SYNCED_INACTIVE
    provisioning with matching versions (node confirmed the client disabled),
    or the origin server is gone entirely (origin_node_id NULL — nothing left
    to clean on any node).
    """
    sub = await get_subscription_with_lock(session, subscription_id)
    if sub is None:
        return False
    if not sub.pending_hard_delete:
        return False
    if sub.status != WhiteInternetStatus.DISABLED:
        return False
    node_confirmed = (
        sub.provisioning_status == WhiteInternetProvisioningStatus.SYNCED_INACTIVE
        and (sub.actual_version or 0) == (sub.desired_version or 0)
    )
    origin_gone = sub.origin_node_id is None
    if not (node_confirmed or origin_gone):
        return False
    await session.delete(sub)
    await session.flush()
    return True


async def enqueue_orphan_cleanup(
    session: AsyncSession,
    server_id: int | None,
    client_uuid: str,
    desired_version: int,
) -> WhiteInternetOrphanCleanup:
    """Durably record that a client UUID must be disabled on a former origin.

    Called in the same transaction as the renew migration, so the record
    survives any crash or restart that would lose a fire-and-forget task.
    """
    row = WhiteInternetOrphanCleanup(
        server_id=server_id,
        client_uuid=client_uuid,
        desired_version=desired_version,
        status="pending",
    )
    session.add(row)
    await session.flush()
    return row


async def mark_orphan_cleanup_done(
    session: AsyncSession,
    cleanup_id: int,
) -> None:
    row = await session.get(WhiteInternetOrphanCleanup, cleanup_id, with_for_update=True)
    if row is None or row.status != "pending":
        return
    row.status = "done"
    row.last_error = None
    await session.flush()


async def mark_orphan_cleanup_failed(
    session: AsyncSession,
    cleanup_id: int,
    error: str,
) -> None:
    row = await session.get(WhiteInternetOrphanCleanup, cleanup_id, with_for_update=True)
    if row is None or row.status != "pending":
        return
    row.attempts = (row.attempts or 0) + 1
    row.last_error = error[:500]
    await session.flush()


async def cancel_pending_orphan_cleanups_for_client(
    session: AsyncSession,
    server_id: int,
    client_uuid: str,
) -> int:
    """Cancel any pending orphan cleanups for a client that has been re-placed on server_id."""
    stmt = (
        update(WhiteInternetOrphanCleanup)
        .where(
            WhiteInternetOrphanCleanup.server_id == server_id,
            WhiteInternetOrphanCleanup.client_uuid == client_uuid,
            WhiteInternetOrphanCleanup.status == "pending",
        )
        .values(status="done", last_error="Cancelled by re-placement on this origin node")
    )
    res = await session.execute(stmt)
    await session.flush()
    return getattr(res, "rowcount", 0) or 0


async def get_white_internet_dashboard_stats(session: AsyncSession) -> dict:
    """Return dashboard metrics for White Internet (active subscriptions, total traffic)."""
    now = now_utc()
    stmt = select(
        func.count(WhiteInternetSubscription.id).filter(
            WhiteInternetSubscription.status == WhiteInternetStatus.ACTIVE,
            WhiteInternetSubscription.expires_at > now,
        ).label("active_count"),
        func.coalesce(func.sum(WhiteInternetSubscription.traffic_used_bytes), 0).label("total_traffic_bytes"),
    )
    result = await session.execute(stmt)
    row = result.one()
    return {
        "active_count": row.active_count or 0,
        "total_traffic_bytes": int(row.total_traffic_bytes or 0),
    }


async def register_hwid_atomic(
    session: AsyncSession,
    subscription_id: int,
    hwid: str,
    max_devices: int,
    ttl_hours: int = WHITE_INTERNET_HWID_TTL_HOURS,
) -> tuple[bool, int, int]:
    """Atomically registers an HWID for a White Internet subscription under row-level lock.

    Returns:
        tuple[allowed: bool, active_count: int, max_devices: int]
    """
    sub = await session.get(
        WhiteInternetSubscription,
        subscription_id,
        with_for_update=True,
    )
    if sub is None:
        return False, 0, max_devices

    sub_limit = getattr(sub, "device_limit", None)
    if isinstance(sub_limit, int) and sub_limit > 0:
        effective_limit = sub_limit
    else:
        effective_limit = max(1, max_devices)

    current_hwids: dict[str, str] = dict(sub.active_hwids or {})
    now = now_utc()
    cutoff = (now - timedelta(hours=ttl_hours)).isoformat()

    # Filter out stale HWIDs
    active_hwids = {
        h: ts for h, ts in current_hwids.items()
        if isinstance(ts, str) and ts >= cutoff
    }

    clean_hwid = str(hwid).strip()[:128]

    if not clean_hwid:
        return False, len(active_hwids), effective_limit

    # If active devices exceed limit (e.g. after tariff downgrade), prune to effective_limit most recently active
    if len(active_hwids) > effective_limit:
        sorted_hwids = sorted(active_hwids.items(), key=lambda item: item[1], reverse=True)
        active_hwids = dict(sorted_hwids[:effective_limit])

    if clean_hwid in active_hwids:
        # Existing device - refresh timestamp
        active_hwids[clean_hwid] = now.isoformat()
        sub.active_hwids = active_hwids
        await session.flush()
        return True, len(active_hwids), effective_limit

    # New device - check limit
    if len(active_hwids) >= effective_limit:
        sub.active_hwids = active_hwids
        await session.flush()
        return False, len(active_hwids), effective_limit

    # Within limit - register
    active_hwids[clean_hwid] = now.isoformat()
    sub.active_hwids = active_hwids
    await session.flush()
    return True, len(active_hwids), effective_limit


async def reset_active_hwids_atomic(
    session: AsyncSession,
    subscription_id: int,
    cooldown_seconds: int = WHITE_INTERNET_DEVICE_RESET_COOLDOWN_SECONDS,
    now: datetime | None = None,
) -> bool:
    """Atomically clears all registered HWIDs for a subscription under row-level lock.

    Raises WhiteInternetResetCooldownError if cooldown period has not elapsed.
    """
    sub = await session.get(
        WhiteInternetSubscription,
        subscription_id,
        with_for_update=True,
    )
    if sub is None:
        return False

    now = now or now_utc()
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    last_reset = getattr(sub, "last_device_reset_at", None)
    if isinstance(last_reset, datetime):
        if last_reset.tzinfo is None:
            last_reset = last_reset.replace(tzinfo=timezone.utc)
        elapsed = (now - last_reset).total_seconds()
        if elapsed < cooldown_seconds:
            remaining = int(cooldown_seconds - elapsed)
            raise WhiteInternetResetCooldownError(
                f"Device reset cooldown is active. Wait {remaining} seconds.",
                remaining_seconds=remaining,
            )

    sub.active_hwids = {}
    sub.last_device_reset_at = now
    await session.flush()
    return True


async def reset_traffic_used_atomic(
    session: AsyncSession,
    subscription_id: int,
) -> WhiteInternetSubscription:
    """Zeroes traffic usage counters for ACTIVE or EXHAUSTED subscription under row lock.

    State Guard: rejects DISABLED, PENDING_DELETE, or EXPIRED subscriptions.
    Preserves last_uplink_snapshot, last_downlink_snapshot, and traffic_stats_epoch
    to prevent worker delta desynchronization.
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
    # Preserves last_uplink_snapshot, last_downlink_snapshot, and traffic_stats_epoch

    if sub.status == WhiteInternetStatus.EXHAUSTED:
        sub.status = WhiteInternetStatus.ACTIVE
        sub.status_reason = None
        sub.desired_version += 1
        sub.provisioning_status = WhiteInternetProvisioningStatus.PENDING_UPDATE

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

    sub.extra_traffic_bytes = (sub.extra_traffic_bytes or 0) + extra_bytes
    total_quota = (sub.base_traffic_bytes or 0) + sub.extra_traffic_bytes
    if total_quota > WHITE_INTERNET_MAX_QUOTA_BYTES:
        raise WhiteInternetQuotaCapExceededError(
            f"Total quota ({total_quota} bytes) exceeds maximum allowed limit ({WHITE_INTERNET_MAX_QUOTA_BYTES} bytes)"
        )
    used = max(0, (sub.traffic_used_bytes or 0) - (sub.traffic_overage_bytes or 0))

    if sub.status == WhiteInternetStatus.EXHAUSTED and total_quota > used:
        sub.status = WhiteInternetStatus.ACTIVE
        sub.status_reason = None
        sub.desired_version += 1
        sub.provisioning_status = WhiteInternetProvisioningStatus.PENDING_UPDATE

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

    total_quota = base_bytes + (sub.extra_traffic_bytes or 0)
    if total_quota > WHITE_INTERNET_MAX_QUOTA_BYTES:
        raise WhiteInternetQuotaCapExceededError(
            f"Total quota ({total_quota} bytes) exceeds maximum allowed limit ({WHITE_INTERNET_MAX_QUOTA_BYTES} bytes)"
        )

    sub.base_traffic_bytes = base_bytes
    used = max(0, (sub.traffic_used_bytes or 0) - (sub.traffic_overage_bytes or 0))

    if sub.status == WhiteInternetStatus.EXHAUSTED and total_quota > used:
        sub.status = WhiteInternetStatus.ACTIVE
        sub.status_reason = None
        sub.desired_version += 1
        sub.provisioning_status = WhiteInternetProvisioningStatus.PENDING_UPDATE

    await session.flush()
    return sub


async def set_device_limit_atomic(
    session: AsyncSession,
    subscription_id: int,
    limit: int,
) -> WhiteInternetSubscription:
    """Updates device limit and applies LRU truncation to active_hwids under row lock."""
    if limit < 1 or limit > WHITE_INTERNET_MAX_DEVICE_LIMIT:
        raise WhiteInternetDeviceLimitExceededError(
            f"Device limit must be between 1 and {WHITE_INTERNET_MAX_DEVICE_LIMIT}"
        )
    sub = await session.get(
        WhiteInternetSubscription,
        subscription_id,
        with_for_update=True,
    )
    if sub is None:
        raise WhiteInternetSubscriptionNotFoundError(f"Subscription {subscription_id} not found")

    sub.device_limit = limit
    current_hwids: dict[str, str] = dict(sub.active_hwids or {})
    if len(current_hwids) > limit:
        sorted_hwids = sorted(current_hwids.items(), key=lambda item: item[1], reverse=True)
        sub.active_hwids = dict(sorted_hwids[:limit])
    await session.flush()
    return sub


