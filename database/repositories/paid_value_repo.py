"""Atomic append-only paid-value ledger operations for balance purchases."""

from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import PaidValueLedgerEntry


class PaidValueLedgerConflictError(RuntimeError):
    pass


_ECONOMIC_FIELDS = (
    "entry_type",
    "user_id",
    "quote_id",
    "tariff_version_id",
    "paid_hours_delta",
    "paid_value_rub_delta",
    "currency",
    "metadata_",
)


def _verify(entry: PaidValueLedgerEntry, values: dict) -> PaidValueLedgerEntry:
    for field in _ECONOMIC_FIELDS:
        if getattr(entry, field) != values.get(field):
            raise PaidValueLedgerConflictError(f"paid_value_ledger_conflict:{field}")
    return entry


_INDEX_WHERE_MAP = {
    "account_purchase": text("entry_type='account_purchase'"),
    "tariff_conversion": text("entry_type='tariff_conversion'"),
    "manual_adjustment": text("entry_type='manual_adjustment'"),
}


async def _insert_or_get(
    session: AsyncSession, values: dict, conflict_column
) -> PaidValueLedgerEntry:
    entry_type = values.get("entry_type")
    index_where_clause = _INDEX_WHERE_MAP.get(entry_type)
    if index_where_clause is None:
        raise ValueError(f"Invalid entry_type: {entry_type}")
    entry_id = await session.scalar(
        insert(PaidValueLedgerEntry)
        .values(**values)
        .on_conflict_do_nothing(
            index_elements=[conflict_column],
            index_where=index_where_clause,
        )
        .returning(PaidValueLedgerEntry.id)
    )
    if entry_id is not None:
        return _verify(await session.get(PaidValueLedgerEntry, entry_id), values)
    existing = await session.scalar(
        select(PaidValueLedgerEntry).where(
            conflict_column == values[conflict_column.key],
            PaidValueLedgerEntry.entry_type == values["entry_type"],
        )
    )
    if existing is None:
        raise PaidValueLedgerConflictError("paid_value_ledger_conflict:not_visible")
    return _verify(existing, values)


async def get_or_create_account_purchase_entry(
    session: AsyncSession,
    *,
    user_id: int,
    quote_id: int,
    tariff_version_id: int,
    paid_hours: int,
    paid_value_rub,
) -> PaidValueLedgerEntry:
    return await _insert_or_get(
        session,
        dict(
            user_id=user_id,
            source_type="quote",
            source_id=str(quote_id),
            entry_type="account_purchase",
            paid_hours_delta=paid_hours,
            paid_value_rub_delta=paid_value_rub,
            currency="RUB",
            tariff_version_id=tariff_version_id,
            quote_id=quote_id,
            metadata_={},
        ),
        PaidValueLedgerEntry.quote_id,
    )


async def get_or_create_conversion_entry(
    session: AsyncSession,
    *,
    user_id: int,
    quote_id: int,
    tariff_version_id: int,
    paid_hours_delta: int,
    paid_value_rub_delta,
    metadata: dict,
) -> PaidValueLedgerEntry:
    return await _insert_or_get(
        session,
        dict(
            user_id=user_id,
            source_type="quote",
            source_id=str(quote_id),
            entry_type="tariff_conversion",
            paid_hours_delta=paid_hours_delta,
            paid_value_rub_delta=paid_value_rub_delta,
            currency="RUB",
            tariff_version_id=tariff_version_id,
            quote_id=quote_id,
            metadata_=metadata,
        ),
        PaidValueLedgerEntry.quote_id,
    )
