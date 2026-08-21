"""Atomic paid-value conversion and tariff change from account balance."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import timedelta, timezone
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
    get_or_create_conversion_entry,
)
from database.repositories.profiles_repo import get_user_profiles_count
from database.repositories.tariff_quotes_repo import (
    get_or_create_current_version,
    lock_checkout_user,
)
from services.audit_service import AuditService
from services.referral_bonus import grant_referral_bonus_for_purchase
from services.subscription import SubscriptionService
from services.subscription_balance_service import get_subscription_balance_snapshot
from services.tariff_change_quote import balance_snapshot_fingerprint
from services.tariff_value_calculator import (
    TariffCalculationError,
    TariffVersionSnapshot,
    calculate_tariff_value,
)
from utils.datetime_helpers import now_utc

logger = logging.getLogger(__name__)


class AccountTariffChangeError(RuntimeError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class AccountTariffChangeIntent:
    quote: TariffQuote
    source_version: TariffVersion
    target_version: TariffVersion
    target_tariff: Tariff
    balance: AccountBalanceSnapshot
    shortage: Decimal


@dataclass(frozen=True)
class AccountTariffChangeSettlement:
    quote: TariffQuote
    debit: AccountLedgerEntry | None
    conversion: PaidValueLedgerEntry
    entitlement: EntitlementEntry
    balance_before: AccountBalanceSnapshot
    balance_after: AccountBalanceSnapshot
    created: bool


def _timestamp(value) -> str | None:
    if value is None:
        return None
    return (
        value.astimezone(timezone.utc)
        .isoformat(timespec="microseconds")
        .replace("+00:00", "Z")
    )


def _decimal(value) -> str:
    fixed = format(Decimal(value), "f")
    return fixed.rstrip("0").rstrip(".") if "." in fixed else fixed


async def get_account_tariff_change_intent(
    session: AsyncSession,
    *,
    user_id: int,
    quote_public_id,
) -> AccountTariffChangeIntent:
    quote = await session.scalar(
        select(TariffQuote).where(
            TariffQuote.public_id == quote_public_id,
            TariffQuote.user_id == user_id,
            TariffQuote.operation_type == "change",
        )
    )
    if quote is None:
        raise AccountTariffChangeError("quote_not_found")
    source = await session.get(TariffVersion, quote.source_tariff_version_id)
    target = await session.get(TariffVersion, quote.target_tariff_version_id)
    tariff = await session.get(Tariff, target.tariff_id) if target else None
    if source is None or target is None or tariff is None:
        raise AccountTariffChangeError("tariff_unavailable")
    balance = await get_account_balance(session, user_id=user_id)
    amount = whole_rubles(
        quote.amount_due_rub, allow_zero=True
    )
    return AccountTariffChangeIntent(
        quote,
        source,
        target,
        tariff,
        balance,
        max(Decimal(0), amount - balance.available),
    )


async def _settled_state(session, quote, amount):
    debit = await session.scalar(
        select(AccountLedgerEntry).where(
            AccountLedgerEntry.entry_type == "purchase_debit",
            AccountLedgerEntry.quote_id == quote.id,
        )
    )
    conversion = await session.scalar(
        select(PaidValueLedgerEntry).where(
            PaidValueLedgerEntry.entry_type == "tariff_conversion",
            PaidValueLedgerEntry.quote_id == quote.id,
        )
    )
    entitlement = await session.scalar(
        select(EntitlementEntry).where(
            EntitlementEntry.beneficiary_user_id == quote.user_id,
            EntitlementEntry.source_type == "quote",
            EntitlementEntry.source_id == str(quote.id),
            EntitlementEntry.entry_type == "tariff_change",
        )
    )
    complete = bool(
        conversion
        and entitlement
        and ((amount == 0 and debit is None) or (amount > 0 and debit))
    )
    return debit, conversion, entitlement, complete


async def _settle_account_tariff_change(
    session: AsyncSession,
    *,
    user_id: int,
    quote_public_id,
) -> AccountTariffChangeSettlement:
    user = await lock_checkout_user(session, user_id)
    if user is None or user.is_deleted or user.is_banned:
        raise AccountTariffChangeError("change_user_ineligible")
    quote = await session.scalar(
        select(TariffQuote)
        .where(
            TariffQuote.public_id == quote_public_id,
            TariffQuote.user_id == user.id,
        )
        .with_for_update()
    )
    if quote is None:
        raise AccountTariffChangeError("quote_not_found")
    if quote.operation_type != "change":
        raise AccountTariffChangeError("quote_operation_mismatch")
    try:
        amount = whole_rubles(
            quote.amount_due_rub, allow_zero=True
        )
    except ValueError as exc:
        raise AccountTariffChangeError("quote_amount_invalid") from exc
    debit, conversion, entitlement, complete = await _settled_state(
        session, quote, amount
    )
    if quote.status == "consumed":
        if not complete:
            raise AccountTariffChangeError("consumed_quote_incomplete")
        current = await get_account_balance(session, user_id=user.id)
        return AccountTariffChangeSettlement(
            quote,
            debit,
            conversion,
            entitlement,
            current,
            current,
            False,
        )
    if quote.status != "active":
        raise AccountTariffChangeError("quote_not_active")
    now = now_utc()
    if quote.expires_at is None or now >= quote.expires_at:
        quote.status = "expired"
        raise AccountTariffChangeError("quote_expired")
    if user.financial_hold:
        raise AccountTariffChangeError("financial_hold")
    if user.subscription_end is None or user.subscription_end <= now:
        raise AccountTariffChangeError("subscription_state_changed")

    source = await session.get(TariffVersion, quote.source_tariff_version_id)
    target = await session.get(TariffVersion, quote.target_tariff_version_id)
    if source is None or target is None or source.tariff_id == target.tariff_id:
        raise AccountTariffChangeError("quote_tariff_version_invalid")
    if user.current_tariff_id is None:
        user.current_tariff_id = source.tariff_id
        await session.flush()
    elif user.current_tariff_id != source.tariff_id:
        raise AccountTariffChangeError("subscription_state_changed")
    tariff = await session.scalar(
        select(Tariff).where(Tariff.id == target.tariff_id).with_for_update()
    )
    if tariff is None or not tariff.is_active:
        raise AccountTariffChangeError("tariff_unavailable")
    current_target = await get_or_create_current_version(session, tariff)
    if current_target.id != target.id:
        raise AccountTariffChangeError("tariff_price_changed")
    if quote.currency != "RUB" or source.currency != "RUB" or target.currency != "RUB":
        raise AccountTariffChangeError("quote_currency_invalid")
    profiles = await get_user_profiles_count(session, user.id)
    if profiles > target.device_limit:
        raise AccountTariffChangeError("too_many_devices")
    is_requested_downgrade = target.device_limit < source.device_limit
    if is_requested_downgrade:
        last_change_entry = await session.scalar(
            select(EntitlementEntry)
            .where(
                EntitlementEntry.beneficiary_user_id == user.id,
                EntitlementEntry.entry_type == "tariff_change",
            )
            .order_by(EntitlementEntry.created_at.desc())
            .limit(1)
        )
        if last_change_entry is not None:
            last_change_at = last_change_entry.created_at
            if last_change_at.tzinfo is None:
                last_change_at = last_change_at.replace(tzinfo=timezone.utc)
            meta = last_change_entry.metadata_ or {}
            was_last_downgrade = bool(meta.get("is_downgrade", False))
            if was_last_downgrade and (now - last_change_at) < timedelta(hours=24):
                raise AccountTariffChangeError("change_cooldown_active")

    snapshot = await get_subscription_balance_snapshot(
        session,
        user_id=user.id,
        as_of=quote.balance_as_of,
        locked_user=user,
    )
    if not snapshot.tracked or snapshot.remaining_paid_value_rub is None:
        logger.warning(
            "settle_account_tariff_change untracked balance: user_id=%s, snapshot_failure_code=%s, coverage_end=%s, subscription_end=%s",
            user.id,
            snapshot.failure_code,
            snapshot.coverage_end,
            user.subscription_end,
        )
        raise AccountTariffChangeError("subscription_balance_untracked")
    fingerprint = balance_snapshot_fingerprint(
        user_id=user.id,
        subscription_end=user.subscription_end,
        snapshot=snapshot,
    )
    mismatches = []
    if _timestamp(quote.source_subscription_end) != _timestamp(user.subscription_end):
        mismatches.append(f"sub_end({_timestamp(quote.source_subscription_end)}!={_timestamp(user.subscription_end)})")
    if quote.source_balance_fingerprint != fingerprint:
        mismatches.append(f"fingerprint({quote.source_balance_fingerprint[:8]}!={fingerprint[:8]})")
    if sorted(quote.source_entitlement_entry_ids or []) != sorted(snapshot.source_entitlement_entry_ids):
        mismatches.append(f"entitlement_ids({quote.source_entitlement_entry_ids}!={snapshot.source_entitlement_entry_ids})")
    if sorted(quote.source_ledger_entry_ids or []) != sorted(snapshot.source_ledger_entry_ids):
        mismatches.append(f"ledger_ids({quote.source_ledger_entry_ids}!={snapshot.source_ledger_entry_ids})")
    if quote.current_paid_hours != snapshot.remaining_paid_hours:
        mismatches.append(f"paid_hours({quote.current_paid_hours}!={snapshot.remaining_paid_hours})")
    if (
        quote.current_paid_value_rub.quantize(Decimal("1.000000"))
        != snapshot.remaining_paid_value_rub.quantize(Decimal("1.000000"))
    ):
        mismatches.append(f"paid_value({quote.current_paid_value_rub}!={snapshot.remaining_paid_value_rub})")
    if quote.bonus_hours != snapshot.remaining_bonus_hours:
        mismatches.append(f"bonus_hours({quote.bonus_hours}!={snapshot.remaining_bonus_hours})")

    if mismatches:
        logger.warning(
            "settle_account_tariff_change quote_source_history_changed: user_id=%s, mismatches=%s",
            user.id,
            ", ".join(mismatches),
        )
        raise AccountTariffChangeError("quote_source_history_changed")
    try:
        calculation = calculate_tariff_value(
            operation_type="change",
            source_paid_hours=snapshot.remaining_paid_hours,
            source_paid_value_rub=snapshot.remaining_paid_value_rub,
            source_tariff=TariffVersionSnapshot(
                source.tariff_id,
                source.id,
                source.duration_hours,
                source.price_rub,
                source.currency,
            ),
            target_tariff=TariffVersionSnapshot(
                target.tariff_id,
                target.id,
                target.duration_hours,
                target.price_rub,
                target.currency,
            ),
            confirmed_additional_payment_rub=amount,
            bonus_hours=snapshot.remaining_bonus_hours,
        )
    except TariffCalculationError as exc:
        raise AccountTariffChangeError("quote_economics_invalid") from exc
    if (
        calculation.required_payment_rub != amount
        or calculation.resulting_paid_hours != quote.resulting_paid_hours
        or calculation.paid_value_after_rub.quantize(Decimal("1.000000")) != quote.resulting_paid_value_rub.quantize(Decimal("1.000000"))
        or calculation.retained_bonus_hours != quote.resulting_bonus_hours
        or calculation.rounding_loss_hours.quantize(Decimal("1.000000000000")) != quote.rounding_loss_hours.quantize(Decimal("1.000000000000"))
        or calculation.rounding_loss_value_rub.quantize(Decimal("1.000000")) != quote.rounding_loss_value_rub.quantize(Decimal("1.000000"))
    ):
        raise AccountTariffChangeError("quote_economics_changed")

    before = await get_account_balance(
        session, user_id=user.id, locked_user=user
    )
    if before.debt > 0:
        raise AccountTariffChangeError("account_debt")
    if before.available < amount:
        raise AccountTariffChangeError("insufficient_balance")
    debit = None
    if amount:
        try:
            debit, debit_created = await create_purchase_debit(
                session,
                user_id=user.id,
                quote_id=quote.id,
                amount=amount,
            )
        except InsufficientAccountBalanceError as exc:
            raise AccountTariffChangeError("insufficient_balance") from exc
        if not debit_created:
            raise AccountTariffChangeError("active_quote_has_existing_debit")

    metadata = {
        "operation_type": "change",
        "balance_as_of": _timestamp(quote.balance_as_of),
        "source_subscription_end": _timestamp(quote.source_subscription_end),
        "source_balance_fingerprint": quote.source_balance_fingerprint,
        "source_entitlement_entry_ids": sorted(
            quote.source_entitlement_entry_ids or []
        ),
        "source_ledger_entry_ids": sorted(quote.source_ledger_entry_ids or []),
        "current_paid_hours": quote.current_paid_hours,
        "current_paid_value_rub": _decimal(quote.current_paid_value_rub),
        "current_bonus_hours": quote.bonus_hours,
        "resulting_paid_hours": quote.resulting_paid_hours,
        "resulting_paid_value_rub": _decimal(quote.resulting_paid_value_rub),
        "resulting_bonus_hours": quote.resulting_bonus_hours,
        "account_debit_id": debit.id if debit else None,
        "is_downgrade": target.device_limit < source.device_limit,
    }
    try:
        conversion = await get_or_create_conversion_entry(
            session,
            user_id=user.id,
            quote_id=quote.id,
            tariff_version_id=target.id,
            paid_hours_delta=(
                quote.resulting_paid_hours - quote.current_paid_hours
            ),
            paid_value_rub_delta=(
                quote.resulting_paid_value_rub - quote.current_paid_value_rub
            ),
            metadata=metadata,
        )
    except PaidValueLedgerConflictError as exc:
        raise AccountTariffChangeError("paid_value_ledger_conflict") from exc
    entitlement_id = await session.scalar(
        insert(EntitlementEntry)
        .values(
            beneficiary_user_id=user.id,
            source_type="quote",
            source_id=str(quote.id),
            entry_type="tariff_change",
            days_delta=0,
            hours_delta=(
                quote.resulting_paid_hours + quote.resulting_bonus_hours
            ),
            device_limit_snapshot=target.device_limit,
            tariff_id_snapshot=target.tariff_id,
            metadata_=metadata,
            created_at=now,
        )
        .on_conflict_do_nothing(constraint="uq_entitlement_entries_source")
        .returning(EntitlementEntry.id)
    )
    if entitlement_id is None:
        raise AccountTariffChangeError("active_quote_has_existing_entitlement")
    entitlement = await session.get(EntitlementEntry, entitlement_id)
    new_end = quote.balance_as_of + timedelta(
        hours=quote.resulting_paid_hours + quote.resulting_bonus_hours
    )
    try:
        await SubscriptionService.replace_subscription(
            session,
            user,
            subscription_end=new_end,
            device_limit=target.device_limit,
            tariff_id=target.tariff_id,
        )
    except ValueError as exc:
        raise AccountTariffChangeError("subscription_state_changed") from exc
    quote.status = "consumed"
    quote.consumed_at = now
    user.last_payment_at = now
    if debit is not None:
        await grant_referral_bonus_for_purchase(
            session,
            purchaser_user_id=user.id,
            quote_id=quote.id,
            purchase_amount=amount,
        )
    await AuditService.log_action(
        session,
        admin_id=0,
        action="ACCOUNT_TARIFF_CHANGE_SETTLED",
        target_type="User",
        target_id=user.id,
        details=(
            f"debit={debit.id if debit else 'none'}, "
            f"conversion={conversion.id}, amount={int(amount)} RUB"
        ),
    )
    after = await get_account_balance(session, user_id=user.id)
    await session.flush()
    return AccountTariffChangeSettlement(
        quote,
        debit,
        conversion,
        entitlement,
        before,
        after,
        True,
    )


async def settle_account_tariff_change(
    session: AsyncSession,
    *,
    user_id: int,
    quote_public_id,
) -> AccountTariffChangeSettlement:
    """Rollback the complete conversion when callers catch a domain error."""
    async with session.begin_nested():
        return await _settle_account_tariff_change(
            session,
            user_id=user_id,
            quote_public_id=quote_public_id,
        )