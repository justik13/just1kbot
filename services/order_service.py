"""Order lifecycle management service for simple billing."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal, ROUND_DOWN
import logging
import math
import uuid

from sqlalchemy import func, or_, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from bot import texts
from config.constants import (
    MIN_TOPUP_RUB,
    REFERRAL_WELCOME_DISCOUNT_PERCENT,
    STANDARD_DOWNGRADE_COOLDOWN_DAYS,
    STANDARD_MAX_DEVICES,
    STANDARD_MAX_EXPIRY_DAYS,
    STANDARD_MIN_DEVICES,
    STANDARD_PLANS,
    VPN_ACCESS_GRACE_HOURS,
    WHITE_INTERNET_BASE_DURATION_DAYS,
    WHITE_INTERNET_MAX_DEVICE_LIMIT,
    WHITE_INTERNET_MAX_EXPIRY_DAYS,
    WHITE_INTERNET_MAX_QUOTA_BYTES,
    WHITE_INTERNET_TOPUP_PACKS,
)
from config.enums import OrderStatus, WhiteInternetProvisioningStatus, WhiteInternetStatus
from config.tariffs import calculate_standard_price
from database.models import AccountLedgerEntry, Order, User
from database.repositories.users_repo import (
    is_eligible_for_referral_first_discount,
)
from database.repositories.user_devices_repo import count_active_user_devices
from database.repositories.account_ledger_repo import (
    create_admin_adjustment,
    create_mixed_cancel_refund,
    create_order_credit,
    create_order_debit,
    create_order_refund_debit,
    get_account_balance,
)
from database.repositories.servers_repo import has_available_service_capacity
from database.repositories.white_internet_repo import get_subscription_by_user_id as get_wi_sub_by_user_id
from integrations.payment_gateways.base import PaymentCreationAmbiguousError
from integrations.payment_gateways.factory import get_payment_gateway
from services.fulfillment_service import FulfillmentService
from services.order_notifications import mark_notify_pending
from services.referral_bonus import reverse_referral_bonus_for_topup
from services.white_internet_service import WhiteInternetService, get_white_internet_tier_price
from utils.datetime_helpers import is_permanent_subscription, now_utc

logger = logging.getLogger(__name__)


class InsufficientBalanceError(Exception):
    def __init__(self, required: Decimal, available: Decimal):
        super().__init__(
            f"Insufficient balance: required {required}, available {available}"
        )
        self.required = required
        self.available = available


class AccountDebtBlockedError(Exception):
    pass


class FinancialHoldBlockedError(Exception):
    pass


class ServiceUnavailableError(Exception):
    pass


class PaymentRetryRequiredError(ValueError):
    pass


async def acquire_user_account_lock(session: AsyncSession, user_id: int) -> None:
    """Acquire canonical per-user transaction advisory lock (-user_id) on PostgreSQL."""
    bind = session.get_bind() if hasattr(session, "get_bind") else None
    if bind and getattr(bind, "dialect", None) and bind.dialect.name == "postgresql":
        await session.execute(
            text("SELECT pg_advisory_xact_lock(:key)"),
            {"key": -int(user_id)},
        )


def extract_manual_review_alert(
    order: Order | None,
    *,
    require_mismatch_key: bool = False,
) -> dict[str, object] | None:
    """Build a one-time admin alert payload for quarantined/mismatched orders.

    Marks ``mismatch_alert_sent = True`` on ``order.metadata_`` so repeated
    webhook/reconciliation/check passes never duplicate the alert.
    """
    if order is None:
        return None
    order_meta = dict(order.metadata_ or {})
    if order_meta.get("mismatch_alert_sent"):
        return None

    has_mismatch_key = bool(
        order_meta.get("payment_amount_mismatch")
        or order_meta.get("refund_amount_mismatch")
        or order_meta.get("payment_mismatch_reason")
        or order_meta.get("payment_id_mismatch")
        or order_meta.get("refund_payment_id_mismatch")
        or order_meta.get("cancel_payment_id_mismatch")
        or order_meta.get("late_payment_after_refund_on_canceled")
        or order_meta.get("late_payment_attempt_rejected")
        or order_meta.get("underpaid")
    )
    if require_mismatch_key:
        if not (order_meta.get("requires_manual_review") and has_mismatch_key):
            return None
    else:
        if not (has_mismatch_key or order_meta.get("requires_manual_review")):
            return None

    reason = (
        order_meta.get("payment_amount_mismatch")
        or order_meta.get("refund_amount_mismatch")
        or order_meta.get("payment_mismatch_reason")
        or order_meta.get("payment_id_mismatch")
        or order_meta.get("refund_payment_id_mismatch")
        or order_meta.get("cancel_payment_id_mismatch")
        or (
            f"late_payment_after_refund_on_canceled: {order_meta.get('late_payment_after_refund_on_canceled')}"
            if order_meta.get("late_payment_after_refund_on_canceled")
            else None
        )
        or (
            f"late_payment_attempt_rejected: {order_meta.get('late_payment_attempt_rejected')}"
            if order_meta.get("late_payment_attempt_rejected")
            else None
        )
        or order_meta.get("manual_review_reason")
        or "requires_manual_review"
    )
    order_meta["mismatch_alert_sent"] = True
    order.metadata_ = order_meta
    return {
        "order_id": order.id,
        "user_id": order.user_id,
        "amount_rub": order.amount_rub,
        "mismatch": str(reason),
    }


class OrderService:
    @staticmethod
    async def acquire_user_account_lock(
        session: AsyncSession, user_id: int
    ) -> None:
        """Acquire canonical per-user advisory lock (-user_id) before any row locks."""
        await acquire_user_account_lock(session, user_id)

    @staticmethod
    async def _load_settlement_user(
        session: AsyncSession, user_id: int
    ) -> User | None:
        """Authoritative user read with canonical advisory + row lock for the settlement boundary."""
        await acquire_user_account_lock(session, user_id)
        return await session.scalar(
            select(User)
            .where(User.id == user_id)
            .with_for_update()
            .execution_options(populate_existing=True)
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

        # Referral bonus accrues on real-money orders: balance top-ups AND direct
        # external-gateway purchases (e.g. YooKassa card payments).
        # Wallet purchases are excluded because they are funded from previously topped-up
        # (or gifted) balance.
        if order.service_type == "topup" or order.payment_method != "wallet":
            from services.referral_bonus import grant_referral_bonus_for_topup

            grant_result = await grant_referral_bonus_for_topup(
                session,
                purchaser_user_id=order.user_id,
                order_id=str(order.id),
                topup_amount=order.amount_rub,
            )
            if (
                grant_result.referrer_bonus > 0
                and grant_result.referrer_user_id is not None
            ):
                referrer_meta = dict(order.metadata_ or {})
                referrer_meta["referrer_notify_pending"] = {
                    "user_id": grant_result.referrer_user_id,
                    "telegram_id": grant_result.referrer_telegram_id,
                    "bonus": str(grant_result.referrer_bonus),
                    "bonus_rate_pct": grant_result.bonus_rate_pct,
                    "tier_upgraded": grant_result.tier_upgraded,
                    "new_tier_name": grant_result.new_tier_name,
                    "new_rate_pct": grant_result.new_rate_pct,
                }
                order.metadata_ = referrer_meta
        # Mixed payment: debit pre-agreed wallet portion first via FIFO (bonuses first)
        mixed_fallback = False
        wallet_debit_raw = (order.metadata_ or {}).get("wallet_debit_rub")
        if wallet_debit_raw and order.payment_method != "wallet" and order.service_type != "topup":
            wallet_debit = Decimal(str(wallet_debit_raw))
            if wallet_debit > 0:
                existing_debit = await session.scalar(
                    select(AccountLedgerEntry).where(
                        AccountLedgerEntry.entry_type == "purchase_debit",
                        AccountLedgerEntry.order_id == order.id,
                    )
                )
                if (order.metadata_ or {}).get("mixed_debit_refunded"):
                    mixed_fallback = True
                    await create_order_credit(
                        session,
                        user_id=order.user_id,
                        amount_rub=order.amount_rub,
                        order_id=order.id,
                        metadata={"source": "mixed_fallback_topup"},
                    )
                    fb_meta = dict(order.metadata_ or {})
                    fb_meta["mixed_fallback_credited"] = True
                    order.metadata_ = fb_meta
                elif existing_debit is None:
                    bal = await get_account_balance(session, user_id=order.user_id, for_update=True)
                    if bal.debt == 0 and bal.available >= wallet_debit:
                        await create_order_debit(
                            session,
                            user_id=order.user_id,
                            amount_rub=wallet_debit,
                            order_id=order.id,
                            metadata={"description": f"Смешанная оплата: {order.description}"},
                        )
                    else:
                        mixed_fallback = True
                        await create_order_credit(
                            session,
                            user_id=order.user_id,
                            amount_rub=order.amount_rub,
                            order_id=order.id,
                            metadata={"source": "mixed_fallback_topup"},
                        )
                        fb_meta = dict(order.metadata_ or {})
                        fb_meta["mixed_fallback_credited"] = True
                        order.metadata_ = fb_meta

        # Fulfill benefits linearly
        if order.status != "refunded" and not mixed_fallback:
            await FulfillmentService.fulfill_order(session, order)
        await session.flush()

    @staticmethod
    async def _find_recent_pending_yookassa_order(
        session: AsyncSession,
        *,
        user_id: int,
        service_type: str,
        amount_rub: Decimal,
        duration_days: int,
        traffic_bytes: int,
        device_limit: int | None,
        op_name: str | None,
        now=None,
    ) -> Order | None:
        now = now or now_utc()
        cutoff = now - timedelta(minutes=15)
        recent_stmt = (
            select(Order)
            .where(
                Order.user_id == user_id,
                Order.service_type == service_type,
                Order.duration_days == duration_days,
                Order.traffic_bytes == traffic_bytes,
                Order.device_limit == device_limit,
                Order.payment_method == "yookassa",
                Order.amount_rub == amount_rub,
                Order.status == "pending",
                Order.created_at >= cutoff,
                or_(Order.expires_at.is_(None), Order.expires_at > now),
            )
            .order_by(Order.created_at.desc())
            .limit(5)
        )
        for cand in (await session.execute(recent_stmt)).scalars().all():
            cand_meta = cand.metadata_ or {}
            if cand_meta.get("operation") == op_name and (
                cand.payment_url or not cand_meta.get("payment_creation_ambiguous")
            ):
                return cand
        return None

    @staticmethod
    async def _cancel_excess_pending_yookassa_orders(
        session: AsyncSession,
        user_id: int,
        *,
        now=None,
        max_active: int = 3,
    ) -> bool:
        """Cancel oldest non-ambiguous active pending YooKassa orders to stay below cap."""
        now = now or now_utc()
        active_pending = list(
            (
                await session.execute(
                    select(Order)
                    .where(
                        Order.user_id == user_id,
                        Order.payment_method == "yookassa",
                        Order.status == "pending",
                        or_(Order.expires_at.is_(None), Order.expires_at > now),
                    )
                    .order_by(Order.created_at.asc())
                    .with_for_update()
                )
            )
            .scalars()
            .all()
        )
        if len(active_pending) < max_active:
            return False

        canceled_any = False
        remaining = len(active_pending)
        for old_order in active_pending:
            if remaining < max_active:
                break
            old_meta = old_order.metadata_ or {}
            if old_meta.get("payment_creation_ambiguous"):
                continue
            OrderService.mark_order_canceled(old_order, reason="superseded_by_new_order")
            updated_meta = dict(old_order.metadata_ or {})
            updated_meta["superseded_at"] = now.isoformat()
            old_order.metadata_ = updated_meta
            await OrderService.refund_reserved_mixed_debit(session, old_order)
            canceled_any = True
            remaining -= 1

        if canceled_any:
            await session.flush()

        if remaining >= max_active:
            raise ValueError(
                "У вас уже есть 3 неоплаченных заказа. Завершите или дождитесь отмены текущих."
            )
        return canceled_any

    @staticmethod
    async def _create_yookassa_order(
        session: AsyncSession,
        *,
        user_id: int,
        service_type: str,
        amount_rub: Decimal,
        duration_days: int,
        traffic_bytes: int = 0,
        device_limit: int | None = None,
        description: str,
        return_url: str | None = None,
        metadata: dict | None = None,
        bot_username: str | None = None,
        idempotency_key: str | None = None,
        wallet_debit: Decimal = Decimal(0),
        locked_user: User | None = None,
    ) -> tuple[Order, bool]:
        """Create (or deduplicate) an external YooKassa order and obtain payment link."""
        order_meta: dict = dict(metadata) if metadata else {}
        op_name = order_meta.get("operation")

        now = now_utc()
        cand = await OrderService._find_recent_pending_yookassa_order(
            session,
            user_id=user_id,
            service_type=service_type,
            amount_rub=amount_rub,
            duration_days=duration_days,
            traffic_bytes=traffic_bytes,
            device_limit=device_limit,
            op_name=op_name,
            now=now,
        )
        if cand is not None:
            if cand.payment_url:
                return cand, False
            # Recent matching pending order exists without payment_url and is not ambiguous:
            # re-request YooKassa with the SAME order_id (Idempotence-Key = order_<cand.id>)
            # so YooKassa returns the exact same payment/URL without creating a duplicate.
            cand_id = cand.id
            cand_amount = cand.amount_rub
            cand_desc = cand.description or description
            if hasattr(session, "commit"):
                await session.commit()
            gateway = get_payment_gateway("yookassa")
            try:
                invoice = await gateway.create_payment_url(
                    order_id=str(cand_id),
                    amount_rub=cand_amount,
                    description=cand_desc,
                    return_url=return_url,
                    bot_username=bot_username,
                )
                await acquire_user_account_lock(session, user_id)
                locked_order = await session.scalar(
                    select(Order).where(Order.id == cand_id).with_for_update()
                )
                target_order = locked_order if locked_order is not None else cand
                if target_order.status == OrderStatus.PENDING:
                    target_order.external_id = invoice.external_id
                    target_order.payment_url = invoice.payment_url
                    await session.flush()
                    if hasattr(session, "commit"):
                        await session.commit()
                return target_order, False
            except PaymentCreationAmbiguousError:
                await acquire_user_account_lock(session, user_id)
                locked_order = await session.scalar(
                    select(Order).where(Order.id == cand_id).with_for_update()
                )
                target_order = locked_order if locked_order is not None else cand
                if target_order.status == OrderStatus.PENDING:
                    held_meta = dict(target_order.metadata_ or {})
                    held_meta["payment_creation_ambiguous"] = True
                    target_order.metadata_ = held_meta
                    await session.flush()
                    if hasattr(session, "commit"):
                        await session.commit()
                raise

        await OrderService._cancel_excess_pending_yookassa_orders(
            session, user_id, now=now
        )

        order = Order(
            id=uuid.uuid4(),
            user_id=user_id,
            service_type=service_type,
            amount_rub=amount_rub,
            duration_days=duration_days,
            traffic_bytes=traffic_bytes,
            device_limit=device_limit,
            payment_method="yookassa",
            idempotency_key=idempotency_key,
            status="pending",
            description=description,
            metadata_=order_meta,
            expires_at=now_utc() + timedelta(minutes=15),
        )
        session.add(order)
        await session.flush()

        if wallet_debit > 0:
            await create_order_debit(
                session,
                user_id=user_id,
                amount_rub=wallet_debit,
                order_id=order.id,
                metadata={"description": f"Резерв смешанной оплаты: {order.description}"},
                locked_user=locked_user,
            )

        # Commit pending order (and mixed wallet reservation) BEFORE calling external
        # YooKassa HTTP API so PostgreSQL advisory/row locks are not held across network I/O
        # and the Order row persists even if the HTTP call times out or becomes ambiguous.
        order_id = order.id
        if hasattr(session, "commit"):
            await session.commit()

        gateway = get_payment_gateway("yookassa")
        try:
            invoice = await gateway.create_payment_url(
                order_id=str(order_id),
                amount_rub=order.amount_rub,
                description=description,
                return_url=return_url,
                bot_username=bot_username,
            )
            await acquire_user_account_lock(session, user_id)
            locked_order = await session.scalar(
                select(Order).where(Order.id == order_id).with_for_update()
            )
            target_order = locked_order if locked_order is not None else order
            if target_order.status == OrderStatus.PENDING:
                target_order.external_id = invoice.external_id
                target_order.payment_url = invoice.payment_url
                await session.flush()
                if hasattr(session, "commit"):
                    await session.commit()
            return target_order, True
        except PaymentCreationAmbiguousError:
            await acquire_user_account_lock(session, user_id)
            locked_order = await session.scalar(
                select(Order).where(Order.id == order_id).with_for_update()
            )
            target_order = locked_order if locked_order is not None else order
            if target_order.status == OrderStatus.PENDING:
                held_meta = dict(target_order.metadata_ or {})
                held_meta["payment_creation_ambiguous"] = True
                target_order.metadata_ = held_meta
                await session.flush()
                if hasattr(session, "commit"):
                    await session.commit()
            raise
        except Exception as exc:
            logger.exception(
                "Gateway failed to create payment for order %s: %s", order_id, exc
            )
            try:
                await acquire_user_account_lock(session, user_id)
                locked_order = await session.scalar(
                    select(Order).where(Order.id == order_id).with_for_update()
                )
                target_order = locked_order if locked_order is not None else order
                if target_order.status == OrderStatus.PENDING:
                    OrderService.mark_order_canceled(target_order, reason="gateway_create_failed")
                    if wallet_debit > 0:
                        await OrderService.refund_reserved_mixed_debit(session, target_order)
                    await session.flush()
                    if hasattr(session, "commit"):
                        await session.commit()
            except Exception:
                logger.exception("Failed to clean up order %s after gateway create failure", order_id)
            raise

    _LOCAL_CANCEL_REASONS = frozenset({
        "superseded_by_new_order",
        "order_expired_24h",
        "ambiguous_creation_timeout",
        "gateway_create_failed",
    })

    @staticmethod
    def is_gateway_canceled(order_meta: dict | None) -> bool:
        """Return True if an order was canceled by the payment gateway (including raw YooKassa reasons)."""
        if not isinstance(order_meta, dict):
            return False
        if order_meta.get("gateway_canceled"):
            return True
        reason = order_meta.get("cancellation_reason")
        if not reason:
            return False
        if reason == "gateway_canceled":
            return True
        return reason not in OrderService._LOCAL_CANCEL_REASONS

    @staticmethod
    def mark_order_canceled(
        order: Order,
        reason: str = "gateway_canceled",
        *,
        gateway_reason: str | None = None,
    ) -> None:
        """Mark order canceled and record cancellation reason to prevent invalid revival."""
        order_meta = dict(order.metadata_ or {})
        is_gw = reason == "gateway_canceled" or bool(gateway_reason) or (
            reason not in OrderService._LOCAL_CANCEL_REASONS
        )
        if order.status == "pending":
            order.status = "canceled"
            order_meta.setdefault("cancellation_reason", reason)
            if is_gw:
                order_meta["gateway_canceled"] = True
            if gateway_reason:
                order_meta["gateway_cancellation_reason"] = str(gateway_reason)
            order.metadata_ = order_meta
        elif order.status == "canceled":
            changed = False
            if "cancellation_reason" not in order_meta:
                order_meta["cancellation_reason"] = reason
                changed = True
            if is_gw and not order_meta.get("gateway_canceled"):
                order_meta["gateway_canceled"] = True
                changed = True
            if gateway_reason and "gateway_cancellation_reason" not in order_meta:
                order_meta["gateway_cancellation_reason"] = str(gateway_reason)
                changed = True
            if changed:
                order.metadata_ = order_meta

    @staticmethod
    async def refund_reserved_mixed_debit(session: AsyncSession, order: Order) -> None:
        """Refund reserved wallet debit for canceled or expired mixed payment orders."""
        meta = dict(order.metadata_ or {})
        if meta.get("mixed_debit_refunded"):
            return
        real_part, bonus_part = await create_mixed_cancel_refund(
            session,
            user_id=order.user_id,
            order_id=order.id,
            key_prefix="mixed_cancel",
            source="mixed_canceled_refund",
        )
        if real_part > 0 or bonus_part > 0:
            meta["mixed_debit_refunded"] = True
            order.metadata_ = meta

    @staticmethod
    async def mark_order_paid(
        session: AsyncSession,
        order_id: uuid.UUID | str,
        *,
        external_id: str | None = None,
        paid_amount_rub: Decimal | None = None,
    ) -> Order | None:
        """Idempotent, atomic order settlement with canonical advisory + row-level locking."""
        order_uuid = uuid.UUID(str(order_id)) if not isinstance(order_id, uuid.UUID) else order_id
        owner_id = await session.scalar(select(Order.user_id).where(Order.id == order_uuid))
        if owner_id is not None:
            await acquire_user_account_lock(session, owner_id)
            await session.scalar(select(User.id).where(User.id == owner_id).with_for_update())
        order = await session.scalar(
            select(Order)
            .where(Order.id == order_uuid)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if not order:
            logger.warning("mark_order_paid: Order %s not found", order_id)
            return None

        if order.status == "paid":
            order._newly_paid = False
            if (order.metadata_ or {}).get("settlement_held"):
                release_user = await OrderService._load_settlement_user(
                    session, order.user_id
                )
                if release_user is not None:
                    still_hold = bool(release_user.financial_hold)
                    still_block = bool(release_user.topup_blocked)
                    if still_hold or (order.service_type == "topup" and still_block):
                        logger.info(
                            "Order %s remains in settlement_held: user %s still under hold/block",
                            order.id,
                            order.user_id,
                        )
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

        if order.status in ("refunded", "partially_refunded"):
            logger.warning("Cannot mark %s order %s as paid", order.status, order.id)
            order._newly_paid = False
            return order

        was_canceled = order.status == "canceled"
        if was_canceled:
            order_meta = dict(order.metadata_ or {})
            if order_meta.get("refund_received_on_canceled"):
                logger.warning(
                    "Cannot revive canceled order %s: refund.succeeded was already received",
                    order.id,
                )
                order_meta["requires_manual_review"] = True
                order_meta["late_payment_after_refund_on_canceled"] = (
                    str(external_id) if external_id else True
                )
                order.metadata_ = order_meta
                order._newly_paid = False
                await session.flush()
                return order
            if OrderService.is_gateway_canceled(order_meta):
                logger.warning(
                    "Cannot mark order %s paid: order was canceled by payment gateway (reason=%s)",
                    order.id,
                    order_meta.get("cancellation_reason"),
                )
                order_meta["requires_manual_review"] = True
                order_meta["manual_review"] = True
                order_meta["late_payment_attempt_rejected"] = (
                    str(external_id) if external_id else True
                )
                order.metadata_ = order_meta
                order._newly_paid = False
                await session.flush()
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
            order_meta["requires_manual_review"] = True
            order_meta["manual_review"] = True
            order_meta["manual_review_reason"] = "underpayment"
            order_meta["refund_amount_mismatch"] = (
                f"Underpaid: expected {order.amount_rub} RUB, received {paid_amount_rub} RUB"
            )
            order.metadata_ = order_meta
            order._newly_paid = False
            await session.flush()
            return order

        order.status = "paid"
        order.paid_at = now_utc()
        order._newly_paid = True
        if was_canceled:
            order_meta = dict(order.metadata_ or {})
            order_meta["revived_from_canceled"] = True
            order.metadata_ = order_meta

        settlement_user = await OrderService._load_settlement_user(
            session, order.user_id
        )
        if settlement_user is not None:
            is_hold = bool(settlement_user.financial_hold)
            is_topup_block = bool(settlement_user.topup_blocked)
            blocked_topup = order.service_type == "topup" and (is_hold or is_topup_block)
            blocked_tariff = order.service_type != "topup" and is_hold
            if blocked_topup or blocked_tariff:
                held_meta = dict(order.metadata_ or {})
                held_meta["settlement_held"] = True
                held_meta["settlement_hold_reason"] = (
                    "financial_hold" if is_hold else "topup_blocked"
                )
                order.metadata_ = held_meta
                order._newly_paid = False
                await session.flush()
                logger.warning(
                    "Order %s paid under hold/block (user %s, service=%s): "
                    "benefits withheld for manual review",
                    order.id,
                    order.user_id,
                    order.service_type,
                )
                return order

        await OrderService._settle_benefits(session, order, was_canceled=was_canceled)
        logger.info("Order %s marked paid and fulfilled successfully", order.id)
        return order

    @staticmethod
    async def _get_standard_downgrade_cap(
        session: AsyncSession, user_id: int, *, window_days: int = 365
    ) -> tuple[int, Decimal]:
        """Return (remaining_paid_cap_rub, effective_discount_ratio) for Standard downgrade."""
        cutoff = now_utc() - timedelta(days=window_days)
        paid_orders = (
            await session.scalars(
                select(Order)
                .where(
                    Order.user_id == user_id,
                    Order.service_type == "standard",
                    Order.status == "paid",
                    Order.paid_at.is_not(None),
                    Order.paid_at >= cutoff,
                )
                .order_by(Order.paid_at.desc())
            )
        ).all()
        if not paid_orders:
            return 0, Decimal(1)

        total_paid_rub = 0
        total_original_rub = 0
        for ord_obj in paid_orders:
            meta = ord_obj.metadata_ or {}
            if meta.get("mixed_fallback_credited") or meta.get("settlement_held"):
                continue
            raw_value = int(Decimal(str(meta.get("total_cost_rub") or ord_obj.amount_rub or 0)))
            bonus_used = int(
                Decimal(
                    str(
                        meta.get("wallet_bonus_debit_rub")
                        or (meta.get("charged_bonus") if ord_obj.payment_method == "wallet" else 0)
                        or 0
                    )
                )
            )
            order_value = max(0, raw_value - max(0, bonus_used))
            if order_value > 0:
                orig_value = int(Decimal(str(meta.get("original_price_rub") or order_value)))
                total_paid_rub += order_value
                total_original_rub += max(order_value, orig_value)

        if total_paid_rub <= 0:
            return 0, Decimal(1)

        already_credited = await session.scalar(
            select(func.coalesce(func.sum(AccountLedgerEntry.amount), 0)).where(
                AccountLedgerEntry.user_id == user_id,
                AccountLedgerEntry.entry_type == "admin_adjustment",
                AccountLedgerEntry.bucket == "bonus",
                AccountLedgerEntry.created_at >= cutoff,
                text("metadata @> CAST(:src AS jsonb)").bindparams(
                    src='{"source": "standard_downgrade_cashback"}'
                ),
            )
        )
        cap_left = max(0, total_paid_rub - int(Decimal(str(already_credited or 0))))
        discount_ratio = (
            min(Decimal(1), Decimal(total_paid_rub) / Decimal(total_original_rub))
            if total_original_rub > 0
            else Decimal(1)
        )
        return cap_left, discount_ratio

    @staticmethod
    async def quote_standard_change(
        session: AsyncSession,
        user_id: int,
        target_device_limit: int,
        target_duration_days: int = 30,
        *,
        operation: str | None = None,
    ) -> dict:
        """Calculate quote for Standard purchase, renewal, mid-term upgrade, or downgrade."""
        if target_device_limit < STANDARD_MIN_DEVICES or target_device_limit > STANDARD_MAX_DEVICES:
            raise ValueError(f"device_limit must be {STANDARD_MIN_DEVICES}..{STANDARD_MAX_DEVICES}")
        if target_duration_days not in STANDARD_PLANS:
            raise ValueError(f"duration_days must be one of {sorted(STANDARD_PLANS)}")

        from database.repositories import vless_subscription_repo

        vless_sub = await vless_subscription_repo.get_subscription_by_user_id(session, user_id)
        now = now_utc()
        sub_end = getattr(vless_sub, "expires_at", None) if vless_sub else None
        current_limit = (getattr(vless_sub, "device_limit", 0) or 0) if vless_sub else 0
        cooldown_until = getattr(vless_sub, "downgrade_cooldown_until", None) if vless_sub else None
        user_obj = await session.get(User, user_id)
        if user_obj is not None:
            from services.subscription import SubscriptionService

            slot_usage = await SubscriptionService.get_device_slot_usage(
                session, user_obj, vless_sub=vless_sub, now=now
            )
            active_hwid_count = int(slot_usage["total_devices"])
            std_assigned_count = int(slot_usage["std_assigned_count"])
        else:
            slot_usage = None
            active_hwid_count = await count_active_user_devices(session, user_id)
            std_assigned_count = active_hwid_count

        is_strictly_active = bool(
            sub_end is not None
            and sub_end > now
            and current_limit > 0
        )
        is_in_grace = bool(
            sub_end is not None
            and current_limit > 0
            and sub_end <= now
            and (now - sub_end) <= timedelta(hours=VPN_ACCESS_GRACE_HOURS)
        )
        is_active = is_strictly_active or is_in_grace
        is_grace_autorenew = bool(
            operation == "renew"
            and sub_end is not None
            and current_limit > 0
            and target_device_limit == current_limit
            and (sub_end > now or (now - sub_end) <= timedelta(hours=24))
        )
        if not is_active and not is_grace_autorenew:
            base_price = Decimal(calculate_standard_price(target_device_limit, target_duration_days))
            discount = Decimal(0)
            if await is_eligible_for_referral_first_discount(session, user_id):
                discount = (base_price * REFERRAL_WELCOME_DISCOUNT_PERCENT).quantize(
                    Decimal(1), rounding=ROUND_DOWN
                )
            due = max(Decimal(1), base_price - discount)
            return {
                "operation": "purchase",
                "due_rub": int(due),
                "original_price_rub": int(base_price),
                "discount_rub": int(discount),
                "bonus_credit_rub": 0,
                "days_left": 0,
                "in_grace": False,
                "can_downgrade": False,
                "blocked_reason": None,
                "cooldown_until": cooldown_until.isoformat() if cooldown_until else None,
                "active_hwid_count": active_hwid_count,
            }

        if target_device_limit == current_limit or (is_in_grace and operation == "renew"):
            blocked_by_devices = False
            renew_blocked_reason: str | None = None
            if is_in_grace and target_device_limit < current_limit:
                if getattr(user_obj, "financial_hold", False):
                    renew_blocked_reason = "На аккаунте действует финансовое ограничение. Обратитесь в поддержку."
                elif slot_usage is not None:
                    from services.subscription import SubscriptionService

                    fits, limit_reason = SubscriptionService.check_standard_limit_fits(
                        slot_usage, target_device_limit
                    )
                    if not fits:
                        renew_blocked_reason = limit_reason
                        blocked_by_devices = True
                elif active_hwid_count > target_device_limit:
                    renew_blocked_reason = (
                        f"Активных устройств ({active_hwid_count}) больше нового лимита ({target_device_limit}). "
                        "Удалите лишние устройства."
                    )
                    blocked_by_devices = True

            due = Decimal(calculate_standard_price(target_device_limit, target_duration_days))
            max_expiry = now + timedelta(days=STANDARD_MAX_EXPIRY_DAYS)
            horizon_exceeded = not is_permanent_subscription(sub_end) and (
                sub_end + timedelta(days=target_duration_days) > max_expiry
            )
            if renew_blocked_reason is None and horizon_exceeded:
                renew_blocked_reason = (
                    f"Максимальный срок продления — {STANDARD_MAX_EXPIRY_DAYS} дн. вперёд."
                )
            return {
                "operation": "renew",
                "due_rub": int(due),
                "original_price_rub": int(due),
                "discount_rub": 0,
                "bonus_credit_rub": 0,
                "days_left": max(0, math.ceil((sub_end - now).total_seconds() / 86400.0)),
                "in_grace": is_in_grace,
                "can_downgrade": False,
                "blocked_reason": renew_blocked_reason,
                "blocked_by_devices": blocked_by_devices,
                "cooldown_until": cooldown_until.isoformat() if cooldown_until else None,
                "active_hwid_count": active_hwid_count,
                "std_assigned_count": std_assigned_count,
            }

        if target_device_limit > current_limit:
            if is_in_grace:
                return {
                    "operation": "upgrade",
                    "due_rub": 0,
                    "original_price_rub": 0,
                    "discount_rub": 0,
                    "bonus_credit_rub": 0,
                    "days_left": 0,
                    "in_grace": True,
                    "can_downgrade": False,
                    "blocked_reason": (
                        "Срок действия подписки завершается (льготный период). "
                        "Продлите подписку с нужным числом устройств."
                    ),
                    "cooldown_until": cooldown_until.isoformat() if cooldown_until else None,
                    "active_hwid_count": active_hwid_count,
                }
            raw_days = max(1, math.ceil((sub_end - now).total_seconds() / 86400.0))
            days_left = min(raw_days, 365) if is_permanent_subscription(sub_end) else raw_days
            diff_30 = calculate_standard_price(target_device_limit, 30) - calculate_standard_price(
                max(STANDARD_MIN_DEVICES, current_limit), 30
            )
            due_rub = max(1, math.ceil(days_left * (diff_30 / 30.0)))
            return {
                "operation": "upgrade",
                "due_rub": int(due_rub),
                "original_price_rub": int(due_rub),
                "discount_rub": 0,
                "bonus_credit_rub": 0,
                "days_left": days_left,
                "in_grace": False,
                "can_downgrade": False,
                "blocked_reason": None,
                "cooldown_until": cooldown_until.isoformat() if cooldown_until else None,
                "active_hwid_count": active_hwid_count,
            }

        # Downgrade: target_device_limit < current_limit
        is_perm = is_permanent_subscription(sub_end)
        full_days_left = 0 if is_perm else max(0, int((sub_end - now).total_seconds() // 86400))
        blocked_reason: str | None = None
        blocked_by_devices = False
        if getattr(user_obj, "financial_hold", False):
            blocked_reason = "На аккаунте действует финансовое ограничение. Обратитесь в поддержку."
        elif cooldown_until and cooldown_until > now:
            blocked_reason = f"Снижение лимита доступно после {cooldown_until.strftime('%d.%m.%Y %H:%M')}"
        elif slot_usage is not None:
            from services.subscription import SubscriptionService

            fits, limit_reason = SubscriptionService.check_standard_limit_fits(
                slot_usage, target_device_limit
            )
            if not fits:
                blocked_reason = limit_reason
                blocked_by_devices = True
        elif active_hwid_count > target_device_limit:
            blocked_reason = (
                f"Активных устройств ({active_hwid_count}) больше нового лимита ({target_device_limit}). "
                "Удалите лишние устройства."
            )
            blocked_by_devices = True

        paid_cap_rub, discount_ratio = await OrderService._get_standard_downgrade_cap(session, user_id)
        if paid_cap_rub > 0 and full_days_left > 0:
            diff_90 = calculate_standard_price(current_limit, 90) - calculate_standard_price(
                target_device_limit, 90
            )
            raw_bonus = Decimal(full_days_left) * (Decimal(diff_90) / Decimal(90)) * discount_ratio
            bonus_credit = min(
                paid_cap_rub,
                max(0, int(raw_bonus.to_integral_value(rounding=ROUND_DOWN))),
            )
        else:
            bonus_credit = 0

        return {
            "operation": "downgrade",
            "due_rub": 0,
            "original_price_rub": 0,
            "discount_rub": 0,
            "bonus_credit_rub": int(bonus_credit),
            "days_left": full_days_left,
            "in_grace": is_in_grace,
            "can_downgrade": blocked_reason is None,
            "blocked_reason": blocked_reason,
            "blocked_by_devices": blocked_by_devices,
            "cooldown_until": cooldown_until.isoformat() if cooldown_until else None,
            "active_hwid_count": active_hwid_count,
            "std_assigned_count": std_assigned_count,
        }

    @staticmethod
    async def downgrade_standard_devices(
        session: AsyncSession,
        user_id: int,
        target_device_limit: int,
    ) -> dict:
        """Downgrade Standard device limit with 7-day cooldown and floor bonus cashback."""
        user = await OrderService._load_settlement_user(session, user_id)
        if user is None:
            raise ValueError("User not found")
        if getattr(user, "financial_hold", False):
            raise FinancialHoldBlockedError(
                "На аккаунте действует финансовое ограничение. Обратитесь в поддержку."
            )

        quote = await OrderService.quote_standard_change(
            session, user_id, target_device_limit, target_duration_days=30
        )
        if quote["operation"] != "downgrade":
            raise ValueError("Target device limit must be lower than current limit")
        if not quote["can_downgrade"]:
            raise ValueError(quote["blocked_reason"] or "Downgrade not allowed")

        from database.repositories import vless_subscription_repo

        vless_sub = await vless_subscription_repo.get_or_create_subscription(session, user_id)
        now = now_utc()
        vless_sub.device_limit = target_device_limit
        vless_sub.downgrade_cooldown_until = now + timedelta(
            days=STANDARD_DOWNGRADE_COOLDOWN_DAYS
        )

        bonus_rub = Decimal(quote["bonus_credit_rub"])
        if bonus_rub > 0:
            await create_admin_adjustment(
                session,
                user_id=user_id,
                signed_amount=bonus_rub,
                idempotency_key=f"std_downgrade:{user_id}:{uuid.uuid4()}",
                metadata={"source": "standard_downgrade_cashback", "target_devices": target_device_limit},
                locked_user=user,
            )

        from services.subscription import SubscriptionService

        await session.flush()
        await SubscriptionService._sync_access_state(session, user, vless_sub=vless_sub)
        return {
            "ok": True,
            "device_limit": vless_sub.device_limit,
            "bonus_credited_rub": int(bonus_rub),
            "cooldown_until": vless_sub.downgrade_cooldown_until.isoformat(),
        }

    @staticmethod
    def _format_idempotent_checkout_response(existing_order: Order) -> dict:
        if existing_order.status == OrderStatus.CANCELED or (
            existing_order.status == OrderStatus.PENDING
            and existing_order.payment_method != "wallet"
            and not existing_order.payment_url
        ):
            raise PaymentRetryRequiredError(
                "Предыдущая попытка оплаты не завершилась. Повторите оформление заказа."
            )
        meta = existing_order.metadata_ or {}
        if existing_order.payment_method == "wallet":
            resp = {
                "order_id": str(existing_order.id),
                "service_type": existing_order.service_type,
                "status": existing_order.status,
                "confirmation_url": None,
                "charged_balance": int(existing_order.amount_rub),
                "charged_bonus": int(meta.get("charged_bonus", 0)),
                "to_pay_rub": 0,
                "total_cost_rub": int(existing_order.amount_rub),
                "idempotent": True,
            }
        else:
            resp = {
                "order_id": str(existing_order.id),
                "service_type": existing_order.service_type,
                "status": existing_order.status,
                "confirmation_url": existing_order.payment_url,
                "charged_balance": int(meta.get("wallet_debit_rub", 0)),
                "charged_bonus": int(meta.get("wallet_bonus_debit_rub", 0)),
                "to_pay_rub": int(existing_order.amount_rub),
                "total_cost_rub": int(meta.get("total_cost_rub", existing_order.amount_rub)),
                "idempotent": True,
            }
        if meta.get("mixed_fallback_credited") or meta.get("fulfillment_error"):
            resp["fulfillment_failed"] = True
            resp["fulfillment_error"] = (
                meta.get("fulfillment_error")
                or "Не удалось активировать подписку. Средства сохранены на балансе."
            )
        return resp

    @staticmethod
    async def checkout_order(
        session: AsyncSession,
        *,
        user_id: int,
        service_type: str,
        device_limit: int | None = None,
        duration_days: int | None = None,
        amount_rub: Decimal | int | None = None,
        gb: int | None = None,
        operation: str | None = None,
        use_balance: bool = True,
        return_url: str | None = None,
        bot_username: str | None = None,
        allow_early_downgrade: bool = False,
        idempotency_key: str | None = None,
    ) -> dict:
        """Unified server-side checkout supporting 100% wallet (bonuses first), mixed payment, or YooKassa."""
        # Canonical lock order: 1) pg_advisory_xact_lock(-user_id) -> 2) User FOR UPDATE -> 3) Order FOR UPDATE.
        user = await OrderService._load_settlement_user(session, user_id)
        clean_idem_key = (idempotency_key or "").strip()[:64] or None
        if clean_idem_key:
            existing_order = (
                await session.execute(
                    select(Order)
                    .where(
                        Order.user_id == user_id,
                        Order.idempotency_key == clean_idem_key,
                    )
                    .with_for_update()
                )
            ).scalar_one_or_none()
            if existing_order is not None:
                existing_meta = existing_order.metadata_ or {}
                if (
                    existing_order.payment_method == "wallet"
                    and (
                        existing_meta.get("mixed_fallback_credited")
                        or existing_meta.get("fulfillment_error")
                    )
                ):
                    retired_meta = dict(existing_meta)
                    retired_meta["superseded_idempotency_key"] = clean_idem_key
                    existing_order.metadata_ = retired_meta
                    existing_order.idempotency_key = (
                        f"{clean_idem_key}:failed:{str(existing_order.id)[:8]}"[:128]
                    )
                    await session.flush()
                else:
                    return OrderService._format_idempotent_checkout_response(existing_order)
        if user is None:
            raise ValueError("User not found")
        if getattr(user, "financial_hold", False):
            raise FinancialHoldBlockedError("Financial hold active on user")

        # Payments maintenance mode guard
        from database.repositories.system_settings_repo import get_system_setting
        maint_val = await get_system_setting(session, "payments_maintenance_enabled", "false")
        if str(maint_val or "").strip().lower() in ("true", "1", "yes"):
            raise ServiceUnavailableError(
                "Платежи и оформление заказов временно приостановлены в связи с техническими работами. "
                "Попробуйте позже."
            )

        order_meta: dict = {}
        traffic_bytes = 0
        final_duration = 0
        final_devices = device_limit
        description = texts.CHECKOUT_DESCRIPTION_DEFAULT

        if service_type == "topup":
            if getattr(user, "topup_blocked", False):
                raise ValueError(texts.TOPUP_ERROR_BLOCKED)
            if amount_rub is None:
                raise ValueError("amount_rub is required for topup")
            total_cost = Decimal(int(amount_rub))
            if total_cost < MIN_TOPUP_RUB:
                raise ValueError(f"Минимальная сумма пополнения: {int(MIN_TOPUP_RUB)} ₽")
            use_balance = False
            order_meta["operation"] = "topup"
            description = f"Пополнение баланса на {int(total_cost)} ₽"
        elif service_type == "standard":
            d_limit = int(device_limit or STANDARD_MIN_DEVICES)
            d_days = int(duration_days or 30)
            quote = await OrderService.quote_standard_change(
                session, user_id, d_limit, d_days, operation=operation
            )
            if quote["operation"] == "downgrade":
                raise ValueError("Use downgrade_standard_devices for device downgrade")
            if quote["operation"] in ("renew", "upgrade") and quote.get("blocked_reason"):
                raise ValueError(quote["blocked_reason"])
            total_cost = Decimal(quote["due_rub"])
            final_devices = d_limit
            final_duration = 0 if quote["operation"] == "upgrade" else d_days
            order_meta["operation"] = quote["operation"]
            if quote.get("discount_rub"):
                order_meta["is_referral_discount"] = True
                order_meta["discount_rub"] = quote["discount_rub"]
                order_meta["original_price_rub"] = quote["original_price_rub"]
            if quote["operation"] == "upgrade":
                description = f"Увеличение устройств до {d_limit}"
            else:
                description = f"Доступ на {d_days} дн. ({d_limit} устр.)"
        elif service_type == "white_internet":
            now = now_utc()
            wi_sub = await get_wi_sub_by_user_id(session, user_id)
            op = operation or ("renew" if wi_sub else "purchase")
            if wi_sub is not None:
                if (
                    wi_sub.status == WhiteInternetStatus.DISABLED
                    and op in ("add_device_slot", "topup")
                ):
                    raise ValueError(texts.WL_SUB_DISABLED)
                if (
                    getattr(wi_sub, "provisioning_status", None)
                    == WhiteInternetProvisioningStatus.PENDING_DELETE
                ):
                    raise ValueError(texts.WL_DEACTIVATION_PENDING)
            if op == "add_device_slot":
                if wi_sub is None:
                    raise ValueError(texts.WL_NO_SUB)
                if wi_sub.status == WhiteInternetStatus.PENDING:
                    raise ValueError(texts.WL_SUB_NOT_READY)
                if wi_sub.status == WhiteInternetStatus.EXPIRED or (
                    wi_sub.expires_at and wi_sub.expires_at <= now
                ):
                    raise ValueError(texts.WL_SUB_EXPIRED)
                cur_wi_limit = max(1, int(getattr(wi_sub, "device_limit", 1) or 1))
                if cur_wi_limit >= WHITE_INTERNET_MAX_DEVICE_LIMIT:
                    raise ValueError(texts.WL_DEVICE_LIMIT_MAX_REACHED)
                total_cost, gb_added = WhiteInternetService.calculate_wi_add_slot_quote(
                    wi_sub.expires_at, now=now
                )
                final_devices = min(WHITE_INTERNET_MAX_DEVICE_LIMIT, cur_wi_limit + 1)
                traffic_bytes = gb_added * (1024**3)
                description = "Доп. устройство высокой устойчивости"
            elif op in ("topup", "topup_gb"):
                op = "topup"
                if gb is None or int(gb) not in WHITE_INTERNET_TOPUP_PACKS:
                    raise ValueError(f"Unsupported traffic pack: {gb}")
                if wi_sub is None:
                    raise ValueError(texts.WL_NO_SUB)
                if wi_sub.status == WhiteInternetStatus.PENDING:
                    raise ValueError(texts.WL_SUB_NOT_READY)
                if wi_sub.status == WhiteInternetStatus.EXPIRED or (
                    wi_sub.expires_at and wi_sub.expires_at <= now
                ):
                    raise ValueError(texts.WL_SUB_EXPIRED)
                pack_gb = int(gb)
                pack_bytes = pack_gb * (1024**3)
                cur_wi_limit = max(1, int(getattr(wi_sub, "device_limit", 1) or 1))
                max_extra_allowed = cur_wi_limit * WHITE_INTERNET_MAX_QUOTA_BYTES
                if (getattr(wi_sub, "extra_traffic_bytes", 0) or 0) + pack_bytes > max_extra_allowed:
                    from database.repositories import white_internet_repo
                    current_available = await white_internet_repo.get_available_quota_bytes(
                        session, wi_sub.id, now
                    )
                    raise ValueError(
                        texts.WL_CAP_EXCEEDED.format(
                            gb=pack_gb, available=current_available // (1024**3)
                        )
                    )
                total_cost = WHITE_INTERNET_TOPUP_PACKS[pack_gb]
                traffic_bytes = pack_bytes
                description = f"Доп. пакет трафика {pack_gb} ГБ"
            else:
                if wi_sub is not None and wi_sub.status == WhiteInternetStatus.DISABLED:
                    op = "purchase"
                elif wi_sub is not None and op == "purchase":
                    if (
                        wi_sub.status
                        in (
                            WhiteInternetStatus.PENDING,
                            WhiteInternetStatus.ACTIVE,
                        )
                        and wi_sub.expires_at
                        and wi_sub.expires_at > now
                    ):
                        raise ValueError(texts.WL_ALREADY_ACTIVE)
                    op = "renew"
                if op == "renew":
                    if wi_sub is None:
                        op = "purchase"
                    else:
                        if wi_sub.status == WhiteInternetStatus.PENDING:
                            raise ValueError(texts.WL_SUB_NOT_READY)
                        base_time = (
                            wi_sub.expires_at
                            if (wi_sub.expires_at and wi_sub.expires_at > now)
                            else now
                        )
                        if base_time + timedelta(days=WHITE_INTERNET_BASE_DURATION_DAYS) > now + timedelta(
                            days=WHITE_INTERNET_MAX_EXPIRY_DAYS
                        ):
                            raise ValueError(texts.WL_RENEWAL_HORIZON_EXCEEDED)
                if op == "renew" and wi_sub is not None:
                    cur_limit = max(1, min(WHITE_INTERNET_MAX_DEVICE_LIMIT, int(wi_sub.device_limit or 1)))
                    is_active_period = bool(
                        wi_sub.expires_at is not None
                        and wi_sub.expires_at > now
                        and wi_sub.status != WhiteInternetStatus.EXPIRED
                    )
                    if device_limit is not None:
                        d_limit = max(1, min(WHITE_INTERNET_MAX_DEVICE_LIMIT, int(device_limit)))
                        if is_active_period and not allow_early_downgrade and d_limit < cur_limit:
                            raise ValueError(
                                "Снизить число слотов можно со следующего периода в разделе «Слоты и трафик»."
                            )
                    elif (not is_active_period or allow_early_downgrade) and wi_sub.pending_device_limit is not None:
                        d_limit = max(1, min(WHITE_INTERNET_MAX_DEVICE_LIMIT, int(wi_sub.pending_device_limit)))
                    else:
                        d_limit = cur_limit
                else:
                    d_limit = max(1, min(WHITE_INTERNET_MAX_DEVICE_LIMIT, int(device_limit or 1)))
                base_cost = get_white_internet_tier_price(d_limit)
                if op == "purchase" and await is_eligible_for_referral_first_discount(session, user_id):
                    discount = (base_cost * REFERRAL_WELCOME_DISCOUNT_PERCENT).quantize(
                        Decimal(1), rounding=ROUND_DOWN
                    )
                    total_cost = max(Decimal(1), base_cost - discount)
                    order_meta["is_referral_discount"] = True
                    order_meta["discount_rub"] = int(discount)
                    order_meta["original_price_rub"] = int(base_cost)
                else:
                    total_cost = base_cost
                final_devices = d_limit
                final_duration = WHITE_INTERNET_BASE_DURATION_DAYS
                description = f"Доп. опция высокой устойчивости ({d_limit} устр., 30 дн.)"
            order_meta["operation"] = op
        else:
            raise ValueError(f"Unsupported service_type: {service_type}")

        if service_type in ("standard", "white_internet"):
            has_capacity = await has_available_service_capacity(
                session,
                service_type,
            )
            if not has_capacity:
                raise ServiceUnavailableError(
                    "Идут технические работы, сейчас оформление недоступно. "
                    "Попробуйте позже или напишите в поддержку."
                )

        bal = await get_account_balance(session, user_id=user_id, for_update=True, locked_user=user)
        if bal.debt > 0 and service_type != "topup":
            raise AccountDebtBlockedError("Имеется задолженность на балансе. Покупки заблокированы до погашения.")

        if not (use_balance and bal.available >= total_cost):
            prelim_wallet_debit = (
                bal.available if (use_balance and bal.available > 0) else Decimal(0)
            )
            prelim_to_pay = total_cost - prelim_wallet_debit
            if (
                prelim_wallet_debit > 0
                and 0 < prelim_to_pay < MIN_TOPUP_RUB
                and total_cost >= MIN_TOPUP_RUB
            ):
                prelim_to_pay = MIN_TOPUP_RUB
            matching_pending = await OrderService._find_recent_pending_yookassa_order(
                session,
                user_id=user_id,
                service_type=service_type,
                amount_rub=prelim_to_pay,
                duration_days=final_duration,
                traffic_bytes=traffic_bytes,
                device_limit=final_devices,
                op_name=order_meta.get("operation"),
            )
            if matching_pending is None:
                canceled_any = await OrderService._cancel_excess_pending_yookassa_orders(
                    session, user_id
                )
                if canceled_any:
                    bal = await get_account_balance(
                        session, user_id=user_id, for_update=True, locked_user=user
                    )

        if use_balance and bal.available >= total_cost:
            if clean_idem_key is None:
                recent_candidates = (
                    await session.execute(
                        select(Order).where(
                            Order.user_id == user_id,
                            Order.service_type == service_type,
                            Order.amount_rub == total_cost,
                            Order.duration_days == final_duration,
                            Order.traffic_bytes == traffic_bytes,
                            Order.device_limit == final_devices,
                            Order.payment_method == "wallet",
                            Order.status == "paid",
                            Order.paid_at >= now_utc() - timedelta(seconds=15),
                        ).order_by(Order.paid_at.desc())
                    )
                ).scalars().all()
                target_op = order_meta.get("operation")
                recent_paid_order = next(
                    (
                        o
                        for o in recent_candidates
                        if (o.metadata_ or {}).get("operation") == target_op
                    ),
                    None,
                )
                if recent_paid_order is not None:
                    meta = recent_paid_order.metadata_ or {}
                    return {
                        "order_id": str(recent_paid_order.id),
                        "service_type": recent_paid_order.service_type,
                        "status": recent_paid_order.status,
                        "confirmation_url": None,
                        "charged_balance": int(recent_paid_order.amount_rub),
                        "charged_bonus": int(meta.get("charged_bonus", 0)),
                        "to_pay_rub": 0,
                        "total_cost_rub": int(recent_paid_order.amount_rub),
                        "idempotent": True,
                    }

            charged_bonus = min(bal.bonus_available, total_cost)
            order_meta["charged_bonus"] = int(charged_bonus)
            order = Order(
                id=uuid.uuid4(),
                user_id=user_id,
                service_type=service_type,
                amount_rub=total_cost,
                duration_days=final_duration,
                traffic_bytes=traffic_bytes,
                device_limit=final_devices,
                payment_method="wallet",
                idempotency_key=clean_idem_key,
                status="paid",
                description=description,
                paid_at=now_utc(),
                metadata_=order_meta,
            )
            try:
                async with session.begin_nested():
                    session.add(order)
                    await session.flush()
            except IntegrityError:
                if clean_idem_key:
                    existing = (
                        await session.execute(
                            select(Order).where(
                                Order.user_id == user_id,
                                Order.idempotency_key == clean_idem_key,
                            )
                        )
                    ).scalar_one_or_none()
                    if existing is not None:
                        return OrderService._format_idempotent_checkout_response(existing)
                raise
            if total_cost > 0:
                await create_order_debit(
                    session,
                    user_id=user_id,
                    amount_rub=total_cost,
                    order_id=order.id,
                    metadata={"description": description},
                    locked_user=user,
                )
            await FulfillmentService.fulfill_order(session, order)
            await session.flush()
            order_meta_after = order.metadata_ or {}
            resp = {
                "order_id": str(order.id),
                "service_type": service_type,
                "status": order.status,
                "confirmation_url": None,
                "charged_balance": int(total_cost),
                "charged_bonus": int(charged_bonus),
                "to_pay_rub": 0,
                "total_cost_rub": int(total_cost),
            }
            if order_meta_after.get("mixed_fallback_credited") or order_meta_after.get("fulfillment_error"):
                resp["fulfillment_failed"] = True
                resp["fulfillment_error"] = (
                    order_meta_after.get("fulfillment_error")
                    or "Не удалось активировать подписку. Средства сохранены на балансе."
                )
            return resp

        if getattr(user, "topup_blocked", False):
            raise ValueError(texts.TOPUP_ERROR_BLOCKED)

        wallet_debit = bal.available if (use_balance and bal.available > 0) else Decimal(0)
        to_pay = total_cost - wallet_debit
        if wallet_debit > 0 and 0 < to_pay < MIN_TOPUP_RUB and total_cost >= MIN_TOPUP_RUB:
            wallet_debit = total_cost - MIN_TOPUP_RUB
            to_pay = MIN_TOPUP_RUB
        charged_bonus = min(bal.bonus_available, wallet_debit) if wallet_debit > 0 else Decimal(0)
        charged_real = wallet_debit - charged_bonus
        if wallet_debit > 0:
            order_meta["wallet_debit_rub"] = int(wallet_debit)
            order_meta["wallet_bonus_debit_rub"] = int(charged_bonus)
            order_meta["wallet_real_debit_rub"] = int(charged_real)
            order_meta["total_cost_rub"] = int(total_cost)
            order_meta["is_mixed_payment"] = True

        order, _is_new_order = await OrderService._create_yookassa_order(
            session,
            user_id=user_id,
            service_type=service_type,
            amount_rub=to_pay,
            duration_days=final_duration,
            traffic_bytes=traffic_bytes,
            device_limit=final_devices,
            description=description,
            return_url=return_url,
            metadata=order_meta,
            bot_username=bot_username,
            idempotency_key=clean_idem_key,
            wallet_debit=wallet_debit,
            locked_user=user,
        )
        return {
            "order_id": str(order.id),
            "service_type": service_type,
            "status": order.status,
            "confirmation_url": order.payment_url,
            "charged_balance": int(wallet_debit),
            "charged_bonus": int(charged_bonus),
            "to_pay_rub": int(to_pay),
            "total_cost_rub": int(total_cost),
        }

    @staticmethod
    async def set_auto_renew(
        session: AsyncSession,
        user_id: int,
        *,
        standard: bool | None = None,
        white_internet: bool | None = None,
    ) -> dict:
        """Persist auto-renew preferences for wallet-balance renewal worker."""
        from database.repositories import vless_subscription_repo

        user = await OrderService._load_settlement_user(session, user_id)
        if user is None:
            raise ValueError("User not found")
        vless_sub = await vless_subscription_repo.get_subscription_by_user_id(session, user_id)
        if standard is not None:
            if vless_sub is None:
                vless_sub = await vless_subscription_repo.get_or_create_subscription(session, user_id)
            vless_sub.auto_renew_enabled = bool(standard)
            from services.subscription import SubscriptionService

            await SubscriptionService._sync_access_state(session, user, vless_sub=vless_sub)
        wi_sub = await get_wi_sub_by_user_id(session, user_id)
        if white_internet is not None and wi_sub is not None:
            wi_sub.auto_renew_enabled = bool(white_internet)
        await session.flush()
        return {
            "standard": bool(vless_sub.auto_renew_enabled) if vless_sub else False,
            "white_internet": bool(wi_sub.auto_renew_enabled) if wi_sub else False,
        }

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

        order_row = None
        if result.order_id:
            try:
                order_uuid = uuid.UUID(result.order_id)
                order_row = (
                    await session.execute(
                        select(Order.id, Order.user_id).where(Order.id == order_uuid)
                    )
                ).one_or_none()
            except (ValueError, TypeError):
                pass

        related_id = getattr(result, "related_external_id", None) or getattr(result, "payment_id", None)
        if not order_row and related_id:
            order_row = (
                await session.execute(
                    select(Order.id, Order.user_id).where(Order.external_id == related_id)
                )
            ).one_or_none()

        if not order_row and result.external_id:
            order_row = (
                await session.execute(
                    select(Order.id, Order.user_id).where(Order.external_id == result.external_id)
                )
            ).one_or_none()

        if not order_row:
            logger.warning(
                "Order not found for webhook: order_id=%s, ext_id=%s",
                result.order_id,
                result.external_id,
            )
            return None

        target_order_id, target_user_id = order_row
        if target_user_id is not None:
            await acquire_user_account_lock(session, target_user_id)
            await session.scalar(select(User.id).where(User.id == target_user_id).with_for_update())

        order = await session.scalar(
            select(Order).where(Order.id == target_order_id).with_for_update()
        )
        if not order:
            return None
        order._webhook_amount_mismatch = False

        # Verify gateway payment identity matches order.external_id
        if order.external_id:
            if result.is_paid and result.external_id and order.external_id != result.external_id:
                logger.warning(
                    "Order %s payment ID mismatch: order.external_id=%s, webhook.external_id=%s",
                    order.id,
                    order.external_id,
                    result.external_id,
                )
                order_meta = dict(order.metadata_ or {})
                order_meta["requires_manual_review"] = True
                order_meta["payment_id_mismatch"] = str(result.external_id)
                order.metadata_ = order_meta
                order._newly_paid = False
                await session.flush()
                return order
            if result.is_refunded and related_id and order.external_id != related_id:
                logger.warning(
                    "Order %s refund payment_id mismatch: order.external_id=%s, webhook.payment_id=%s",
                    order.id,
                    order.external_id,
                    related_id,
                )
                order_meta = dict(order.metadata_ or {})
                order_meta["requires_manual_review"] = True
                order_meta["refund_payment_id_mismatch"] = str(related_id)
                order.metadata_ = order_meta
                await session.flush()
                return order
            if result.is_canceled and result.external_id and order.external_id != result.external_id:
                logger.warning(
                    "Order %s cancel payment ID mismatch: order.external_id=%s, webhook.external_id=%s",
                    order.id,
                    order.external_id,
                    result.external_id,
                )
                order_meta = dict(order.metadata_ or {})
                order_meta["requires_manual_review"] = True
                order_meta["cancel_payment_id_mismatch"] = str(result.external_id)
                order.metadata_ = order_meta
                await session.flush()
                return order

        if result.is_paid:
            paid_order = await OrderService.mark_order_paid(
                session,
                order.id,
                external_id=result.external_id,
                paid_amount_rub=result.amount_rub,
            )
            if paid_order is None:
                if OrderService.is_gateway_canceled(order.metadata_):
                    logger.warning(
                        "Ignoring payment.succeeded webhook for gateway_canceled order %s (ext_id=%s)",
                        order.id,
                        result.external_id,
                    )
                    return order
                if (order.metadata_ or {}).get("underpaid"):
                    order._newly_paid = False
                    return order
                return None
            return paid_order

        if result.is_canceled:
            if order.status == "pending":
                OrderService.mark_order_canceled(order, reason="gateway_canceled")
                await OrderService.refund_reserved_mixed_debit(session, order)
                await session.flush()
                logger.info(
                    "Order %s marked canceled via gateway webhook (external_id=%s)",
                    order.id,
                    result.external_id,
                )
            return order

        if result.is_refunded:
            return await OrderService.process_refund_webhook(
                session,
                order,
                refund_amount=result.amount_rub,
                refund_external_id=result.external_id,
                already_locked=True,
            )
        mismatch_reason = getattr(result, "mismatch_reason", None) or getattr(result, "error", None)
        is_mismatch = bool(
            getattr(result, "is_amount_mismatch", False)
            or mismatch_reason
            or (result.event_type == "payment.succeeded" and not result.is_paid)
        )
        if is_mismatch:
            logger.critical(
                "Webhook currency/amount/status mismatch for order %s (ext_id=%s, event=%s, amount=%s, reason=%s)",
                order.id,
                result.external_id,
                result.event_type,
                result.amount_rub,
                mismatch_reason,
            )
            order_meta = dict(order.metadata_ or {})
            order_meta["requires_manual_review"] = True
            order_meta["payment_amount_mismatch"] = str(
                mismatch_reason or f"event={result.event_type}, amount={result.amount_rub}"
            )
            order.metadata_ = order_meta
            order._newly_paid = False
            order._webhook_amount_mismatch = True
            await session.flush()
            return order

        logger.info(
            "Webhook event %s for order %s has no actionable transition, acknowledging",
            result.event_type,
            order.id,
        )
        return order

    @staticmethod
    async def process_refund_webhook(
        session: AsyncSession,
        order: Order,
        *,
        refund_amount: Decimal | None,
        refund_external_id: str | None = None,
        already_locked: bool = False,
    ) -> Order | None:
        """Process partial or cumulative full refund with canonical lock order."""
        if not already_locked:
            if order.user_id is not None:
                await acquire_user_account_lock(session, order.user_id)
                await session.scalar(
                    select(User.id).where(User.id == order.user_id).with_for_update()
                )
            locked_order = await session.scalar(
                select(Order).where(Order.id == order.id).with_for_update()
            )
            if not locked_order:
                return None
            order = locked_order

        if order.status == "refunded":
            logger.info(
                "Order %s already marked refunded, ignoring duplicate webhook",
                order.id,
            )
            return order

        if order.status == "canceled":
            logger.warning(
                "Refund received for canceled order %s (ext_id=%s, amount=%s); recording flag to prevent resurrection",
                order.id,
                refund_external_id,
                refund_amount,
            )
            order_meta = dict(order.metadata_ or {})
            order_meta["refund_received_on_canceled"] = True
            order_meta["requires_manual_review"] = True
            if refund_external_id:
                processed_refund_ids = list(order_meta.get("refund_external_ids") or [])
                if refund_external_id not in processed_refund_ids:
                    processed_refund_ids.append(refund_external_id)
                order_meta["refund_external_ids"] = processed_refund_ids
                order_meta["refund_external_id"] = refund_external_id
            if refund_amount is not None:
                order_meta["refund_on_canceled_amount_rub"] = str(refund_amount)
            order.metadata_ = order_meta
            await session.flush()
            return order

        if order.status not in ("paid", "partially_refunded"):
            logger.warning(
                "Refund received for non-paid order %s (status=%s), deferring to retry",
                order.id,
                order.status,
            )
            return None

        if refund_amount is None or refund_amount <= 0:
            logger.warning(
                "Refund amount missing or non-positive for order %s (got %s, ext_id=%s); flagging manual review",
                order.id,
                refund_amount,
                refund_external_id,
            )
            order_meta = dict(order.metadata_ or {})
            order_meta["requires_manual_review"] = True
            order_meta["refund_amount_mismatch"] = f"Missing or invalid refund amount: {refund_amount}"
            if refund_external_id:
                order_meta["refund_external_id"] = refund_external_id
            order.metadata_ = order_meta
            await session.flush()
            return order

        order_meta = dict(order.metadata_ or {})
        processed_refund_ids = list(order_meta.get("refund_external_ids") or [])
        prev_ext_id = order_meta.get("refund_external_id")
        if prev_ext_id and prev_ext_id not in processed_refund_ids:
            processed_refund_ids.append(prev_ext_id)
        if refund_external_id and refund_external_id in processed_refund_ids:
            logger.info(
                "Refund %s for order %s already processed, ignoring duplicate webhook",
                refund_external_id,
                order.id,
            )
            return order

        prev_raw = order_meta.get("partial_refund_amount_rub")
        try:
            prev_refunded = Decimal(str(prev_raw)) if prev_raw is not None else Decimal(0)
        except Exception:
            prev_refunded = Decimal(0)
        if prev_refunded < 0:
            prev_refunded = Decimal(0)

        cumulative_refunded = prev_refunded + refund_amount
        if refund_amount > order.amount_rub or cumulative_refunded > order.amount_rub:
            logger.warning(
                "Refund amount mismatch or missing for order %s: expected <= %s, got %s (cumulative=%s, ext_id=%s)",
                order.id,
                order.amount_rub,
                refund_amount,
                cumulative_refunded,
                refund_external_id,
            )
            order_meta["requires_manual_review"] = True
            order_meta["refund_amount_mismatch"] = (
                f"Refund {refund_amount} (cumulative {cumulative_refunded}) exceeds order {order.amount_rub}"
            )
            if refund_external_id:
                order_meta["refund_external_id"] = refund_external_id
            order.metadata_ = order_meta
            await session.flush()
            return order

        is_partial = cumulative_refunded < order.amount_rub
        order.status = "refunded" if not is_partial else "partially_refunded"
        order.refunded_at = now_utc()
        if refund_external_id:
            order_meta["refund_external_id"] = refund_external_id
            processed_refund_ids.append(refund_external_id)
            order_meta["refund_external_ids"] = processed_refund_ids
        order_meta["partial_refund_amount_rub"] = str(cumulative_refunded)
        if is_partial:
            order_meta["requires_manual_review"] = True
            order.metadata_ = order_meta
            await session.flush()
            logger.warning(
                "Partial refund %s RUB (cumulative %s/%s RUB) for order %s: flagged for manual review without auto-revoke",
                refund_amount,
                cumulative_refunded,
                order.amount_rub,
                order.id,
            )
            return order
        if not order_meta.get("refund_amount_mismatch") and not order_meta.get("payment_id_mismatch"):
            order_meta.pop("requires_manual_review", None)
        order.metadata_ = order_meta

        effective_refund_debit = min(cumulative_refunded, order.amount_rub)
        if order.service_type == "topup" or (order.metadata_ or {}).get("mixed_fallback_credited"):
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
                    (refund_external_id or "").strip()
                    or f"refund_{order.id}"
                )
                await create_order_refund_debit(
                    session,
                    user_id=order.user_id,
                    amount_rub=effective_refund_debit,
                    order_id=order.id,
                    refund_id=refund_ref,
                    metadata={"source": "yookassa_refund", "partial": is_partial},
                )
                await reverse_referral_bonus_for_topup(
                    session,
                    order_id=order.id,
                    refund_id=refund_ref,
                )
                balance_after = await get_account_balance(session, user_id=order.user_id)
                if balance_after.debt > 0:
                    order_meta = dict(order.metadata_ or {})
                    order_meta["requires_manual_review"] = True
                    order_meta["negative_balance_rub"] = str(balance_after.accounting_position)
                    order_meta["refund_amount_mismatch"] = (
                        f"Negative real balance detected: -{balance_after.debt} RUB"
                    )
                    order.metadata_ = order_meta
                    debt_user = await OrderService._load_settlement_user(session, order.user_id)
                    if debt_user is not None:
                        debt_user.financial_hold = True
                        debt_user.financial_block_reason = "refund_debt"
                        from services.subscription import SubscriptionService

                        await SubscriptionService.sync_access_state(session, debt_user)
                    await session.flush()
            else:
                logger.info(
                    "Order %s topup has no payment_credit in ledger; skipping refund debit",
                    order.id,
                )
        else:
            refund_ref = (
                (refund_external_id or "").strip()
                or f"refund_{order.id}"
            )
            if order.payment_method != "wallet":
                await reverse_referral_bonus_for_topup(
                    session,
                    order_id=order.id,
                    refund_id=refund_ref,
                )
            order_meta = dict(order.metadata_ or {})
            if not order_meta.get("mixed_debit_refunded"):
                real_restored, bonus_restored = await create_mixed_cancel_refund(
                    session,
                    user_id=order.user_id,
                    order_id=order.id,
                    key_prefix="mixed_refund",
                    source="mixed_order_refund_restore",
                )
                if real_restored > 0 or bonus_restored > 0:
                    order_meta["mixed_debit_refunded"] = True
            if not order_meta.get("settlement_held") and not order_meta.get("benefits_revoked"):
                await FulfillmentService.revoke_order(session, order)
                order_meta["benefits_revoked"] = True
                logger.info(
                    "Order %s refunded (%s RUB) and revoked",
                    order.id,
                    order.amount_rub,
                )
            else:
                logger.info(
                    "Order %s refunded; skipping revocation (settlement_held=%s, benefits_revoked=%s)",
                    order.id,
                    bool(order_meta.get("settlement_held")),
                    bool(order_meta.get("benefits_revoked")),
                )
            order.metadata_ = order_meta
        await session.flush()
        return order
