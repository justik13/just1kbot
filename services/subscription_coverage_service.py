"""Domain service managing canonical subscription coverage slots (EntitlementGrants) and User projections."""

from datetime import datetime, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from config.constants import PERMANENT_END_DATE, PERMANENT_SUBSCRIPTION_DAYS
from config.enums import EntitlementGrantStatus, EntitlementGrantType
from database.models import EntitlementGrant, User
from database.repositories import entitlement_grants_repo
from utils.datetime_helpers import now_utc


async def append_awg_grant(
    session: AsyncSession,
    *,
    user_id: int,
    duration_hours: int,
    source_type: str,
    source_id: str,
    paid_value_rub: Decimal = Decimal("0.000000"),
    grant_type: EntitlementGrantType | str = EntitlementGrantType.PAID_PURCHASE,
    purchase_id: int | None = None,
    tariff_version_id: int | None = None,
    device_limit: int = 1,
    as_of: datetime | None = None,
    locked_user: User | None = None,
) -> EntitlementGrant:
    """Queue a new non-overlapping coverage slot in the user's subscription sequence under user lock."""
    now = as_of or now_utc()
    user = locked_user
    if user is None:
        user = await session.scalar(select(User).where(User.id == user_id).with_for_update())
    if user is None:
        raise LookupError(f"User not found: {user_id}")

    active_grants = await entitlement_grants_repo.get_active_grants_for_user(
        session, user_id, for_update=True
    )

    # Permanent check: cannot append to a permanent subscription
    if user.subscription_end and user.subscription_end >= PERMANENT_END_DATE:
        raise ValueError("Cannot append coverage to a permanent subscription")
    for g in active_grants:
        if g.coverage_end >= PERMANENT_END_DATE:
            raise ValueError("Cannot append coverage to a permanent subscription")

    # Determine sequence start: after latest active grant or from now
    future_ends = [g.coverage_end for g in active_grants if g.coverage_end > now]
    if user.subscription_end and user.subscription_end > now:
        future_ends.append(user.subscription_end)
    coverage_start = max(future_ends) if future_ends else now


    is_permanent = duration_hours >= PERMANENT_SUBSCRIPTION_DAYS * 24
    if is_permanent:
        coverage_end = PERMANENT_END_DATE
    else:
        coverage_end = coverage_start + timedelta(hours=duration_hours)

    grant = await entitlement_grants_repo.create_grant(
        session,
        user_id=user_id,
        purchase_id=purchase_id,
        service_type="awg",
        source_type=source_type,
        source_id=source_id,
        grant_type=grant_type,
        coverage_start=coverage_start,
        coverage_end=coverage_end,
        original_duration_hours=duration_hours,
        paid_value_rub=paid_value_rub,
        tariff_version_id=tariff_version_id,
        device_limit=device_limit,
        status=EntitlementGrantStatus.ACTIVE,
    )

    # Update User materialized projection
    user.subscription_end = coverage_end
    if device_limit > user.device_limit:
        user.device_limit = device_limit
    await session.flush()

    return grant


