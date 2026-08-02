"""Transaction-neutral subscription balance snapshot API."""
from datetime import datetime

from database.repositories.subscription_balance_repo import load_subscription_balance_history
from services.subscription_balance_projector import (
    EntitlementEvent, LedgerEntry, PaymentSnapshot, TariffVersionSnapshot,
    project_subscription_balance,
)


async def get_subscription_balance_snapshot(session, *, user_id: int, as_of: datetime, for_update: bool = False,
                                            locked_user=None):
    user, entries, ledger, payments, versions = await load_subscription_balance_history(
        session, user_id=user_id, for_update=for_update, locked_user=locked_user)
    entitlement_values = tuple(EntitlementEvent(
        id=x.id, user_id=x.beneficiary_user_id, source_type=x.source_type,
        source_id=x.source_id, entry_type=x.entry_type, hours_delta=x.days_delta * 24,
        created_at=x.created_at, reversed_entry_id=x.reversed_entry_id,
    ) for x in entries)
    ledger_values = tuple(LedgerEntry(
        id=x.id, user_id=x.user_id, entry_type=x.entry_type,
        paid_hours_delta=x.paid_hours_delta, paid_value_rub_delta=x.paid_value_rub_delta,
        currency=x.currency, tariff_version_id=x.tariff_version_id,
        payment_id=x.payment_id, reversal_of_id=x.reversal_of_id,
        quote_id=x.quote_id,
    ) for x in ledger)
    payment_values = {x.id: PaymentSnapshot(
        id=x.id, user_id=x.user_id, tariff_id=x.tariff_id,
        tariff_version_id=x.tariff_version_id, amount=x.amount, currency=x.currency,
        snapshot_duration_hours=x.snapshot_duration_days * 24 if x.snapshot_duration_days else None,
        snapshot_amount=x.snapshot_amount, snapshot_currency=x.snapshot_currency,
    ) for x in payments}
    version_values = {x.id: TariffVersionSnapshot(
        id=x.id, tariff_id=x.tariff_id, duration_hours=x.duration_hours,
        price_rub=x.price_rub, currency=x.currency,
    ) for x in versions}
    return project_subscription_balance(as_of=as_of, subscription_end=user.subscription_end,
        entitlement_events=entitlement_values, ledger_entries=ledger_values,
        tariff_versions=version_values, payments=payment_values)
