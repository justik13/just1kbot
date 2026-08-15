"""Referral rewards as spendable, separately attributable account credits."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from decimal import Decimal, ROUND_DOWN

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import AccountLedgerAllocation, AccountLedgerEntry, User
from database.repositories.account_ledger_repo import (
    _credit_capacity,
    get_account_balance,
)

_logger = logging.getLogger(__name__)

REFERRAL_BONUS_RATE = Decimal("0.10")
REFERRAL_BONUS_SOURCE = "referral_bonus"


@dataclass(frozen=True)
class ReferralBonusGrantResult:
    referrer_bonus: Decimal = Decimal("0")
    purchaser_welcome_bonus: Decimal = Decimal("0")

    def __iter__(self):
        yield self.referrer_bonus
        yield self.purchaser_welcome_bonus

    def __int__(self) -> int:
        return int(self.referrer_bonus)

    def __float__(self) -> float:
        return float(self.referrer_bonus)

    def __eq__(self, other):
        if isinstance(other, Decimal):
            return self.referrer_bonus == other
        if isinstance(other, (int, float)):
            return self.referrer_bonus == Decimal(str(other))
        if isinstance(other, tuple) and len(other) == 2:
            return (self.referrer_bonus, self.purchaser_welcome_bonus) == other
        if isinstance(other, ReferralBonusGrantResult):
            return (
                self.referrer_bonus == other.referrer_bonus
                and self.purchaser_welcome_bonus == other.purchaser_welcome_bonus
            )
        return False

    def __hash__(self):
        return hash((self.referrer_bonus, self.purchaser_welcome_bonus))

    def __add__(self, other):
        if isinstance(other, ReferralBonusGrantResult):
            return self.referrer_bonus + other.referrer_bonus
        return self.referrer_bonus + other

    def __radd__(self, other):
        return other + self.referrer_bonus


def calculate_referral_bonus(purchase_amount: object) -> Decimal:
    """Return 10% of a purchase, rounded down to whole rubles."""
    amount = Decimal(str(purchase_amount))
    if not amount.is_finite() or amount <= 0:
        return Decimal("0")
    return (amount * REFERRAL_BONUS_RATE).quantize(
        Decimal("1"), rounding=ROUND_DOWN
    )


async def is_first_topup_eligible(
    session: AsyncSession,
    *,
    user_id: int,
) -> bool:
    """Return True if user was referred by someone and has no completed top-ups yet."""
    purchaser = await session.scalar(
        select(User).where(
            User.id == user_id,
            User.is_deleted.is_(False),
        )
    )
    if purchaser is None or purchaser.referred_by is None:
        return False
    if purchaser.referred_by == purchaser.telegram_id:
        return False

    from database.models import Payment
    from sqlalchemy import func

    count = await session.scalar(
        select(func.count(Payment.id)).where(
            Payment.user_id == user_id,
            Payment.credited_at.is_not(None),
            Payment.fulfillment_status == "succeeded",
        )
    )
    return (count or 0) == 0


async def grant_referral_bonus_for_topup(
    session: AsyncSession,
    *,
    purchaser_user_id: int,
    payment_id: int,
    topup_amount: object,
) -> ReferralBonusGrantResult:
    """Credit the referrer with 10% of a top-up, and credit the purchaser with 10% if it is their first top-up."""
    bonus = calculate_referral_bonus(topup_amount)
    if bonus <= 0:
        return ReferralBonusGrantResult(
            referrer_bonus=Decimal("0"),
            purchaser_welcome_bonus=Decimal("0"),
        )

    purchaser = await session.scalar(
        select(User)
        .where(
            User.id == purchaser_user_id,
            User.is_deleted.is_(False),
        )
        .with_for_update()
    )
    if purchaser is None or purchaser.referred_by is None:
        return ReferralBonusGrantResult(
            referrer_bonus=Decimal("0"),
            purchaser_welcome_bonus=Decimal("0"),
        )

    if purchaser.referred_by == purchaser.telegram_id:
        return ReferralBonusGrantResult(
            referrer_bonus=Decimal("0"),
            purchaser_welcome_bonus=Decimal("0"),
        )

    referrer = await session.scalar(
        select(User)
        .where(
            User.telegram_id == purchaser.referred_by,
            User.is_deleted.is_(False),
        )
        .with_for_update()
    )
    if referrer is None or referrer.is_banned:
        return ReferralBonusGrantResult(
            referrer_bonus=Decimal("0"),
            purchaser_welcome_bonus=Decimal("0"),
        )

    # 1. Grant 10% bonus to referrer for every top-up
    referrer_bonus_granted = Decimal("0")
    idempotency_key = f"referral-bonus:topup:{payment_id}:{referrer.id}"
    existing = await session.scalar(
        select(AccountLedgerEntry).where(
            AccountLedgerEntry.idempotency_key == idempotency_key
        )
    )
    if existing is None:
        session.add(
            AccountLedgerEntry(
                user_id=referrer.id,
                entry_type="admin_adjustment",
                amount=bonus,
                currency="RUB",
                payment_id=None,
                quote_id=None,
                reversal_of_id=None,
                idempotency_key=idempotency_key,
                metadata_={
                    "source_type": REFERRAL_BONUS_SOURCE,
                    "referrer_user_id": referrer.id,
                    "referred_user_id": purchaser.id,
                    "referred_telegram_id": purchaser.telegram_id,
                    "topup_payment_id": payment_id,
                    "bonus_rate": str(REFERRAL_BONUS_RATE),
                },
            )
        )
        referrer_bonus_granted = bonus
        from services.audit_service import AuditService
        await AuditService.log_action(
            session,
            admin_id=0,
            action="REFERRAL_BONUS_GRANTED",
            target_type="user",
            target_id=referrer.id,
            details={
                "amount": int(bonus),
                "from_user_id": purchaser.id,
                "payment_id": payment_id,
            },
        )
    else:
        referrer_bonus_granted = existing.amount

    # 2. Check if this is the purchaser's first successful top-up. If so, grant purchaser +10% bonus as well.
    purchaser_welcome_granted = Decimal("0")
    from database.models import Payment
    from sqlalchemy import func

    prev_credited = await session.scalar(
        select(func.count(Payment.id)).where(
            Payment.user_id == purchaser.id,
            Payment.credited_at.is_not(None),
            Payment.fulfillment_status == "succeeded",
            Payment.id != payment_id,
        )
    )
    if (prev_credited or 0) == 0:
        purchaser_key = f"referral-bonus:first-topup-welcome:{purchaser.id}"
        existing_purchaser = await session.scalar(
            select(AccountLedgerEntry).where(
                AccountLedgerEntry.idempotency_key == purchaser_key
            )
        )
        if existing_purchaser is None:
            session.add(
                AccountLedgerEntry(
                    user_id=purchaser.id,
                    entry_type="admin_adjustment",
                    amount=bonus,
                    currency="RUB",
                    payment_id=None,
                    quote_id=None,
                    reversal_of_id=None,
                    idempotency_key=purchaser_key,
                    metadata_={
                        "source_type": REFERRAL_BONUS_SOURCE,
                        "reason": "first_topup_welcome",
                        "purchaser_user_id": purchaser.id,
                        "referrer_telegram_id": purchaser.referred_by,
                        "topup_payment_id": payment_id,
                        "bonus_rate": str(REFERRAL_BONUS_RATE),
                    },
                )
            )
            purchaser_welcome_granted = bonus
            from services.audit_service import AuditService
            await AuditService.log_action(
                session,
                admin_id=0,
                action="WELCOME_BONUS_GRANTED",
                target_type="user",
                target_id=purchaser.id,
                details={
                    "amount": int(bonus),
                    "payment_id": payment_id,
                },
            )
        else:
            purchaser_welcome_granted = existing_purchaser.amount

    await session.flush()
    return ReferralBonusGrantResult(
        referrer_bonus=referrer_bonus_granted,
        purchaser_welcome_bonus=purchaser_welcome_granted,
    )


async def grant_referral_bonus_for_purchase(
    session: AsyncSession,
    *,
    purchaser_user_id: int,
    quote_id: int,
    purchase_amount: object,
) -> Decimal:
    """Legacy alias for backward compatibility."""
    return Decimal("0")


async def reverse_referral_bonus_for_topup(
    session: AsyncSession,
    *,
    payment_id: int,
) -> Decimal:
    """Debit/reverse the referral bonus previously credited for a top-up if the top-up is refunded."""
    from database.models import Payment

    payment = await session.get(Payment, payment_id)
    if payment is None or payment.user_id is None:
        return Decimal("0")

    # Refund processing can race with another refund finalizer or with a top-up
    # bonus grant. Use the same purchaser -> referrer lock order as the grant
    # path so the matching credit and its reversal are observed atomically.
    purchaser = await session.scalar(
        select(User)
        .where(User.id == payment.user_id)
        .with_for_update()
    )
    if purchaser is None or not purchaser.referred_by:
        return Decimal("0")

    referrer = await session.scalar(
        select(User)
        .where(User.telegram_id == purchaser.referred_by)
        .with_for_update()
    )
    if referrer is None:
        return Decimal("0")

    total_reversed = Decimal("0")

    # 1. Reverse referrer bonus for this top-up if present. The purchaser and
    # referrer row locks serialize this lookup with bonus creation, preventing
    # two refund finalizers from both attempting the same unique reversal.
    candidate_credits = (
        await session.scalars(
            select(AccountLedgerEntry).where(
                AccountLedgerEntry.user_id == referrer.id,
                AccountLedgerEntry.entry_type == "admin_adjustment",
                AccountLedgerEntry.amount > 0,
                AccountLedgerEntry.reversal_of_id.is_(None),
            )
        )
    ).all()
    matching_credit = next(
        (
            c for c in candidate_credits
            if (c.metadata_ or {}).get("topup_payment_id") == payment_id
            and (c.metadata_ or {}).get("source_type") == REFERRAL_BONUS_SOURCE
        ),
        None,
    )

    if matching_credit is not None:
        idempotency_key = f"referral-bonus-reversal:topup:{payment_id}:{matching_credit.user_id}"
        existing = await session.scalar(
            select(AccountLedgerEntry).where(
                AccountLedgerEntry.idempotency_key == idempotency_key
            )
        )
        if existing is None:
            reversal_amount = -abs(Decimal(matching_credit.amount))
            entry = AccountLedgerEntry(
                user_id=matching_credit.user_id,
                entry_type="admin_adjustment",
                amount=reversal_amount,
                currency="RUB",
                payment_id=None,
                quote_id=None,
                reversal_of_id=None,
                idempotency_key=idempotency_key,
                metadata_={
                    "source_type": REFERRAL_BONUS_SOURCE,
                    "reason": "topup_refund_reversal",
                    "topup_payment_id": payment_id,
                    "original_credit_id": matching_credit.id,
                },
            )
            session.add(entry)
            await session.flush()
            reversal_capacity = await _credit_capacity(session, matching_credit)
            allocation_amount = min(abs(reversal_amount), reversal_capacity)
            if allocation_amount > 0:
                session.add(
                    AccountLedgerAllocation(
                        user_id=matching_credit.user_id,
                        credit_entry_id=matching_credit.id,
                        debit_entry_id=entry.id,
                        amount=allocation_amount,
                        idempotency_key=f"alloc:{idempotency_key}",
                    )
                )
            _logger.debug(
                "Referral bonus reversal created for referrer user_id=%s, payment_id=%s, amount=%s",
                matching_credit.user_id, payment_id, reversal_amount,
            )
            total_reversed += abs(reversal_amount)
        else:
            _logger.debug(
                "Referral bonus reversal already exists for referrer, payment_id=%s", payment_id
            )
            total_reversed += Decimal(abs(existing.amount))
    else:
        _logger.debug(
            "No referral bonus credit found for referrer, payment_id=%s", payment_id
        )

    # 2. Reverse purchaser welcome bonus for this top-up if present
    purchaser_credits = (
        await session.scalars(
            select(AccountLedgerEntry).where(
                AccountLedgerEntry.user_id == purchaser.id,
                AccountLedgerEntry.entry_type == "admin_adjustment",
                AccountLedgerEntry.amount > 0,
                AccountLedgerEntry.reversal_of_id.is_(None),
            )
        )
    ).all()
    matching_purchaser_credit = next(
        (
            c for c in purchaser_credits
            if (c.metadata_ or {}).get("topup_payment_id") == payment_id
            and (c.metadata_ or {}).get("reason") == "first_topup_welcome"
        ),
        None,
    )

    if matching_purchaser_credit is not None:
        purchaser_rev_key = f"referral-bonus-reversal:first-topup-welcome:{payment_id}:{purchaser.id}"
        existing_purchaser_rev = await session.scalar(
            select(AccountLedgerEntry).where(
                AccountLedgerEntry.idempotency_key == purchaser_rev_key
            )
        )
        if existing_purchaser_rev is None:
            p_reversal_amount = -abs(Decimal(matching_purchaser_credit.amount))
            p_entry = AccountLedgerEntry(
                user_id=purchaser.id,
                entry_type="admin_adjustment",
                amount=p_reversal_amount,
                currency="RUB",
                payment_id=None,
                quote_id=None,
                reversal_of_id=None,
                idempotency_key=purchaser_rev_key,
                metadata_={
                    "source_type": REFERRAL_BONUS_SOURCE,
                    "reason": "topup_refund_reversal",
                    "topup_payment_id": payment_id,
                    "original_credit_id": matching_purchaser_credit.id,
                },
            )
            session.add(p_entry)
            await session.flush()
            p_reversal_capacity = await _credit_capacity(
                session, matching_purchaser_credit
            )
            p_allocation_amount = min(abs(p_reversal_amount), p_reversal_capacity)
            if p_allocation_amount > 0:
                session.add(
                    AccountLedgerAllocation(
                        user_id=purchaser.id,
                        credit_entry_id=matching_purchaser_credit.id,
                        debit_entry_id=p_entry.id,
                        amount=p_allocation_amount,
                        idempotency_key=f"alloc:{purchaser_rev_key}",
                    )
                )
            _logger.debug(
                "Welcome bonus reversal created for purchaser user_id=%s, payment_id=%s, amount=%s",
                purchaser.id, payment_id, p_reversal_amount,
            )
            total_reversed += abs(p_reversal_amount)
        else:
            _logger.debug(
                "Welcome bonus reversal already exists for purchaser, payment_id=%s", payment_id
            )
            total_reversed += Decimal(abs(existing_purchaser_rev.amount))
    else:
        _logger.debug(
            "No welcome bonus credit found for purchaser, payment_id=%s", payment_id
        )

    await session.flush()
    return total_reversed


async def get_referral_bonus_balance(
    session: AsyncSession,
    *,
    user_id: int,
) -> Decimal:
    """Return the remaining, attributable referral credit balance."""
    credits = (
        await session.scalars(
            select(AccountLedgerEntry).where(
                AccountLedgerEntry.user_id == user_id,
                AccountLedgerEntry.entry_type == "admin_adjustment",
                AccountLedgerEntry.amount > 0,
                AccountLedgerEntry.metadata_["source_type"].as_string()
                == REFERRAL_BONUS_SOURCE,
            )
        )
    ).all()
    if not credits:
        return Decimal("0")

    credit_ids = [credit.id for credit in credits]
    reversal_rows = (
        await session.scalars(
            select(AccountLedgerEntry).where(
                AccountLedgerEntry.user_id == user_id,
                AccountLedgerEntry.entry_type == "admin_adjustment",
                AccountLedgerEntry.amount < 0,
                AccountLedgerEntry.metadata_["source_type"].as_string()
                == REFERRAL_BONUS_SOURCE,
                AccountLedgerEntry.metadata_["reason"].as_string()
                == "topup_refund_reversal",
            )
        )
    ).all()
    fully_reversed_credit_ids: set[int] = set()
    for reversal in reversal_rows:
        original_credit_id = (reversal.metadata_ or {}).get("original_credit_id")
        if original_credit_id in credit_ids:
            fully_reversed_credit_ids.add(int(original_credit_id))

    allocations = (
        await session.execute(
            select(
                AccountLedgerAllocation.credit_entry_id,
                AccountLedgerAllocation.debit_entry_id,
                AccountLedgerAllocation.amount,
            ).where(AccountLedgerAllocation.credit_entry_id.in_(credit_ids))
        )
    ).all()

    debit_ids = {row.debit_entry_id for row in allocations}
    reversed_debits: set[int] = set()
    if debit_ids:
        reversed_debits = set(
            (
                await session.scalars(
                    select(AccountLedgerEntry.reversal_of_id).where(
                        AccountLedgerEntry.entry_type == "purchase_reversal",
                        AccountLedgerEntry.reversal_of_id.in_(debit_ids),
                    )
                )
            ).all()
        )

    used_by_credit: dict[int, Decimal] = {}
    for row in allocations:
        if row.credit_entry_id in fully_reversed_credit_ids:
            continue
        if row.debit_entry_id in reversed_debits:
            continue
        used_by_credit[row.credit_entry_id] = (
            used_by_credit.get(row.credit_entry_id, Decimal("0"))
            + Decimal(row.amount)
        )

    remaining = sum(
        max(
            Decimal("0"),
            Decimal("0")
            if credit.id in fully_reversed_credit_ids
            else Decimal(credit.amount)
            - used_by_credit.get(credit.id, Decimal("0")),
        )
        for credit in credits
    )

    balance = await get_account_balance(session, user_id=user_id)
    if balance.debt > 0:
        remaining = max(Decimal("0"), remaining - balance.debt)

    return remaining
