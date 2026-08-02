"""Persistence boundary for immutable tariff versions and checkout quotes."""

import uuid
from datetime import timedelta
from decimal import Decimal

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import Payment, Tariff, TariffQuote, TariffVersion, User
from utils.datetime_helpers import now_utc

QUOTE_LIFETIME = timedelta(minutes=15)


class CheckoutQuoteConflictError(RuntimeError):
    pass


async def lock_checkout_user(session: AsyncSession, user_id: int) -> User | None:
    """The sole per-user checkout lock; callers derive state only afterwards."""
    await session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": -user_id})
    return await session.scalar(
        select(User).where(User.id == user_id).with_for_update()
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
            or existing.confirmed_payment_required_rub != version.price_rub
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
        current_paid_value_rub=Decimal("0"),
        bonus_hours=0,
        confirmed_payment_required_rub=version.price_rub,
        resulting_paid_hours=version.duration_hours,
        resulting_paid_value_rub=version.price_rub,
        resulting_bonus_hours=0,
        rounding_loss_hours=Decimal("0"),
        rounding_loss_value_rub=Decimal("0"),
        currency="RUB",
        status="active",
        created_at=now,
        expires_at=now + QUOTE_LIFETIME,
    )
    session.add(quote)
    await session.flush()
    return quote, version


async def reissue_checkout_quote_for_existing_payment(
    session: AsyncSession,
    *,
    quote: TariffQuote,
    payment: Payment,
    tariff: Tariff,
) -> TariffQuote:
    """Create a fresh immutable quote around the same provider payment.

    The previous quote is retained as history. Reissuing is allowed only for a
    recent provider-backed purchase/renew payment whose complete frozen economics
    still match the currently offered tariff.
    """
    now = now_utc()
    version = await session.get(TariffVersion, quote.target_tariff_version_id)
    if (
        quote.operation_type not in {"purchase", "renew"}
        or payment.tariff_quote_id != quote.id
        or quote.payment_id != payment.id
        or payment.user_id != quote.user_id
        or version is None
        or payment.tariff_version_id != version.id
        or payment.tariff_id != version.tariff_id
        or tariff.id != version.tariff_id
        or Decimal(tariff.price_rub) != version.price_rub
        or tariff.duration_days * 24 != version.duration_hours
        or tariff.device_limit != version.device_limit
        or version.currency != "RUB"
        or payment.amount != quote.confirmed_payment_required_rub
        or payment.amount != version.price_rub
        or payment.snapshot_amount != payment.amount
        or payment.snapshot_currency != quote.currency
        or payment.currency != quote.currency
        or payment.snapshot_duration_days != version.duration_hours // 24
        or payment.snapshot_device_limit != version.device_limit
        or payment.external_id is None
        or payment.provider_status not in {"pending", "waiting_for_capture"}
        or now - payment.created_at >= timedelta(hours=24)
    ):
        raise CheckoutQuoteConflictError("existing_payment_snapshot_changed")

    if quote.status == "active":
        if quote.expires_at > now:
            return quote
        quote.status = "expired"
    elif quote.status == "expired":
        pass
    elif (
        quote.status == "cancelled"
        and quote.diagnostic_reason == "checkout_abandoned_by_user"
        and payment.checkout_status == "abandoned"
        and payment.user_cancel_requested_at is not None
    ):
        pass
    else:
        raise CheckoutQuoteConflictError("existing_quote_not_reissuable")

    # Keep the old cancelled/expired quote as immutable history, detach only its
    # reciprocal pointer, and bind the payment to a newly issued active quote.
    quote.payment_id = None
    await session.flush()

    reissued = TariffQuote(
        public_id=uuid.uuid4(),
        user_id=quote.user_id,
        operation_type=quote.operation_type,
        source_tariff_version_id=quote.source_tariff_version_id,
        target_tariff_version_id=quote.target_tariff_version_id,
        current_paid_hours=quote.current_paid_hours,
        current_paid_value_rub=quote.current_paid_value_rub,
        bonus_hours=quote.bonus_hours,
        confirmed_payment_required_rub=quote.confirmed_payment_required_rub,
        resulting_paid_hours=quote.resulting_paid_hours,
        resulting_paid_value_rub=quote.resulting_paid_value_rub,
        resulting_bonus_hours=quote.resulting_bonus_hours,
        rounding_loss_hours=quote.rounding_loss_hours,
        rounding_loss_value_rub=quote.rounding_loss_value_rub,
        currency=quote.currency,
        status="active",
        created_at=now,
        expires_at=now + QUOTE_LIFETIME,
        balance_as_of=quote.balance_as_of,
        source_subscription_end=quote.source_subscription_end,
        source_balance_fingerprint=quote.source_balance_fingerprint,
        source_entitlement_entry_ids=quote.source_entitlement_entry_ids,
        source_ledger_entry_ids=quote.source_ledger_entry_ids,
    )
    session.add(reissued)
    await session.flush()

    payment.tariff_quote_id = reissued.id
    payment.checkout_status = "active"
    payment.user_cancel_requested_at = None
    reissued.payment_id = payment.id
    await session.flush()
    return reissued
