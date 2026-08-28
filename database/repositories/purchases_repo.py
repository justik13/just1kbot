"""Repository for querying user purchase logs (tariff purchases, renewals, changes, and admin grants)."""

import re
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from bot import texts
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from config.enums import TariffQuoteOperation
from database.models import AuditLog, TariffQuote, TariffVersion, User


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


async def get_purchase_logs_paginated(
    session: AsyncSession,
    page: int = 1,
    per_page: int = 10,
) -> tuple[list[PurchaseLogEntry], int]:
    offset = max(0, (page - 1) * per_page)
    needed = offset + per_page

    # 1. Fetch consumed TariffQuotes (bounded by needed count)
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

    # 2. Fetch admin sub grants/extensions from AuditLog (bounded by needed count)
    audit_actions = [
        "GRANT",
        "ADMIN_SUB_GRANT",
        "EXTEND",
        "ADMIN_SUB_EXTEND",
        "CHANGE_TARIFF",
        "ADMIN_SUB_CHANGE",
        "REDUCE",
        "ADMIN_SUB_REDUCE",
    ]
    audit_stmt = (
        select(AuditLog)
        .where(AuditLog.action.in_(audit_actions))
        .order_by(AuditLog.created_at.desc())
        .limit(needed)
    )
    audit_results = (await session.execute(audit_stmt)).scalars().all()

    # Collect user IDs for audit logs to fetch user info
    audit_user_ids = {
        log.target_id
        for log in audit_results
        if log.target_id and log.target_type in ("User", "user")
    }
    users_by_id: dict[int, User] = {}
    users_by_tg_id: dict[int, User] = {}
    if audit_user_ids:
        users = (
            await session.scalars(
                select(User).where(
                    or_(
                        User.id.in_(audit_user_ids),
                        User.telegram_id.in_(audit_user_ids),
                    )
                )
            )
        ).all()
        for u in users:
            users_by_id[u.id] = u
            users_by_tg_id[u.telegram_id] = u

    entries: list[PurchaseLogEntry] = []

    # Map TariffQuotes
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

        op_title_map = {
            TariffQuoteOperation.PURCHASE: texts.PAYMENT_OP_TITLE_PURCHASE,
            TariffQuoteOperation.RENEW: texts.PAYMENT_OP_TITLE_RENEW,
            TariffQuoteOperation.CHANGE: texts.PAYMENT_OP_TITLE_CHANGE,
        }
        op_title = op_title_map.get(quote.operation_type, texts.PAYMENT_OP_TITLE_DEFAULT)

        created_at = quote.consumed_at or quote.created_at

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
                created_at=created_at,
            )
        )

    # Map AuditLog manual admin actions
    op_audit_map = {
        "GRANT": ("grant", texts.AUDIT_ACTIONS.get("ADMIN_SUB_GRANT", "")),
        "ADMIN_SUB_GRANT": ("grant", texts.AUDIT_ACTIONS.get("ADMIN_SUB_GRANT", "")),
        "EXTEND": ("extend", texts.AUDIT_ACTIONS.get("ADMIN_SUB_EXTEND", "")),
        "ADMIN_SUB_EXTEND": ("extend", texts.AUDIT_ACTIONS.get("ADMIN_SUB_EXTEND", "")),
        "CHANGE_TARIFF": ("change", texts.AUDIT_ACTIONS.get("ADMIN_SUB_CHANGE", "")),
        "ADMIN_SUB_CHANGE": ("change", texts.AUDIT_ACTIONS.get("ADMIN_SUB_CHANGE", "")),
        "REDUCE": ("reduce", texts.AUDIT_ACTIONS.get("ADMIN_SUB_REDUCE", "")),
        "ADMIN_SUB_REDUCE": ("reduce", texts.AUDIT_ACTIONS.get("ADMIN_SUB_REDUCE", "")),
    }
    for log in audit_results:
        u = users_by_tg_id.get(log.target_id) or users_by_id.get(log.target_id)
        tg_id = u.telegram_id if u else (log.target_id or 0)
        username = u.username if u else None
        user_label = f"@{username}" if username else f"ID: {tg_id}"

        op_type, op_title = op_audit_map.get(
            log.action, ("grant", texts.AUDIT_ACTIONS.get("ADMIN_SUB_GRANT", ""))
        )

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

    if len(quote_results) < needed and len(audit_results) < needed:
        total = len(entries)
    else:
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
                    AuditLog.action.in_(audit_actions)
                )
            )
        ) or 0
        total = quote_count + audit_count

    paged_entries = entries[offset : offset + per_page]

    return paged_entries, total


async def get_purchase_log_by_id(
    session: AsyncSession,
    entry_id: str,
) -> PurchaseLogEntry | None:
    if entry_id.startswith("quote_"):
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
        op_title_map = {
            TariffQuoteOperation.PURCHASE: texts.PAYMENT_OP_TITLE_PURCHASE,
            TariffQuoteOperation.RENEW: texts.PAYMENT_OP_TITLE_RENEW,
            TariffQuoteOperation.CHANGE: texts.PAYMENT_OP_TITLE_CHANGE,
        }
        op_title = op_title_map.get(quote.operation_type, texts.PAYMENT_OP_TITLE_DEFAULT)

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
        op_audit_map = {
            "GRANT": ("grant", texts.AUDIT_ACTIONS.get("ADMIN_SUB_GRANT", "")),
            "ADMIN_SUB_GRANT": ("grant", texts.AUDIT_ACTIONS.get("ADMIN_SUB_GRANT", "")),
            "EXTEND": ("extend", texts.AUDIT_ACTIONS.get("ADMIN_SUB_EXTEND", "")),
            "ADMIN_SUB_EXTEND": ("extend", texts.AUDIT_ACTIONS.get("ADMIN_SUB_EXTEND", "")),
            "CHANGE_TARIFF": ("change", texts.AUDIT_ACTIONS.get("ADMIN_SUB_CHANGE", "")),
            "ADMIN_SUB_CHANGE": ("change", texts.AUDIT_ACTIONS.get("ADMIN_SUB_CHANGE", "")),
            "REDUCE": ("reduce", texts.AUDIT_ACTIONS.get("ADMIN_SUB_REDUCE", "")),
            "ADMIN_SUB_REDUCE": ("reduce", texts.AUDIT_ACTIONS.get("ADMIN_SUB_REDUCE", "")),
        }
        op_type, op_title = op_audit_map.get(
            log.action, ("grant", texts.AUDIT_ACTIONS.get("ADMIN_SUB_GRANT", ""))
        )
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
