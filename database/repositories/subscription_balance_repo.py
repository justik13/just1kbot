"""Read-only persistence boundary for subscription balance projection."""
from sqlalchemy import select

from database.models import EntitlementEntry, PaidValueLedgerEntry, Payment, TariffVersion, User


async def load_subscription_balance_history(session, *, user_id: int, for_update: bool = False,
                                            locked_user=None):
    user = locked_user
    if user is None:
        user_query = select(User).where(User.id == user_id)
        if for_update:
            user_query = user_query.with_for_update()
        user = await session.scalar(user_query)
    if user is None:
        raise LookupError("subscription_balance_user_not_found")

    entitlements = (await session.scalars(select(EntitlementEntry).where(
        EntitlementEntry.beneficiary_user_id == user_id
    ).order_by(EntitlementEntry.created_at, EntitlementEntry.id))).all()
    ledger = (await session.scalars(select(PaidValueLedgerEntry).where(
        PaidValueLedgerEntry.user_id == user_id
    ).order_by(PaidValueLedgerEntry.created_at, PaidValueLedgerEntry.id))).all()
    payment_ids = {row.payment_id for row in ledger if row.payment_id is not None}
    # Referral entitlement source IDs are payment IDs but may belong to another user.
    payment_ids.update(int(row.source_id) for row in entitlements
        if row.source_type == "payment" and row.source_id.isdigit())
    payments = (await session.scalars(select(Payment).where(Payment.id.in_(payment_ids)))).all() if payment_ids else []
    version_ids = {row.tariff_version_id for row in ledger}
    versions = (await session.scalars(select(TariffVersion).where(TariffVersion.id.in_(version_ids)))).all() if version_ids else []
    return user, entitlements, ledger, payments, versions
