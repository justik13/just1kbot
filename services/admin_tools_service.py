"""Admin Mini App operations: direct messages, mass bonus/extend, finances, audit logs."""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from datetime import timedelta
from decimal import Decimal
import html
import logging
from typing import Any
from uuid import UUID, uuid4

from aiogram import Bot
from aiogram.exceptions import TelegramForbiddenError
from sqlalchemy import String, and_, cast, desc, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from bot.keyboards.notifications import (
    get_action_notification_keyboard,
    get_bonus_credited_keyboard,
)
from config.enums import AccountLedgerEntryType, AdminAuditAction, OrderServiceType, OrderStatus
from database.connection import session_scope
from database.models import (
    AccountLedgerEntry,
    AuditLog,
    Order,
    User,
    WhiteInternetSubscription,
)
from database.repositories.account_ledger_repo import create_admin_adjustment
from database.repositories.vless_subscription_repo import get_or_create_subscription
from services.audit_service import AuditService
from services.broadcast_service import get_audience_targets
from services.subscription import SubscriptionService
from services.white_internet_service import WhiteInternetService
from utils.datetime_helpers import now_utc
from utils.rate_limiter import broadcast_send_limiter
from utils.telegram import safe_send_message

logger = logging.getLogger(__name__)

_active_bulk_tasks: set[asyncio.Task] = set()


def _track_bulk_task(coro: Any, *, name: str) -> asyncio.Task:
    task = asyncio.create_task(coro, name=name)
    _active_bulk_tasks.add(task)
    task.add_done_callback(_active_bulk_tasks.discard)
    return task


async def stop_admin_bulk_tasks() -> None:
    tasks = list(_active_bulk_tasks)
    _active_bulk_tasks.clear()
    for task in tasks:
        if not task.done():
            task.cancel()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)


async def send_direct_message(
    bot: Bot | None,
    *,
    admin_id: int,
    user_id: int,
    text: str,
) -> dict[str, Any]:
    if bot is None:
        raise ValueError("Telegram bot instance is unavailable")

    clean_text = (text or "").strip()
    if not clean_text:
        raise ValueError("Текст сообщения не может быть пустым")

    async with session_scope() as session:
        user = await session.get(User, user_id)
        if user is None or user.is_deleted:
            raise LookupError("Пользователь не найден")
        tg_id = int(user.telegram_id)

    try:
        await safe_send_message(
            bot,
            tg_id,
            clean_text,
            reply_markup=get_action_notification_keyboard("Открыть приложение", tab="connect"),
            parse_mode="HTML",
            raise_forbidden=True,
        )
    except TelegramForbiddenError as exc:
        async with session_scope() as session:
            await session.execute(
                update(User).where(User.id == user_id).values(is_bot_blocked=True)
            )
        raise ValueError("Пользователь заблокировал бота") from exc

    async with session_scope() as session:
        await AuditService.log_action(
            session,
            admin_id=admin_id,
            action=AdminAuditAction.ADMIN_DIRECT_MESSAGE_SENT,
            target_type="user",
            target_id=user_id,
            details={"telegram_id": tg_id, "preview": clean_text[:200]},
        )

    return {"user_id": user_id, "telegram_id": tg_id, "sent": True}


