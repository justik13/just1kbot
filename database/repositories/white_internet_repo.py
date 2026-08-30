"""Transactional persistence boundary for White Internet subscriptions and quota grants."""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal
from typing import Sequence

from sqlalchemy import BigInteger, case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from config.constants import (
    WHITE_INTERNET_BASE_DURATION_DAYS,
    WHITE_INTERNET_BASE_PRICE_RUB,
    WHITE_INTERNET_BASE_TRAFFIC_BYTES,
    WHITE_INTERNET_MAX_QUOTA_BYTES,
)
from config.enums import WhiteInternetGrantType, WhiteInternetProvisioningStatus, WhiteInternetStatus
from database.models import WhiteInternetQuotaGrant, WhiteInternetSubscription
from utils.datetime_helpers import now_utc


class WhiteInternetError(RuntimeError):
    """Base exception for White Internet domain errors."""
    pass


class WhiteInternetQuotaCapExceededError(WhiteInternetError):
    """Raised when an extra quota purchase would exceed the maximum accumulation limit (500 GiB)."""
    pass


class WhiteInternetSubscriptionNotFoundError(WhiteInternetError):
    """Raised when a subscription is not found."""
    pass


class WhiteInternetInactiveSubscriptionError(WhiteInternetError):
    """Raised when an operation requires an active subscription but the subscription is expired/disabled."""
    pass


async def get_subscription_by_token(
    session: AsyncSession, token: str
) -> WhiteInternetSubscription | None:
    """Fetch subscription by its secret subscription feed token."""
    stmt = select(WhiteInternetSubscription).where(WhiteInternetSubscription.token == token)
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


async def get_subscription_by_user_id(
    session: AsyncSession, user_id: int
) -> WhiteInternetSubscription | None:
    """Fetch subscription by user ID."""
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
    """Fetch subscription by primary key."""
    stmt = select(WhiteInternetSubscription).where(WhiteInternetSubscription.id == subscription_id)
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


async def get_subscription_with_lock(
    session: AsyncSession, subscription_id: int
) -> WhiteInternetSubscription | None:
    """Lock subscription row (FOR UPDATE) ensuring strict lock ordering."""
    stmt = (
        select(WhiteInternetSubscription)
        .where(WhiteInternetSubscription.id == subscription_id)
        .with_for_update()
    )
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


async def get_available_quota_bytes(
    session: AsyncSession, subscription_id: int, now: datetime | None = None
) -> int:
    """
    Calculate available quota: SUM(bytes_remaining WHERE expires_at > now).
    This is the Single Source of Truth (SSOT) for entitlement limits.
    """
    if now is None:
        now = now_utc()
    stmt = (
        select(func.coalesce(func.sum(WhiteInternetQuotaGrant.bytes_remaining), 0))
        .where(
            WhiteInternetQuotaGrant.subscription_id == subscription_id,
            WhiteInternetQuotaGrant.expires_at > now,
            WhiteInternetQuotaGrant.bytes_remaining > 0,
        )
    )
    total = await session.scalar(stmt)
    return int(total or 0)


