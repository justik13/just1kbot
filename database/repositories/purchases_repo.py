"""Repository for querying user purchase logs (orders, tariff purchases, and admin grants)."""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
import re
import uuid

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from config.enums import AdminAuditAction, TariffQuoteOperation
from database.models import (
    AccountLedgerAllocation,
    AccountLedgerEntry,
    AuditLog,
    Order,
    TariffQuote,
    TariffVersion,
    User,
)


@dataclass
class PurchaseLogEntry:
    id: str
    numeric_id: int
    user_id: int
    telegram_id: int
    username: str | None
    user_label: str
    operation_type: str  # "purchase", "renew", "change", "grant", "extend"
    operation_title: str
    tariff_name: str
    device_limit: int
    duration_days: int
    amount_rub: Decimal
    created_at: datetime
    # Real vs bonus breakdown of the wallet debit behind this purchase.
    # None means unknown / not applicable (topups, admin grants).
    real_amount_rub: Decimal | None = None
    bonus_amount_rub: Decimal | None = None


_AUDIT_ACTION_TO_OP: dict[AdminAuditAction, tuple[str, AdminAuditAction]] = {
    AdminAuditAction.GRANT: ("grant", AdminAuditAction.ADMIN_SUB_GRANT),
    AdminAuditAction.ADMIN_SUB_GRANT: ("grant", AdminAuditAction.ADMIN_SUB_GRANT),
    AdminAuditAction.EXTEND: ("extend", AdminAuditAction.ADMIN_SUB_EXTEND),
    AdminAuditAction.ADMIN_SUB_EXTEND: ("extend", AdminAuditAction.ADMIN_SUB_EXTEND),
    AdminAuditAction.CHANGE_TARIFF: ("change", AdminAuditAction.ADMIN_SUB_CHANGE),
    AdminAuditAction.ADMIN_SUB_CHANGE: ("change", AdminAuditAction.ADMIN_SUB_CHANGE),
    AdminAuditAction.REDUCE: ("reduce", AdminAuditAction.ADMIN_SUB_REDUCE),
    AdminAuditAction.ADMIN_SUB_REDUCE: ("reduce", AdminAuditAction.ADMIN_SUB_REDUCE),
}

AUDIT_PURCHASE_ACTIONS: list[str] = [a.value for a in _AUDIT_ACTION_TO_OP]


def get_quote_op_title(op: str | TariffQuoteOperation) -> str:
    from bot import texts

    op_title_map = {
        TariffQuoteOperation.PURCHASE: getattr(texts, "PAYMENT_OP_TITLE_PURCHASE", "Покупка"),
        TariffQuoteOperation.RENEW: getattr(texts, "PAYMENT_OP_TITLE_RENEW", "Продление"),
        TariffQuoteOperation.CHANGE: getattr(texts, "PAYMENT_OP_TITLE_CHANGE", "Смена тарифа"),
    }
    return op_title_map.get(op, getattr(texts, "PAYMENT_OP_TITLE_DEFAULT", "Операция"))


def get_audit_op_info(action: str | AdminAuditAction) -> tuple[str, str]:
    from bot import texts

    action_val = action.value if isinstance(action, AdminAuditAction) else str(action)
    action_enum = getattr(AdminAuditAction, action_val, None) or next(
        (a for a in AdminAuditAction if a.value == action_val), None
    )
    if action_enum and action_enum in _AUDIT_ACTION_TO_OP:
        op_type, canonical_enum = _AUDIT_ACTION_TO_OP[action_enum]
        title = (
            texts.AUDIT_ACTIONS.get(action_val)
            or texts.AUDIT_ACTIONS.get(canonical_enum.value)
            or action_val
        )
    else:
        op_type = "grant"
        title = texts.AUDIT_ACTIONS.get(action_val) or action_val
    return op_type, title