async def _run_mass_bonus_worker(
    bot: Bot | None,
    *,
    admin_id: int,
    batch_id: str,
    targets: list[tuple[int, int, bool]],
    amount_rub: int,
    reason: str,
    notify_users: bool,
) -> None:
    credited = 0
    notified = 0
    failed = 0
    reason_safe = html.escape(reason) if reason else "Начисление от администратора"
    notify_text = (
        f"<b>Вам начислен бонус +{amount_rub} ₽</b>\n\n"
        f"Комментарий: {reason_safe}"
    )

    for uid, tg_id, is_blocked in targets:
        try:
            async with session_scope() as session:
                _entry, created = await create_admin_adjustment(
                    session,
                    user_id=uid,
                    signed_amount=amount_rub,
                    idempotency_key=f"mass_bonus:{batch_id}:{uid}",
                    metadata={
                        "type": "mass_bonus",
                        "batch_id": batch_id,
                        "admin_id": admin_id,
                        "reason": reason,
                    },
                )
            if created:
                credited += 1
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            failed += 1
            logger.warning("Mass bonus %s failed for user %s: %s", batch_id, uid, exc)
            continue

        if notify_users and bot is not None and not is_blocked:
            try:
                await broadcast_send_limiter.acquire()
                await safe_send_message(
                    bot,
                    tg_id,
                    notify_text,
                    reply_markup=get_bonus_credited_keyboard(),
                    parse_mode="HTML",
                    raise_forbidden=True,
                )
                notified += 1
            except TelegramForbiddenError:
                try:
                    async with session_scope() as session:
                        await session.execute(
                            update(User).where(User.id == uid).values(is_bot_blocked=True)
                        )
                except Exception:
                    pass
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.debug("Mass bonus push failed for tg_id=%s: %s", tg_id, exc)

    if bot is not None:
        summary = (
            f"✅ <b>Массовое начисление завершено</b>\n\n"
            f"Начислено: <b>{credited}</b> польз. по <b>+{amount_rub} ₽</b>\n"
            f"Уведомлений доставлено: <b>{notified}</b>\n"
            f"Ошибок: <b>{failed}</b>"
        )
        try:
            await safe_send_message(
                bot,
                admin_id,
                summary,
            )
        except Exception:
            pass


async def start_mass_bonus(
    bot: Bot | None,
    *,
    admin_id: int,
    target_audience: str,
    amount_rub: int,
    reason: str,
    notify_users: bool = True,
) -> dict[str, Any]:
    if not (1 <= amount_rub <= 50000):
        raise ValueError("Сумма должна быть от 1 до 50000 ₽")
    if any(not t.done() for t in _active_bulk_tasks):
        raise ValueError("Массовая операция уже выполняется. Дождитесь завершения.")

    batch_id = uuid4().hex[:12]
    async with session_scope() as session:
        targets = await get_audience_targets(
            session,
            target_audience,
            admin_id=admin_id,
            include_bot_blocked=True,
        )
        if not targets:
            raise ValueError("Нет пользователей для выбранной аудитории")

        await AuditService.log_action(
            session,
            admin_id=admin_id,
            action=AdminAuditAction.MASS_BONUS_GRANTED,
            target_type="mass_bonus",
            details={
                "batch_id": batch_id,
                "audience": target_audience,
                "amount_rub": amount_rub,
                "target_count": len(targets),
                "notify_users": notify_users,
                "reason": reason,
            },
        )

    _track_bulk_task(
        _run_mass_bonus_worker(
            bot,
            admin_id=admin_id,
            batch_id=batch_id,
            targets=targets,
            amount_rub=amount_rub,
            reason=reason,
            notify_users=notify_users,
        ),
        name=f"mass_bonus:{batch_id}",
    )
    return {
        "batch_id": batch_id,
        "target_count": len(targets),
        "amount_rub": amount_rub,
        "status": "started",
    }


