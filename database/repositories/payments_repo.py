
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from database.models import Payment
from utils.datetime_helpers import now_utc


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
) -> list:
    from datetime import datetime, timezone
    from database.models import Order

    order_stmt = (
        select(Order)
        .where(Order.user_id == user_id, Order.service_type == "topup")
        .order_by(Order.created_at.desc())
    )
    payment_stmt = (
        select(Payment)
        .where(Payment.user_id == user_id)
        .order_by(Payment.created_at.desc())
    )
    if limit is not None:
        order_stmt = order_stmt.limit(limit)
        payment_stmt = payment_stmt.limit(limit)
    orders = list((await session.scalars(order_stmt)).all())
    legacy_payments = list((await session.scalars(payment_stmt)).all())

    def _item_dt(item):
        dt = getattr(item, "created_at", None)
        if dt is None:
            return datetime.min.replace(tzinfo=timezone.utc)
        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc)
        return dt

    combined = sorted(
        [*orders, *legacy_payments],
        key=_item_dt,
        reverse=True,
    )
    if limit is not None:
        return combined[:limit]
    return combined


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
    from database.models import Order

    return int(
        await session.scalar(
            select(func.count(Order.id)).where(
                Order.tariff_id == tariff_id,
                Order.payment_method != "wallet",
                Order.status == "pending",
                or_(
                    Order.expires_at.is_(None),
                    Order.expires_at > now_utc(),
                ),
            )
        )
        or 0
    )

