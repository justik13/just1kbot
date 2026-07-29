"""Atomic, append-only paid-value ledger operations."""
from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import PaidValueLedgerEntry


async def _insert_or_get(session: AsyncSession, values: dict, conflict_column) -> PaidValueLedgerEntry:
    entry_id = await session.scalar(
        insert(PaidValueLedgerEntry).values(**values).on_conflict_do_nothing(
            index_elements=[conflict_column],
            index_where=text(f"entry_type='{values['entry_type']}'"),
        ).returning(PaidValueLedgerEntry.id)
    )
    if entry_id is not None:
        return await session.get(PaidValueLedgerEntry, entry_id)
    return await session.scalar(select(PaidValueLedgerEntry).where(
        conflict_column == values[conflict_column.key],
        PaidValueLedgerEntry.entry_type == values["entry_type"],
    ))


async def get_or_create_confirmed_payment_entry(
    session: AsyncSession, *, user_id: int, payment_id: int, quote_id: int,
    tariff_version_id: int, paid_hours: int, paid_value_rub,
) -> PaidValueLedgerEntry:
    return await _insert_or_get(session, dict(
        user_id=user_id, source_type="payment", source_id=str(payment_id),
        entry_type="confirmed_payment", paid_hours_delta=paid_hours,
        paid_value_rub_delta=paid_value_rub, currency="RUB",
        tariff_version_id=tariff_version_id, quote_id=quote_id,
        payment_id=payment_id, metadata_={},
    ), PaidValueLedgerEntry.payment_id)


async def get_or_create_conversion_entry(session: AsyncSession, *, user_id: int,
    quote_id: int, tariff_version_id: int, paid_hours_delta: int,
    paid_value_rub_delta) -> PaidValueLedgerEntry:
    return await _insert_or_get(session, dict(
        user_id=user_id, source_type="quote", source_id=str(quote_id),
        entry_type="tariff_conversion", paid_hours_delta=paid_hours_delta,
        paid_value_rub_delta=paid_value_rub_delta, currency="RUB",
        tariff_version_id=tariff_version_id, quote_id=quote_id, metadata_={},
    ), PaidValueLedgerEntry.quote_id)


async def get_or_create_payment_reversal_entry(
    session: AsyncSession, *, original: PaidValueLedgerEntry,
) -> PaidValueLedgerEntry:
    return await _insert_or_get(session, dict(
        user_id=original.user_id, source_type="paid_value_entry",
        source_id=str(original.id), entry_type="payment_reversal",
        paid_hours_delta=-original.paid_hours_delta,
        paid_value_rub_delta=-original.paid_value_rub_delta, currency="RUB",
        tariff_version_id=original.tariff_version_id, quote_id=original.quote_id,
        payment_id=original.payment_id, reversal_of_id=original.id, metadata_={},
    ), PaidValueLedgerEntry.reversal_of_id)