async def _run_mass_extend_worker(
    bot: Bot | None,
    *,
    admin_id: int,
    batch_id: str,
    service_scope: str,
    targets: list[tuple[int, int, bool]],
    days: int,
    reason: str,
    notify_users: bool,
) -> None:
    extended = 0
    skipped = 0
    notified = 0
    failed = 0
    scope_label = (
        "Белый Интернет" if service_scope == "white_internet" else "Стандартный доступ"
    )
    reason_safe = html.escape(reason) if reason else "Продление от администратора"
    notify_text = (
        f"<b>Ваша подписка ({scope_label}) продлена на +{days} дн.</b>\n\n"
        f"Комментарий: {reason_safe}"
    )

    for uid, tg_id, is_blocked in targets:
        did_extend = False
        try:
            async with session_scope() as session:
                if service_scope == "standard":
                    user_row = await session.get(User, uid)
                    if user_row is not None and not user_row.is_deleted:
                        vless_sub = await get_or_create_subscription(session, uid)
                        target_limit = None if (vless_sub.device_limit or 0) >= 2 else 2
                        updated = await SubscriptionService.extend_subscription(
                            session,
                            tg_id,
                            days=days,
                            new_device_limit=target_limit,
                            admin_id=admin_id,
                            reason=reason or f"mass_extend:{batch_id}",
                        )
                        if updated is not None:
                            did_extend = True

                if service_scope == "white_internet":
                    wi_sub = await session.scalar(
                        select(WhiteInternetSubscription).where(
                            WhiteInternetSubscription.user_id == uid
                        )
                    )
                    if wi_sub is not None:
                        ok, _msg, _sub = await WhiteInternetService.extend_subscription(
                            session, uid, days
                        )
                        if ok:
                            did_extend = True
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            failed += 1
            logger.warning("Mass extend %s failed for user %s: %s", batch_id, uid, exc)
            continue

        if not did_extend:
            skipped += 1
            continue
        extended += 1

        if notify_users and bot is not None and not is_blocked:
            try:
                await broadcast_send_limiter.acquire()
                await safe_send_message(
                    bot,
                    tg_id,
                    notify_text,
                    reply_markup=get_action_notification_keyboard("Открыть кабинет", tab="connect"),
                    parse_mode="HTML",
                    raise_forbidden=True,
                )
                notified += 1
            except TelegramForbiddenError:
                try:
                    async with session_scope() as session:
                        await session.execute(
                            update(User).where(User.id == uid).values(is_bot_blocked=True)
                        )
                except Exception:
                    pass
            except asyncio.CancelledError:
                raise
            except Exception:
                pass

    if bot is not None:
        summary = (
            f"✅ <b>Массовое продление завершено ({scope_label})</b>\n\n"
            f"Продлено: <b>{extended}</b> польз. (+{days} дн.)\n"
            f"Пропущено: <b>{skipped}</b>\n"
            f"Уведомлений доставлено: <b>{notified}</b>\n"
            f"Ошибок: <b>{failed}</b>"
        )
        try:
            await safe_send_message(
                bot,
                admin_id,
                summary,
            )
        except Exception:
            pass


async def start_mass_extend(
    bot: Bot | None,
    *,
    admin_id: int,
    service_scope: str,
    target_audience: str,
    days: int,
    reason: str,
    notify_users: bool = True,
) -> dict[str, Any]:
    if service_scope not in ("standard", "white_internet"):
        raise ValueError("Укажите сервис: standard или white_internet")
    if not (1 <= days <= 365):
        raise ValueError("Срок продления должен быть от 1 до 365 дней")
    if any(not t.done() for t in _active_bulk_tasks):
        raise ValueError("Массовая операция уже выполняется. Дождитесь завершения.")

    batch_id = uuid4().hex[:12]
    async with session_scope() as session:
        targets = await get_audience_targets(
            session,
            target_audience,
            admin_id=admin_id,
            include_bot_blocked=True,
        )
        if service_scope == "white_internet" and targets:
            from config.enums import WhiteInternetStatus

            eligible_ids = set(
                (
                    await session.scalars(
                        select(WhiteInternetSubscription.user_id).where(
                            WhiteInternetSubscription.user_id.in_([t[0] for t in targets]),
                            WhiteInternetSubscription.status != WhiteInternetStatus.DISABLED,
                        )
                    )
                ).all()
            )
            targets = [t for t in targets if t[0] in eligible_ids]

        if not targets:
            raise ValueError("Нет подходящих пользователей для продления")

        await AuditService.log_action(
            session,
            admin_id=admin_id,
            action=AdminAuditAction.MASS_SUB_EXTEND,
            target_type="mass_extend",
            details={
                "batch_id": batch_id,
                "service_scope": service_scope,
                "audience": target_audience,
                "days": days,
                "target_count": len(targets),
                "notify_users": notify_users,
                "reason": reason,
            },
        )

    _track_bulk_task(
        _run_mass_extend_worker(
            bot,
            admin_id=admin_id,
            batch_id=batch_id,
            service_scope=service_scope,
            targets=targets,
            days=days,
            reason=reason,
            notify_users=notify_users,
        ),
        name=f"mass_extend:{batch_id}",
    )
    return {
        "batch_id": batch_id,
        "service_scope": service_scope,
        "target_count": len(targets),
        "days": days,
        "status": "started",
    }


