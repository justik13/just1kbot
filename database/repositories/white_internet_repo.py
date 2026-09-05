"""Transactional persistence boundary for White Internet subscriptions."""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from config.constants import (
    WHITE_INTERNET_BASE_DURATION_DAYS,
    WHITE_INTERNET_BASE_PRICE_RUB,
    WHITE_INTERNET_BASE_TRAFFIC_BYTES,
    WHITE_INTERNET_MAX_QUOTA_BYTES,
)
from config.enums import WhiteInternetProvisioningStatus, WhiteInternetStatus
from database.models import WhiteInternetSubscription
from utils.datetime_helpers import now_utc


class WhiteInternetError(RuntimeError):
    """Base exception for White Internet domain errors."""


class WhiteInternetQuotaCapExceededError(WhiteInternetError):
    """Raised when an extra quota purchase would exceed the maximum accumulation limit."""


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
    stmt = (
        select(WhiteInternetSubscription.id)
        .where(WhiteInternetSubscription.user_id == user_id)
        .limit(1)
    )
    return await session.scalar(stmt) is not None


async def get_subscription_by_id(
    session: AsyncSession, subscription_id: int
) -> WhiteInternetSubscription | None:
    return await session.scalar(
        select(WhiteInternetSubscription).where(WhiteInternetSubscription.id == subscription_id)
    )


async def get_subscription_with_lock(
    session: AsyncSession, subscription_id: int
) -> WhiteInternetSubscription | None:
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
    await session.refresh(subscription)
    return subscription


async def renew_subscription_atomic(
    session: AsyncSession,
    *,
    subscription_id: int,
    quote_id: int,
    price_rub: Decimal = WHITE_INTERNET_BASE_PRICE_RUB,
    duration_days: int = WHITE_INTERNET_BASE_DURATION_DAYS,
    base_bytes: int = WHITE_INTERNET_BASE_TRAFFIC_BYTES,
    now: datetime | None = None,
) -> WhiteInternetSubscription:
    now = now or now_utc()
    sub = await get_subscription_with_lock(session, subscription_id)
    if sub is None:
        raise WhiteInternetSubscriptionNotFoundError(f"Subscription {subscription_id} not found")
    if sub.status in (WhiteInternetStatus.DISABLED, WhiteInternetStatus.PENDING):
        raise WhiteInternetInactiveSubscriptionError("Subscription is not eligible for renewal")

    used_quota = max(0, (sub.traffic_used_bytes or 0) - (sub.traffic_overage_bytes or 0))
    total_left = max(
        0,
        ((sub.base_traffic_bytes or 0) + (sub.extra_traffic_bytes or 0))
        - used_quota,
    )
    extra_rollover = min(sub.extra_traffic_bytes or 0, total_left)

    is_grace_valid = now <= (sub.expires_at + timedelta(days=7))
    new_extra = extra_rollover if is_grace_valid else 0

    base_time = sub.expires_at if sub.expires_at > now else now
    new_expires_at = base_time + timedelta(days=duration_days)

    sub.base_traffic_bytes = base_bytes
    sub.extra_traffic_bytes = new_extra
    sub.expires_at = new_expires_at
    sub.status = WhiteInternetStatus.ACTIVE
    sub.status_reason = None
    sub.desired_version += 1
    sub.provisioning_status = WhiteInternetProvisioningStatus.PENDING_UPDATE

    # Reset period usage counters; DO NOT reset last_uplink_snapshot / last_downlink_snapshot!
    sub.traffic_used_bytes = 0
    sub.traffic_uplink_bytes = 0
    sub.traffic_downlink_bytes = 0
    sub.traffic_overage_bytes = 0

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
    if sub.status == WhiteInternetStatus.EXPIRED or sub.expires_at <= now:
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