async def get_active_grants_for_deduction(
    session: AsyncSession, subscription_id: int, now: datetime | None = None
) -> Sequence[WhiteInternetQuotaGrant]:
    """
    Acquire active grants under row locks ordered deterministically:
    BASE-First, followed by TOPUP FIFO (created_at ASC, id ASC).
    """
    if now is None:
        now = now_utc()
    stmt = (
        select(WhiteInternetQuotaGrant)
        .where(
            WhiteInternetQuotaGrant.subscription_id == subscription_id,
            WhiteInternetQuotaGrant.bytes_remaining > 0,
            WhiteInternetQuotaGrant.expires_at > now,
        )
        .order_by(
            case((WhiteInternetQuotaGrant.grant_type == WhiteInternetGrantType.BASE, 0), else_=1).asc(),
            WhiteInternetQuotaGrant.created_at.asc(),
            WhiteInternetQuotaGrant.id.asc(),
        )
        .with_for_update()
    )
    result = await session.execute(stmt)
    return result.scalars().all()


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
    """Create a new White Internet subscription along with its initial BASE quota grant."""
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
        traffic_limit_bytes=base_bytes,
        traffic_used_bytes=0,
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

    base_grant = WhiteInternetQuotaGrant(
        subscription_id=subscription.id,
        grant_type=WhiteInternetGrantType.BASE,
        bytes_granted=base_bytes,
        bytes_remaining=base_bytes,
        price_rub=price_rub,
        quote_id=quote_id,
        expires_at=expires_at,
        created_at=now,
    )
    session.add(base_grant)
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
) -> WhiteInternetSubscription:
    """
    Atomic renewal under strict lock order:
    1. Lock subscription FOR UPDATE
    2. Lock grants FOR UPDATE
    3. Expire old BASE grant (bytes_remaining = 0)
    4. Carryover active unexpired TOPUP grants to new_expires_at
    5. Issue fresh BASE grant
    6. Advance desired_version if subscription was EXHAUSTED/EXPIRED
    """
    now = now_utc()
    sub = await get_subscription_with_lock(session, subscription_id)
    if sub is None:
        raise WhiteInternetSubscriptionNotFoundError(f"Subscription {subscription_id} not found")

    # Extension logic: if still active, extend from expires_at; if already expired, start from now
    base_time = sub.expires_at if sub.expires_at > now else now
    new_expires_at = base_time + timedelta(days=duration_days)
    sub.expires_at = new_expires_at

    # Lock all grants for this subscription
    stmt_grants = (
        select(WhiteInternetQuotaGrant)
        .where(WhiteInternetQuotaGrant.subscription_id == sub.id)
        .with_for_update()
    )
    grants_res = await session.execute(stmt_grants)
    all_grants = grants_res.scalars().all()

    # Expire old BASE grants
    for grant in all_grants:
        if grant.grant_type == WhiteInternetGrantType.BASE and grant.bytes_remaining > 0:
            grant.bytes_remaining = 0

    # Carryover active TOPUP grants: extend their expires_at to match the subscription renewal
    for grant in all_grants:
        if (
            grant.grant_type == WhiteInternetGrantType.TOPUP
            and grant.bytes_remaining > 0
            and grant.expires_at >= now
        ):
            grant.expires_at = new_expires_at

    # Insert fresh BASE grant
    new_base_grant = WhiteInternetQuotaGrant(
        subscription_id=sub.id,
        grant_type=WhiteInternetGrantType.BASE,
        bytes_granted=base_bytes,
        bytes_remaining=base_bytes,
        price_rub=price_rub,
        quote_id=quote_id,
        expires_at=new_expires_at,
        created_at=now,
    )
    session.add(new_base_grant)
    await session.flush()

    # Re-evaluate status and available quota
    available = await get_available_quota_bytes(session, sub.id, now)
    sub.traffic_limit_bytes = available + sub.traffic_used_bytes

    if sub.status in (WhiteInternetStatus.EXHAUSTED, WhiteInternetStatus.EXPIRED):
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
) -> WhiteInternetQuotaGrant:
    """
    Atomic top-up quota purchase:
    1. Lock subscription FOR UPDATE
    2. Check Cap 500 GiB invariant under lock
    3. Create TOPUP grant with expires_at == subscription.expires_at
    4. If subscription was EXHAUSTED, transition to ACTIVE and bump desired_version
    """
    now = now_utc()
    sub = await get_subscription_with_lock(session, subscription_id)
    if sub is None:
        raise WhiteInternetSubscriptionNotFoundError(f"Subscription {subscription_id} not found")

    if sub.status == WhiteInternetStatus.EXPIRED or sub.expires_at <= now:
        raise WhiteInternetInactiveSubscriptionError(
            "Cannot top up an expired subscription. Please renew first."
        )

    pack_bytes = pack_gb * 1024 * 1024 * 1024

    # Check Cap 500 GiB
    current_available = await get_available_quota_bytes(session, sub.id, now)
    if current_available + pack_bytes > WHITE_INTERNET_MAX_QUOTA_BYTES:
        raise WhiteInternetQuotaCapExceededError(
            f"Adding {pack_gb} GiB would exceed the 500 GiB maximum accumulation cap."
        )

    topup_grant = WhiteInternetQuotaGrant(
        subscription_id=sub.id,
        grant_type=WhiteInternetGrantType.TOPUP,
        bytes_granted=pack_bytes,
        bytes_remaining=pack_bytes,
        price_rub=price_rub,
        quote_id=quote_id,
        expires_at=sub.expires_at,
        created_at=now,
    )
    session.add(topup_grant)
    await session.flush()

    sub.traffic_limit_bytes += pack_bytes

    if sub.status == WhiteInternetStatus.EXHAUSTED:
        sub.status = WhiteInternetStatus.ACTIVE
        sub.status_reason = None
        sub.desired_version += 1
        sub.provisioning_status = WhiteInternetProvisioningStatus.PENDING_UPDATE

    await session.flush()
    return topup_grant


async def deduct_traffic_atomic(
    session: AsyncSession,
    *,
    subscription_id: int,
    delta_bytes: int,
    now: datetime | None = None,
) -> tuple[int, bool, int]:
    """
    Deduct consumed traffic delta atomically from grant ledger:
    - Order: Base-First, then TOPUP FIFO.
    - Quota overshoot handling: If delta exceeds available quota,
      remaining grants are reduced to 0, total delta is added to traffic_used_bytes,
      overshoot is returned as unallocated_overage, and status becomes EXHAUSTED.
    Returns: (consumed_from_grants, is_exhausted, unallocated_overage)
    """
    if delta_bytes <= 0:
        return 0, False, 0

    if now is None:
        now = now_utc()

    # Lock subscription first
    sub = await get_subscription_with_lock(session, subscription_id)
    if sub is None:
        raise WhiteInternetSubscriptionNotFoundError(f"Subscription {subscription_id} not found")

    # Lock active grants
    grants = await get_active_grants_for_deduction(session, subscription_id, now)

    remaining_to_deduct = delta_bytes
    consumed_from_grants = 0

    for grant in grants:
        if remaining_to_deduct <= 0:
            break
        deduct_from_grant = min(grant.bytes_remaining, remaining_to_deduct)
        grant.bytes_remaining -= deduct_from_grant
        remaining_to_deduct -= deduct_from_grant
        consumed_from_grants += deduct_from_grant

    # Always record full actual traffic in cumulative summary
    sub.traffic_used_bytes += delta_bytes

    unallocated_overage = max(remaining_to_deduct, 0)

    # Check if quota is fully exhausted
    available_after = await get_available_quota_bytes(session, subscription_id, now)
    became_exhausted = False

    if available_after == 0 and sub.status == WhiteInternetStatus.ACTIVE:
        sub.status = WhiteInternetStatus.EXHAUSTED
        sub.status_reason = "quota_exhausted"
        sub.desired_version += 1
        sub.provisioning_status = WhiteInternetProvisioningStatus.PENDING_UPDATE
        became_exhausted = True

    await session.flush()
    return consumed_from_grants, became_exhausted, unallocated_overage
