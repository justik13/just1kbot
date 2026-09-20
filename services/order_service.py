"""Order lifecycle management service for simple billing."""

from __future__ import annotations

from decimal import Decimal
import logging
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import Order, Tariff
from database.repositories.account_ledger_repo import (
    create_order_credit,
    create_order_debit,
    create_order_refund_debit,
    get_account_balance,
)
from integrations.payment_gateways.factory import get_payment_gateway
from services.fulfillment_service import FulfillmentService
from utils.datetime_helpers import now_utc

logger = logging.getLogger(__name__)


class InsufficientBalanceError(Exception):
    def __init__(self, required: Decimal, available: Decimal):
        super().__init__(
            f"Insufficient balance: required {required}, available {available}"
        )
        self.required = required
        self.available = available


class OrderService:
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
    ) -> Order:
        """Create a new commercial order and obtain payment link if external gateway."""
        if tariff_id is not None:
            tariff = await session.get(Tariff, tariff_id)
            if tariff:
                if amount_rub is None:
                    amount_rub = Decimal(tariff.price_rub)
                if duration_days is None:
                    duration_days = tariff.duration_days
                if device_limit is None:
                    device_limit = tariff.device_limit
                if description is None:
                    description = f"Тариф: {tariff.name}"

        final_amount = amount_rub if amount_rub is not None else Decimal("0.00")
        final_duration = duration_days if duration_days is not None else 0
        final_desc = description or f"Заказ {service_type}"

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
        )
        session.add(order)
        await session.flush()

        if payment_method != "wallet":
            gateway = get_payment_gateway(payment_method)
            invoice = await gateway.create_payment_url(
                order_id=str(order.id),
                amount_rub=order.amount_rub,
                description=final_desc,
                return_url=return_url,
            )
            order.external_id = invoice.external_id
            order.payment_url = invoice.payment_url

        await session.commit()
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
    ) -> Order:
        """Pay for an order immediately using internal wallet balance."""
        if tariff_id is not None:
            tariff = await session.get(Tariff, tariff_id)
            if tariff:
                if amount_rub is None:
                    amount_rub = Decimal(tariff.price_rub)
                if duration_days is None:
                    duration_days = tariff.duration_days
                if device_limit is None:
                    device_limit = tariff.device_limit
                if description is None:
                    description = f"Тариф: {tariff.name}"

        cost = amount_rub if amount_rub is not None else Decimal("0.00")
        balance_snapshot = await get_account_balance(
            session, user_id=user_id, for_update=True
        )
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
            description=description or f"Оплата с баланса {service_type}",
            paid_at=now_utc(),
        )
        session.add(order)
        await session.flush()

        # Debit wallet ledger
        await create_order_debit(
            session,
            user_id=user_id,
            amount_rub=cost,
            order_id=order.id,
            metadata={"description": order.description},
        )

        # Fulfill
        await FulfillmentService.fulfill_order(session, order)
        await session.commit()
        return order

    @staticmethod
    async def process_webhook_event(
        session: AsyncSession,
        payload: dict,
        bot: object | None = None,
    ) -> bool:
        """Handle incoming webhook event (payment succeeded or refund succeeded)."""
        gateway = get_payment_gateway("yookassa")
        result = await gateway.parse_webhook(payload)
        if not result.order_id and not result.external_id:
            logger.warning(
                "Received webhook without order_id or external_id: %s", payload
            )
            return False

        order = None
        if result.order_id:
            try:
                order_uuid = uuid.UUID(result.order_id)
                order = await session.get(Order, order_uuid)
            except (ValueError, TypeError):
                pass

        if not order and result.external_id:
            order = await session.scalar(
                select(Order).where(Order.external_id == result.external_id)
            )

        if not order:
            logger.warning(
                "Order not found for webhook: order_id=%s, ext_id=%s",
                result.order_id,
                result.external_id,
            )
            return False

        if result.is_paid:
            if order.status == "paid":
                logger.info(
                    "Order %s already marked paid, ignoring duplicate webhook",
                    order.id,
                )
                return True
            order.status = "paid"
            order.paid_at = now_utc()
            if result.external_id:
                order.external_id = result.external_id

            # Create ledger records
            if order.service_type == "topup":
                await create_order_credit(
                    session,
                    user_id=order.user_id,
                    amount_rub=order.amount_rub,
                    order_id=order.id,
                    metadata={"source": "yookassa_topup"},
                )
            else:
                await create_order_credit(
                    session,
                    user_id=order.user_id,
                    amount_rub=order.amount_rub,
                    order_id=order.id,
                    metadata={"source": "yookassa_payment"},
                )
                await create_order_debit(
                    session,
                    user_id=order.user_id,
                    amount_rub=order.amount_rub,
                    order_id=order.id,
                    metadata={"description": order.description},
                )

            await FulfillmentService.fulfill_order(session, order)
            await session.commit()
            logger.info("Order %s successfully paid and fulfilled", order.id)

            if bot:
                try:
                    from database.models import User

                    user = await session.get(User, order.user_id)
                    if user and user.telegram_id:
                        from bot import texts
                        from bot.formatters import get_tariff_display_name
                        from bot.keyboards import get_payment_success_keyboard
                        from utils.telegram import EFFECT_CONFETTI, render_hub

                        balance = await get_account_balance(session, user_id=user.id)
                        tariff_name = get_tariff_display_name(order.device_limit or 2)
                        await render_hub(
                            bot,
                            user.telegram_id,
                            texts.PAYMENT_PURCHASE_SUCCESS_CARD.format(
                                operation_title=texts.PURCHASE_COMPLETED,
                                tariff_name=tariff_name,
                                duration_days=order.duration_days,
                                charged=int(order.amount_rub),
                                real_balance=int(balance.real_available),
                                bonus_balance=int(balance.bonus_available),
                            ),
                            get_payment_success_keyboard(),
                            message_effect_id=EFFECT_CONFETTI,
                            force_new=True,
                        )
                except Exception as exc:
                    logger.warning(
                        "Could not notify user of order fulfillment: %s", exc
                    )

            return True

        if result.is_refunded:
            if order.status == "refunded":
                logger.info(
                    "Order %s already marked refunded, ignoring duplicate webhook",
                    order.id,
                )
                return True
            order.status = "refunded"
            order.refunded_at = now_utc()

            await create_order_refund_debit(
                session,
                user_id=order.user_id,
                amount_rub=order.amount_rub,
                order_id=order.id,
                metadata={"source": "yookassa_refund"},
            )

            await FulfillmentService.revoke_order(session, order)
            await session.commit()
            logger.info("Order %s refunded and revoked", order.id)
            return True

        return False
