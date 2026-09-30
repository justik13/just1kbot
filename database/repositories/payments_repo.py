
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from database.models import Payment


async def has_successful_topup(
    session: AsyncSession,
    *,
    user_id: int,
) -> bool:
    """Return True if user has ever had at least one credited top-up payment or paid topup order."""
    from database.models import Order

    stmt = (
        select(func.count(Payment.id))
        .where(
            Payment.user_id == user_id,
            Payment.credited_at.is_not(None),
        )
    )
    count = await session.scalar(stmt)
    if (count or 0) > 0:
        return True

    order_stmt = (
        select(func.count(Order.id))
        .where(
            Order.user_id == user_id,
            Order.service_type == "topup",
            Order.status == "paid",
        )
    )
    order_count = await session.scalar(order_stmt)
    return (order_count or 0) > 0





async def get_user_payments(
    session: AsyncSession, user_id: int, limit: int | None = None
) -> list[Payment]:
    stmt = (
        select(Payment)
        .where(Payment.user_id == user_id)
        .order_by(Payment.created_at.desc())
    )
    if limit is not None:
        stmt = stmt.limit(limit)
    result = await session.execute(stmt)
    return result.scalars().all()


async def get_payment_by_id(
    session: AsyncSession, payment_id: int
) -> Payment | None:
    if not isinstance(payment_id, int) or payment_id < 1 or payment_id > 2_147_483_647:
        return None
    stmt = (
        select(Payment)
        .options(
            selectinload(Payment.user),
        )
        .where(Payment.id == payment_id)
    )
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


async def get_pending_payments_count_for_tariff(
    session: AsyncSession,
    tariff_id: int,
) -> int:
    from database.models import TariffQuote, TariffVersion
    return int(
        await session.scalar(
            select(func.count(TariffQuote.id))
            .join(TariffVersion, TariffQuote.target_tariff_version_id == TariffVersion.id)
            .where(
                TariffVersion.tariff_id == tariff_id,
                TariffQuote.status == "active",
            )
        )
        or 0
    )

