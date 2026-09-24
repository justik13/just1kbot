"""Referral rewards as spendable, separately attributable account credits."""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass
from decimal import ROUND_DOWN, Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from config.enums import AdminAuditAction
from database.models import AccountLedgerAllocation, AccountLedgerEntry, User
from database.repositories.account_ledger_repo import (
    _credit_capacity,  # noqa: F401
    get_account_balance,
)

_logger = logging.getLogger(__name__)

REFERRAL_BONUS_RATE = Decimal("0.10")
REFERRAL_BONUS_SOURCE = "referral_bonus"


@dataclass(frozen=True)
class ReferralBonusGrantResult:
    referrer_bonus: Decimal = Decimal(0)
    purchaser_welcome_bonus: Decimal = Decimal(0)

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
        return Decimal(0)
    return (amount * REFERRAL_BONUS_RATE).quantize(
        Decimal(1), rounding=ROUND_DOWN
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

    from sqlalchemy import func

    from database.models import Order, Payment

    count = await session.scalar(
        select(func.count(Payment.id)).where(
            Payment.user_id == user_id,
            Payment.credited_at.is_not(None),
            Payment.fulfillment_status == "succeeded",
        )
    )
    order_count = await session.scalar(
        select(func.count(Order.id)).where(
            Order.user_id == user_id,
            Order.service_type == "topup",
            Order.status == "paid",
        )
    )
    return (count or 0) == 0 and (order_count or 0) == 0


async def grant_referral_bonus_for_topup(
    session: AsyncSession,
    *,
    purchaser_user_id: int,
    payment_id: int | None = None,
    order_id: str | None = None,
    topup_amount: object,
) -> ReferralBonusGrantResult:
    """Credit the referrer with 10% of a top-up, and credit the purchaser with 10% if it is their first top-up."""
    bonus = calculate_referral_bonus(topup_amount)
    if bonus <= 0:
        return ReferralBonusGrantResult(
            referrer_bonus=Decimal(0),
            purchaser_welcome_bonus=Decimal(0),
        )

    purchaser = await session.scalar(
        select(User)
        .where(
            User.id == purchaser_user_id,
            User.is_deleted.is_(False),
        )
    )
    if purchaser is None or purchaser.referred_by is None:
        return ReferralBonusGrantResult(
            referrer_bonus=Decimal(0),
            purchaser_welcome_bonus=Decimal(0),
        )

    if purchaser.referred_by == purchaser.telegram_id:
        return ReferralBonusGrantResult(
            referrer_bonus=Decimal(0),
            purchaser_welcome_bonus=Decimal(0),
        )

    referrer = await session.scalar(
        select(User)
        .where(
            User.telegram_id == purchaser.referred_by,
            User.is_deleted.is_(False),
        )
    )
    if referrer is None or referrer.is_banned:
        return ReferralBonusGrantResult(
            referrer_bonus=Decimal(0),
            purchaser_welcome_bonus=Decimal(0),
        )

    if purchaser.id == referrer.id:
        return ReferralBonusGrantResult(
            referrer_bonus=Decimal(0),
            purchaser_welcome_bonus=Decimal(0),
        )

    # 1. Grant 10% bonus to referrer for every top-up
    referrer_bonus_granted = Decimal(0)
    op_id = order_id or str(payment_id or "unknown")
    idempotency_key = f"referral-bonus:topup:{op_id}:{referrer.id}"
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
                    "topup_order_id": str(order_id) if order_id is not None else None,
                    "bonus_rate": str(REFERRAL_BONUS_RATE),
                },
            )
        )
        referrer_bonus_granted = bonus
        from services.audit_service import AuditService
        await AuditService.log_action(
            session,
            admin_id=0,
            action=AdminAuditAction.REFERRAL_BONUS_GRANTED,
            target_type="user",
            target_id=referrer.id,
            details={
                "amount": int(bonus),
                "from_user_id": purchaser.id,
                "payment_id": payment_id,
                "order_id": order_id,
            },
        )
    else:
        referrer_bonus_granted = Decimal(0)

    # 2. Check if this is the purchaser's first successful top-up. If so, grant purchaser +10% bonus as well.
    purchaser_welcome_granted = Decimal(0)
    from sqlalchemy import func

    from database.models import Order, Payment

    order_uuid = None
    if order_id:
        try:
            order_uuid = uuid.UUID(str(order_id))
        except (ValueError, TypeError):
            pass

    payment_subq = (
        select(func.count(Payment.id))
        .where(
            Payment.user_id == purchaser.id,
            Payment.credited_at.is_not(None),
            Payment.fulfillment_status == "succeeded",
            *((Payment.id < payment_id,) if payment_id is not None else ()),
        )
        .scalar_subquery()
    )

    order_subq = (
        select(func.count(Order.id))
        .where(
            Order.user_id == purchaser.id,
            Order.service_type == "topup",
            Order.status == "paid",
            *((Order.id != order_uuid,) if order_uuid else ()),
        )
        .scalar_subquery()
    )

    prev_credited = (
        await session.scalar(
            select(func.coalesce(payment_subq, 0) + func.coalesce(order_subq, 0))
        )
    ) or 0
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
                        "topup_order_id": str(order_id) if order_id is not None else None,
                        "bonus_rate": str(REFERRAL_BONUS_RATE),
                    },
                )
            )
            purchaser_welcome_granted = bonus
            from services.audit_service import AuditService
            await AuditService.log_action(
                session,
                admin_id=0,
                action=AdminAuditAction.WELCOME_BONUS_GRANTED,
                target_type="user",
                target_id=purchaser.id,
                details={
                    "amount": int(bonus),
                    "referrer_telegram_id": purchaser.referred_by,
                    "payment_id": payment_id,
                },
            )
        else:
            purchaser_welcome_granted = Decimal(0)

    await session.flush()
    return ReferralBonusGrantResult(
        referrer_bonus=referrer_bonus_granted,
        purchaser_welcome_bonus=purchaser_welcome_granted,
    )


