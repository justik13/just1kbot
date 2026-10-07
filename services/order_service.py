"""Order lifecycle management service for simple billing."""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal, ROUND_DOWN, ROUND_HALF_UP
import logging
import math
import uuid

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from bot import texts
from config.constants import REFERRAL_WELCOME_DISCOUNT_PERCENT
from database.models import AccountLedgerEntry, Order, Tariff, User
from database.repositories.users_repo import (
    is_eligible_for_referral_first_discount,
)
from database.repositories.account_ledger_repo import (
    create_order_credit,
    create_order_debit,
    create_order_refund_debit,
    get_account_balance,
)
from services.order_notifications import mark_notify_pending
from integrations.payment_gateways.base import PaymentCreationAmbiguousError
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
    async def _load_settlement_user(
        session: AsyncSession, user_id: int
    ) -> User | None:
        """Authoritative user read for the settlement boundary.

        Never use session.get() here: it may return the identity-map
        cached instance without SQL (and without any row lock), so a hold
        set concurrently (e.g. between UserContextMiddleware load and an
        order_check press) would be invisible. An explicit SELECT ...
        FOR UPDATE always hits the database and locks the row.
        Same lock order as FulfillmentService (Order -> User), so no new
        deadlock class is introduced.
        """
        return await session.scalar(
            select(User).where(User.id == user_id).with_for_update()
        )

    @staticmethod
    async def _settle_benefits(
        session: AsyncSession, order: Order, *, was_canceled: bool
    ) -> None:
        """Credit wallet / grant referral bonus / fulfill access."""
        # Notification debt: money just moved (fresh settlement or hold
        # release). Whoever delivers the push first (webhook / order_check /
        # credit-notify worker) clears the flag; the worker is the backstop
        # for users who already left the payment screen.
        mark_notify_pending(order)
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

        # Referral bonus accrues ONLY on balance top-ups: direct tariff
        # purchases (external gateway or wallet) must not mint referrer
        # bonuses, otherwise refunds of non-topup orders would leave
        # unreversed bonuses behind (reversal covers top-ups only).
        if order.service_type == "topup":
            from services.referral_bonus import grant_referral_bonus_for_topup

            grant_result = await grant_referral_bonus_for_topup(
                session,
                purchaser_user_id=order.user_id,
                order_id=str(order.id),
                topup_amount=order.amount_rub,
            )
            # Referrer push debt: the bonus money moved now, but the referrer
            # is not looking at any screen. Armed only for genuinely new
            # grants (never for historical orders, so no backfill spam);
            # cleared by the credit-notify worker after delivery.
            if (
                grant_result.referrer_bonus > 0
                and grant_result.referrer_user_id is not None
            ):
                referrer_meta = dict(order.metadata_ or {})
                referrer_meta["referrer_notify_pending"] = {
                    "user_id": grant_result.referrer_user_id,
                    "telegram_id": grant_result.referrer_telegram_id,
                    "bonus": str(grant_result.referrer_bonus),
                }
                order.metadata_ = referrer_meta

        # Fulfill benefits linearly
        await FulfillmentService.fulfill_order(session, order)
        await session.flush()
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
    async def _resolve_order_terms(
        session: AsyncSession,
        *,
        user: User | None,
        user_id: int,
        tariff_id: int | None,
        amount_rub: Decimal | None,
        duration_days: int | None,
        device_limit: int | None,
        description: str | None,
        order_meta: dict,
    ) -> tuple[Decimal | None, int | None, int | None, str | None]:
        """Resolve price, duration, device limit and description for a tariff order.

        This is the single source of truth for checkout pricing. It is shared by
        ``create_order`` (external gateway) and ``pay_from_wallet`` (internal
        balance) so the referral first-purchase discount and the tariff-change
        proration can never drift between the two payment paths.

        ``order_meta`` is mutated in place with the pricing annotations that the
        order row and the fulfilment step read back.
        """
        if tariff_id is None:
            return amount_rub, duration_days, device_limit, description

        tariff = await session.get(Tariff, tariff_id)
        if not tariff:
            return amount_rub, duration_days, device_limit, description

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
        is_renewal = bool(
            user
            and current_tid
            and current_tid == tariff.id
            and sub_end
            and sub_end > now
        )

        if is_tariff_change:
            order_meta["is_tariff_change"] = True
            order_meta["operation"] = "change"
            current_tariff = await session.get(Tariff, current_tid)
            due_rub, resulting_days = OrderService.calculate_tariff_change(
                current_tariff, tariff, sub_end, now=now
            )
            if amount_rub is None:
                amount_rub = due_rub
            if duration_days is None:
                duration_days = resulting_days
        else:
            if is_renewal:
                order_meta["is_renewal"] = True
                order_meta["operation"] = "renew"
            elif "operation" not in order_meta:
                order_meta["operation"] = "purchase"
            if amount_rub is None:
                base_cost = Decimal(tariff.price_rub)
                if await is_eligible_for_referral_first_discount(session, user_id):
                    discount = (base_cost * REFERRAL_WELCOME_DISCOUNT_PERCENT).quantize(
                        Decimal(1), rounding=ROUND_DOWN
                    )
                    amount_rub = max(Decimal(1), base_cost - discount)
                    order_meta["is_referral_discount"] = True
                    order_meta["discount_rub"] = int(discount)
                    order_meta["original_price_rub"] = int(base_cost)
                else:
                    amount_rub = base_cost
            if duration_days is None:
                duration_days = tariff.duration_days

        if device_limit is None:
            device_limit = tariff.device_limit
        if description is None:
            description = f"{texts.CHECKOUT_DESCRIPTION_DEFAULT} ({tariff.name})"

        return amount_rub, duration_days, device_limit, description

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
        amount_rub, duration_days, device_limit, description = (
            await OrderService._resolve_order_terms(
                session,
                user=user,
                user_id=user_id,
                tariff_id=tariff_id,
                amount_rub=amount_rub,
                duration_days=duration_days,
                device_limit=device_limit,
                description=description,
                order_meta=order_meta,
            )
        )

        final_amount = amount_rub if amount_rub is not None else Decimal("0.00")
        final_duration = duration_days if duration_days is not None else 0
        final_desc = description or texts.CHECKOUT_DESCRIPTION_DEFAULT

        # Lock user to serialize concurrent order creation (prevent double-clicks/races)
        try:
            await session.execute(
                text("SELECT pg_advisory_xact_lock(:key)"),
                {"key": -user_id},
            )
        except Exception:
            pass

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
                order_id = order.id
                logger.exception(
                    "Gateway failed to create payment for order %s: %s", order_id, exc
                )
                if isinstance(exc, PaymentCreationAmbiguousError):
                    # The provider may have created the payment (timeout, 5xx,
                    # unreadable response). Rolling back would destroy the
                    # order together with the only reference to it, and the
                    # incoming `payment.succeeded` webhook would then find
                    # nothing and be retried by YooKassa for 24h. Keep the
                    # order so the webhook can still settle it, and flag it
                    # for operators: no payment_url means it stays invisible
                    # in the funnel and the user simply retries the purchase.
                    held_meta = dict(order.metadata_ or {})
                    held_meta["payment_creation_ambiguous"] = True
                    order.metadata_ = held_meta
                    await session.flush()
                    # Commit here, not at the end of the request: the caller
                    # now performs Telegram I/O inside the same transaction,
                    # and any failure there would roll the order back and
                    # reintroduce exactly the money-loss this branch prevents.
                    # The order is the only row this request has written, and
                    # the external side effect may already exist, so making it
                    # durable immediately is the correct boundary.
                    await session.commit()
                    raise
                # NOTE: rollback() expires EVERY ORM object of this session
                # (including the caller's User). Callers must capture
                # user_id/telegram_id as plain ints BEFORE calling
                # create_order() and never touch ORM attributes in except.
                # Prod 2026-09-29: logger.exception(..., user.id) after this
                # rollback raised MissingGreenlet and masked YooKassa TIMEOUT.
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
        # Acquire advisory lock then row lock to avoid lock inversion deadlock
        try:
            await session.execute(
                text("SELECT pg_advisory_xact_lock(:key)"),
                {"key": -user_id},
            )
        except Exception:
            pass
        user = await session.get(User, user_id, with_for_update=True)
        if user and getattr(user, "financial_hold", False):
            raise FinancialHoldBlockedError("Financial hold active on user")

        order_meta: dict = dict(metadata) if metadata else {}
        amount_rub, duration_days, device_limit, description = (
            await OrderService._resolve_order_terms(
                session,
                user=user,
                user_id=user_id,
                tariff_id=tariff_id,
                amount_rub=amount_rub,
                duration_days=duration_days,
                device_limit=device_limit,
                description=description,
                order_meta=order_meta,
            )
        )

        cost = amount_rub if amount_rub is not None else Decimal("0.00")
        balance_snapshot = await get_account_balance(
            session, user_id=user_id, for_update=True, locked_user=user
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
                locked_user=user,
            )

        # Fulfill
        await FulfillmentService.fulfill_order(session, order)
        await session.flush()
        return order

    @staticmethod
    def mark_order_canceled(order: Order, reason: str = "gateway_canceled") -> None:
        """Mark order canceled and record cancellation reason to prevent invalid revival."""
        order_meta = dict(order.metadata_ or {})
        if order.status == "pending":
            order.status = "canceled"
            order_meta.setdefault("cancellation_reason", reason)
            order.metadata_ = order_meta
        elif order.status == "canceled":
            if "cancellation_reason" not in order_meta:
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
            order._newly_paid = False
            if (order.metadata_ or {}).get("settlement_held"):
                # Release path: a repeat settlement attempt (e.g. order_check
                # after the hold was lifted) completes withheld benefits.
                # Credit/bonus inserts are idempotent; the flag is cleared
                # first so a second release is a no-op early return.
                release_user = await OrderService._load_settlement_user(
                    session, order.user_id
                )
                if release_user is not None:
                    still_hold = bool(
                        getattr(release_user, "financial_hold", False)
                    )
                    still_block = bool(
                        getattr(release_user, "topup_blocked", False)
                    )
                    still_blocked = still_hold or (
                        order.service_type == "topup" and still_block
                    )
                    if still_blocked:
                        return order
                cleared_meta = dict(order.metadata_ or {})
                cleared_meta.pop("settlement_held", None)
                cleared_meta.pop("settlement_hold_reason", None)
                order.metadata_ = cleared_meta
                order._newly_paid = True
                await OrderService._settle_benefits(
                    session, order, was_canceled=False
                )
                logger.info(
                    "Order %s released from settlement hold and fulfilled",
                    order.id,
                )
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

        if external_id:
            order.external_id = external_id

        if paid_amount_rub is not None and paid_amount_rub < order.amount_rub:
            logger.error(
                "Order %s underpaid: expected %s, got %s",
                order.id,
                order.amount_rub,
                paid_amount_rub,
            )
            order_meta = dict(order.metadata_ or {})
            order_meta["underpaid"] = True
            order_meta["underpaid_expected"] = str(order.amount_rub)
            order_meta["underpaid_received"] = str(paid_amount_rub)
            order_meta["manual_review"] = True
            order_meta["manual_review_reason"] = "underpayment"
            order.metadata_ = order_meta
            await session.flush()
            return None

        order.status = "paid"
        order.paid_at = now_utc()
        order._newly_paid = True
        if was_canceled:
            order_meta = dict(order.metadata_ or {})
            order_meta["revived_from_canceled"] = True
            order.metadata_ = order_meta

        # Settlement boundary: funnel guards (topup_blocked/financial_hold)
        # use stale ORM state and only cover creation. Money arriving here
        # (webhook or manual order_check) must not credit/fulfill a blocked
        # user. Mark paid (money fact) but hold benefits for manual review.
        # No new status (migration-free): flag in metadata_.
        settlement_user = await OrderService._load_settlement_user(
            session, order.user_id
        )
        if settlement_user is not None:
            is_hold = bool(getattr(settlement_user, "financial_hold", False))
            is_topup_block = bool(getattr(settlement_user, "topup_blocked", False))
            blocked_topup = order.service_type == "topup" and (is_hold or is_topup_block)
            blocked_tariff = order.service_type != "topup" and is_hold
            if blocked_topup or blocked_tariff:
                held_meta = dict(order.metadata_ or {})
                held_meta["settlement_held"] = True
                held_meta["settlement_hold_reason"] = (
                    "financial_hold" if is_hold else "topup_blocked"
                )
                order.metadata_ = held_meta
                # Not a fresh settlement for UX purposes: suppress the
                # "credited" notification in the webhook handler, which keys
                # off status == paid and _newly_paid.
                order._newly_paid = False
                await session.flush()
                logger.warning(
                    "Order %s paid under hold/block (user %s, service=%s): "
                    "credited/fulfillment withheld for manual review",
                    order.id,
                    order.user_id,
                    order.service_type,
                )
                return order

        await OrderService._settle_benefits(session, order, was_canceled=was_canceled)
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

        # Verify gateway payment identity matches order.external_id
        if order.external_id:
            if result.is_paid and result.external_id and order.external_id != result.external_id:
                logger.warning(
                    "Order %s payment ID mismatch: order.external_id=%s, webhook.external_id=%s",
                    order.id,
                    order.external_id,
                    result.external_id,
                )
                return None
            if result.is_refunded and related_id and order.external_id != related_id:
                logger.warning(
                    "Order %s refund payment_id mismatch: order.external_id=%s, webhook.payment_id=%s",
                    order.id,
                    order.external_id,
                    related_id,
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

            order_meta = dict(order.metadata_ or {})
            refunded_so_far = Decimal(str(order_meta.get("refunded_amount_rub", "0")))
            processed_refund_ids = list(order_meta.get("processed_refund_ids", []))

            if result.amount_rub is not None:
                refund_amount = result.amount_rub
            else:
                # The gateway payload carries no amount (amount.value missing).
                # YooKassa always sends amount on refund.succeeded, so this is
                # a malformed event: fail closed (defer to retry/manual
                # review) instead of assuming any amount. Assuming the
                # remainder would always exactly close the order and could
                # wrongly revoke service on untrusted data.
                logger.error(
                    "Refund %s for order %s has no amount in payload "
                    "(refunded so far %s of %s): "
                    "deferring to retry/manual review",
                    result.external_id,
                    order.id,
                    refunded_so_far,
                    order.amount_rub,
                )
                order_meta = dict(order.metadata_ or {})
                order_meta["refund_amount_missing"] = str(
                    result.external_id or "unknown"
                )
                order.metadata_ = order_meta
                await session.flush()
                return None

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
                has_credit = bool(
                    await session.scalar(
                        select(func.count(AccountLedgerEntry.id)).where(
                            AccountLedgerEntry.order_id == order.id,
                            AccountLedgerEntry.entry_type == "payment_credit",
                        )
                    )
                )

                if has_credit:
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
                else:
                    logger.info(
                        "Order %s topup has no payment_credit in ledger (held or uncredited); skipping refund debit",
                        order.id,
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
