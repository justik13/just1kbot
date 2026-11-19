"""Transactional persistence boundary for the user's real and bonus account buckets."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from sqlalchemy import func, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import AccountLedgerEntry, User

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


def _snapshot_from_positions(real_pos: Decimal, bonus_pos: Decimal) -> AccountBalanceSnapshot:
    debt = max(ZERO, -real_pos)
    if debt > ZERO:
        real_avail = ZERO
        bonus_avail = ZERO
    else:
        real_avail = max(ZERO, real_pos)
        bonus_avail = max(ZERO, bonus_pos)
    return AccountBalanceSnapshot(
        accounting_position=real_pos + bonus_pos,
        available=real_avail + bonus_avail,
        debt=debt,
        real_position=real_pos,
        bonus_position=bonus_pos,
        real_available=real_avail,
        bonus_available=bonus_avail,
    )


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
    row = (
        await session.execute(
            select(
                func.coalesce(
                    func.sum(AccountLedgerEntry.amount).filter(
                        AccountLedgerEntry.bucket == "real"
                    ),
                    0,
                ),
                func.coalesce(
                    func.sum(AccountLedgerEntry.amount).filter(
                        AccountLedgerEntry.bucket == "bonus"
                    ),
                    0,
                ),
            ).where(AccountLedgerEntry.user_id == user_id)
        )
    ).one()
    return _snapshot_from_positions(Decimal(row[0] or 0), Decimal(row[1] or 0))


async def get_account_balances_batch(
    session: AsyncSession,
    user_ids: list[int],
) -> dict[int, AccountBalanceSnapshot]:
    """Batch-fetch balances for a list of users in a single query."""
    if not user_ids:
        return {}
    rows = (
        await session.execute(
            select(
                AccountLedgerEntry.user_id,
                func.coalesce(
                    func.sum(AccountLedgerEntry.amount).filter(
                        AccountLedgerEntry.bucket == "real"
                    ),
                    0,
                ),
                func.coalesce(
                    func.sum(AccountLedgerEntry.amount).filter(
                        AccountLedgerEntry.bucket == "bonus"
                    ),
                    0,
                ),
            )
            .where(AccountLedgerEntry.user_id.in_(user_ids))
            .group_by(AccountLedgerEntry.user_id)
        )
    ).all()
    result: dict[int, AccountBalanceSnapshot] = {
        uid: _snapshot_from_positions(Decimal(r or 0), Decimal(b or 0))
        for uid, r, b in rows
    }
    empty = _snapshot_from_positions(ZERO, ZERO)
    for uid in user_ids:
        result.setdefault(uid, empty)
    return result


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
    entry = await session.scalar(select(AccountLedgerEntry).where(lookup_cond))
    expected = {
        "user_id": values["user_id"],
        "entry_type": values["entry_type"],
        "bucket": values["bucket"],
        "amount": values["amount"],
        "currency": values["currency"],
        "order_id": values.get("order_id"),
        "idempotency_key": values["idempotency_key"],
    }
    if entry is None or not _same_entry(entry, expected):
        raise AccountLedgerConflictError("account_ledger_idempotency_conflict")
    return entry, False


async def get_order_debit_split(
    session: AsyncSession,
    order_id: object,
) -> tuple[Decimal, Decimal]:
    """Return (real_debited, bonus_debited) recorded as purchase_debit for an order."""
    rows = (
        await session.execute(
            select(
                AccountLedgerEntry.bucket,
                func.coalesce(func.sum(AccountLedgerEntry.amount), 0),
            )
            .where(
                AccountLedgerEntry.order_id == order_id,
                AccountLedgerEntry.entry_type == "purchase_debit",
            )
            .group_by(AccountLedgerEntry.bucket)
        )
    ).all()
    real_debited = ZERO
    bonus_debited = ZERO
    for bucket, sum_amt in rows:
        amt = abs(Decimal(sum_amt or 0))
        if bucket == "real":
            real_debited = amt
        elif bucket == "bonus":
            bonus_debited = amt
    return real_debited, bonus_debited


async def create_order_debit(
    session: AsyncSession,
    *,
    user_id: int,
    amount_rub: object,
    order_id: object,
    metadata: dict | None = None,
    locked_user: User | None = None,
) -> tuple[AccountLedgerEntry | None, bool]:
    """Debit an order from the user's balance (bonus bucket first, then real bucket)."""
    amount = abs(whole_rubles(amount_rub, allow_zero=True))
    user = await lock_account_user(session, user_id, locked_user=locked_user)
    if amount == 0:
        return None, False

    existing = (
        await session.scalars(
            select(AccountLedgerEntry).where(
                AccountLedgerEntry.entry_type == "purchase_debit",
                AccountLedgerEntry.order_id == order_id,
            )
        )
    ).all()
    if existing:
        existing_total = sum(abs(Decimal(e.amount)) for e in existing)
        if any(e.user_id != user.id for e in existing) or existing_total != amount:
            raise AccountLedgerConflictError("purchase_debit_conflict")
        return existing[-1], False

    snapshot = await get_account_balance(
        session, user_id=user.id, for_update=False, locked_user=user
    )
    if snapshot.available < amount:
        raise InsufficientAccountBalanceError("insufficient_available_balance")

    bonus_part = min(snapshot.bonus_available, amount)
    real_part = amount - bonus_part
    last_entry: AccountLedgerEntry | None = None
    any_created = False

    for bucket, part in (("bonus", bonus_part), ("real", real_part)):
        if part <= ZERO:
            continue
        values = {
            "user_id": user.id,
            "entry_type": "purchase_debit",
            "bucket": bucket,
            "amount": -abs(whole_rubles(part)),
            "currency": "RUB",
            "order_id": order_id,
            "idempotency_key": f"order_debit:{order_id}:{bucket}",
            "metadata_": metadata or {},
        }
        entry, created = await _insert_or_get_entry(
            session,
            values=values,
            economic_lookup=(
                (AccountLedgerEntry.entry_type == "purchase_debit")
                & (AccountLedgerEntry.order_id == order_id)
                & (AccountLedgerEntry.bucket == bucket)
            ),
        )
        last_entry = entry
        any_created = any_created or created

    return last_entry, any_created


