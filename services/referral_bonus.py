"""Referral rewards as spendable, separately attributable account credits."""

from __future__ import annotations

from decimal import Decimal, ROUND_DOWN

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import AccountLedgerAllocation, AccountLedgerEntry, User
from database.repositories.account_ledger_repo import get_account_balance

REFERRAL_BONUS_RATE = Decimal("0.10")
REFERRAL_BONUS_SOURCE = "referral_bonus"


def calculate_referral_bonus(purchase_amount: object) -> Decimal:
    """Return 10% of a purchase, rounded down to whole rubles."""
    amount = Decimal(str(purchase_amount))
    if not amount.is_finite() or amount <= 0:
        return Decimal("0")
    return (amount * REFERRAL_BONUS_RATE).quantize(
        Decimal("1"), rounding=ROUND_DOWN
    )


async def grant_referral_bonus_for_purchase(
    session: AsyncSession,
    *,
    purchaser_user_id: int,
    quote_id: int,
    purchase_amount: object,
) -> Decimal:
    """Credit the referrer with 10% of a successful in-bot purchase.

    The credit is stored as an ``admin_adjustment`` with a strict metadata tag
    so existing FIFO account spending remains compatible without adding a new
    database enum/check constraint. It is idempotent per purchase+referrer.
    """
    bonus = calculate_referral_bonus(purchase_amount)
    if bonus <= 0:
        return Decimal("0")

    purchaser = await session.scalar(
        select(User).where(
            User.id == purchaser_user_id,
            User.is_deleted.is_(False),
        )
    )
    if purchaser is None or purchaser.referred_by is None:
        return Decimal("0")

    if purchaser.referred_by == purchaser.telegram_id:
        return Decimal("0")

    referrer = await session.scalar(
        select(User)
        .where(
            User.telegram_id == purchaser.referred_by,
            User.is_deleted.is_(False),
        )
        .with_for_update()
    )
    if referrer is None or referrer.is_banned:
        return Decimal("0")

    idempotency_key = f"referral-bonus:{quote_id}:{referrer.id}"
    existing = await session.scalar(
        select(AccountLedgerEntry).where(
            AccountLedgerEntry.idempotency_key == idempotency_key
        )
    )
    if existing is not None:
        if (
            existing.user_id != referrer.id
            or existing.entry_type != "admin_adjustment"
            or existing.amount != bonus
        ):
            raise RuntimeError("referral_bonus_idempotency_conflict")
        return Decimal(existing.amount)

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
                "purchase_quote_id": quote_id,
                "bonus_rate": str(REFERRAL_BONUS_RATE),
            },
        )
    )
    await session.flush()
    return bonus


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
        if row.debit_entry_id in reversed_debits:
            continue
        used_by_credit[row.credit_entry_id] = (
            used_by_credit.get(row.credit_entry_id, Decimal("0"))
            + Decimal(row.amount)
        )

    remaining = sum(
        max(
            Decimal("0"),
            Decimal(credit.amount)
            - used_by_credit.get(credit.id, Decimal("0")),
        )
        for credit in credits
    )

    balance = await get_account_balance(session, user_id=user_id)
    if balance.debt > 0:
        remaining = max(Decimal("0"), remaining - balance.debt)

    return remaining