async def _purchase_funds_splits(
    session: AsyncSession,
    *,
    quote_ids: set[int] | frozenset[int] = frozenset(),
    order_ids: set[uuid.UUID] | frozenset[uuid.UUID] = frozenset(),
) -> tuple[dict[int, tuple[Decimal, Decimal]], dict[uuid.UUID, tuple[Decimal, Decimal]]]:
    """Batch (real_rub, bonus_rub) split per purchase debit. Read-only.

    Real part comes from ``payment_credit`` lots, bonus part from
    ``admin_adjustment`` lots via FIFO allocations. Purchases without a
    ledger debit (direct card payments) are absent from the result.
    """
    by_quote: dict[int, tuple[Decimal, Decimal]] = {}
    by_order: dict[uuid.UUID, tuple[Decimal, Decimal]] = {}
    if not quote_ids and not order_ids:
        return by_quote, by_order
    conds = []
    if quote_ids:
        conds.append(AccountLedgerEntry.quote_id.in_(quote_ids))
    if order_ids:
        conds.append(AccountLedgerEntry.order_id.in_(order_ids))
    debit_rows = (
        await session.execute(
            select(AccountLedgerEntry).where(
                AccountLedgerEntry.entry_type == "purchase_debit",
                or_(*conds) if len(conds) > 1 else conds[0],
            )
        )
    ).scalars().all()
    debit_ids = [debit.id for debit in debit_rows]
    if not debit_ids:
        return by_quote, by_order
    agg_rows = (
        await session.execute(
            select(
                AccountLedgerAllocation.debit_entry_id,
                AccountLedgerEntry.entry_type,
                func.sum(AccountLedgerAllocation.amount),
            )
            .join(
                AccountLedgerEntry,
                AccountLedgerEntry.id == AccountLedgerAllocation.credit_entry_id,
            )
            .where(AccountLedgerAllocation.debit_entry_id.in_(debit_ids))
            .group_by(
                AccountLedgerAllocation.debit_entry_id,
                AccountLedgerEntry.entry_type,
            )
        )
    ).all()
    per_debit: dict[int, dict[str, Decimal]] = {}
    for debit_id, entry_type, total in agg_rows:
        per_debit.setdefault(debit_id, {})[entry_type] = Decimal(total or 0)
    for debit in debit_rows:
        parts = per_debit.get(debit.id)
        if not parts:
            continue
        split = (
            parts.get("payment_credit", Decimal(0)),
            parts.get("admin_adjustment", Decimal(0)),
        )
        if debit.quote_id is not None and debit.quote_id in quote_ids:
            by_quote[debit.quote_id] = split
        if debit.order_id is not None and debit.order_id in order_ids:
            by_order[debit.order_id] = split
    return by_quote, by_order


