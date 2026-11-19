"""Order lifecycle management service for simple billing."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal, ROUND_DOWN
import logging
import math
import uuid

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from bot import texts
from config.constants import (
    MIN_TOPUP_RUB,
    REFERRAL_WELCOME_DISCOUNT_PERCENT,
    STANDARD_DOWNGRADE_COOLDOWN_DAYS,
    STANDARD_MAX_DEVICES,
    STANDARD_MIN_DEVICES,
    STANDARD_PLANS,
    WHITE_INTERNET_BASE_DURATION_DAYS,
    WHITE_INTERNET_MAX_DEVICE_LIMIT,
    WHITE_INTERNET_MAX_EXPIRY_DAYS,
    WHITE_INTERNET_MAX_QUOTA_BYTES,
    WHITE_INTERNET_TOPUP_PACKS,
)
from config.enums import WhiteInternetProvisioningStatus, WhiteInternetStatus
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
from utils.datetime_helpers import now_utc

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


class OrderService:
    @staticmethod
    async def _load_settlement_user(
        session: AsyncSession, user_id: int
    ) -> User | None:
        """Authoritative user read with row lock for the settlement boundary."""
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
    ) -> tuple[Order, bool]:
        """Create (or deduplicate) an external YooKassa order and obtain payment link."""
        order_meta: dict = dict(metadata) if metadata else {}

        cutoff = now_utc() - timedelta(minutes=15)
        existing = await session.scalar(
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
                Order.payment_url.is_not(None),
            )
            .order_by(Order.created_at.desc())
            .limit(1)
        )
        if existing and existing.payment_url:
            return existing, False

        order = Order(
            id=uuid.uuid4(),
            user_id=user_id,
            service_type=service_type,
            amount_rub=amount_rub,
            duration_days=duration_days,
            traffic_bytes=traffic_bytes,
            device_limit=device_limit,
            payment_method="yookassa",
            status="pending",
            description=description,
            metadata_=order_meta,
            expires_at=now_utc() + timedelta(minutes=15),
        )
        session.add(order)
        await session.flush()

        gateway = get_payment_gateway("yookassa")
        try:
            invoice = await gateway.create_payment_url(
                order_id=str(order.id),
                amount_rub=order.amount_rub,
                description=description,
                return_url=return_url,
                bot_username=bot_username,
            )
            order.external_id = invoice.external_id
            order.payment_url = invoice.payment_url
        except PaymentCreationAmbiguousError:
            held_meta = dict(order.metadata_ or {})
            held_meta["payment_creation_ambiguous"] = True
            order.metadata_ = held_meta
            await session.flush()
            await session.commit()
            raise
        except Exception as exc:
            order_id = order.id
            logger.exception(
                "Gateway failed to create payment for order %s: %s", order_id, exc
            )
            await session.rollback()
            raise

        await session.flush()
        return order, True

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
        """Idempotent, atomic order settlement with row-level locking."""
        order_uuid = uuid.UUID(str(order_id)) if not isinstance(order_id, uuid.UUID) else order_id
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
    async def _is_standard_paid_subscription(session: AsyncSession, user_id: int) -> bool:
        """Check whether user has at least one paid non-free Standard order."""
        paid_id = await session.scalar(
            select(Order.id)
            .where(
                Order.user_id == user_id,
                Order.service_type == "standard",
                Order.status == "paid",
                Order.amount_rub > 0,
            )
            .limit(1)
        )
        return paid_id is not None

    @staticmethod
    async def quote_standard_change(
        session: AsyncSession,
        user_id: int,
        target_device_limit: int,
        target_duration_days: int = 30,
    ) -> dict:
        """Calculate quote for Standard purchase, renewal, mid-term upgrade, or downgrade."""
        if target_device_limit < STANDARD_MIN_DEVICES or target_device_limit > STANDARD_MAX_DEVICES:
            raise ValueError(f"device_limit must be {STANDARD_MIN_DEVICES}..{STANDARD_MAX_DEVICES}")
        if target_duration_days not in STANDARD_PLANS:
            raise ValueError(f"duration_days must be one of {sorted(STANDARD_PLANS)}")

        user = await session.get(User, user_id)
        now = now_utc()
        sub_end = getattr(user, "subscription_end", None) if user else None
        current_limit = (getattr(user, "device_limit", 0) or 0) if user else 0
        cooldown_until = getattr(user, "standard_downgrade_cooldown_until", None) if user else None
        active_hwid_count = await count_active_user_devices(session, user_id)

        is_active = bool(sub_end and sub_end > now and current_limit > 0)
        if not is_active:
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
                "can_downgrade": False,
                "blocked_reason": None,
                "cooldown_until": cooldown_until.isoformat() if cooldown_until else None,
                "active_hwid_count": active_hwid_count,
            }

        if target_device_limit == current_limit:
            due = Decimal(calculate_standard_price(target_device_limit, target_duration_days))
            return {
                "operation": "renew",
                "due_rub": int(due),
                "original_price_rub": int(due),
                "discount_rub": 0,
                "bonus_credit_rub": 0,
                "days_left": max(0, math.ceil((sub_end - now).total_seconds() / 86400.0)),
                "can_downgrade": False,
                "blocked_reason": None,
                "cooldown_until": cooldown_until.isoformat() if cooldown_until else None,
                "active_hwid_count": active_hwid_count,
            }

        if target_device_limit > current_limit:
            days_left = max(1, math.ceil((sub_end - now).total_seconds() / 86400.0))
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
                "can_downgrade": False,
                "blocked_reason": None,
                "cooldown_until": cooldown_until.isoformat() if cooldown_until else None,
                "active_hwid_count": active_hwid_count,
            }

        # Downgrade: target_device_limit < current_limit
        full_days_left = max(0, int((sub_end - now).total_seconds() // 86400))
        blocked_reason: str | None = None
        if cooldown_until and cooldown_until > now:
            blocked_reason = f"Снижение лимита доступно после {cooldown_until.strftime('%d.%m.%Y %H:%M')}"
        elif active_hwid_count > target_device_limit:
            blocked_reason = (
                f"Активных устройств ({active_hwid_count}) больше нового лимита ({target_device_limit}). "
                "Удалите лишние устройства."
            )

        is_paid = await OrderService._is_standard_paid_subscription(session, user_id)
        if is_paid and full_days_left > 0:
            diff_30 = calculate_standard_price(current_limit, 30) - calculate_standard_price(
                target_device_limit, 30
            )
            bonus_credit = max(0, math.floor(full_days_left * (diff_30 / 30.0)))
        else:
            bonus_credit = 0

        return {
            "operation": "downgrade",
            "due_rub": 0,
            "original_price_rub": 0,
            "discount_rub": 0,
            "bonus_credit_rub": int(bonus_credit),
            "days_left": full_days_left,
            "can_downgrade": blocked_reason is None,
            "blocked_reason": blocked_reason,
            "cooldown_until": cooldown_until.isoformat() if cooldown_until else None,
            "active_hwid_count": active_hwid_count,
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

        quote = await OrderService.quote_standard_change(
            session, user_id, target_device_limit, target_duration_days=30
        )
        if quote["operation"] != "downgrade":
            raise ValueError("Target device limit must be lower than current limit")
        if not quote["can_downgrade"]:
            raise ValueError(quote["blocked_reason"] or "Downgrade not allowed")

        now = now_utc()
        user.device_limit = target_device_limit
        user.standard_downgrade_cooldown_until = now + timedelta(
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
            )

        from services.subscription import SubscriptionService

        await session.flush()
        await SubscriptionService._sync_access_state(session, user)
        return {
            "ok": True,
            "device_limit": user.device_limit,
            "bonus_credited_rub": int(bonus_rub),
            "cooldown_until": user.standard_downgrade_cooldown_until.isoformat(),
        }

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
    ) -> dict:
        """Unified server-side checkout supporting 100% wallet (bonuses first), mixed payment, or YooKassa."""
        # Acquire advisory lock BEFORE row lock to prevent lock-inversion deadlocks.
        bind = session.get_bind()
        if bind and getattr(bind, "dialect", None) and bind.dialect.name == "postgresql":
            await session.execute(
                text("SELECT pg_advisory_xact_lock(:key)"),
                {"key": -user_id},
            )
        user = await OrderService._load_settlement_user(session, user_id)
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
        checked_hub_id: int | None = None

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
            quote = await OrderService.quote_standard_change(session, user_id, d_limit, d_days)
            if quote["operation"] == "downgrade":
                raise ValueError("Use downgrade_standard_devices for device downgrade")
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
            checked_hub_id = getattr(wi_sub, "hub_server_id", None) if wi_sub else None
            op = operation or ("renew" if wi_sub else "purchase")
            if wi_sub is not None:
                if wi_sub.status == WhiteInternetStatus.DISABLED:
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
            elif op == "topup":
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
                if wi_sub is not None and op == "purchase":
                    if (
                        wi_sub.status
                        in (
                            WhiteInternetStatus.PENDING,
                            WhiteInternetStatus.ACTIVE,
                            WhiteInternetStatus.EXHAUSTED,
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
                hub_server_id=checked_hub_id,
            )
            if not has_capacity:
                raise ServiceUnavailableError(
                    "Идут технические работы, сейчас оформление недоступно. "
                    "Попробуйте позже или напишите в поддержку."
                )

        bal = await get_account_balance(session, user_id=user_id, for_update=True, locked_user=user)
        if use_balance and bal.debt > 0:
            raise AccountDebtBlockedError("Account debt prevents wallet purchases")

        if use_balance and bal.available >= total_cost:
            order = Order(
                id=uuid.uuid4(),
                user_id=user_id,
                service_type=service_type,
                amount_rub=total_cost,
                duration_days=final_duration,
                traffic_bytes=traffic_bytes,
                device_limit=final_devices,
                payment_method="wallet",
                status="paid",
                description=description,
                paid_at=now_utc(),
                metadata_=order_meta,
            )
            session.add(order)
            await session.flush()
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
            charged_bonus = min(bal.bonus_available, total_cost)
            return {
                "order_id": str(order.id),
                "status": order.status,
                "confirmation_url": None,
                "charged_balance": int(total_cost),
                "charged_bonus": int(charged_bonus),
                "to_pay_rub": 0,
                "total_cost_rub": int(total_cost),
            }

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

        order, is_new_order = await OrderService._create_yookassa_order(
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
        )
        if is_new_order and wallet_debit > 0:
            await create_order_debit(
                session,
                user_id=user.id,
                amount_rub=wallet_debit,
                order_id=order.id,
                metadata={"description": f"Резерв смешанной оплаты: {order.description}"},
                locked_user=user,
            )
        return {
            "order_id": str(order.id),
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
        user = await OrderService._load_settlement_user(session, user_id)
        if user is None:
            raise ValueError("User not found")
        if standard is not None:
            user.auto_renew_enabled = bool(standard)
            from services.subscription import SubscriptionService

            await SubscriptionService._sync_access_state(session, user)
        wi_sub = await get_wi_sub_by_user_id(session, user_id)
        if white_internet is not None and wi_sub is not None:
            wi_sub.auto_renew_enabled = bool(white_internet)
        await session.flush()
        return {
            "standard": bool(user.auto_renew_enabled),
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
            if order.status == "refunded":
                logger.info(
                    "Order %s already marked refunded, ignoring duplicate webhook",
                    order.id,
                )
                return order

            if order.status not in ("paid", "partially_refunded"):
                logger.warning(
                    "Refund received for non-paid order %s (status=%s), deferring to retry",
                    order.id,
                    order.status,
                )
                return None

            refund_amount = (
                result.amount_rub
                if (result.amount_rub is not None and result.amount_rub > 0)
                else order.amount_rub
            )
            if refund_amount > order.amount_rub:
                logger.warning(
                    "Refund amount mismatch or missing for order %s: expected <= %s, got %s (ext_id=%s)",
                    order.id,
                    order.amount_rub,
                    result.amount_rub,
                    result.external_id,
                )
                order_meta = dict(order.metadata_ or {})
                order_meta["requires_manual_review"] = True
                order_meta["refund_amount_mismatch"] = (
                    f"Refund {refund_amount} exceeds order {order.amount_rub}"
                )
                if result.external_id:
                    order_meta["refund_external_id"] = result.external_id
                order.metadata_ = order_meta
                await session.flush()
                return order

            is_partial = refund_amount < order.amount_rub
            order.status = "refunded" if not is_partial else "partially_refunded"
            order.refunded_at = now_utc()
            order_meta = dict(order.metadata_ or {})
            if result.external_id:
                order_meta["refund_external_id"] = result.external_id
            if is_partial:
                order_meta["partial_refund_amount_rub"] = str(refund_amount)
            order.metadata_ = order_meta

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
                        (result.external_id or "").strip()
                        or f"refund_{order.id}"
                    )
                    await create_order_refund_debit(
                        session,
                        user_id=order.user_id,
                        amount_rub=refund_amount,
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
                    (result.external_id or "").strip()
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
        logger.info(
            "Webhook event %s for order %s has no actionable transition, acknowledging",
            result.event_type,
            order.id,
        )
        return order
