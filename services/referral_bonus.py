"""Referral rewards as spendable, separately attributable account credits."""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass
from decimal import ROUND_DOWN, Decimal

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from config.constants import REFERRAL_DEFAULT_RATE, REFERRAL_TIERS
from config.enums import AdminAuditAction
from database.models import AccountLedgerEntry, User
from database.repositories.account_ledger_repo import (
    _credit_capacity,  # noqa: F401
    get_account_balance,
)

_logger = logging.getLogger(__name__)

REFERRAL_BONUS_RATE = REFERRAL_DEFAULT_RATE
REFERRAL_BONUS_SOURCE = "referral_bonus"


@dataclass(frozen=True)
class ReferralTierInfo:
    rate: Decimal
    name: str
    needed_for_next: int | None = None
    next_tier_name: str | None = None
    next_rate: Decimal | None = None


def get_referral_tier(active_count: int) -> ReferralTierInfo:
    """Determine referral tier, rate, and progress to next tier based on active referrals count."""
    count = max(0, int(active_count))
    tiers = sorted(REFERRAL_TIERS, key=lambda t: t[0])
    current_tier = tiers[0]
    next_tier = None

    for i, tier in enumerate(tiers):
        threshold, _rate, _name = tier
        if count >= threshold:
            current_tier = tier
            next_tier = tiers[i + 1] if i + 1 < len(tiers) else None
        else:
            break

    _threshold, rate, name = current_tier
    if next_tier is not None:
        next_threshold, next_rate, next_name = next_tier
        return ReferralTierInfo(
            rate=rate,
            name=name,
            needed_for_next=next_threshold - count,
            next_tier_name=next_name,
            next_rate=next_rate,
        )
    return ReferralTierInfo(
        rate=rate,
        name=name,
        needed_for_next=None,
        next_tier_name=None,
        next_rate=None,
    )


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


