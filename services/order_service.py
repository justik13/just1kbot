"""Order lifecycle management service for simple billing."""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP
import logging
import math
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bot import texts
from database.models import Order, Tariff, User
from database.repositories.account_ledger_repo import (
    create_order_credit,
    create_order_debit,
    create_order_refund_debit,
    get_account_balance,
)
from integrations.payment_gateways.factory import get_payment_gateway
from services.fulfillment_service import FulfillmentService
from services.referral_bonus import reverse_referral_bonus_for_topup
from utils.datetime_helpers import now_utc

logger = logging.getLogger(__name__)


class InsufficientBalanceError(Exception):
    def __init__(self, required: Decimal, available: Decimal):
        super().__init__(
            f"Insufficient balance: required {required}, available {available}"
        )
        self.required = required
        self.available = available


class FinancialHoldBlockedError(Exception):
    pass


class AccountDebtBlockedError(Exception):
    pass


class OrderService:
    @staticmethod
    def calculate_tariff_change(
        current_tariff: Tariff | None,
        target_tariff: Tariff,
        subscription_end: datetime | None,
        now: datetime | None = None,
    ) -> tuple[Decimal, int]:
        """Calculate required payment in RUB and resulting days for tariff change."""
        now = now or now_utc()
        if not subscription_end or subscription_end <= now or not current_tariff:
            return Decimal(target_tariff.price_rub), target_tariff.duration_days

        days_left = max(
            0,
            math.ceil((subscription_end - now).total_seconds() / 86400.0)
            if subscription_end > now
            else 0,
        )
        if days_left <= 0:
            return Decimal(target_tariff.price_rub), target_tariff.duration_days

        c_days = float(current_tariff.duration_days) if current_tariff.duration_days > 0 else 30.0
        c_price = float(current_tariff.price_rub) if current_tariff.price_rub > 0 else 0.0
        t_days = float(target_tariff.duration_days) if target_tariff.duration_days > 0 else 30.0
        t_price = float(target_tariff.price_rub) if target_tariff.price_rub > 0 else 0.0

        if c_price <= 0.0 or t_price <= 0.0:
            return Decimal(target_tariff.price_rub), target_tariff.duration_days

        c_daily = c_price / c_days
        t_daily = t_price / t_days

        remaining_rub = Decimal(round(days_left * c_daily))
        target_cost = Decimal(target_tariff.price_rub)

        if remaining_rub >= target_cost:
            # User has sufficient unspent value for at least 1 cycle of target tariff.
            # Free switch (0 ₽ due), unspent value converts to target tariff days:
            due_rub = Decimal("0.00")
            if abs(c_daily - t_daily) < 1e-6:
                # Same daily rate: exact days preserved without any rounding drift
                resulting_days = days_left
            else:
                resulting_days = max(1, round(float(remaining_rub) / t_daily))
        else:
            # Remaining value is less than 1 cycle of target tariff.
            # Unspent value is applied as a discount on the target tariff:
            due_rub = max(Decimal("0.00"), target_cost - remaining_rub)
            resulting_days = target_tariff.duration_days

        return due_rub, resulting_days

    @staticmethod
    async def create_order(
        session: AsyncSession,
        *,
        user_id: int,
        service_type: str = "awg",
        tariff_id: int | None = None,
        amount_rub: Decimal | None = None,
        duration_days: int | None = None,
        traffic_bytes: int = 0,
        device_limit: int | None = None,
        payment_method: str = "yookassa",
        description: str | None = None,
        return_url: str | None = None,
        metadata: dict | None = None,
        bot_username: str | None = None,
    ) -> Order:
        """Create a new commercial order and obtain payment link if external gateway."""
        user = await session.get(User, user_id)
        order_meta: dict = dict(metadata) if metadata else {}
        if tariff_id is not None:
            tariff = await session.get(Tariff, tariff_id)
            if tariff:
                now = now_utc()
                current_tid = getattr(user, "current_tariff_id", None)
                sub_end = getattr(user, "subscription_end", None)
                is_tariff_change = bool(
                    user
                    and current_tid
                    and current_tid != tariff.id
                    and sub_end
                    and sub_end > now
                )
                if is_tariff_change:
                    order_meta["is_tariff_change"] = True
                    current_tariff = await session.get(Tariff, current_tid)
                    due_rub, resulting_days = OrderService.calculate_tariff_change(
                        current_tariff, tariff, sub_end, now=now
                    )
                    if amount_rub is None:
                        amount_rub = due_rub
                    if duration_days is None:
                        duration_days = resulting_days
                else:
                    if amount_rub is None:
                        amount_rub = Decimal(tariff.price_rub)
                    if duration_days is None:
                        duration_days = tariff.duration_days
                if device_limit is None:
                    device_limit = tariff.device_limit
                if description is None:
                    description = f"{texts.CHECKOUT_DESCRIPTION_DEFAULT} ({tariff.name})"

        final_amount = amount_rub if amount_rub is not None else Decimal("0.00")
        final_duration = duration_days if duration_days is not None else 0
        final_desc = description or texts.CHECKOUT_DESCRIPTION_DEFAULT

        # 1. Deduplication guard for pending external orders (prevent double-clicks)
        if payment_method != "wallet":
            cutoff = now_utc() - timedelta(minutes=15)
            existing = await session.scalar(
                select(Order)
                .where(
                    Order.user_id == user_id,
                    Order.service_type == service_type,
                    Order.tariff_id == tariff_id,
                    Order.duration_days == final_duration,
                    Order.traffic_bytes == traffic_bytes,
                    Order.device_limit == device_limit,
                    Order.payment_method == payment_method,
                    Order.amount_rub == final_amount,
                    Order.status == "pending",
                    Order.created_at >= cutoff,
                    Order.payment_url.is_not(None),
                )
                .order_by(Order.created_at.desc())
                .limit(1)
            )
            if existing and existing.payment_url:
                return existing

        order = Order(
            id=uuid.uuid4(),
            user_id=user_id,
            service_type=service_type,
            tariff_id=tariff_id,
            amount_rub=final_amount,
            duration_days=final_duration,
            traffic_bytes=traffic_bytes,
            device_limit=device_limit,
            payment_method=payment_method,
            status="pending",
            description=final_desc,
            metadata_=order_meta,
        )
        session.add(order)
        await session.flush()

        if payment_method != "wallet":
            gateway = get_payment_gateway(payment_method)
            try:
                invoice = await gateway.create_payment_url(
                    order_id=str(order.id),
                    amount_rub=order.amount_rub,
                    description=final_desc,
                    return_url=return_url,
                    bot_username=bot_username,
                )
                order.external_id = invoice.external_id
                order.payment_url = invoice.payment_url
            except Exception as exc:
                logger.exception(
                    "Gateway failed to create payment for order %s: %s", order.id, exc
                )
                await session.rollback()
                raise

        await session.flush()
        return order

    @staticmethod
    async def pay_from_wallet(
        session: AsyncSession,
        *,
        user_id: int,
        service_type: str = "awg",
        tariff_id: int | None = None,
        amount_rub: Decimal | None = None,
        duration_days: int | None = None,
        traffic_bytes: int = 0,
        device_limit: int | None = None,
        description: str | None = None,
        metadata: dict | None = None,
    ) -> Order:
        """Pay for an order immediately using internal wallet balance."""
        user = await session.get(User, user_id)
        if user and getattr(user, "financial_hold", False):
            raise FinancialHoldBlockedError("Financial hold active on user")

        order_meta: dict = dict(metadata) if metadata else {}
        if tariff_id is not None:
            tariff = await session.get(Tariff, tariff_id)
            if tariff:
                now = now_utc()
                current_tid = getattr(user, "current_tariff_id", None)
                sub_end = getattr(user, "subscription_end", None)
                is_tariff_change = bool(
                    user
                    and current_tid
                    and current_tid != tariff.id
                    and sub_end
                    and sub_end > now
                )
                if is_tariff_change:
                    order_meta["is_tariff_change"] = True
                    current_tariff = await session.get(Tariff, current_tid)
                    due_rub, resulting_days = OrderService.calculate_tariff_change(
                        current_tariff, tariff, sub_end, now=now
                    )
                    if amount_rub is None:
                        amount_rub = due_rub
                    if duration_days is None:
                        duration_days = resulting_days
                else:
                    if amount_rub is None:
                        amount_rub = Decimal(tariff.price_rub)
                    if duration_days is None:
                        duration_days = tariff.duration_days
                if device_limit is None:
                    device_limit = tariff.device_limit
                if description is None:
                    description = f"{texts.CHECKOUT_DESCRIPTION_DEFAULT} ({tariff.name})"

        cost = amount_rub if amount_rub is not None else Decimal("0.00")
        balance_snapshot = await get_account_balance(
            session, user_id=user_id, for_update=True
        )
        if balance_snapshot.debt > 0:
            raise AccountDebtBlockedError("Account debt prevents wallet purchases")
        if balance_snapshot.available < cost:
            raise InsufficientBalanceError(
                required=cost, available=balance_snapshot.available
            )

        order = Order(
            id=uuid.uuid4(),
            user_id=user_id,
            service_type=service_type,
            tariff_id=tariff_id,
            amount_rub=cost,
            duration_days=duration_days if duration_days is not None else 0,
            traffic_bytes=traffic_bytes,
            device_limit=device_limit,
            payment_method="wallet",
            status="paid",
            description=description or texts.CHECKOUT_DESCRIPTION_DEFAULT,
            paid_at=now_utc(),
            metadata_=order_meta,
        )
        session.add(order)
        await session.flush()

        # Debit wallet ledger
        if cost > 0:
            await create_order_debit(
                session,
                user_id=user_id,
                amount_rub=cost,
                order_id=order.id,
                metadata={"description": order.description},
            )

        # Fulfill
        await FulfillmentService.fulfill_order(session, order)
        await session.flush()
        return order

    @staticmethod
    def mark_order_canceled(order: Order, reason: str = "gateway_canceled") -> None:
        """Mark order canceled and record cancellation reason to prevent invalid revival."""
        if order.status in ("pending", "canceled"):
            order.status = "canceled"
            order_meta = dict(order.metadata_ or {})
            order_meta["cancellation_reason"] = reason
            order.metadata_ = order_meta

    @staticmethod
    async def mark_order_paid(
        session: AsyncSession,
        order_id: uuid.UUID | str,
        *,
        external_id: str | None = None,
        paid_amount_rub: Decimal | None = None,
    ) -> Order | None:
        """Idempotent, atomic order settlement with row-level locking."""
        order_uuid = uuid.UUID(str(order_id)) if not isinstance(order_id, uuid.UUID) else order_id
        order = await session.scalar(
            select(Order).where(Order.id == order_uuid).with_for_update()
        )
        if not order:
            logger.warning("mark_order_paid: Order %s not found", order_id)
            return None

        if order.status == "paid":
            logger.info("Order %s already marked paid, ignoring duplicate execution", order.id)
            order._newly_paid = False
            return order

        if order.status == "refunded":
            logger.warning("Cannot mark refunded order %s as paid", order.id)
            order._newly_paid = False
            return order

        was_canceled = order.status == "canceled"
        if was_canceled:
            cancellation_reason = (order.metadata_ or {}).get("cancellation_reason")
            if cancellation_reason == "gateway_canceled":
                logger.warning(
                    "Cannot mark order %s paid: order was canceled by payment gateway",
                    order.id,
                )
                order._newly_paid = False
                return None
            logger.info(
                "Reviving canceled order %s on valid payment received", order.id
            )

        if paid_amount_rub is not None and paid_amount_rub < order.amount_rub:
            logger.error(
                "Order %s underpaid: expected %s, got %s",
                order.id,
                order.amount_rub,
                paid_amount_rub,
            )
            return None

        order.status = "paid"
        order.paid_at = now_utc()
        order._newly_paid = True
        if was_canceled:
            order_meta = dict(order.metadata_ or {})
            order_meta["revived_from_canceled"] = True
            order.metadata_ = order_meta
        if external_id:
            order.external_id = external_id

        # Ledger records: ONLY topup credits user's bot wallet
        if order.service_type == "topup":
            credit_meta = {"source": f"{order.payment_method}_topup"}
            if was_canceled:
                credit_meta["revived"] = True
            await create_order_credit(
                session,
                user_id=order.user_id,
                amount_rub=order.amount_rub,
                order_id=order.id,
                metadata=credit_meta,
            )
            from services.referral_bonus import grant_referral_bonus_for_topup

            await grant_referral_bonus_for_topup(
                session,
                purchaser_user_id=order.user_id,
                order_id=str(order.id),
                topup_amount=order.amount_rub,
            )

        # Fulfill benefits linearly
        await FulfillmentService.fulfill_order(session, order)
        await session.flush()
        logger.info("Order %s marked paid and fulfilled successfully", order.id)
        return order

    @staticmethod
    async def process_webhook_event(
        session: AsyncSession,
        payload: dict,
    ) -> Order | None:
        """Handle incoming webhook event (payment succeeded or refund succeeded)."""
        gateway = get_payment_gateway("yookassa")
        result = await gateway.parse_webhook(payload)
        if not result.order_id and not result.external_id:
            logger.warning(
                "Received webhook without order_id or external_id: %s", payload
            )
            return None

        order = None
        if result.order_id:
            try:
                order_uuid = uuid.UUID(result.order_id)
                order = await session.scalar(
                    select(Order).where(Order.id == order_uuid).with_for_update()
                )
            except (ValueError, TypeError):
                pass

        related_id = getattr(result, "related_external_id", None) or getattr(result, "payment_id", None)
        if not order and related_id:
            order = await session.scalar(
                select(Order).where(Order.external_id == related_id).with_for_update()
            )

        if not order and result.external_id:
            order = await session.scalar(
                select(Order).where(Order.external_id == result.external_id).with_for_update()
            )

        if not order:
            logger.warning(
                "Order not found for webhook: order_id=%s, ext_id=%s",
                result.order_id,
                result.external_id,
            )
            return None

        if result.is_paid:
            paid_order = await OrderService.mark_order_paid(
                session,
                order.id,
                external_id=result.external_id,
                paid_amount_rub=result.amount_rub,
            )
            if paid_order is None:
                cancellation_reason = (order.metadata_ or {}).get("cancellation_reason")
                if cancellation_reason == "gateway_canceled":
                    logger.warning(
                        "Ignoring payment.succeeded webhook for gateway_canceled order %s (ext_id=%s)",
                        order.id,
                        result.external_id,
                    )
                    order_meta = dict(order.metadata_ or {})
                    order_meta["late_payment_attempt_rejected"] = str(result.external_id)
                    order.metadata_ = order_meta
                    order._newly_paid = False
                    await session.flush()
                    return order
                return None
            return paid_order

        if result.is_canceled:
            if order.status == "pending":
                OrderService.mark_order_canceled(order, reason="gateway_canceled")
                await session.flush()
                logger.info(
                    "Order %s marked canceled via gateway webhook (external_id=%s)",
                    order.id,
                    result.external_id,
                )
            return order

        if result.is_refunded:
            if order.status not in ("paid", "refunded"):
                logger.warning(
                    "Refund received for non-paid order %s (status=%s), deferring to retry",
                    order.id,
                    order.status,
                )
                return None

            refund_amount = (
                result.amount_rub
                if result.amount_rub is not None
                else order.amount_rub
            )
            order_meta = dict(order.metadata_ or {})
            refunded_so_far = Decimal(str(order_meta.get("refunded_amount_rub", "0")))
            processed_refund_ids = list(order_meta.get("processed_refund_ids", []))

            # Deduplication for the exact same refund event
            if result.external_id and result.external_id in processed_refund_ids:
                logger.info(
                    "Refund %s for order %s already processed, ignoring duplicate webhook",
                    result.external_id,
                    order.id,
                )
                return order

            if order.status == "refunded" and refunded_so_far >= order.amount_rub:
                logger.info(
                    "Order %s already marked fully refunded, ignoring duplicate webhook",
                    order.id,
                )
                return order

            new_total_refunded = refunded_so_far + refund_amount
            order_meta["refunded_amount_rub"] = str(new_total_refunded)
            if result.external_id:
                processed_refund_ids.append(result.external_id)
            order_meta["processed_refund_ids"] = processed_refund_ids
            order.metadata_ = order_meta

            if new_total_refunded >= order.amount_rub:
                order.status = "refunded"
            order.refunded_at = now_utc()

            if order.service_type == "topup":
                refund_ref = (
                    (result.external_id or "").strip()
                    or f"refund_{len(processed_refund_ids)}"
                )
                target_cumulative = new_total_refunded.quantize(
                    Decimal("1"), rounding=ROUND_HALF_UP
                )
                prev_cumulative = refunded_so_far.quantize(
                    Decimal("1"), rounding=ROUND_HALF_UP
                )
                ledger_delta = target_cumulative - prev_cumulative

                if ledger_delta > 0:
                    await create_order_refund_debit(
                        session,
                        user_id=order.user_id,
                        amount_rub=ledger_delta,
                        order_id=order.id,
                        refund_id=refund_ref,
                        metadata={"source": "yookassa_refund"},
                    )
                await reverse_referral_bonus_for_topup(
                    session,
                    order_id=order.id,
                    refund_amount=refund_amount,
                    original_topup_amount=order.amount_rub,
                    total_refunded_amount=new_total_refunded,
                    refund_id=refund_ref,
                )

            is_fully_refunded = (new_total_refunded >= order.amount_rub or order.status == "refunded")
            if is_fully_refunded:
                await FulfillmentService.revoke_order(session, order)
                logger.info(
                    "Order %s fully refunded (total %s / %s) and revoked",
                    order.id,
                    new_total_refunded,
                    order.amount_rub,
                )
            else:
                logger.info(
                    "Order %s partially refunded %s RUB (total %s / %s), revocation deferred until full refund",
                    order.id,
                    refund_amount,
                    new_total_refunded,
                    order.amount_rub,
                )
            await session.flush()
            return order
        logger.info(
            "Webhook event %s for order %s has no actionable transition, acknowledging",
            result.event_type,
            order.id,
        )
        return order
