"""Transaction-neutral subscription balance snapshot API."""

from datetime import datetime

from database.repositories.subscription_balance_repo import (
    load_subscription_balance_history,
)
from services.subscription_balance_projector import (
    EntitlementEvent,
    LedgerEntry,
    TariffVersionSnapshot,
    project_subscription_balance,
)


async def get_subscription_balance_snapshot(
    session,
    *,
    user_id: int,
    as_of: datetime,
    for_update: bool = False,
    locked_user=None,
):
    user, entries, ledger, versions = await load_subscription_balance_history(
        session,
        user_id=user_id,
        for_update=for_update,
        locked_user=locked_user,
    )
    entitlement_values = tuple(
        EntitlementEvent(
            id=item.id,
            user_id=item.beneficiary_user_id,
            source_type=item.source_type,
            source_id=item.source_id,
            entry_type=item.entry_type,
            hours_delta=(
                item.hours_delta
                if item.hours_delta is not None
                else item.days_delta * 24
            ),
            created_at=item.created_at,
            reversed_entry_id=item.reversed_entry_id,
            metadata=item.metadata_,
        )
        for item in entries
    )
    ledger_values = tuple(
        LedgerEntry(
            id=item.id,
            user_id=item.user_id,
            entry_type=item.entry_type,
            paid_hours_delta=item.paid_hours_delta,
            paid_value_rub_delta=item.paid_value_rub_delta,
            currency=item.currency,
            tariff_version_id=item.tariff_version_id,
            quote_id=item.quote_id,
            metadata=item.metadata_,
            created_at=item.created_at,
        )
        for item in ledger
    )
    version_values = {
        item.id: TariffVersionSnapshot(
            id=item.id,
            tariff_id=item.tariff_id,
            duration_hours=item.duration_hours,
            price_rub=item.price_rub,
            currency=item.currency,
        )
        for item in versions
    }
    return project_subscription_balance(
        as_of=as_of,
        subscription_end=user.subscription_end,
        entitlement_events=entitlement_values,
        ledger_entries=ledger_values,
        tariff_versions=version_values,
    )
