from typing import Optional, List

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from database.models import Payment, PaymentEvent


async def has_successful_topup(
    session: AsyncSession,
    *,
    user_id: int,
) -> bool:
    """Return True if user has ever had at least one credited top-up payment."""
    stmt = (
        select(func.count(Payment.id))
        .where(
            Payment.user_id == user_id,
            Payment.credited_at.is_not(None),
        )
    )
    count = await session.scalar(stmt)
    return (count or 0) > 0





async def get_user_payments(session: AsyncSession, user_id: int) -> List[Payment]:
    stmt = (
        select(Payment)
        .where(Payment.user_id == user_id)
        .order_by(Payment.created_at.desc())
    )
    result = await session.execute(stmt)
    return result.scalars().all()


async def get_payment_by_id(
    session: AsyncSession, payment_id: int
) -> Optional[Payment]:
    stmt = (
        select(Payment)
        .options(
            selectinload(Payment.user),
        )
        .where(Payment.id == payment_id)
    )
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


async def get_payment_by_id_for_update(
    session: AsyncSession, payment_id: int
) -> Optional[Payment]:
    stmt = (
        select(Payment)
        .options(
            selectinload(Payment.user),
        )
        .where(Payment.id == payment_id)
        .with_for_update()
    )
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


async def get_payment_by_id_simple(
    session: AsyncSession, payment_id: int
) -> Optional[Payment]:
    stmt = select(Payment).where(Payment.id == payment_id)
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


async def mark_payment_as_cancelled(
    session: AsyncSession, payment_id: int
) -> bool:
    result = await session.execute(
        update(Payment)
        .where(Payment.id == payment_id, Payment.provider_status == "pending")
        .values(provider_status="canceled")
    )
    await session.flush()
    return result.rowcount > 0


async def log_payment_event(
    session: AsyncSession,
    payment_id: int,
    event_type: str,
    *,
    provider_status: Optional[str] = None,
    reason: Optional[str] = None,
    source: Optional[str] = None,
    details: Optional[str] = None,
) -> PaymentEvent:
    event = PaymentEvent(
        payment_id=payment_id,
        event_type=event_type,
        provider_status=provider_status,
        reason=reason,
        source=source,
        details=details,
    )
    session.add(event)
    await session.flush()
    return event


async def get_pending_payments_count_for_tariff(
    session: AsyncSession,
    tariff_id: int,
) -> int:
    return 0

