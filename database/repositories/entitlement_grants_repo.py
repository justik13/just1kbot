"""Repository for canonical EntitlementGrant persistence and retrieval."""

from collections.abc import Sequence
from datetime import datetime
from decimal import Decimal

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from config.enums import EntitlementGrantStatus, EntitlementGrantType
from database.models import EntitlementGrant
from utils.datetime_helpers import now_utc


async def create_grant(
    session: AsyncSession,
    *,
    user_id: int,
    coverage_start: datetime,
    coverage_end: datetime,
    original_duration_hours: int,
    source_type: str,
    source_id: str,
    paid_value_rub: Decimal = Decimal("0.000000"),
    grant_type: EntitlementGrantType | str = EntitlementGrantType.PAID_PURCHASE,
    purchase_id: int | None = None,
    service_type: str = "awg",
    tariff_version_id: int | None = None,
    device_limit: int = 1,
    status: EntitlementGrantStatus | str = EntitlementGrantStatus.ACTIVE,
) -> EntitlementGrant:
    grant = EntitlementGrant(
        user_id=user_id,
        purchase_id=purchase_id,
        service_type=service_type,
        source_type=source_type,
        source_id=source_id,
        grant_type=grant_type.value if isinstance(grant_type, EntitlementGrantType) else str(grant_type),
        coverage_start=coverage_start,
        coverage_end=coverage_end,
        original_duration_hours=original_duration_hours,
        paid_value_rub=paid_value_rub,
        tariff_version_id=tariff_version_id,
        device_limit=device_limit,
        status=status.value if isinstance(status, EntitlementGrantStatus) else str(status),
        created_at=now_utc(),
    )
    session.add(grant)
    await session.flush()
    return grant


async def get_active_grants_for_user(
    session: AsyncSession,
    user_id: int,
    *,
    as_of: datetime | None = None,
    for_update: bool = False,
) -> list[EntitlementGrant]:
    stmt = (
        select(EntitlementGrant)
        .where(
            EntitlementGrant.user_id == user_id,
            EntitlementGrant.status == EntitlementGrantStatus.ACTIVE.value,
        )
        .order_by(EntitlementGrant.coverage_start.asc(), EntitlementGrant.id.asc())
    )
    if as_of is not None:
        stmt = stmt.where(EntitlementGrant.coverage_end > as_of)
    if for_update:
        stmt = stmt.with_for_update()
    return list((await session.scalars(stmt)).all())


async def get_grant_by_id(
    session: AsyncSession,
    grant_id: int,
    *,
    for_update: bool = False,
) -> EntitlementGrant | None:
    stmt = select(EntitlementGrant).where(EntitlementGrant.id == grant_id)
    if for_update:
        stmt = stmt.with_for_update()
    return await session.scalar(stmt)


async def get_grants_by_purchase_id(
    session: AsyncSession,
    purchase_id: int,
) -> list[EntitlementGrant]:
    stmt = (
        select(EntitlementGrant)
        .where(EntitlementGrant.purchase_id == purchase_id)
        .order_by(EntitlementGrant.coverage_start.asc(), EntitlementGrant.id.asc())
    )
    return list((await session.scalars(stmt)).all())


async def set_grant_status(
    session: AsyncSession,
    grant: EntitlementGrant | int,
    status: EntitlementGrantStatus | str,
) -> EntitlementGrant:
    g = await get_grant_by_id(session, grant, for_update=True) if isinstance(grant, int) else grant
    if g is None:
        raise LookupError(f"EntitlementGrant not found: {grant}")
    g.status = status.value if isinstance(status, EntitlementGrantStatus) else str(status)
    await session.flush()
    return g


async def update_grant_coverage_end(
    session: AsyncSession,
    grant: EntitlementGrant | int,
    new_coverage_end: datetime,
) -> EntitlementGrant:
    g = await get_grant_by_id(session, grant, for_update=True) if isinstance(grant, int) else grant
    if g is None:
        raise LookupError(f"EntitlementGrant not found: {grant}")
    if new_coverage_end <= g.coverage_start:
        raise ValueError("new_coverage_end must be after coverage_start")
    g.coverage_end = new_coverage_end
    await session.flush()
    return g


async def revoke_grants_by_ids(
    session: AsyncSession,
    grant_ids: Sequence[int],
) -> int:
    if not grant_ids:
        return 0
    stmt = (
        update(EntitlementGrant)
        .where(EntitlementGrant.id.in_(grant_ids))
        .values(status=EntitlementGrantStatus.REVOKED.value)
    )
    result = await session.execute(stmt)
    await session.flush()
    return result.rowcount
