"""Admin Mini App operations: direct messages, mass bonus/extend, finances, audit logs."""

from __future__ import annotations

import asyncio
from datetime import timedelta
from decimal import Decimal
import html
import logging
from typing import Any
from uuid import uuid4

from aiogram import Bot
from aiogram.exceptions import TelegramForbiddenError
from sqlalchemy import desc, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from bot.keyboards.notifications import get_dismiss_notification_keyboard
from config.enums import AdminAuditAction, OrderStatus, PaymentProviderStatus
from database.connection import session_scope
from database.models import (
    AuditLog,
    Order,
    Payment,
    User,
    WhiteInternetSubscription,
)
from database.repositories.account_ledger_repo import create_admin_adjustment
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
            reply_markup=get_dismiss_notification_keyboard(),
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
        f"🎁 <b>Вам начислен бонус +{amount_rub} ₽</b>\n\n"
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
                    reply_markup=get_dismiss_notification_keyboard(),
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
                reply_markup=get_dismiss_notification_keyboard(),
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
    scope_label = "Белый Интернет" if service_scope == "white_internet" else "Стандартный доступ"
    reason_safe = html.escape(reason) if reason else "Продление от администратора"
    notify_text = (
        f"🎁 <b>Ваша подписка ({scope_label}) продлена на +{days} дн.</b>\n\n"
        f"Комментарий: {reason_safe}"
    )

    for uid, tg_id, is_blocked in targets:
        did_extend = False
        try:
            async with session_scope() as session:
                if service_scope == "white_internet":
                    ok, _msg, _sub = await WhiteInternetService.extend_subscription(
                        session, uid, days
                    )
                    did_extend = bool(ok)
                else:
                    updated = await SubscriptionService.extend_subscription(
                        session,
                        tg_id,
                        days=days,
                        admin_id=admin_id,
                        reason=reason or f"mass_extend:{batch_id}",
                    )
                    did_extend = updated is not None
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
                    reply_markup=get_dismiss_notification_keyboard(),
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
                reply_markup=get_dismiss_notification_keyboard(),
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

    batch_id = uuid4().hex[:12]
    async with session_scope() as session:
        targets = await get_audience_targets(
            session,
            target_audience,
            admin_id=admin_id,
            include_bot_blocked=True,
        )
        if service_scope == "white_internet" and targets:
            eligible_ids = set(
                (
                    await session.scalars(
                        select(WhiteInternetSubscription.user_id).where(
                            WhiteInternetSubscription.user_id.in_([t[0] for t in targets]),
                            WhiteInternetSubscription.is_trial.is_(False),
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


async def _revenue_for_period(session: AsyncSession, since: Any) -> Decimal:
    # Exclude balance-settled orders from cashflow totals to avoid double-counting topups.
    external_orders_sum = await session.scalar(
        select(func.coalesce(func.sum(Order.amount_rub), 0)).where(
            Order.status == OrderStatus.PAID,
            Order.payment_method != "balance",
            Order.created_at >= since,
        )
    )
    legacy_topups_sum = await session.scalar(
        select(func.coalesce(func.sum(Payment.amount), 0)).where(
            Payment.provider_status == PaymentProviderStatus.SUCCEEDED,
            Payment.created_at >= since,
        )
    )
    return Decimal(str(external_orders_sum or 0)) + Decimal(str(legacy_topups_sum or 0))


async def list_finances(
    session: AsyncSession,
    *,
    kind: str = "orders",
    status: str = "",
    q: str = "",
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

    items: list[dict[str, Any]] = []
    total = 0

    if kind == "payments":
        stmt = select(Payment, User).join(User, User.id == Payment.user_id)
        count_stmt = select(func.count(Payment.id)).join(User, User.id == Payment.user_id)
        if clean_status:
            stmt = stmt.where(Payment.provider_status == clean_status)
            count_stmt = count_stmt.where(Payment.provider_status == clean_status)
        if clean_q:
            conds = [Payment.public_order_id.ilike(f"%{clean_q}%"), User.username.ilike(f"%{clean_q}%")]
            if clean_q.isdigit():
                num = int(clean_q)
                conds.extend([Payment.id == num, Payment.user_id == num, User.telegram_id == num])
            stmt = stmt.where(or_(*conds))
            count_stmt = count_stmt.where(or_(*conds))

        total = int((await session.scalar(count_stmt)) or 0)
        rows = (
            await session.execute(
                stmt.order_by(desc(Payment.id)).offset(safe_offset).limit(safe_limit)
            )
        ).all()
        for pay, usr in rows:
            items.append({
                "kind": "payment",
                "id": pay.id,
                "user_id": pay.user_id,
                "telegram_id": usr.telegram_id,
                "username": usr.username or "",
                "amount_rub": float(pay.amount),
                "status": pay.provider_status,
                "fulfillment_status": pay.fulfillment_status,
                "service_type": "topup",
                "payment_method": "yookassa",
                "reference": pay.public_order_id,
                "created_at": pay.created_at.isoformat() if pay.created_at else None,
            })
    else:
        stmt = select(Order, User).join(User, User.id == Order.user_id)
        count_stmt = select(func.count(Order.id)).join(User, User.id == Order.user_id)
        if clean_status:
            stmt = stmt.where(Order.status == clean_status)
            count_stmt = count_stmt.where(Order.status == clean_status)
        if clean_q:
            conds = [
                Order.external_id.ilike(f"%{clean_q}%"),
                Order.description.ilike(f"%{clean_q}%"),
                User.username.ilike(f"%{clean_q}%"),
            ]
            if clean_q.isdigit():
                num = int(clean_q)
                conds.extend([Order.id == num, Order.user_id == num, User.telegram_id == num])
            stmt = stmt.where(or_(*conds))
            count_stmt = count_stmt.where(or_(*conds))

        total = int((await session.scalar(count_stmt)) or 0)
        rows = (
            await session.execute(
                stmt.order_by(desc(Order.id)).offset(safe_offset).limit(safe_limit)
            )
        ).all()
        for order, usr in rows:
            items.append({
                "kind": "order",
                "id": order.id,
                "user_id": order.user_id,
                "telegram_id": usr.telegram_id,
                "username": usr.username or "",
                "amount_rub": float(order.amount_rub),
                "status": order.status,
                "service_type": order.service_type,
                "duration_days": order.duration_days,
                "device_limit": order.device_limit,
                "payment_method": order.payment_method,
                "reference": order.external_id or order.description or "",
                "created_at": order.created_at.isoformat() if order.created_at else None,
                "paid_at": order.paid_at.isoformat() if order.paid_at else None,
            })

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
    q: str = "",
    offset: int = 0,
    limit: int = 40,
) -> dict[str, Any]:
    stmt = select(AuditLog)
    count_stmt = select(func.count(AuditLog.id))
    clean_action = (action or "").strip()
    clean_q = (q or "").strip()

    if clean_action:
        stmt = stmt.where(AuditLog.action == clean_action)
        count_stmt = count_stmt.where(AuditLog.action == clean_action)
    if clean_q:
        conds = [AuditLog.details.ilike(f"%{clean_q}%"), AuditLog.action.ilike(f"%{clean_q}%")]
        if clean_q.isdigit():
            num = int(clean_q)
            conds.extend([AuditLog.admin_id == num, AuditLog.target_id == num, AuditLog.id == num])
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