_MAX_INT32 = 2_147_483_647
_MAX_INT64 = 9_223_372_036_854_775_807

_ANOMALY_METADATA_KEYS: tuple[str, ...] = (
    "requires_manual_review",
    "settlement_held",
    "payment_amount_mismatch",
    "refund_amount_mismatch",
    "payment_id_mismatch",
    "refund_payment_id_mismatch",
    "late_payment_after_refund_on_canceled",
    "underpaid",
    "mixed_fallback_credited",
    "fulfillment_error",
    "negative_balance_rub",
    "partial_refund_amount_rub",
)


def extract_order_metadata_flags(metadata: object) -> list[str]:
    """Extract truthy anomaly/attention flag names from Order.metadata_."""
    if not isinstance(metadata, dict):
        return []
    return [key for key in _ANOMALY_METADATA_KEYS if bool(metadata.get(key))]


async def _revenue_for_period(session: AsyncSession, since: Any) -> Decimal:
    # Exclude internal wallet spends to avoid double-counting topups.
    external_orders_sum = await session.scalar(
        select(func.coalesce(func.sum(Order.amount_rub), 0)).where(
            Order.status == OrderStatus.PAID,
            Order.payment_method != "wallet",
            func.coalesce(Order.paid_at, Order.created_at) >= since,
        )
    )
    return Decimal(str(external_orders_sum or 0))


async def get_overview_finances_summary(session: AsyncSession) -> dict[str, Any]:
    """Return compact revenue and attention-order counts for admin overview."""
    now = now_utc()
    rev_24h = await _revenue_for_period(session, now - timedelta(hours=24))
    rev_7d = await _revenue_for_period(session, now - timedelta(days=7))
    attention_count = int(
        (
            await session.scalar(
                select(func.count(Order.id)).where(
                    or_(
                        Order.metadata_["requires_manual_review"].astext == "true",
                        Order.metadata_["settlement_held"].astext == "true",
                    )
                )
            )
        )
        or 0
    )
    return {
        "revenue_24h": float(rev_24h),
        "revenue_7d": float(rev_7d),
        "attention_orders_count": attention_count,
    }


async def build_orders_payment_breakdown(
    session: AsyncSession,
    orders: Sequence[Order],
) -> dict[UUID, dict[str, Any]]:
    """Compute per-order external/bonus/real-balance payment breakdown in a single batch query."""
    if not orders:
        return {}

    order_ids = [order.id for order in orders]
    ledger_rows = (
        await session.execute(
            select(
                AccountLedgerEntry.order_id,
                AccountLedgerEntry.bucket,
                func.coalesce(func.sum(func.abs(AccountLedgerEntry.amount)), 0),
            )
            .where(
                AccountLedgerEntry.order_id.in_(order_ids),
                AccountLedgerEntry.entry_type == AccountLedgerEntryType.PURCHASE_DEBIT,
            )
            .group_by(AccountLedgerEntry.order_id, AccountLedgerEntry.bucket)
        )
    ).all()

    ledger_by_order: dict[UUID, dict[str, float]] = {}
    for oid, bucket, amount_sum in ledger_rows:
        if oid is None:
            continue
        buckets = ledger_by_order.setdefault(oid, {})
        buckets[str(bucket)] = float(amount_sum or 0)

    result: dict[UUID, dict[str, Any]] = {}
    for order in orders:
        payment_method = order.payment_method
        is_wallet = payment_method == "wallet"
        external_paid_rub = 0.0 if is_wallet else float(order.amount_rub or 0)

        buckets = ledger_by_order.get(order.id)
        if buckets is not None:
            bonus_paid_rub = float(buckets.get("bonus", 0.0))
            real_balance_paid_rub = float(buckets.get("real", 0.0))
            wallet_debit_rub = bonus_paid_rub + real_balance_paid_rub
        else:
            bonus_paid_rub = 0.0
            real_balance_paid_rub = 0.0
            meta = order.metadata_ if isinstance(order.metadata_, dict) else {}
            raw_wallet_debit = meta.get("wallet_debit_rub")
            if raw_wallet_debit is not None:
                try:
                    wallet_debit_rub = max(0.0, float(raw_wallet_debit))
                except (TypeError, ValueError):
                    wallet_debit_rub = 0.0
            else:
                wallet_debit_rub = float(order.amount_rub or 0) if is_wallet else 0.0

        result[order.id] = {
            "payment_method": payment_method,
            "external_paid_rub": external_paid_rub,
            "bonus_paid_rub": bonus_paid_rub,
            "real_balance_paid_rub": real_balance_paid_rub,
            "wallet_debit_rub": wallet_debit_rub,
            "total_rub": external_paid_rub + wallet_debit_rub,
        }
    return result