async def reverse_referral_bonus_for_topup(
    session: AsyncSession,
    *,
    payment_id: int | None = None,
    order_id: str | uuid.UUID | None = None,
) -> Decimal:
    """Debit/reverse the referral bonus previously credited for a top-up if the top-up is refunded."""
    from sqlalchemy import text

    if payment_id is None and order_id is None:
        return Decimal(0)

    order_str: str | None = None
    if order_id is not None:
        try:
            order_uuid = (
                uuid.UUID(str(order_id))
                if not isinstance(order_id, uuid.UUID)
                else order_id
            )
            order_str = str(order_uuid)
        except (ValueError, TypeError, AttributeError) as err:
            raise ValueError(f"Invalid order_id for referral reversal: {order_id}") from err

    op_id = order_str if order_str is not None else str(payment_id)

    if order_str is not None:
        metadata_filter = {
            "topup_order_id": order_str,
            "source_type": REFERRAL_BONUS_SOURCE,
        }
    else:
        metadata_filter = {
            "topup_payment_id": payment_id,
            "source_type": REFERRAL_BONUS_SOURCE,
        }

    credits = (
        await session.scalars(
            select(AccountLedgerEntry).where(
                AccountLedgerEntry.entry_type == "admin_adjustment",
                AccountLedgerEntry.amount > 0,
                text("metadata @> CAST(:metadata_filter AS jsonb)").bindparams(
                    metadata_filter=json.dumps(metadata_filter)
                ),
            )
        )
    ).all()

    total_reversed = Decimal(0)
    for credit in credits:
        idempotency_key = f"referral-bonus-reversal:{op_id}:{credit.user_id}"
        orig_filter = json.dumps({"original_credit_id": credit.id})
        existing = await session.scalar(
            select(AccountLedgerEntry).where(
                AccountLedgerEntry.user_id == credit.user_id,
                AccountLedgerEntry.entry_type == "admin_adjustment",
                AccountLedgerEntry.amount < 0,
                (
                    (AccountLedgerEntry.idempotency_key == idempotency_key)
                    | (
                        AccountLedgerEntry.idempotency_key
                        == f"referral-bonus-reversal:topup:{op_id}:{credit.user_id}"
                    )
                    | (
                        AccountLedgerEntry.idempotency_key
                        == f"referral-bonus-reversal:first-topup-welcome:{op_id}:{credit.user_id}"
                    )
                    | text("metadata @> CAST(:orig_filter AS jsonb)").bindparams(
                        orig_filter=orig_filter
                    )
                ),
            )
        )
        if (
            existing is not None
            and getattr(existing, "entry_type", None) == "admin_adjustment"
            and isinstance(getattr(existing, "amount", None), (int, float, Decimal))
        ):
            _logger.debug(
                "Referral bonus reversal already exists for credit_id=%s, user_id=%s, op_id=%s",
                credit.id,
                credit.user_id,
                op_id,
            )
            total_reversed += Decimal(abs(existing.amount))
            continue

        reversal_amount = -abs(Decimal(credit.amount))
        reversal_entry = AccountLedgerEntry(
            user_id=credit.user_id,
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
                "topup_order_id": order_str,
                "original_credit_id": credit.id,
            },
        )
        session.add(reversal_entry)
        _logger.debug(
            "Referral bonus reversal created for credit_id=%s, user_id=%s, op_id=%s, amount=%s",
            credit.id,
            credit.user_id,
            op_id,
            reversal_amount,
        )
        total_reversed += abs(reversal_amount)

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
                AccountLedgerEntry.metadata_["source_type"].astext
                == REFERRAL_BONUS_SOURCE,
            )
        )
    ).all()
    if not credits:
        return Decimal(0)

    credit_ids = [credit.id for credit in credits]
    reversal_rows = (
        await session.scalars(
            select(AccountLedgerEntry).where(
                AccountLedgerEntry.user_id == user_id,
                AccountLedgerEntry.entry_type == "admin_adjustment",
                AccountLedgerEntry.amount < 0,
                AccountLedgerEntry.metadata_["source_type"].astext
                == REFERRAL_BONUS_SOURCE,
                AccountLedgerEntry.metadata_["reason"].astext
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
            used_by_credit.get(row.credit_entry_id, Decimal(0))
            + Decimal(row.amount)
        )

    remaining = sum(
        max(
            Decimal(0),
            Decimal(0)
            if credit.id in fully_reversed_credit_ids
            else Decimal(credit.amount)
            - used_by_credit.get(credit.id, Decimal(0)),
        )
        for credit in credits
    )

    balance = await get_account_balance(session, user_id=user_id)
    if balance.debt > 0:
        remaining = max(Decimal(0), remaining - balance.debt)

    return remaining