async def get_purchase_logs_paginated(
    session: AsyncSession,
    page: int = 1,
    per_page: int = 10,
) -> tuple[list[PurchaseLogEntry], int]:
    offset = max(0, (page - 1) * per_page)
    needed = offset + per_page

    entries: list[PurchaseLogEntry] = []

    # 1. Fetch paid orders
    order_stmt = (
        select(Order)
        .where(
            Order.status == "paid",
            Order.service_type.in_(("awg", "white_internet", "topup")),
        )
        .options(selectinload(Order.user), selectinload(Order.tariff))
        .order_by(Order.paid_at.desc().nullslast(), Order.created_at.desc())
        .limit(needed)
    )
    order_results = (await session.execute(order_stmt)).scalars().all()
    _, order_splits = await _purchase_funds_splits(
        session,
        order_ids={o.id for o in order_results if o.service_type != "topup"},
    )
    for ord_item in order_results:
        user = ord_item.user
        tg_id = user.telegram_id if user else 0
        username = user.username if user else None
        user_label = f"@{username}" if username else f"ID: {tg_id}"
        tariff_obj = getattr(ord_item, "tariff", None)
        is_change = bool(
            ord_item.metadata_ and ord_item.metadata_.get("is_tariff_change")
        )
        if ord_item.service_type == "topup":
            op_type = "topup"
            op_title = "Пополнение"
            tariff_name = "Баланс"
        elif is_change:
            op_type = "change"
            op_title = "Смена тарифа"
            tariff_name = tariff_obj.name if tariff_obj else "Тариф"
        else:
            op_type = "purchase"
            op_title = "Покупка"
            tariff_name = (
                tariff_obj.name
                if tariff_obj
                else (
                    "White Internet"
                    if getattr(ord_item, "service_type", None) == "white_internet"
                    else "Тариф"
                )
            )
        if ord_item.service_type == "topup":
            real_amount_rub, bonus_amount_rub = None, None
        elif ord_item.id in order_splits:
            real_amount_rub, bonus_amount_rub = order_splits[ord_item.id]
        else:
            # Direct external payment (card): no wallet debit behind it.
            real_amount_rub, bonus_amount_rub = ord_item.amount_rub, Decimal(0)
        entries.append(
            PurchaseLogEntry(
                id=f"order_{ord_item.id}",
                numeric_id=0,
                user_id=user.id if user else 0,
                telegram_id=tg_id,
                username=username,
                user_label=user_label,
                operation_type=op_type,
                operation_title=op_title,
                tariff_name=tariff_name,
                device_limit=ord_item.device_limit or 2,
                duration_days=ord_item.duration_days,
                amount_rub=ord_item.amount_rub,
                created_at=ord_item.paid_at or ord_item.created_at,
                real_amount_rub=real_amount_rub,
                bonus_amount_rub=bonus_amount_rub,
            )
        )

    # 2. Fetch consumed TariffQuotes (historical)
    quote_stmt = (
        select(TariffQuote)
        .where(TariffQuote.status == "consumed")
        .options(
            selectinload(TariffQuote.user),
            selectinload(TariffQuote.target_tariff_version).selectinload(
                TariffVersion.tariff
            ),
        )
        .order_by(TariffQuote.consumed_at.desc().nullslast(), TariffQuote.created_at.desc())
        .limit(needed)
    )
    quote_results = (await session.execute(quote_stmt)).scalars().all()
    quote_splits, _ = await _purchase_funds_splits(
        session,
        quote_ids={q.id for q in quote_results},
    )
    for quote in quote_results:
        user = quote.user
        tg_id = user.telegram_id if user else 0
        username = user.username if user else None
        user_label = f"@{username}" if username else f"ID: {tg_id}"
        target_ver = quote.target_tariff_version
        if target_ver:
            tariff_name = target_ver.name_snapshot
            if target_ver.tariff and target_ver.tariff.name:
                tariff_name = target_ver.tariff.name
            dev_limit = target_ver.device_limit
            dur_days = target_ver.duration_days
        else:
            tariff_name = "Тариф"
            dev_limit = 1
            dur_days = 30
        op_title = get_quote_op_title(quote.operation_type)
        quote_split = quote_splits.get(quote.id)
        if quote_split is not None:
            real_amount_rub, bonus_amount_rub = quote_split
        else:
            # No ledger debit found (should not happen): do not invent numbers.
            real_amount_rub, bonus_amount_rub = None, None
        entries.append(
            PurchaseLogEntry(
                id=f"quote_{quote.id}",
                numeric_id=quote.id,
                user_id=user.id if user else 0,
                telegram_id=tg_id,
                username=username,
                user_label=user_label,
                operation_type=quote.operation_type,
                operation_title=op_title,
                tariff_name=tariff_name,
                device_limit=dev_limit,
                duration_days=dur_days,
                amount_rub=quote.amount_due_rub or Decimal(0),
                created_at=quote.consumed_at or quote.created_at,
                real_amount_rub=real_amount_rub,
                bonus_amount_rub=bonus_amount_rub,
            )
        )

    # 3. Fetch AuditLogs for admin grants
    audit_stmt = (
        select(AuditLog)
        .where(AuditLog.action.in_(AUDIT_PURCHASE_ACTIONS))
        .order_by(AuditLog.created_at.desc())
        .limit(needed)
    )
    audit_results = (await session.execute(audit_stmt)).scalars().all()
    user_cache = {}
    for log in audit_results:
        u = None
        if log.target_id:
            if log.target_id not in user_cache:
                u = await session.scalar(
                    select(User).where(User.telegram_id == log.target_id)
                )
                if not u:
                    u = await session.get(User, log.target_id)
                user_cache[log.target_id] = u
            else:
                u = user_cache[log.target_id]
        tg_id = u.telegram_id if u else (log.target_id or 0)
        username = u.username if u else None
        user_label = f"@{username}" if username else f"ID: {tg_id}"
        op_type, op_title = get_audit_op_info(log.action)
        tariff_name = "Подписка"
        dev_limit = 1
        dur_days = 30
        if log.details:
            days_match = re.search(r"days?=(\d+)|(\d+)\s*(?:дн|day)", log.details, re.IGNORECASE)
            if days_match:
                dur_days = int(days_match.group(1) or days_match.group(2))
        entries.append(
            PurchaseLogEntry(
                id=f"audit_{log.id}",
                numeric_id=log.id,
                user_id=u.id if u else (log.target_id or 0),
                telegram_id=tg_id,
                username=username,
                user_label=user_label,
                operation_type=op_type,
                operation_title=op_title,
                tariff_name=tariff_name,
                device_limit=dev_limit,
                duration_days=dur_days,
                amount_rub=Decimal("0.00"),
                created_at=log.created_at,
            )
        )

    entries.sort(key=lambda x: x.created_at, reverse=True)

    if (
        len(order_results) < needed
        and len(quote_results) < needed
        and len(audit_results) < needed
    ):
        total = len(entries)
    else:
        order_count = (
            await session.scalar(
                select(func.count(Order.id)).where(
                    Order.status == "paid",
                    Order.service_type.in_(("awg", "white_internet", "topup")),
                )
            )
        ) or 0
        quote_count = (
            await session.scalar(
                select(func.count(TariffQuote.id)).where(
                    TariffQuote.status == "consumed"
                )
            )
        ) or 0
        audit_count = (
            await session.scalar(
                select(func.count(AuditLog.id)).where(
                    AuditLog.action.in_(AUDIT_PURCHASE_ACTIONS)
                )
            )
        ) or 0
        total = order_count + quote_count + audit_count

    paged_entries = entries[offset : offset + per_page]
    return paged_entries, total