async def list_finances(
    session: AsyncSession,
    *,
    kind: str = "orders",
    status: str = "",
    q: str = "",
    attention_only: bool = False,
    offset: int = 0,
    limit: int = 25,
) -> dict[str, Any]:
    now = now_utc()
    rev_24h = await _revenue_for_period(session, now - timedelta(hours=24))
    rev_7d = await _revenue_for_period(session, now - timedelta(days=7))
    rev_30d = await _revenue_for_period(session, now - timedelta(days=30))

    paid_orders_count = int(
        (
            await session.scalar(
                select(func.count(Order.id)).where(Order.status == OrderStatus.PAID)
            )
        )
        or 0
    )

    clean_q = (q or "").strip()
    clean_status = (status or "").strip()
    safe_limit = max(1, min(int(limit), 100))
    safe_offset = max(0, int(offset))

    stmt = select(Order, User).join(User, User.id == Order.user_id)
    count_stmt = select(func.count(Order.id)).join(User, User.id == Order.user_id)
    if kind == "topups":
        stmt = stmt.where(Order.service_type == OrderServiceType.TOPUP)
        count_stmt = count_stmt.where(Order.service_type == OrderServiceType.TOPUP)
    elif kind == "subscriptions":
        sub_types = [OrderServiceType.STANDARD, OrderServiceType.WHITE_INTERNET]
        stmt = stmt.where(Order.service_type.in_(sub_types))
        count_stmt = count_stmt.where(Order.service_type.in_(sub_types))
    elif kind == "gateway":
        stmt = stmt.where(Order.payment_method != "wallet")
        count_stmt = count_stmt.where(Order.payment_method != "wallet")

    if clean_status:
        stmt = stmt.where(Order.status == clean_status)
        count_stmt = count_stmt.where(Order.status == clean_status)
    if attention_only:
        attn_cond = or_(
            Order.metadata_["requires_manual_review"].astext == "true",
            Order.metadata_["settlement_held"].astext == "true",
        )
        stmt = stmt.where(attn_cond)
        count_stmt = count_stmt.where(attn_cond)
    if clean_q:
        escaped_q = clean_q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        clean_user_q = clean_q.lstrip("@")
        conds = [
            cast(Order.id, String).ilike(f"%{escaped_q}%", escape="\\"),
            Order.external_id.ilike(f"%{escaped_q}%", escape="\\"),
            Order.description.ilike(f"%{escaped_q}%", escape="\\"),
        ]
        if clean_user_q:
            escaped_user_q = clean_user_q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            conds.extend([
                User.username.ilike(f"%{escaped_user_q}%", escape="\\"),
                User.first_name.ilike(f"%{escaped_user_q}%", escape="\\"),
            ])
        try:
            conds.append(Order.id == UUID(clean_q))
        except ValueError:
            pass
        if clean_q.isdigit():
            num = int(clean_q)
            if 0 < num <= _MAX_INT64:
                conds.append(User.telegram_id == num)
            if 0 < num <= _MAX_INT32:
                conds.append(Order.user_id == num)
        stmt = stmt.where(or_(*conds))
        count_stmt = count_stmt.where(or_(*conds))

    total = int((await session.scalar(count_stmt)) or 0)
    rows = (
        await session.execute(
            stmt.order_by(desc(Order.created_at), desc(Order.id))
            .offset(safe_offset)
            .limit(safe_limit)
        )
    ).all()
    breakdown_map = await build_orders_payment_breakdown(
        session, [order for order, _usr in rows]
    )
    items = [
        {
            "kind": "order",
            "id": str(order.id),
            "user_id": order.user_id,
            "telegram_id": usr.telegram_id,
            "username": usr.username or "",
            "amount_rub": float(order.amount_rub),
            "status": order.status,
            "service_type": order.service_type,
            "duration_days": order.duration_days,
            "device_limit": order.device_limit,
            **breakdown_map[order.id],
            "external_id": order.external_id,
            "reference": order.external_id or order.description or "",
            "metadata_flags": extract_order_metadata_flags(order.metadata_),
            "created_at": order.created_at.isoformat() if order.created_at else None,
            "paid_at": order.paid_at.isoformat() if order.paid_at else None,
            "refunded_at": order.refunded_at.isoformat() if order.refunded_at else None,
            "expires_at": order.expires_at.isoformat() if order.expires_at else None,
        }
        for order, usr in rows
    ]

    return {
        "summary": {
            "revenue_24h": float(rev_24h),
            "revenue_7d": float(rev_7d),
            "revenue_30d": float(rev_30d),
            "paid_orders_count": paid_orders_count,
        },
        "total": total,
        "offset": safe_offset,
        "limit": safe_limit,
        "items": items,
    }