def calculate_referral_bonus(
    purchase_amount: object, rate: Decimal | None = None
) -> Decimal:
    """Return referral bonus of a purchase, rounded down to whole rubles."""
    amount = Decimal(str(purchase_amount))
    if not amount.is_finite() or amount <= 0:
        return Decimal(0)
    actual_rate = rate if rate is not None else REFERRAL_DEFAULT_RATE
    return (amount * actual_rate).quantize(
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
    if (
        purchaser is None
        or not hasattr(purchaser, "referred_by")
        or purchaser.referred_by is None
    ):
        return False
    if purchaser.referred_by == getattr(purchaser, "telegram_id", None):
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
    purchaser = await session.scalar(
        select(User)
        .where(
            User.id == purchaser_user_id,
            User.is_deleted.is_(False),
        )
        .with_for_update()
    )
    if (
        purchaser is None
        or not hasattr(purchaser, "referred_by")
        or purchaser.referred_by is None
    ):
        return ReferralBonusGrantResult(
            referrer_bonus=Decimal(0),
            purchaser_welcome_bonus=Decimal(0),
        )

    if purchaser.referred_by == getattr(purchaser, "telegram_id", None):
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
    if (
        referrer is None
        or not hasattr(referrer, "is_banned")
        or referrer.is_banned
        or not hasattr(referrer, "id")
    ):
        return ReferralBonusGrantResult(
            referrer_bonus=Decimal(0),
            purchaser_welcome_bonus=Decimal(0),
        )

    if getattr(purchaser, "id", None) == getattr(referrer, "id", None):
        return ReferralBonusGrantResult(
            referrer_bonus=Decimal(0),
            purchaser_welcome_bonus=Decimal(0),
        )

    from database.repositories.users_repo import get_user_active_referrals_count

    active_count = await get_user_active_referrals_count(session, referrer.telegram_id)
    tier_info = get_referral_tier(active_count)
    bonus = calculate_referral_bonus(topup_amount, rate=tier_info.rate)
    if bonus <= 0:
        return ReferralBonusGrantResult(
            referrer_bonus=Decimal(0),
            purchaser_welcome_bonus=Decimal(0),
        )

    # 1. Grant tiered bonus (15%..30%) to referrer
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
                    "bonus_rate": str(tier_info.rate),
                    "tier_name": tier_info.name,
                    "active_referrals_count": active_count,
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
                "bonus_rate": str(tier_info.rate),
                "tier_name": tier_info.name,
                "active_referrals_count": active_count,
            },
        )
    else:
        referrer_bonus_granted = Decimal(0)

    # 2. Purchaser welcome bonus is granted as a 25% checkout discount on first order
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
    refund_amount: Decimal | None = None,
    original_topup_amount: Decimal | None = None,
    total_refunded_amount: Decimal | None = None,
    refund_id: str | None = None,
) -> Decimal:
    """Debit/reverse the referral bonus previously credited for a top-up if the top-up is refunded."""
    from decimal import ROUND_HALF_UP

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
    ref_suffix = f":{refund_id}" if refund_id else ""

    for credit in credits:
        idempotency_key = f"referral-bonus-reversal:{op_id}:{credit.user_id}{ref_suffix}"

        existing = await session.scalar(
            select(AccountLedgerEntry).where(
                AccountLedgerEntry.user_id == credit.user_id,
                AccountLedgerEntry.entry_type == "admin_adjustment",
                AccountLedgerEntry.amount < 0,
                AccountLedgerEntry.idempotency_key == idempotency_key,
            )
        )
        if (
            existing is not None
            and getattr(existing, "entry_type", None) == "admin_adjustment"
            and isinstance(getattr(existing, "amount", None), (int, float, Decimal))
        ):
            _logger.debug(
                "Referral bonus reversal already exists for credit_id=%s, user_id=%s, op_id=%s, refund_id=%s",
                credit.id,
                credit.user_id,
                op_id,
                refund_id,
            )
            total_reversed += Decimal(abs(existing.amount))
            continue

        orig_credit_filter = json.dumps({"original_credit_id": credit.id})
        prev_reversals = (
            await session.scalars(
                select(AccountLedgerEntry).where(
                    AccountLedgerEntry.user_id == credit.user_id,
                    AccountLedgerEntry.entry_type == "admin_adjustment",
                    AccountLedgerEntry.amount < 0,
                    text("metadata @> CAST(:orig_credit_filter AS jsonb)").bindparams(
                        orig_credit_filter=orig_credit_filter
                    ),
                )
            )
        ).all()
        already_reversed = sum(
            (abs(Decimal(r.amount)) for r in prev_reversals if getattr(r, "amount", 0) < 0),
            Decimal(0),
        )
        max_can_reverse = max(Decimal(0), Decimal(credit.amount) - already_reversed)
        if max_can_reverse <= Decimal(0):
            _logger.debug(
                "Referral bonus credit_id=%s already fully reversed (%s / %s)",
                credit.id,
                already_reversed,
                credit.amount,
            )
            continue

        # Cumulative calculation prevents rounding abuse across series of partial refunds
        if total_refunded_amount is not None and original_topup_amount and original_topup_amount > 0:
            ratio = min(Decimal(1), max(Decimal(0), Decimal(str(total_refunded_amount)) / Decimal(str(original_topup_amount))))
            target_cumulative = (Decimal(credit.amount) * ratio).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
            reversal_val = min(max_can_reverse, max(Decimal(0), target_cumulative - already_reversed))
        elif refund_amount is not None:
            credit_rate = None
            if getattr(credit, "metadata_", None) and "bonus_rate" in credit.metadata_:
                try:
                    credit_rate = Decimal(str(credit.metadata_["bonus_rate"]))
                except Exception:
                    credit_rate = None
            reversal_val = min(max_can_reverse, calculate_referral_bonus(refund_amount, rate=credit_rate))
        else:
            reversal_val = max_can_reverse

        if reversal_val <= Decimal(0):
            continue

        reversal_amount = -abs(reversal_val)
        meta_payment_id = payment_id or (credit.metadata_ or {}).get("topup_payment_id")
        meta_order_id = order_str or (credit.metadata_ or {}).get("topup_order_id")
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
                "topup_payment_id": meta_payment_id,
                "topup_order_id": meta_order_id,
                "original_credit_id": credit.id,
                "refund_id": refund_id,
                "refund_amount": str(refund_amount) if refund_amount is not None else None,
                "total_refunded_amount": str(total_refunded_amount) if total_refunded_amount is not None else None,
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
    """Return available referral bonus balance capped by user's available bonus funds."""
    from sqlalchemy import func

    net_referral = await session.scalar(
        select(func.coalesce(func.sum(AccountLedgerEntry.amount), 0)).where(
            AccountLedgerEntry.user_id == user_id,
            AccountLedgerEntry.entry_type == "admin_adjustment",
            AccountLedgerEntry.metadata_["source_type"].astext == REFERRAL_BONUS_SOURCE,
        )
    )
    balance = await get_account_balance(session, user_id=user_id)
    remaining = max(Decimal(0), Decimal(str(net_referral or 0)))
    if balance.debt > 0:
        remaining = max(Decimal(0), remaining - balance.debt)
    return min(remaining, balance.bonus_available)
