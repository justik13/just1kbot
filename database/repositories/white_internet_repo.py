"""Transactional persistence boundary for White Internet subscriptions and quota grants."""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal
import inspect
from typing import Sequence


from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from config.constants import (
    WHITE_INTERNET_BASE_DURATION_DAYS,
    WHITE_INTERNET_BASE_PRICE_RUB,
    WHITE_INTERNET_BASE_TRAFFIC_BYTES,
    WHITE_INTERNET_MAX_QUOTA_BYTES,
)
from config.enums import WhiteInternetGrantType, WhiteInternetProvisioningStatus, WhiteInternetStatus
from database.models import WhiteInternetQuotaGrant, WhiteInternetSubscription, WhiteInternetTrafficEvent
from utils.datetime_helpers import now_utc


class WhiteInternetError(RuntimeError):
    """Base exception for White Internet domain errors."""


class WhiteInternetQuotaCapExceededError(WhiteInternetError):
    """Raised when an extra quota purchase would exceed the maximum accumulation limit."""


class WhiteInternetSubscriptionNotFoundError(WhiteInternetError):
    """Raised when a subscription is not found."""


class WhiteInternetInactiveSubscriptionError(WhiteInternetError):
    """Raised when an operation requires a live subscription."""


async def get_subscription_by_token(session: AsyncSession, token: str) -> WhiteInternetSubscription | None:
    return (await session.execute(
        select(WhiteInternetSubscription).where(WhiteInternetSubscription.token == token)
    )).scalar_one_or_none()