async def replace_awg_coverage(
    session: AsyncSession,
    *,
    user_id: int,
    source_purchase_id: int | None,
    new_paid_hours: int,
    new_paid_value_rub: Decimal,
    retained_bonus_hours: int,
    target_tariff_id: int,
    target_tariff_version_id: int,
    target_device_limit: int,
    as_of: datetime | None = None,
    locked_user: User | None = None,
) -> tuple[EntitlementGrant | None, EntitlementGrant | None]:
    """Execute tariff change replacement under user lock.

    Revokes current active grants and starts fresh coverage at `as_of`.
    Creates separate paid_change and bonus_change grants to preserve distinct economic nature.
    """
    now = as_of or now_utc()
    user = locked_user
    if user is None:
        user = await session.scalar(select(User).where(User.id == user_id).with_for_update())
    if user is None:
        raise LookupError(f"User not found: {user_id}")

    active_grants = await entitlement_grants_repo.get_active_grants_for_user(
        session, user_id, for_update=True
    )

    # Revoke old grants
    if active_grants:
        await entitlement_grants_repo.revoke_grants_by_ids(
            session, [g.id for g in active_grants]
        )

    paid_grant = None
    bonus_grant = None
    current_cursor = now

    if new_paid_hours > 0:
        paid_end = current_cursor + timedelta(hours=new_paid_hours)
        paid_grant = await entitlement_grants_repo.create_grant(
            session,
            user_id=user_id,
            purchase_id=source_purchase_id,
            service_type="awg",
            source_type="tariff_change",
            source_id=f"change:{source_purchase_id or 'direct'}",
            grant_type=EntitlementGrantType.PAID_CHANGE,
            coverage_start=current_cursor,
            coverage_end=paid_end,
            original_duration_hours=new_paid_hours,
            paid_value_rub=new_paid_value_rub,
            tariff_version_id=target_tariff_version_id,
            device_limit=target_device_limit,
            status=EntitlementGrantStatus.ACTIVE,
        )
        current_cursor = paid_end

    if retained_bonus_hours > 0:
        bonus_end = current_cursor + timedelta(hours=retained_bonus_hours)
        bonus_grant = await entitlement_grants_repo.create_grant(
            session,
            user_id=user_id,
            purchase_id=source_purchase_id,
            service_type="awg",
            source_type="tariff_change",
            source_id=f"change_bonus:{source_purchase_id or 'direct'}",
            grant_type=EntitlementGrantType.BONUS_CHANGE,
            coverage_start=current_cursor,
            coverage_end=bonus_end,
            original_duration_hours=retained_bonus_hours,
            paid_value_rub=Decimal("0.000000"),
            tariff_version_id=target_tariff_version_id,
            device_limit=target_device_limit,
            status=EntitlementGrantStatus.ACTIVE,
        )
        current_cursor = bonus_end

    # Update User projection
    user.subscription_end = current_cursor if current_cursor > now else None
    user.current_tariff_id = target_tariff_id
    user.device_limit = target_device_limit
    await session.flush()

    return paid_grant, bonus_grant


async def reduce_awg_coverage(
    session: AsyncSession,
    *,
    user_id: int,
    new_end: datetime,
    locked_user: User | None = None,
) -> None:
    """Cascade revoke/truncate grants from tail when subscription duration is reduced."""
    user = locked_user
    if user is None:
        user = await session.scalar(select(User).where(User.id == user_id).with_for_update())
    if user is None:
        raise LookupError(f"User not found: {user_id}")

    active_grants = await entitlement_grants_repo.get_active_grants_for_user(
        session, user_id, for_update=True
    )
    # Sort descending by coverage_end
    active_grants.sort(key=lambda g: g.coverage_end, reverse=True)

    for grant in active_grants:
        if grant.coverage_start >= new_end:
            # Entire grant is beyond new_end -> full revoke
            await entitlement_grants_repo.set_grant_status(
                session, grant, EntitlementGrantStatus.REVOKED
            )
        elif grant.coverage_start < new_end < grant.coverage_end:
            # Boundary grant: truncate coverage_end
            await entitlement_grants_repo.update_grant_coverage_end(
                session, grant, new_end
            )
        elif grant.coverage_end <= new_end:
            # Remaining tail is within new_end
            break

    user.subscription_end = new_end
    await session.flush()


async def sync_user_subscription_projection(
    session: AsyncSession,
    user_id: int,
    *,
    as_of: datetime | None = None,
    locked_user: User | None = None,
) -> datetime | None:
    """Recompute and update User.subscription_end strictly from active EntitlementGrants."""
    now = as_of or now_utc()
    user = locked_user
    if user is None:
        user = await session.scalar(select(User).where(User.id == user_id).with_for_update())
    if user is None:
        raise LookupError(f"User not found: {user_id}")

    active_grants = await entitlement_grants_repo.get_active_grants_for_user(
        session, user_id, as_of=now, for_update=True
    )
    max_end = max((g.coverage_end for g in active_grants), default=None)
    user.subscription_end = max_end
    await session.flush()
    return max_end
