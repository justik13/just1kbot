
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import Tariff

ALLOWED_TARIFF_UPDATE_FIELDS = {
    "duration_days",
    "device_limit",
    "price_rub",
    "is_active",
    "sort_order",
}


async def get_active_tariffs(
    session: AsyncSession,
    service_type: str = "awg",
) -> list[Tariff]:
    stmt = (
        select(Tariff)
        .where(
            Tariff.is_active.is_(True),
            Tariff.service_type == service_type,
        )
        .order_by(
            Tariff.device_limit,
            Tariff.sort_order,
            Tariff.duration_days,
        )
    )
    result = await session.execute(stmt)
    return result.scalars().all()


async def get_tariff_by_id(
    session: AsyncSession,
    tariff_id: int,
) -> Tariff | None:
    if not isinstance(tariff_id, int) or tariff_id < 1 or tariff_id > 2_147_483_647:
        return None
    stmt = select(Tariff).where(Tariff.id == tariff_id)
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


async def update_tariff(
    session: AsyncSession,
    tariff: Tariff,
    **kwargs,
) -> Tariff:
    for key, value in kwargs.items():
        if key not in ALLOWED_TARIFF_UPDATE_FIELDS:
            continue
        setattr(tariff, key, value)
    await session.flush()
    await session.refresh(tariff)
    return tariff


async def get_tariff_count(session: AsyncSession) -> int:
    stmt = select(func.count(Tariff.id))
    result = await session.execute(stmt)
    return result.scalar_one()


async def get_tariffs_paginated(
    session: AsyncSession,
    page: int = 1,
    per_page: int = 10,
) -> list[Tariff]:
    offset = (page - 1) * per_page
    result = await session.execute(
        select(Tariff)
        .order_by(
            Tariff.device_limit,
            Tariff.sort_order,
            Tariff.duration_days,
        )
        .offset(offset)
        .limit(per_page)
    )
    return result.scalars().all()
