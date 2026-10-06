"""Repository for querying user purchase logs (orders, tariff purchases, and admin grants)."""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
import re
import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from config.enums import AdminAuditAction
from database.models import (
    AccountLedgerAllocation,
    AccountLedgerEntry,
    AuditLog,
    Order,
    User,
)


@dataclass
class PurchaseLogEntry:
    id: str
    numeric_id: str | int
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
    payment_method: str = "wallet"


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


def _wi_operation_title(operation: str | None) -> tuple[str, str] | None:
    """Title override for White Internet wallet orders from metadata.

    Returns (operation_type, title) or None when the order carries no known
    wallet operation. Trial keeps the historical default title.
    """
    from bot import texts

    titles = {
        "purchase": getattr(texts, "PAYMENT_OP_TITLE_PURCHASE", "Покупка"),
        "renew": getattr(texts, "PAYMENT_OP_TITLE_RENEW", "Продление"),
        "change": getattr(texts, "PAYMENT_OP_TITLE_CHANGE", "Смена тарифа"),
    }
    if operation in titles:
        return operation, titles[operation]
    if operation == "add_device_slot":
        return "add_device_slot", "Доп. устройство"
    if operation in ("topup", "topup_quota"):
        return "topup_quota", "Докупка трафика"
    if operation in ("trial", "topup_or_device_slot"):
        return operation, getattr(texts, "PAYMENT_OP_TITLE_DEFAULT", "Операция")
    return None


def _apply_wi_operation_title(
    entry: PurchaseLogEntry, metadata: dict | None
) -> None:
    """Override a wallet order entry title from its stored operation."""
    operation = metadata.get("operation") if isinstance(metadata, dict) else None
    titled = _wi_operation_title(operation)
    if titled is not None:
        entry.operation_type, entry.operation_title = titled


def _build_order_purchase_entry(
    ord_item: Order,
    *,
    real_amount_rub: Decimal | None = None,
    bonus_amount_rub: Decimal | None = None,
) -> PurchaseLogEntry:
    user = ord_item.user
    tg_id = user.telegram_id if user else 0
    username = user.username if user else None
    user_label = f"@{username}" if username else f"ID: {tg_id}"
    tariff_obj = getattr(ord_item, "tariff", None)
    meta = ord_item.metadata_ if isinstance(ord_item.metadata_, dict) else {}
    is_change = bool(meta.get("is_tariff_change"))
    meta_tariff_name = meta.get("tariff_name")
    meta_op = meta.get("operation")

    d_limit = ord_item.device_limit if isinstance(getattr(ord_item, "device_limit", None), (int, float)) else 2
    t_bytes = ord_item.traffic_bytes if isinstance(getattr(ord_item, "traffic_bytes", None), (int, float)) else 0
    d_days = ord_item.duration_days if isinstance(getattr(ord_item, "duration_days", None), (int, float)) else 0

    if ord_item.service_type == "topup":
        op_type = "topup"
        op_title = "Пополнение"
        tariff_name = "Баланс"
    elif ord_item.service_type == "white_internet":
        if meta_op == "add_device_slot" or (
            d_limit
            and not t_bytes
            and d_days == 0
        ):
            op_type = "add_device_slot"
            op_title = "Доп. устройство"
            tariff_name = "+1 слот"
        elif meta_op in ("topup", "topup_quota") or (
            t_bytes > 0 and d_days == 0
        ):
            op_type = "topup_quota"
            op_title = "Докупка трафика"
            pack_gb = max(1, int(t_bytes) // (1024**3))
            tariff_name = f"+{pack_gb} ГБ"
        elif meta_op == "renew":
            op_type = "renew"
            op_title = "Продление"
            tariff_name = meta_tariff_name or (tariff_obj.name if tariff_obj else "White Internet")
        else:
            op_type = "purchase"
            op_title = "Покупка"
            tariff_name = meta_tariff_name or (tariff_obj.name if tariff_obj else "White Internet")
    elif is_change or meta_op == "change":
        op_type = "change"
        op_title = "Смена тарифа"
        tariff_name = meta_tariff_name or (tariff_obj.name if tariff_obj else "Тариф")
    elif meta.get("is_renewal") or meta_op == "renew":
        op_type = "renew"
        op_title = "Продление"
        tariff_name = meta_tariff_name or (tariff_obj.name if tariff_obj else "Тариф")
    else:
        op_type = "purchase"
        op_title = "Покупка"
        tariff_name = (
            meta_tariff_name
            or (tariff_obj.name if tariff_obj else None)
            or (
                "White Internet"
                if getattr(ord_item, "service_type", None) == "white_internet"
                else "Тариф"
            )
        )

    entry = PurchaseLogEntry(
        id=f"order_{ord_item.id}",
        numeric_id=str(ord_item.id)[:8],
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
        payment_method=ord_item.payment_method or "wallet",
    )
    if ord_item.service_type == "white_internet":
        _apply_wi_operation_title(entry, ord_item.metadata_)
    return entry


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
    order_ids: set[uuid.UUID] | frozenset[uuid.UUID] = frozenset(),
) -> dict[uuid.UUID, tuple[Decimal, Decimal]]:
    """Batch (real_rub, bonus_rub) split per order purchase debit. Read-only.

    Real part comes from ``payment_credit`` lots, bonus part from
    ``admin_adjustment`` lots via FIFO allocations. Orders without a
    ledger debit (direct card payments) are absent from the result.
    """
    by_order: dict[uuid.UUID, tuple[Decimal, Decimal]] = {}
    if not order_ids:
        return by_order
    debit_rows = (
        await session.execute(
            select(AccountLedgerEntry).where(
                AccountLedgerEntry.entry_type == "purchase_debit",
                AccountLedgerEntry.order_id.in_(order_ids),
            )
        )
    ).scalars().all()
    debit_ids = [debit.id for debit in debit_rows]
    if not debit_ids:
        return by_order
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
        if debit.order_id is not None and debit.order_id in order_ids:
            by_order[debit.order_id] = (
                parts.get("payment_credit", Decimal(0)),
                parts.get("admin_adjustment", Decimal(0)),
            )
    return by_order