async def get_subscription_by_user_id(session: AsyncSession, user_id: int) -> WhiteInternetSubscription | None:
    stmt = (
        select(WhiteInternetSubscription)
        .where(WhiteInternetSubscription.user_id == user_id)
        .order_by(WhiteInternetSubscription.id.desc())
        .limit(1)
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def get_subscription_by_id(session: AsyncSession, subscription_id: int) -> WhiteInternetSubscription | None:
    return await session.scalar(select(WhiteInternetSubscription).where(WhiteInternetSubscription.id == subscription_id))


async def get_subscription_with_lock(session: AsyncSession, subscription_id: int) -> WhiteInternetSubscription | None:
    stmt = (
        select(WhiteInternetSubscription)
        .where(WhiteInternetSubscription.id == subscription_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def get_available_quota_bytes(session: AsyncSession, subscription_id: int, now: datetime | None = None) -> int:
    now = now or now_utc()
    stmt = select(func.coalesce(func.sum(WhiteInternetQuotaGrant.bytes_remaining), 0)).where(
        WhiteInternetQuotaGrant.subscription_id == subscription_id,
        WhiteInternetQuotaGrant.expires_at > now,
        WhiteInternetQuotaGrant.bytes_remaining > 0,
    )
    return int(await session.scalar(stmt) or 0)


async def get_period_grants(
    session: AsyncSession, subscription_id: int, now: datetime | None = None
) -> Sequence[WhiteInternetQuotaGrant]:
    """Returns all grants active in current period (expires_at > now), including depleted ones."""
    now = now or now_utc()
    stmt = (
        select(WhiteInternetQuotaGrant)
        .where(
            WhiteInternetQuotaGrant.subscription_id == subscription_id,
            WhiteInternetQuotaGrant.expires_at > now,
        )
        .order_by(
            case((WhiteInternetQuotaGrant.grant_type == WhiteInternetGrantType.BASE, 0), else_=1).asc(),
            WhiteInternetQuotaGrant.created_at.asc(),
            WhiteInternetQuotaGrant.id.asc(),
        )
    )
    return (await session.execute(stmt)).scalars().all()


async def get_active_grants_for_deduction(
    session: AsyncSession, subscription_id: int, now: datetime | None = None
) -> Sequence[WhiteInternetQuotaGrant]:
    now = now or now_utc()
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
    return (await session.execute(stmt)).scalars().all()


async def _lock_all_grants(session: AsyncSession, subscription_id: int) -> Sequence[WhiteInternetQuotaGrant]:
    stmt = (
        select(WhiteInternetQuotaGrant)
        .where(WhiteInternetQuotaGrant.subscription_id == subscription_id)
        .order_by(WhiteInternetQuotaGrant.id.asc())
        .with_for_update()
    )
    result = await session.execute(stmt)
    if hasattr(result, "scalars"):
        scalars = result.scalars()
        if inspect.iscoroutine(scalars):
            scalars = await scalars
        if hasattr(scalars, "all"):
            val = scalars.all()
            if inspect.iscoroutine(val):
                return await val
            return val
    return []


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
    grants = await _lock_all_grants(session, sub.id)
    for grant in grants:
        grant.bytes_remaining = 0
    await session.flush()
    return sub


async def create_white_internet_subscription(
    session: AsyncSession, *, user_id: int, origin_node_id: int, token: str, uuid: str,
    quote_id: int, price_rub: Decimal = WHITE_INTERNET_BASE_PRICE_RUB,
    duration_days: int = WHITE_INTERNET_BASE_DURATION_DAYS,
    base_bytes: int = WHITE_INTERNET_BASE_TRAFFIC_BYTES,
) -> WhiteInternetSubscription:
    now = now_utc()
    expires_at = now + timedelta(days=duration_days)
    subscription = WhiteInternetSubscription(
        user_id=user_id, origin_node_id=origin_node_id, token=token, uuid=uuid,
        status=WhiteInternetStatus.PENDING, status_reason=None, started_at=now,
        expires_at=expires_at, traffic_limit_bytes=base_bytes, traffic_used_bytes=0,
        traffic_overage_bytes=0, last_uplink_snapshot=0, last_downlink_snapshot=0,
        traffic_stats_epoch=None,
        provisioning_status=WhiteInternetProvisioningStatus.PENDING_CREATE,
        desired_version=1, actual_version=0, last_reconciled_node_epoch=None,
    )
    session.add(subscription)
    await session.flush()
    session.add(WhiteInternetQuotaGrant(
        subscription_id=subscription.id, grant_type=WhiteInternetGrantType.BASE,
        bytes_granted=base_bytes, bytes_remaining=base_bytes, price_rub=price_rub,
        quote_id=quote_id, expires_at=expires_at, created_at=now,
    ))
    await session.flush()
    await session.refresh(subscription)
    return subscription


async def renew_subscription_atomic(
    session: AsyncSession, *, subscription_id: int, quote_id: int,
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
    all_grants = await _lock_all_grants(session, sub.id)
    base_time = sub.expires_at if sub.expires_at > now else now
    new_expires_at = base_time + timedelta(days=duration_days)
    for grant in all_grants:
        if grant.grant_type == WhiteInternetGrantType.BASE:
            grant.bytes_remaining = 0
        elif grant.grant_type == WhiteInternetGrantType.TOPUP and grant.bytes_remaining > 0:
            if grant.expires_at > now:
                grant.expires_at = new_expires_at
            else:
                grant.bytes_remaining = 0
    sub.expires_at = new_expires_at
    sub.status = WhiteInternetStatus.ACTIVE
    sub.status_reason = None
    sub.desired_version += 1
    sub.provisioning_status = WhiteInternetProvisioningStatus.PENDING_UPDATE

    # Period Usage Reset: Historical traffic events remain permanently preserved in
    # WhiteInternetTrafficEvent records. Reset period consumption so UI and Subscription-Userinfo
    # reflect exact current period usage, while preserving the node snapshot baseline to prevent
    # double-counting historical Xray counters.
    sub.traffic_used_bytes = 0
    sub.traffic_uplink_bytes = 0
    sub.traffic_downlink_bytes = 0
    sub.traffic_overage_bytes = 0

    session.add(WhiteInternetQuotaGrant(
        subscription_id=sub.id, grant_type=WhiteInternetGrantType.BASE,
        bytes_granted=base_bytes, bytes_remaining=base_bytes, price_rub=price_rub,
        quote_id=quote_id, expires_at=new_expires_at, created_at=now,
    ))
    await session.flush()
    available = await get_available_quota_bytes(session, sub.id, now)
    sub.traffic_limit_bytes = available
    await session.flush()
    await session.refresh(sub)
    return sub


async def topup_quota_atomic(
    session: AsyncSession, *, subscription_id: int, quote_id: int, pack_gb: int, price_rub: Decimal
) -> WhiteInternetQuotaGrant:
    now = now_utc()
    sub = await get_subscription_with_lock(session, subscription_id)
    if sub is None:
        raise WhiteInternetSubscriptionNotFoundError(f"Subscription {subscription_id} not found")
    if sub.status in (WhiteInternetStatus.PENDING, WhiteInternetStatus.DISABLED):
        raise WhiteInternetInactiveSubscriptionError("Subscription is not eligible for top-up")
    if sub.status == WhiteInternetStatus.EXPIRED or sub.expires_at <= now:
        raise WhiteInternetInactiveSubscriptionError("Cannot top up an expired subscription")
    await _lock_all_grants(session, sub.id)
    current_available = await get_available_quota_bytes(session, sub.id, now)
    pack_bytes = pack_gb * 1024 * 1024 * 1024
    if current_available + pack_bytes > WHITE_INTERNET_MAX_QUOTA_BYTES:
        raise WhiteInternetQuotaCapExceededError(
            f"Adding {pack_gb} GiB would exceed the 500 GiB maximum accumulation cap."
        )
    grant = WhiteInternetQuotaGrant(
        subscription_id=sub.id, grant_type=WhiteInternetGrantType.TOPUP,
        bytes_granted=pack_bytes, bytes_remaining=pack_bytes, price_rub=price_rub,
        quote_id=quote_id, expires_at=sub.expires_at, created_at=now,
    )
    session.add(grant)
    sub.traffic_limit_bytes += pack_bytes
    if sub.status == WhiteInternetStatus.EXHAUSTED:
        sub.status = WhiteInternetStatus.ACTIVE
        sub.status_reason = None
        sub.desired_version += 1
        sub.provisioning_status = WhiteInternetProvisioningStatus.PENDING_UPDATE
    await session.flush()
    return grant


async def record_traffic_event_atomic(
    session: AsyncSession,
    *,
    subscription_id: int,
    node_epoch: str,
    node_boot_id: str | None,
    node_starttime: int | None,
    snapshot_uplink_before: int,
    snapshot_uplink_after: int,
    snapshot_downlink_before: int,
    snapshot_downlink_after: int,
    delta_uplink: int,
    delta_downlink: int,
    allocated_bytes: int,
    overage_bytes: int,
    now: datetime | None = None,
) -> WhiteInternetTrafficEvent | None:
    """
    Creates an immutable audit event for accounted traffic transition.
    If delta is 0, no event is created (returns None).
    """
    if delta_uplink == 0 and delta_downlink == 0:
        return None

    try:
        bind = session.get_bind()
        is_pg = bind.dialect.name == "postgresql"
    except Exception:
        is_pg = True

    if is_pg:
        from sqlalchemy.dialects.postgresql import insert as pg_insert

        stmt = (
            pg_insert(WhiteInternetTrafficEvent)
            .values(
                subscription_id=subscription_id,
                node_epoch=node_epoch,
                node_boot_id=node_boot_id,
                node_starttime=node_starttime,
                snapshot_uplink_before=snapshot_uplink_before,
                snapshot_uplink_after=snapshot_uplink_after,
                snapshot_downlink_before=snapshot_downlink_before,
                snapshot_downlink_after=snapshot_downlink_after,
                delta_uplink=delta_uplink,
                delta_downlink=delta_downlink,
                allocated_bytes=allocated_bytes,
                overage_bytes=overage_bytes,
                created_at=now or now_utc(),
            )
            .on_conflict_do_nothing(
                index_elements=[
                    "subscription_id",
                    "node_epoch",
                    "snapshot_uplink_after",
                    "snapshot_downlink_after",
                ]
            )
        )
        await session.execute(stmt)
        return None

    existing = await session.scalar(
        select(WhiteInternetTrafficEvent).where(
            WhiteInternetTrafficEvent.subscription_id == subscription_id,
            WhiteInternetTrafficEvent.node_epoch == node_epoch,
            WhiteInternetTrafficEvent.snapshot_uplink_after == snapshot_uplink_after,
            WhiteInternetTrafficEvent.snapshot_downlink_after == snapshot_downlink_after,
        )
    )
    if existing is not None:
        return existing

    event = WhiteInternetTrafficEvent(
        subscription_id=subscription_id,
        node_epoch=node_epoch,
        node_boot_id=node_boot_id,
        node_starttime=node_starttime,
        snapshot_uplink_before=snapshot_uplink_before,
        snapshot_uplink_after=snapshot_uplink_after,
        snapshot_downlink_before=snapshot_downlink_before,
        snapshot_downlink_after=snapshot_downlink_after,
        delta_uplink=delta_uplink,
        delta_downlink=delta_downlink,
        allocated_bytes=allocated_bytes,
        overage_bytes=overage_bytes,
        created_at=now or now_utc(),
    )
    session.add(event)
    await session.flush()
    return event


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

    # If subscription already expired or disabled, entire delta is unallocated overage
    if sub.expires_at <= now or sub.status in (WhiteInternetStatus.EXPIRED, WhiteInternetStatus.DISABLED):
        if sub.status not in (WhiteInternetStatus.EXPIRED, WhiteInternetStatus.DISABLED):
            sub.status = WhiteInternetStatus.EXPIRED
            sub.status_reason = "subscription_expired"
            sub.desired_version += 1
            sub.provisioning_status = WhiteInternetProvisioningStatus.PENDING_DELETE
        sub.traffic_overage_bytes = (sub.traffic_overage_bytes or 0) + delta_bytes
        sub.traffic_used_bytes = (sub.traffic_used_bytes or 0) + delta_bytes
        sub.traffic_uplink_bytes = (sub.traffic_uplink_bytes or 0) + max(0, delta_uplink)
        sub.traffic_downlink_bytes = (sub.traffic_downlink_bytes or 0) + max(0, delta_downlink)
        await session.flush()
        return 0, False, delta_bytes

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

    unallocated_overage = max(remaining_to_deduct, 0)
    sub.traffic_used_bytes = (sub.traffic_used_bytes or 0) + delta_bytes
    sub.traffic_uplink_bytes = (sub.traffic_uplink_bytes or 0) + max(0, delta_uplink)
    sub.traffic_downlink_bytes = (sub.traffic_downlink_bytes or 0) + max(0, delta_downlink)
    sub.traffic_overage_bytes = (sub.traffic_overage_bytes or 0) + unallocated_overage

    available_after = await get_available_quota_bytes(session, subscription_id, now)
    became_exhausted = False
    if available_after == 0 and sub.status == WhiteInternetStatus.ACTIVE:
        sub.status = WhiteInternetStatus.EXHAUSTED
        sub.status_reason = "quota_exhausted"
        sub.desired_version += 1
        sub.provisioning_status = WhiteInternetProvisioningStatus.PENDING_UPDATE
        became_exhausted = True

    # Update cache-only traffic_limit_bytes field
    sub.traffic_limit_bytes = available_after + (sub.traffic_used_bytes - (sub.traffic_overage_bytes or 0))
    await session.flush()
    return consumed_from_grants, became_exhausted, unallocated_overage


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
) -> tuple[int, bool, int, WhiteInternetTrafficEvent | None]:
    """
    Atomically deduplicates traffic snapshot and deducts quota from active grants.

    - Computes delta_uplink and delta_downlink from before/after counters.
    - If total delta <= 0 (or counters monotonic violation): returns (0, False, available, None).
    - Acquires row lock on subscription and active quota grants (SELECT ... FOR UPDATE).
    - Calculates planned grant consumption (BASE first, then FIFO TOPUP) and overage such that
      allocated_bytes + overage_bytes == delta_uplink + delta_downlink
      (strictly satisfying ck_white_internet_traffic_events_conservation).
    - Inserts WhiteInternetTrafficEvent with ON CONFLICT DO NOTHING RETURNING id.
    - If insertion returned no row (already recorded snapshot / duplicate):
      returns (0, False, available, None) WITHOUT deducting quota or modifying state.
    - If insertion succeeded:
      applies grant deductions, updates subscription counters (used, overage, uplink, downlink,
      last snapshots, stats epoch), marks EXHAUSTED if quota reached 0, and returns
      (allocated_bytes, became_exhausted, available_after, event).
    """
    now = now or now_utc()
    str_node_epoch = str(node_epoch)

    delta_uplink = snapshot_uplink_after - snapshot_uplink_before
    delta_downlink = snapshot_downlink_after - snapshot_downlink_before
    total_delta = delta_uplink + delta_downlink

    if delta_uplink < 0 or delta_downlink < 0 or total_delta <= 0:
        sub = await get_subscription_by_id(session, subscription_id)
        if sub is None:
            raise WhiteInternetSubscriptionNotFoundError(f"Subscription {subscription_id} not found")
        available = await get_available_quota_bytes(session, subscription_id, now)
        return 0, False, available, None

    sub = await get_subscription_with_lock(session, subscription_id)
    if sub is None:
        raise WhiteInternetSubscriptionNotFoundError(f"Subscription {subscription_id} not found")

    is_expired_or_disabled = (
        sub.expires_at <= now or sub.status in (WhiteInternetStatus.EXPIRED, WhiteInternetStatus.DISABLED)
    )
    if is_expired_or_disabled:
        allocated_bytes = 0
        overage_bytes = total_delta
        grant_deductions: list[tuple[WhiteInternetQuotaGrant, int]] = []
    else:
        grants = await get_active_grants_for_deduction(session, subscription_id, now)
        remaining_to_deduct = total_delta
        consumed_from_grants = 0
        grant_deductions = []
        for grant in grants:
            if remaining_to_deduct <= 0:
                break
            deduct = min(grant.bytes_remaining, remaining_to_deduct)
            grant_deductions.append((grant, deduct))
            remaining_to_deduct -= deduct
            consumed_from_grants += deduct

        allocated_bytes = consumed_from_grants
        overage_bytes = max(0, remaining_to_deduct)

    try:
        bind = session.get_bind()
        is_pg = bind.dialect.name == "postgresql"
    except Exception:
        is_pg = True

    event: WhiteInternetTrafficEvent | None = None

    if is_pg:
        from sqlalchemy.dialects.postgresql import insert as pg_insert

        insert_stmt = (
            pg_insert(WhiteInternetTrafficEvent)
            .values(
                subscription_id=subscription_id,
                node_epoch=str_node_epoch,
                node_boot_id=node_boot_id,
                node_starttime=node_starttime,
                snapshot_uplink_before=snapshot_uplink_before,
                snapshot_uplink_after=snapshot_uplink_after,
                snapshot_downlink_before=snapshot_downlink_before,
                snapshot_downlink_after=snapshot_downlink_after,
                delta_uplink=delta_uplink,
                delta_downlink=delta_downlink,
                allocated_bytes=allocated_bytes,
                overage_bytes=overage_bytes,
                created_at=now,
            )
            .on_conflict_do_nothing(
                index_elements=[
                    "subscription_id",
                    "node_epoch",
                    "snapshot_uplink_after",
                    "snapshot_downlink_after",
                ]
            )
            .returning(
                WhiteInternetTrafficEvent.id,
                WhiteInternetTrafficEvent.created_at,
            )
        )
        res = (await session.execute(insert_stmt)).first()
        if res is None:
            # Duplicate snapshot: already processed, advance baseline markers
            sub.last_uplink_snapshot = max(sub.last_uplink_snapshot or 0, snapshot_uplink_after)
            sub.last_downlink_snapshot = max(sub.last_downlink_snapshot or 0, snapshot_downlink_after)
            sub.traffic_stats_epoch = str_node_epoch
            await session.flush()
            available = await get_available_quota_bytes(session, subscription_id, now)
            return 0, False, available, None

        event = WhiteInternetTrafficEvent(
            id=res[0],
            subscription_id=subscription_id,
            node_epoch=str_node_epoch,
            node_boot_id=node_boot_id,
            node_starttime=node_starttime,
            snapshot_uplink_before=snapshot_uplink_before,
            snapshot_uplink_after=snapshot_uplink_after,
            snapshot_downlink_before=snapshot_downlink_before,
            snapshot_downlink_after=snapshot_downlink_after,
            delta_uplink=delta_uplink,
            delta_downlink=delta_downlink,
            allocated_bytes=allocated_bytes,
            overage_bytes=overage_bytes,
            created_at=res[1],
        )
    else:
        existing = await session.scalar(
            select(WhiteInternetTrafficEvent).where(
                WhiteInternetTrafficEvent.subscription_id == subscription_id,
                WhiteInternetTrafficEvent.node_epoch == str_node_epoch,
                WhiteInternetTrafficEvent.snapshot_uplink_after == snapshot_uplink_after,
                WhiteInternetTrafficEvent.snapshot_downlink_after == snapshot_downlink_after,
            )
        )
        if existing is not None:
            sub.last_uplink_snapshot = max(sub.last_uplink_snapshot or 0, snapshot_uplink_after)
            sub.last_downlink_snapshot = max(sub.last_downlink_snapshot or 0, snapshot_downlink_after)
            sub.traffic_stats_epoch = str_node_epoch
            await session.flush()
            available = await get_available_quota_bytes(session, subscription_id, now)
            return 0, False, available, None

        event = WhiteInternetTrafficEvent(
            subscription_id=subscription_id,
            node_epoch=str_node_epoch,
            node_boot_id=node_boot_id,
            node_starttime=node_starttime,
            snapshot_uplink_before=snapshot_uplink_before,
            snapshot_uplink_after=snapshot_uplink_after,
            snapshot_downlink_before=snapshot_downlink_before,
            snapshot_downlink_after=snapshot_downlink_after,
            delta_uplink=delta_uplink,
            delta_downlink=delta_downlink,
            allocated_bytes=allocated_bytes,
            overage_bytes=overage_bytes,
            created_at=now,
        )
        session.add(event)
        await session.flush()

    for grant, deduct in grant_deductions:
        grant.bytes_remaining -= deduct

    if is_expired_or_disabled:
        if sub.status not in (WhiteInternetStatus.EXPIRED, WhiteInternetStatus.DISABLED):
            sub.status = WhiteInternetStatus.EXPIRED
            sub.status_reason = "subscription_expired"
            sub.desired_version += 1
            sub.provisioning_status = WhiteInternetProvisioningStatus.PENDING_DELETE

    sub.traffic_used_bytes = (sub.traffic_used_bytes or 0) + total_delta
    sub.traffic_uplink_bytes = (sub.traffic_uplink_bytes or 0) + delta_uplink
    sub.traffic_downlink_bytes = (sub.traffic_downlink_bytes or 0) + delta_downlink
    sub.traffic_overage_bytes = (sub.traffic_overage_bytes or 0) + overage_bytes
    sub.last_uplink_snapshot = snapshot_uplink_after
    sub.last_downlink_snapshot = snapshot_downlink_after
    sub.traffic_stats_epoch = str_node_epoch

    available_after = await get_available_quota_bytes(session, subscription_id, now)
    became_exhausted = False
    if available_after == 0 and sub.status == WhiteInternetStatus.ACTIVE:
        sub.status = WhiteInternetStatus.EXHAUSTED
        sub.status_reason = "quota_exhausted"
        sub.desired_version += 1
        sub.provisioning_status = WhiteInternetProvisioningStatus.PENDING_UPDATE
        became_exhausted = True

    # Update cache-only traffic_limit_bytes field
    sub.traffic_limit_bytes = available_after + (sub.traffic_used_bytes - (sub.traffic_overage_bytes or 0))
    await session.flush()
    return allocated_bytes, became_exhausted, available_after, event
