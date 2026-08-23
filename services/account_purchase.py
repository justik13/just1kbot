"""Quote-backed purchase and renewal settlement from the real-money account."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import (
    AccountLedgerEntry,
    EntitlementEntry,
    PaidValueLedgerEntry,
    Tariff,
    TariffQuote,
    TariffVersion,
    User,
)
from database.repositories.account_ledger_repo import (
    AccountBalanceSnapshot,
    InsufficientAccountBalanceError,
    create_purchase_debit,
    get_account_balance,
    whole_rubles,
)
from database.repositories.paid_value_repo import (
    PaidValueLedgerConflictError,
    get_or_create_account_purchase_entry,
)
from database.repositories.profiles_repo import get_user_profiles_count
from database.repositories.tariff_quotes_repo import (
    CheckoutQuoteConflictError,
    get_active_financial_quotes_for_update,
    get_or_create_checkout_quote,
    get_or_create_current_version,
    lock_checkout_user,
)
from services.audit_service import AuditService
from services.referral_bonus import grant_referral_bonus_for_purchase
from services.subscription import SubscriptionService
from utils.datetime_helpers import now_utc


class AccountPurchaseError(RuntimeError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class AccountPurchaseIntent:
    quote: TariffQuote
    version: TariffVersion
    tariff: Tariff
    balance: AccountBalanceSnapshot
    shortage: Decimal


@dataclass(frozen=True)
class AccountPurchaseSettlement:
    quote: TariffQuote
    debit: AccountLedgerEntry
    balance_before: AccountBalanceSnapshot
    balance_after: AccountBalanceSnapshot
    created: bool


def _active(user: User, at) -> bool:
    return bool(user.subscription_end and user.subscription_end > at)


async def prepare_account_purchase(
    session: AsyncSession,
    *,
    user_id: int,
    tariff_id: int,
) -> AccountPurchaseIntent:
    user = await lock_checkout_user(session, user_id)
    if user is None or user.is_deleted:
        raise AccountPurchaseError("purchase_user_missing")
    if user.is_banned:
        raise AccountPurchaseError("purchase_user_banned")
    if user.financial_hold:
        raise AccountPurchaseError("financial_hold")
    balance = await get_account_balance(
        session, user_id=user.id, locked_user=user
    )
    if balance.debt > 0:
        raise AccountPurchaseError("account_debt")
    now = now_utc()

    # Lock active quotes before locking Tariff to preserve DAG: User -> Quote -> Tariff
    active_quotes = await get_active_financial_quotes_for_update(
        session, user_id=user.id, as_of=now
    )
    for active_quote in active_quotes:
        if active_quote.operation_type == "change":
            raise AccountPurchaseError("active_tariff_change_quote_exists")
        target_tariff_id = await session.scalar(
            select(TariffVersion.tariff_id).where(
                TariffVersion.id == active_quote.target_tariff_version_id
            )
        )
        if target_tariff_id != tariff_id:
            active_quote.status = "cancelled"
            active_quote.diagnostic_reason = "replaced_by_new_balance_quote"

    tariff = await session.scalar(
        select(Tariff).where(Tariff.id == tariff_id).with_for_update()
    )
    if tariff is None or not tariff.is_active:
        raise AccountPurchaseError("tariff_unavailable")
    operation_type = "purchase"
    if _active(user, now):
        if user.current_tariff_id is None:
            raise AccountPurchaseError("current_tariff_unknown")
        current = await session.get(Tariff, user.current_tariff_id)
        if current is None:
            raise AccountPurchaseError("current_tariff_unknown")
        if current.device_limit != tariff.device_limit:
            raise AccountPurchaseError("tariff_change_required")
        operation_type = "renew"

    try:
        quote, version = await get_or_create_checkout_quote(
            session,
            user_id=user.id,
            tariff=tariff,
            operation_type=operation_type,
        )
    except CheckoutQuoteConflictError as exc:
        raise AccountPurchaseError(str(exc)) from exc
    price = whole_rubles(version.price_rub)
    shortage = max(Decimal(0), price - balance.available)
    return AccountPurchaseIntent(quote, version, tariff, balance, shortage)


async def get_account_purchase_intent(
    session: AsyncSession,
    *,
    user_id: int,
    quote_public_id,
) -> AccountPurchaseIntent:
    quote = await session.scalar(
        select(TariffQuote).where(
            TariffQuote.public_id == quote_public_id,
            TariffQuote.user_id == user_id,
        )
    )
    if quote is None:
        raise AccountPurchaseError("quote_not_found")
    version = await session.get(TariffVersion, quote.target_tariff_version_id)
    tariff = await session.get(Tariff, version.tariff_id) if version else None
    if version is None or tariff is None:
        raise AccountPurchaseError("tariff_unavailable")
    balance = await get_account_balance(session, user_id=user_id)
    price = whole_rubles(quote.amount_due_rub, allow_zero=True)
    return AccountPurchaseIntent(
        quote,
        version,
        tariff,
        balance,
        max(Decimal(0), price - balance.available),
    )


async def cancel_account_purchase_quote(
    session: AsyncSession,
    *,
    user_id: int,
    quote_public_id,
) -> TariffQuote:
    """Cancel an active balance-purchase quote before settlement.

    The quote is locked before changing its status. A quote that already has
    a purchase debit is never cancelled here: that would hide a financial
    operation from the settlement/idempotency machinery.
    """
    user = await lock_checkout_user(session, user_id)
    if user is None or user.is_deleted:
        raise AccountPurchaseError("purchase_user_missing")
    quote = await session.scalar(
        select(TariffQuote)
        .where(
            TariffQuote.public_id == quote_public_id,
            TariffQuote.user_id == user_id,
        )
        .with_for_update()
    )
    if quote is None:
        raise AccountPurchaseError("quote_not_found")
    if quote.operation_type not in {"purchase", "renew"}:
        raise AccountPurchaseError("quote_operation_mismatch")
    if quote.status != "active":
        raise AccountPurchaseError("quote_not_active")

    existing_debit = await session.scalar(
        select(AccountLedgerEntry.id).where(
            AccountLedgerEntry.entry_type == "purchase_debit",
            AccountLedgerEntry.quote_id == quote.id,
        )
    )
    if existing_debit is not None:
        raise AccountPurchaseError("active_quote_has_existing_debit")

    quote.status = "cancelled"
    quote.diagnostic_reason = "cancelled_by_user"
    await session.flush()
    return quote


async def _get_or_create_entitlement(
    session: AsyncSession,
    *,
    quote: TariffQuote,
    version: TariffVersion,
    debit_id: int,
) -> tuple[EntitlementEntry, bool]:
    days = version.duration_hours // 24
    entry_id = await session.scalar(
        insert(EntitlementEntry)
        .values(
            beneficiary_user_id=quote.user_id,
            source_type="quote",
            source_id=str(quote.id),
            entry_type="account_purchase_grant",
            days_delta=days,
            hours_delta=version.duration_hours,
            device_limit_snapshot=version.device_limit,
            tariff_id_snapshot=version.tariff_id,
            metadata_={
                "operation_type": quote.operation_type,
                "account_debit_id": debit_id,
            },
        )
        .on_conflict_do_nothing(constraint="uq_entitlement_entries_source")
        .returning(EntitlementEntry.id)
    )
    created = entry_id is not None
    entry = (
        await session.get(EntitlementEntry, entry_id)
        if created
        else await session.scalar(
            select(EntitlementEntry).where(
                EntitlementEntry.beneficiary_user_id == quote.user_id,
                EntitlementEntry.source_type == "quote",
                EntitlementEntry.source_id == str(quote.id),
                EntitlementEntry.entry_type == "account_purchase_grant",
            )
        )
    )
    if (
        entry is None
        or entry.days_delta != days
        or entry.device_limit_snapshot != version.device_limit
        or entry.tariff_id_snapshot != version.tariff_id
    ):
        raise AccountPurchaseError("entitlement_idempotency_conflict")
    return entry, created


async def _settled_state(
    session: AsyncSession, quote: TariffQuote
) -> tuple[AccountLedgerEntry | None, bool]:
    debit = await session.scalar(
        select(AccountLedgerEntry).where(
            AccountLedgerEntry.entry_type == "purchase_debit",
            AccountLedgerEntry.quote_id == quote.id,
        )
    )
    paid = await session.scalar(
        select(PaidValueLedgerEntry.id).where(
            PaidValueLedgerEntry.entry_type == "account_purchase",
            PaidValueLedgerEntry.quote_id == quote.id,
        )
    )
    entitlement = await session.scalar(
        select(EntitlementEntry.id).where(
            EntitlementEntry.source_type == "quote",
            EntitlementEntry.source_id == str(quote.id),
            EntitlementEntry.entry_type == "account_purchase_grant",
        )
    )
    return debit, bool(debit and paid and entitlement)


async def _settle_account_purchase(
    session: AsyncSession,
    *,
    user_id: int,
    quote_public_id,
) -> AccountPurchaseSettlement:
    user = await lock_checkout_user(session, user_id)
    if user is None or user.is_deleted or user.is_banned:
        raise AccountPurchaseError("purchase_user_ineligible")
    quote = await session.scalar(
        select(TariffQuote)
        .where(
            TariffQuote.public_id == quote_public_id,
            TariffQuote.user_id == user.id,
        )
        .with_for_update()
    )
    if quote is None:
        raise AccountPurchaseError("quote_not_found")
    existing_debit, complete = await _settled_state(session, quote)
    if quote.status == "consumed":
        if not complete:
            raise AccountPurchaseError("consumed_quote_incomplete")
        current = await get_account_balance(session, user_id=user.id)
        return AccountPurchaseSettlement(
            quote, existing_debit, current, current, False
        )
    if quote.status != "active":
        raise AccountPurchaseError("quote_not_active")
    now = now_utc()
    if quote.expires_at <= now:
        quote.status = "expired"
        raise AccountPurchaseError("quote_expired")
    if quote.operation_type not in {"purchase", "renew"}:
        raise AccountPurchaseError("quote_operation_mismatch")
    if user.financial_hold:
        raise AccountPurchaseError("financial_hold")

    version = await session.get(TariffVersion, quote.target_tariff_version_id)
    if version is None:
        raise AccountPurchaseError("tariff_unavailable")
    tariff = await session.scalar(
        select(Tariff)
        .where(Tariff.id == version.tariff_id)
        .with_for_update()
    )
    if tariff is None or not tariff.is_active:
        raise AccountPurchaseError("tariff_unavailable")
    current_version = await get_or_create_current_version(session, tariff)
    if current_version.id != version.id:
        raise AccountPurchaseError("tariff_price_changed")
    amount = whole_rubles(quote.amount_due_rub)
    if (
        amount != whole_rubles(version.price_rub)
        or quote.currency != "RUB"
        or version.currency != "RUB"
    ):
        raise AccountPurchaseError("quote_price_mismatch")

    active = _active(user, now)
    if quote.operation_type == "purchase" and active:
        raise AccountPurchaseError("subscription_state_changed")
    if quote.operation_type == "renew":
        if not active or user.current_tariff_id is None:
            raise AccountPurchaseError("subscription_state_changed")
        current_tariff = await session.get(Tariff, user.current_tariff_id)
        if (
            current_tariff is None
            or current_tariff.device_limit != version.device_limit
        ):
            raise AccountPurchaseError("subscription_state_changed")
    profiles = await get_user_profiles_count(session, user.id)
    if profiles > version.device_limit:
        raise AccountPurchaseError("too_many_devices")

    before = await get_account_balance(session, user_id=user.id)
    if before.debt > 0:
        raise AccountPurchaseError("account_debt")
    try:
        debit, debit_created = await create_purchase_debit(
            session,
            user_id=user.id,
            quote_id=quote.id,
            amount=amount,
        )
    except InsufficientAccountBalanceError as exc:
        raise AccountPurchaseError("insufficient_balance") from exc
    if not debit_created:
        raise AccountPurchaseError("active_quote_has_existing_debit")
    try:
        await get_or_create_account_purchase_entry(
            session,
            user_id=user.id,
            quote_id=quote.id,
            tariff_version_id=version.id,
            paid_hours=version.duration_hours,
            paid_value_rub=amount,
        )
    except PaidValueLedgerConflictError as exc:
        raise AccountPurchaseError("paid_value_ledger_conflict") from exc
    _, entitlement_created = await _get_or_create_entitlement(
        session, quote=quote, version=version, debit_id=debit.id
    )
    if not entitlement_created:
        raise AccountPurchaseError("active_quote_has_existing_entitlement")
    days, remainder = divmod(version.duration_hours, 24)
    if remainder or days <= 0:
        raise AccountPurchaseError("tariff_duration_not_whole_days")
    updated = await SubscriptionService.extend_subscription(
        session,
        user.telegram_id,
        days,
        version.device_limit,
        version.tariff_id,
    )
    if updated is None:
        raise AccountPurchaseError("purchase_user_missing")
    quote.status = "consumed"
    quote.consumed_at = now
    user.last_payment_at = now
    await grant_referral_bonus_for_purchase(
        session,
        purchaser_user_id=user.id,
        quote_id=quote.id,
        purchase_amount=amount,
    )
    await AuditService.log_action(
        session,
        admin_id=0,
        action="ACCOUNT_PURCHASE_SETTLED",
        target_type="User",
        target_id=user.id,
        details=(
            f"operation={quote.operation_type}, debit={debit.id}, "
            f"amount={int(amount)} RUB"
        ),
    )
    after = await get_account_balance(session, user_id=user.id)
    await session.flush()
    return AccountPurchaseSettlement(quote, debit, before, after, True)


async def settle_account_purchase(
    session: AsyncSession,
    *,
    user_id: int,
    quote_public_id,
) -> AccountPurchaseSettlement:
    """Rollback every local side effect when a caught domain error is raised."""
    async with session.begin_nested():
        return await _settle_account_purchase(
            session,
            user_id=user_id,
            quote_public_id=quote_public_id,
        )