async def create_order_credit(
    session: AsyncSession,
    *,
    user_id: int,
    amount_rub: Decimal,
    order_id: object,
    metadata: dict | None = None,
) -> tuple[AccountLedgerEntry, bool]:
    """Credit real YooKassa top-up/fallback funds to the 'real' bucket."""
    amount = abs(whole_rubles(amount_rub))
    values = {
        "user_id": user_id,
        "entry_type": "payment_credit",
        "bucket": "real",
        "amount": amount,
        "currency": "RUB",
        "order_id": order_id,
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


async def create_mixed_cancel_refund(
    session: AsyncSession,
    *,
    user_id: int,
    order_id: object,
    key_prefix: str = "mixed_cancel",
    source: str = "mixed_canceled_refund",
) -> tuple[Decimal, Decimal]:
    """Restore real and bonus parts debited for an order back into their original buckets."""
    real_part, bonus_part = await get_order_debit_split(session, order_id)
    if real_part > ZERO:
        real_key = f"{key_prefix}_real_refund:{order_id}"
        await _insert_or_get_entry(
            session,
            values={
                "user_id": user_id,
                "entry_type": "payment_credit",
                "bucket": "real",
                "amount": abs(whole_rubles(real_part)),
                "currency": "RUB",
                "order_id": None,
                "idempotency_key": real_key,
                "metadata_": {"source": source, "order_id": str(order_id)},
            },
            economic_lookup=AccountLedgerEntry.idempotency_key == real_key,
        )
    if bonus_part > ZERO:
        await create_admin_adjustment(
            session,
            user_id=user_id,
            signed_amount=bonus_part,
            idempotency_key=f"{key_prefix}_bonus_refund:{order_id}",
            metadata={"source": f"{source}_bonus", "order_id": str(order_id)},
        )
    return real_part, bonus_part


async def create_order_refund_debit(
    session: AsyncSession,
    *,
    user_id: int,
    amount_rub: Decimal,
    order_id: object,
    refund_id: str,
    metadata: dict | None = None,
) -> tuple[AccountLedgerEntry, bool]:
    """Debit refunded real YooKassa top-up funds from the 'real' bucket."""
    if not isinstance(refund_id, str) or not refund_id.strip():
        raise ValueError("refund_id must be a non-empty string")
    from decimal import ROUND_HALF_UP

    quantized_amount = Decimal(str(amount_rub)).quantize(
        Decimal("1"), rounding=ROUND_HALF_UP
    )
    if quantized_amount <= 0:
        raise ValueError("Refund debit amount must be at least 1 ruble")
    amount = -abs(whole_rubles(quantized_amount))

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
                        AccountLedgerEntry.entry_type == "refund_debit",
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
        "bucket": "real",
        "amount": amount,
        "currency": "RUB",
        "order_id": order_id,
        "idempotency_key": idempotency_key,
        "metadata_": metadata or {},
    }
    return await _insert_or_get_entry(session, values=values)


async def create_admin_adjustment(
    session: AsyncSession,
    *,
    user_id: int,
    signed_amount: object,
    idempotency_key: str,
    metadata: dict,
    cap_to_available: bool = False,
) -> tuple[AccountLedgerEntry | None, bool]:
    """Credit or debit the internal 'bonus' bucket."""
    user = await lock_account_user(session, user_id)
    amount = Decimal(str(signed_amount))
    if (
        isinstance(signed_amount, float)
        or not amount.is_finite()
        or amount == 0
        or amount != amount.to_integral_value()
    ):
        raise ValueError("adjustment must be non-zero whole rubles")

    if amount < 0:
        bal = await get_account_balance(
            session, user_id=user.id, for_update=False, locked_user=user
        )
        if bal.bonus_available < abs(amount):
            if not cap_to_available:
                raise InsufficientAccountBalanceError("insufficient_bonus_balance")
            if bal.bonus_available <= ZERO:
                return None, False
            amount = -bal.bonus_available

    values = {
        "user_id": user.id,
        "entry_type": "admin_adjustment",
        "bucket": "bonus",
        "amount": amount.quantize(Decimal("1.00")),
        "currency": "RUB",
        "idempotency_key": idempotency_key,
        "metadata_": dict(metadata),
    }
    return await _insert_or_get_entry(
        session,
        values=values,
        economic_lookup=AccountLedgerEntry.idempotency_key == idempotency_key,
    )


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