async def get_purchase_log_by_id(
    session: AsyncSession,
    entry_id: str,
) -> PurchaseLogEntry | None:
    if entry_id.startswith("order_"):
        raw_id = entry_id.split("_", 1)[1]
        try:
            order_uuid = uuid.UUID(raw_id)
        except ValueError:
            return None
        stmt = (
            select(Order)
            .where(Order.id == order_uuid)
            .options(selectinload(Order.user), selectinload(Order.tariff))
        )
        ord_item = (await session.execute(stmt)).scalar_one_or_none()
        if not ord_item:
            return None
        user = ord_item.user
        tg_id = user.telegram_id if user else 0
        username = user.username if user else None
        user_label = f"@{username}" if username else f"ID: {tg_id}"
        tariff_obj = ord_item.tariff
        is_change = bool(
            ord_item.metadata_ and ord_item.metadata_.get("is_tariff_change")
        )
        if ord_item.service_type == "topup":
            op_type = "topup"
            op_title = "Пополнение"
            tariff_name = "Баланс"
        elif is_change:
            op_type = "change"
            op_title = "Смена тарифа"
            tariff_name = tariff_obj.name if tariff_obj else "Тариф"
        else:
            op_type = "purchase"
            op_title = "Покупка"
            tariff_name = (
                tariff_obj.name
                if tariff_obj
                else (
                    "White Internet"
                    if ord_item.service_type == "white_internet"
                    else "Тариф"
                )
            )
        if ord_item.service_type == "topup":
            real_amount_rub, bonus_amount_rub = None, None
        else:
            _, single_order_splits = await _purchase_funds_splits(
                session, order_ids={ord_item.id}
            )
            if ord_item.id in single_order_splits:
                real_amount_rub, bonus_amount_rub = single_order_splits[ord_item.id]
            else:
                real_amount_rub, bonus_amount_rub = ord_item.amount_rub, Decimal(0)
        return PurchaseLogEntry(
            id=f"order_{ord_item.id}",
            numeric_id=0,
            user_id=user.id if user else 0,
            telegram_id=tg_id,
            username=username,
            user_label=user_label,
            operation_type=op_type,
            operation_title=op_title,
            tariff_name=tariff_name,
            device_limit=ord_item.device_limit or 2,
            duration_days=ord_item.duration_days,
            amount_rub=ord_item.amount_rub,
            created_at=ord_item.paid_at or ord_item.created_at,
            real_amount_rub=real_amount_rub,
            bonus_amount_rub=bonus_amount_rub,
        )

    elif entry_id.startswith("quote_"):
        try:
            q_id = int(entry_id.split("_", 1)[1])
        except ValueError:
            return None
        stmt = (
            select(TariffQuote)
            .where(TariffQuote.id == q_id)
            .options(
                selectinload(TariffQuote.user),
                selectinload(TariffQuote.target_tariff_version).selectinload(
                    TariffVersion.tariff
                ),
            )
        )
        quote = (await session.execute(stmt)).scalar_one_or_none()
        if not quote:
            return None
        user = quote.user
        tg_id = user.telegram_id if user else 0
        username = user.username if user else None
        user_label = f"@{username}" if username else f"ID: {tg_id}"
        target_ver = quote.target_tariff_version
        if target_ver:
            tariff_name = target_ver.name_snapshot
            if target_ver.tariff and target_ver.tariff.name:
                tariff_name = target_ver.tariff.name
            dev_limit = target_ver.device_limit
            dur_days = target_ver.duration_days
        else:
            tariff_name = "Тариф"
            dev_limit = 1
            dur_days = 30
        op_title = get_quote_op_title(quote.operation_type)

        single_quote_splits, _ = await _purchase_funds_splits(
            session, quote_ids={quote.id}
        )
        quote_split = single_quote_splits.get(quote.id)
        if quote_split is not None:
            real_amount_rub, bonus_amount_rub = quote_split
        else:
            real_amount_rub, bonus_amount_rub = None, None
        return PurchaseLogEntry(
            id=f"quote_{quote.id}",
            numeric_id=quote.id,
            user_id=user.id if user else 0,
            telegram_id=tg_id,
            username=username,
            user_label=user_label,
            operation_type=quote.operation_type,
            operation_title=op_title,
            tariff_name=tariff_name,
            device_limit=dev_limit,
            duration_days=dur_days,
            amount_rub=quote.amount_due_rub or Decimal(0),
            created_at=quote.consumed_at or quote.created_at,
            real_amount_rub=real_amount_rub,
            bonus_amount_rub=bonus_amount_rub,
        )

    elif entry_id.startswith("audit_"):
        try:
            a_id = int(entry_id.split("_", 1)[1])
        except ValueError:
            return None
        log = await session.get(AuditLog, a_id)
        if not log:
            return None
        u = None
        if log.target_id:
            u = await session.scalar(
                select(User).where(User.telegram_id == log.target_id)
            )
            if not u:
                u = await session.get(User, log.target_id)
        tg_id = u.telegram_id if u else (log.target_id or 0)
        username = u.username if u else None
        user_label = f"@{username}" if username else f"ID: {tg_id}"
        op_type, op_title = get_audit_op_info(log.action)
        tariff_name = "Подписка"
        dev_limit = 1
        dur_days = 30
        if log.details:
            days_match = re.search(r"days?=(\d+)|(\d+)\s*(?:дн|day)", log.details, re.IGNORECASE)
            if days_match:
                dur_days = int(days_match.group(1) or days_match.group(2))

        return PurchaseLogEntry(
            id=f"audit_{log.id}",
            numeric_id=log.id,
            user_id=u.id if u else (log.target_id or 0),
            telegram_id=tg_id,
            username=username,
            user_label=user_label,
            operation_type=op_type,
            operation_title=op_title,
            tariff_name=tariff_name,
            device_limit=dev_limit,
            duration_days=dur_days,
            amount_rub=Decimal("0.00"),
            created_at=log.created_at,
        )
    return None
