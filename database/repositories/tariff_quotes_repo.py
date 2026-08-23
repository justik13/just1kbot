"""Persistence boundary for immutable tariff versions and checkout quotes."""

import uuid
from datetime import timedelta
from decimal import Decimal

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import Tariff, TariffQuote, TariffVersion, User
from utils.datetime_helpers import now_utc

QUOTE_LIFETIME = timedelta(minutes=15)


class CheckoutQuoteConflictError(RuntimeError):
    pass


async def lock_checkout_user(session: AsyncSession, user_id: int | object) -> User | None:
    """The sole per-user checkout lock; callers derive state only afterwards."""
    if isinstance(user_id, User):
        uid = user_id.id
    elif hasattr(user_id, "user_id"):
        uid = getattr(user_id, "user_id")
    elif hasattr(user_id, "id") and not isinstance(user_id, int):
        uid = getattr(user_id, "id")
    else:
        uid = user_id
    try:
        uid_int = int(uid)
    except (TypeError, ValueError):
        return None

    await session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": -uid_int})
    return await session.scalar(
        select(User).where(User.id == uid_int).with_for_update()
    )


async def get_or_create_current_version(
    session: AsyncSession, tariff: Tariff
) -> TariffVersion:
    # Serializes version allocation and prevents two distinct snapshots for one edit.
    await session.execute(
        text("SELECT pg_advisory_xact_lock(:key)"), {"key": tariff.id}
    )
    version = await session.scalar(
        select(TariffVersion)
        .where(
            TariffVersion.tariff_id == tariff.id,
            TariffVersion.name_snapshot == tariff.name,
            TariffVersion.duration_hours == tariff.duration_days * 24,
            TariffVersion.device_limit == tariff.device_limit,
            TariffVersion.price_rub == Decimal(tariff.price_rub),
            TariffVersion.currency == "RUB",
        )
        .order_by(TariffVersion.version_number.desc())
        .limit(1)
    )
    if version:
        return version
    number = (
        await session.scalar(
            select(TariffVersion.version_number)
            .where(TariffVersion.tariff_id == tariff.id)
            .order_by(TariffVersion.version_number.desc())
            .limit(1)
        )
        or 0
    ) + 1
    version = TariffVersion(
        tariff_id=tariff.id,
        version_number=number,
        name_snapshot=tariff.name,
        duration_hours=tariff.duration_days * 24,
        device_limit=tariff.device_limit,
        price_rub=Decimal(tariff.price_rub),
        currency="RUB",
    )
    session.add(version)
    await session.flush()
    return version


async def expire_quotes(session: AsyncSession, user_id: int, as_of=None) -> None:
    now = as_of or now_utc()
    rows = (
        await session.scalars(
            select(TariffQuote)
            .where(
                TariffQuote.user_id == user_id,
                TariffQuote.status == "active",
                TariffQuote.expires_at <= now,
            )
            .with_for_update()
        )
    ).all()
    for quote in rows:
        quote.status = "expired"


async def get_active_financial_quotes_for_update(
    session: AsyncSession,
    *,
    user_id: int,
    as_of=None,
) -> list[TariffQuote]:
    """Expire then lock financial quotes; caller already owns the user lock."""
    await expire_quotes(session, user_id, as_of)
    return list(
        (
            await session.scalars(
                select(TariffQuote)
                .where(
                    TariffQuote.user_id == user_id,
                    TariffQuote.status == "active",
                    TariffQuote.operation_type.in_(("purchase", "renew", "change")),
                )
                .order_by(TariffQuote.id)
                .with_for_update()
            )
        ).all()
    )


async def get_or_create_checkout_quote(
    session: AsyncSession,
    *,
    user_id: int,
    tariff: Tariff,
    operation_type: str,
) -> tuple[TariffQuote, TariffVersion]:
    if operation_type not in {"purchase", "renew"}:
        raise ValueError("checkout quote must be purchase or renew")
    active = await get_active_financial_quotes_for_update(session, user_id=user_id)
    if any(row.operation_type == "change" for row in active):
        raise CheckoutQuoteConflictError("active_tariff_change_quote_exists")
    version = await get_or_create_current_version(session, tariff)
    existing = next(
        (
            row
            for row in active
            if row.operation_type in {"purchase", "renew"}
            and row.target_tariff_version_id == version.id
        ),
        None,
    )
    if existing:
        if (
            existing.operation_type != operation_type
            or existing.amount_due_rub != version.price_rub
            or existing.resulting_paid_hours != version.duration_hours
            or existing.resulting_paid_value_rub != version.price_rub
            or existing.currency != version.currency
        ):
            raise CheckoutQuoteConflictError("active_checkout_quote_conflict")
        return existing, version
    now = now_utc()
    quote = TariffQuote(
        public_id=uuid.uuid4(),
        user_id=user_id,
        operation_type=operation_type,
        target_tariff_version_id=version.id,
        current_paid_hours=0,
        current_paid_value_rub=Decimal(0),
        bonus_hours=0,
        amount_due_rub=version.price_rub,
        resulting_paid_hours=version.duration_hours,
        resulting_paid_value_rub=version.price_rub,
        resulting_bonus_hours=0,
        rounding_loss_hours=Decimal(0),
        rounding_loss_value_rub=Decimal(0),
        currency="RUB",
        status="active",
        created_at=now,
        expires_at=now + QUOTE_LIFETIME,
    )
    session.add(quote)
    await session.flush()
    return quote, version