async def get_purchase_logs_paginated(
    session: AsyncSession,
    page: int = 1,
    per_page: int = 10,
) -> tuple[list[PurchaseLogEntry], int]:
    offset = max(0, (page - 1) * per_page)
    needed = offset + per_page

    entries: list[PurchaseLogEntry] = []

    # 1. Fetch paid orders (excluding topup)
    order_stmt = (
        select(Order)
        .where(
            Order.status == "paid",
            Order.service_type.in_(("awg", "white_internet")),
        )
        .options(selectinload(Order.user), selectinload(Order.tariff))
        .order_by(Order.paid_at.desc().nullslast(), Order.created_at.desc())
        .limit(needed)
    )
    order_results = (await session.execute(order_stmt)).scalars().all()
    for ord_item in order_results:
        entries.append(_build_order_purchase_entry(ord_item))

    # 2. Fetch AuditLogs for admin grants
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
                payment_method="admin",
            )
        )

    entries.sort(key=lambda x: x.created_at, reverse=True)

    if len(order_results) < needed and len(audit_results) < needed:
        total = len(entries)
    else:
        order_count = (
            await session.scalar(
                select(func.count(Order.id)).where(
                    Order.status == "paid",
                    Order.service_type.in_(("awg", "white_internet")),
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
        total = order_count + audit_count

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
            .where(
                Order.id == order_uuid,
                Order.status == "paid",
                Order.service_type.in_(("awg", "white_internet")),
            )
            .options(selectinload(Order.user), selectinload(Order.tariff))
        )
        ord_item = (await session.execute(stmt)).scalar_one_or_none()
        if not ord_item:
            return None

        single_order_splits = await _purchase_funds_splits(
            session, order_ids={ord_item.id}
        )
        if ord_item.id in single_order_splits:
            real_amount_rub, bonus_amount_rub = single_order_splits[ord_item.id]
        elif ord_item.payment_method == "wallet":
            real_amount_rub, bonus_amount_rub = None, None
        else:
            real_amount_rub, bonus_amount_rub = ord_item.amount_rub, Decimal(0)

        return _build_order_purchase_entry(
            ord_item,
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
            payment_method="admin",
        )
    return None
