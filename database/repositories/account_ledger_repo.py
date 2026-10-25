"""Transactional persistence boundary for the user's real-money account.

All public mutators use the same per-user advisory/row lock as checkout quotes.
The ledger and allocations are append-only; mutable reservations only move from
``active`` to one terminal state.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from sqlalchemy import case, func, or_, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import (
    AccountLedgerAllocation,
    AccountLedgerEntry,
    User,
)

ZERO = Decimal(0)


class AccountLedgerError(RuntimeError):
    pass


class AccountLedgerConflictError(AccountLedgerError):
    pass


class InsufficientAccountBalanceError(AccountLedgerError):
    pass


class AccountLedgerInvariantError(AccountLedgerError):
    pass


@dataclass(frozen=True)
class AccountBalanceSnapshot:
    accounting_position: Decimal
    available: Decimal
    debt: Decimal
    real_position: Decimal = ZERO
    bonus_position: Decimal = ZERO
    real_available: Decimal = ZERO
    bonus_available: Decimal = ZERO


def whole_rubles(value: object, *, allow_zero: bool = False) -> Decimal:
    if isinstance(value, float):
        raise ValueError("float money is forbidden")
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError("invalid money amount") from exc
    if not amount.is_finite() or amount != amount.to_integral_value():
        raise ValueError("amount must be whole rubles")
    if amount < 0 or (amount == 0 and not allow_zero):
        raise ValueError("amount must be positive")
    return amount.quantize(Decimal("1.00"))


async def lock_checkout_user(session: AsyncSession, user_id: int) -> User | None:
    """The sole per-user checkout lock; callers derive state only afterwards."""
    await session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": -user_id})
    return await session.scalar(
        select(User).where(User.id == user_id).with_for_update()
    )


async def lock_account_user(
    session: AsyncSession,
    user_id: int,
    *,
    locked_user: User | None = None,
) -> User:
    user = locked_user or await lock_checkout_user(session, user_id)
    if user is None:
        raise LookupError("account_user_not_found")
    return user


async def get_account_balance(
    session: AsyncSession,
    *,
    user_id: int,
    for_update: bool = False,
    locked_user: User | None = None,
) -> AccountBalanceSnapshot:
    if for_update:
        await lock_account_user(session, user_id, locked_user=locked_user)
    position = Decimal(
        await session.scalar(
            select(func.coalesce(func.sum(AccountLedgerEntry.amount), 0)).where(
                AccountLedgerEntry.user_id == user_id
            )
        )
        or ZERO
    )
    debt = max(ZERO, -position)
    if position <= ZERO:
        return AccountBalanceSnapshot(
            accounting_position=position,
            available=ZERO,
            debt=debt,
            real_position=ZERO,
            bonus_position=ZERO,
            real_available=ZERO,
            bonus_available=ZERO,
        )

    bonus_credited = max(
        ZERO,
        Decimal(
            await session.scalar(
                select(func.coalesce(func.sum(AccountLedgerEntry.amount), 0)).where(
                    AccountLedgerEntry.user_id == user_id,
                    AccountLedgerEntry.entry_type == "admin_adjustment",
                )
            )
            or ZERO
        ),
    )

    if bonus_credited <= ZERO:
        return AccountBalanceSnapshot(
            accounting_position=position,
            available=position,
            debt=debt,
            real_position=position,
            bonus_position=ZERO,
            real_available=position,
            bonus_available=ZERO,
        )

    bonus_spent = Decimal(
        await session.scalar(
            select(func.coalesce(func.sum(AccountLedgerAllocation.amount), 0))
            .join(
                AccountLedgerEntry,
                AccountLedgerEntry.id == AccountLedgerAllocation.credit_entry_id,
            )
            .where(
                AccountLedgerAllocation.user_id == user_id,
                AccountLedgerEntry.entry_type == "admin_adjustment",
            )
        )
        or ZERO
    )

    unspent_bonus = max(ZERO, bonus_credited - bonus_spent)
    bonus_available = min(position, unspent_bonus)
    real_available = position - bonus_available

    return AccountBalanceSnapshot(
        accounting_position=position,
        available=position,
        debt=debt,
        real_position=real_available,
        bonus_position=bonus_available,
        real_available=real_available,
        bonus_available=bonus_available,
    )



def _same_entry(entry: AccountLedgerEntry, expected: dict) -> bool:
    return all(getattr(entry, field) == value for field, value in expected.items())


async def _insert_or_get_entry(
    session: AsyncSession,
    *,
    values: dict,
    economic_lookup=None,
) -> tuple[AccountLedgerEntry, bool]:
    entry_id = await session.scalar(
        insert(AccountLedgerEntry)
        .values(**values)
        .on_conflict_do_nothing()
        .returning(AccountLedgerEntry.id)
    )
    if entry_id is not None:
        return await session.get(AccountLedgerEntry, entry_id), True
    lookup_cond = AccountLedgerEntry.idempotency_key == values["idempotency_key"]
    if economic_lookup is not None:
        lookup_cond = lookup_cond | economic_lookup
    entry = await session.scalar(
        select(AccountLedgerEntry).where(lookup_cond)
    )
    expected = {
        "user_id": values["user_id"],
        "entry_type": values["entry_type"],
        "amount": values["amount"],
        "currency": values["currency"],
        "payment_id": values.get("payment_id"),
        "quote_id": values.get("quote_id"),
        "order_id": values.get("order_id"),
        "reversal_of_id": values.get("reversal_of_id"),
        "idempotency_key": values["idempotency_key"],
    }
    if entry is None or not _same_entry(entry, expected):
        raise AccountLedgerConflictError("account_ledger_idempotency_conflict")
    return entry, False


async def create_order_debit(
    session: AsyncSession,
    *,
    user_id: int,
    amount_rub: object,
    order_id: object,
    metadata: dict | None = None,
    locked_user: User | None = None,
) -> tuple[AccountLedgerEntry | None, bool]:
    amount = -abs(whole_rubles(amount_rub, allow_zero=True))
    user = await lock_account_user(session, user_id, locked_user=locked_user)
    if amount == 0:
        return None, False
    existing = await session.scalar(
        select(AccountLedgerEntry).where(
            AccountLedgerEntry.entry_type == "purchase_debit",
            AccountLedgerEntry.order_id == order_id,
        )
    )
    if existing is not None:
        if existing.user_id != user.id or existing.amount != amount:
            raise AccountLedgerConflictError("purchase_debit_conflict")
        return existing, False
    snapshot = await get_account_balance(
        session, user_id=user.id, for_update=False, locked_user=user
    )
    if snapshot.available < -amount:
        raise InsufficientAccountBalanceError("insufficient_available_balance")
    values = {
        "user_id": user.id,
        "entry_type": "purchase_debit",
        "amount": amount,
        "currency": "RUB",
        "payment_id": None,
        "quote_id": None,
        "order_id": order_id,
        "reversal_of_id": None,
        "idempotency_key": f"order_debit:{order_id}",
        "metadata_": metadata or {},
    }
    debit, created = await _insert_or_get_entry(
        session,
        values=values,
        economic_lookup=(
            (AccountLedgerEntry.entry_type == "purchase_debit")
            & (AccountLedgerEntry.order_id == order_id)
        ),
    )
    if created:
        # Mirror the retired quote-debit path: attribute the spend to credit
        # lots FIFO (bonus lots first) so balance buckets stay exact.
        await _allocate_fifo(session, user_id=user.id, debit=debit, amount=-amount)
    return debit, created


async def create_order_credit(
    session: AsyncSession,
    *,
    user_id: int,
    amount_rub: Decimal,
    order_id: object,
    metadata: dict | None = None,
) -> tuple[AccountLedgerEntry, bool]:
    amount = abs(whole_rubles(amount_rub))
    values = {
        "user_id": user_id,
        "entry_type": "payment_credit",
        "amount": amount,
        "currency": "RUB",
        "payment_id": None,
        "quote_id": None,
        "order_id": order_id,
        "reversal_of_id": None,
        "idempotency_key": f"order_credit:{order_id}",
        "metadata_": metadata or {},
    }
    return await _insert_or_get_entry(
        session,
        values=values,
        economic_lookup=(
            (AccountLedgerEntry.entry_type == "payment_credit")
            & (AccountLedgerEntry.order_id == order_id)
        ),
    )


async def create_order_refund_debit(
    session: AsyncSession,
    *,
    user_id: int,
    amount_rub: Decimal,
    order_id: object,
    refund_id: str,
    metadata: dict | None = None,
) -> tuple[AccountLedgerEntry, bool]:
    if not isinstance(refund_id, str) or not refund_id.strip():
        raise ValueError("refund_id must be a non-empty string")
    from decimal import ROUND_HALF_UP

    quantized_amount = Decimal(str(amount_rub)).quantize(
        Decimal("1"), rounding=ROUND_HALF_UP
    )
    if quantized_amount <= 0:
        raise ValueError("Refund debit amount must be at least 1 ruble")
    amount = -abs(whole_rubles(quantized_amount))

    # Cumulative cap (restores the pre-PR payment_debit_exceeds_topup guard
    # for the order model): all refund/chargeback debits for the order must
    # never exceed the credited amount. Without this, duplicate refund events
    # with distinct IDs would over-debit the wallet into unbounded debt.
    # The cap applies only when the original credit is readable; direct
    # low-level callers with stubbed sessions keep the legacy behavior.
    try:
        credit_total = Decimal(
            str(
                await session.scalar(
                    select(
                        func.coalesce(func.sum(AccountLedgerEntry.amount), 0)
                    ).where(
                        AccountLedgerEntry.order_id == order_id,
                        AccountLedgerEntry.entry_type == "payment_credit",
                    )
                )
            )
        )
        debited_total = Decimal(
            str(
                await session.scalar(
                    select(
                        func.coalesce(func.sum(AccountLedgerEntry.amount), 0)
                    ).where(
                        AccountLedgerEntry.order_id == order_id,
                        AccountLedgerEntry.entry_type.in_(
                            ("refund_debit", "chargeback_debit")
                        ),
                    )
                )
            )
        )
    except (InvalidOperation, TypeError, ValueError):
        credit_total = None
        debited_total = None
    if (
        credit_total is not None
        and debited_total is not None
        and credit_total > 0
        and abs(debited_total) + abs(amount) > credit_total
    ):
        raise AccountLedgerInvariantError("refund_debit_exceeds_credit")

    idempotency_key = f"order_refund:{order_id}:{refund_id.strip()}"
    values = {
        "user_id": user_id,
        "entry_type": "refund_debit",
        "amount": amount,
        "currency": "RUB",
        "payment_id": None,
        "quote_id": None,
        "order_id": order_id,
        "reversal_of_id": None,
        "idempotency_key": idempotency_key,
        "metadata_": metadata or {},
    }
    return await _insert_or_get_entry(
        session,
        values=values,
    )


async def create_admin_adjustment(
    session: AsyncSession,
    *,
    user_id: int,
    signed_amount: object,
    idempotency_key: str,
    metadata: dict,
) -> tuple[AccountLedgerEntry, bool]:
    await lock_account_user(session, user_id)
    amount = Decimal(str(signed_amount))
    if (
        isinstance(signed_amount, float)
        or not amount.is_finite()
        or amount == 0
        or amount != amount.to_integral_value()
    ):
        raise ValueError("adjustment must be non-zero whole rubles")
    values = {
        "user_id": user_id,
        "entry_type": "admin_adjustment",
        "amount": amount.quantize(Decimal("1.00")),
        "currency": "RUB",
        "payment_id": None,
        "quote_id": None,
        "reversal_of_id": None,
        "idempotency_key": idempotency_key,
        "metadata_": dict(metadata),
    }
    entry, created = await _insert_or_get_entry(
        session,
        values=values,
        economic_lookup=AccountLedgerEntry.idempotency_key == idempotency_key,
    )
    if created and amount < 0:
        await _allocate_fifo(
            session, user_id=user_id, debit=entry, amount=abs(amount)
        )
    return entry, created


async def _batch_credit_capacities(
    session: AsyncSession, credits: list[AccountLedgerEntry]
) -> dict[int, Decimal]:
    if not credits:
        return {}

    credit_ids = [c.id for c in credits]
    order_ids = [c.order_id for c in credits if c.order_id is not None]
    payment_ids = [c.payment_id for c in credits if c.payment_id is not None]

    allocations = (
        await session.scalars(
            select(AccountLedgerAllocation).where(
                AccountLedgerAllocation.credit_entry_id.in_(credit_ids)
            )
        )
    ).all()

    used_by_credit: dict[int, Decimal] = {}
    for alloc in allocations:
        used_by_credit[alloc.credit_entry_id] = (
            used_by_credit.get(alloc.credit_entry_id, ZERO)
            + Decimal(alloc.amount)
        )

    refund_by_order: dict[object, Decimal] = {}
    refund_by_payment: dict[int, Decimal] = {}

    if order_ids or payment_ids:
        conds = []
        if order_ids:
            conds.append(AccountLedgerEntry.order_id.in_(order_ids))
        if payment_ids:
            conds.append(AccountLedgerEntry.payment_id.in_(payment_ids))
        rows = (
            await session.execute(
                select(
                    AccountLedgerEntry.order_id,
                    AccountLedgerEntry.payment_id,
                    func.coalesce(func.sum(AccountLedgerEntry.amount), 0),
                )
                .where(
                    AccountLedgerEntry.entry_type.in_(
                        ("refund_debit", "chargeback_debit")
                    ),
                    or_(*conds),
                )
                .group_by(AccountLedgerEntry.order_id, AccountLedgerEntry.payment_id)
            )
        ).all()
        for o_id, p_id, sum_amt in rows:
            if o_id is not None:
                refund_by_order[o_id] = (
                    refund_by_order.get(o_id, ZERO) + abs(Decimal(sum_amt))
                )
            if p_id is not None:
                refund_by_payment[p_id] = (
                    refund_by_payment.get(p_id, ZERO) + abs(Decimal(sum_amt))
                )

    user_id = credits[0].user_id if credits else None
    rev_adj: dict[int, Decimal] = {}
    if user_id is not None:
        adj_rows = (
            await session.execute(
                select(AccountLedgerEntry.metadata_, AccountLedgerEntry.amount).where(
                    AccountLedgerEntry.user_id == user_id,
                    AccountLedgerEntry.entry_type == "admin_adjustment",
                    AccountLedgerEntry.amount < 0,
                )
            )
        ).all()
        for meta, amt in adj_rows:
            orig = (meta or {}).get("original_credit_id")
            if orig is not None:
                try:
                    orig_id = int(orig)
                    rev_adj[orig_id] = rev_adj.get(orig_id, ZERO) + abs(Decimal(amt))
                except (ValueError, TypeError):
                    pass

    capacities: dict[int, Decimal] = {}
    for credit in credits:
        used = used_by_credit.get(credit.id, ZERO)
        ext_debit = ZERO
        if credit.order_id is not None:
            ext_debit += refund_by_order.get(credit.order_id, ZERO)
        if credit.payment_id is not None:
            ext_debit += refund_by_payment.get(credit.payment_id, ZERO)
        adj_debit = rev_adj.get(credit.id, ZERO)
        capacities[credit.id] = Decimal(credit.amount) - used - ext_debit - adj_debit

    return capacities


async def _allocate_fifo(
    session: AsyncSession,
    *,
    user_id: int,
    debit: AccountLedgerEntry,
    amount: Decimal,
) -> list[AccountLedgerAllocation]:
    credits = (
        await session.scalars(
            select(AccountLedgerEntry)
            .where(
                AccountLedgerEntry.user_id == user_id,
                AccountLedgerEntry.amount > 0,
                AccountLedgerEntry.entry_type.in_(
                    ("payment_credit", "admin_adjustment")
                ),
            )
            .order_by(
                case(
                    (AccountLedgerEntry.entry_type == "admin_adjustment", 0),
                    else_=1,
                ),
                AccountLedgerEntry.created_at,
                AccountLedgerEntry.id,
            )
            .with_for_update()

        )
    ).all()
    remaining = amount
    created: list[AccountLedgerAllocation] = []
    # Single batched capacity lookup for every candidate credit instead of
    # one query per lot (N+1). Capacities are computed from pre-existing
    # allocations; allocations created below belong to this debit only and
    # never affect an earlier lot's capacity, so precomputation is exact.
    capacities = await _batch_credit_capacities(session, list(credits))
    for credit in credits:
        capacity = max(ZERO, capacities.get(credit.id, ZERO))
        chunk = min(capacity, remaining)
        if chunk <= 0:
            continue
        allocation = AccountLedgerAllocation(
            user_id=user_id,
            credit_entry_id=credit.id,
            debit_entry_id=debit.id,
            amount=chunk,
            idempotency_key=f"allocation:{debit.id}:{credit.id}",
        )
        session.add(allocation)
        await session.flush()
        created.append(allocation)
        remaining -= chunk
        if remaining == 0:
            break
    if remaining:
        raise AccountLedgerInvariantError("available_balance_has_no_credit_lots")
    return created


async def get_account_history(
    session: AsyncSession,
    *,
    user_id: int,
    limit: int = 20,
    offset: int = 0,
) -> list[AccountLedgerEntry]:
    if limit < 1 or limit > 100:
        raise ValueError("history limit must be 1..100")
    stmt = (
        select(AccountLedgerEntry)
        .where(AccountLedgerEntry.user_id == user_id)
        .order_by(
            AccountLedgerEntry.created_at.desc(),
            AccountLedgerEntry.id.desc(),
        )
        .limit(limit)
    )
    if offset > 0:
        stmt = stmt.offset(offset)
    return list((await session.scalars(stmt)).all())


async def get_account_history_count(
    session: AsyncSession,
    *,
    user_id: int,
) -> int:
    return int(
        await session.scalar(
            select(func.count(AccountLedgerEntry.id)).where(
                AccountLedgerEntry.user_id == user_id
            )
        )
        or 0
    )