async def list_audit_logs(
    session: AsyncSession,
    *,
    action: str = "",
    target_type: str = "",
    q: str = "",
    offset: int = 0,
    limit: int = 40,
) -> dict[str, Any]:
    stmt = select(AuditLog)
    count_stmt = select(func.count(AuditLog.id))
    clean_action = (action or "").strip()
    clean_target_type = (target_type or "").strip()
    clean_q = (q or "").strip()

    if clean_action:
        stmt = stmt.where(AuditLog.action == clean_action)
        count_stmt = count_stmt.where(AuditLog.action == clean_action)
    if clean_target_type:
        stmt = stmt.where(func.lower(AuditLog.target_type) == clean_target_type.lower())
        count_stmt = count_stmt.where(func.lower(AuditLog.target_type) == clean_target_type.lower())
    if clean_q:
        escaped_q = clean_q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        conds = [
            AuditLog.details.ilike(f"%{escaped_q}%", escape="\\"),
            AuditLog.action.ilike(f"%{escaped_q}%", escape="\\"),
        ]
        if clean_q.isdigit():
            num = int(clean_q)
            if 0 <= num <= _MAX_INT64:
                conds.extend([AuditLog.admin_id == num, AuditLog.target_id == num])
            if 0 < num <= _MAX_INT64:
                conds.append(
                    and_(
                        func.lower(AuditLog.target_type) == "user",
                        AuditLog.target_id.in_(
                            select(User.id).where(User.telegram_id == num)
                        ),
                    )
                )
            if 0 < num <= _MAX_INT32:
                conds.append(AuditLog.id == num)
        stmt = stmt.where(or_(*conds))
        count_stmt = count_stmt.where(or_(*conds))

    safe_limit = max(1, min(int(limit), 100))
    safe_offset = max(0, int(offset))

    total = int((await session.scalar(count_stmt)) or 0)
    rows = (
        await session.scalars(
            stmt.order_by(desc(AuditLog.id)).offset(safe_offset).limit(safe_limit)
        )
    ).all()
    recent_actions = list(
        (
            await session.scalars(
                select(AuditLog.action).distinct().order_by(AuditLog.action.asc()).limit(100)
            )
        ).all()
    )

    return {
        "total": total,
        "offset": safe_offset,
        "limit": safe_limit,
        "actions": recent_actions,
        "items": [
            {
                "id": row.id,
                "admin_id": row.admin_id,
                "action": row.action,
                "target_type": row.target_type or "",
                "target_id": row.target_id,
                "details": row.details or "",
                "created_at": row.created_at.isoformat() if row.created_at else None,
            }
            for row in rows
        ],
    }
