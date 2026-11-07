"""Order status check and cancellation routes for topups and invoices."""
from __future__ import annotations

import logging
import uuid

from aiogram import F, Router
from aiogram.types import CallbackQuery
from sqlalchemy.ext.asyncio import AsyncSession

from bot import texts
from database.models import Order, User
from database.repositories.account_ledger_repo import get_account_balance
from integrations.payment_gateways.factory import get_payment_gateway
from services.order_notifications import mark_notified
from services.order_service import OrderService
from utils.telegram import (
    EFFECT_CONFETTI,
    render_hub,
    safe_callback_answer,
)
from .credit_notify import build_tariff_success_card

router = Router()
logger = logging.getLogger(__name__)


def _uuid_from_callback(data: str) -> uuid.UUID | None:
    try:
        return uuid.UUID(data.split(":", 1)[1])
    except (ValueError, IndexError, AttributeError):
        return None


@router.callback_query(F.data.startswith("order_check:"))
async def handle_order_check(
    callback: CallbackQuery,
    session: AsyncSession,
    db_user: User | None = None,
) -> None:
    if not db_user:
        await safe_callback_answer(callback, texts.ERROR_USER_NOT_FOUND, show_alert=True)
        return

    order_id = _uuid_from_callback(callback.data)
    if not order_id:
        await safe_callback_answer(callback, texts.ERROR_INVALID_REQUEST, show_alert=True)
        return

    order = await session.get(Order, order_id)
    if not order or order.user_id != db_user.id:
        await safe_callback_answer(callback, texts.PAYMENT_PURCHASE_INVALID_OPERATION, show_alert=True)
        return

    if order.status == "refunded":
        await safe_callback_answer(callback, texts.PAYMENT_PURCHASE_INVALID_OPERATION, show_alert=True)
        return

    if order.status == "paid":
        if (order.metadata_ or {}).get("settlement_held"):
            paid_order = await OrderService.mark_order_paid(session, order.id)
            if not paid_order or (paid_order.metadata_ or {}).get("settlement_held"):
                await safe_callback_answer(
                    callback,
                    texts.PAYMENT_DISPUTE_BLOCKED_NOTICE,
                    show_alert=True,
                )
                return
            order = paid_order

        if order.service_type == "topup":
            from .balance_routes import _render_balance
            from bot.keyboards.payment import get_topup_credit_keyboard

            order_context = (
                (order.metadata_ or {}).get("context")
                if order.metadata_
                else None
            )
            kb = (
                get_topup_credit_keyboard(order_context)
                if order_context
                else None
            )
            await _render_balance(
                callback.bot,
                callback.message.chat.id,
                session,
                db_user,
                notice=texts.TOPUP_CREDITED_NOTICE,
                message_effect_id=EFFECT_CONFETTI,
                force_new=True,
                custom_keyboard=kb,
            )
            # Push delivered straight to the user looking at the screen:
            # clear the debt so the credit-notify worker does not duplicate it.
            mark_notified(order)
            await safe_callback_answer(callback)
            return

        balance = await get_account_balance(session, user_id=db_user.id)
        text, keyboard = build_tariff_success_card(order, balance)
        await render_hub(
            callback.bot,
            callback.message.chat.id,
            text,
            keyboard,
            message_effect_id=EFFECT_CONFETTI,
            force_new=True,
        )
        mark_notified(order)
        await safe_callback_answer(callback)
        return

    if order.external_id:
        gateway = get_payment_gateway(order.payment_method)
        status_res = await gateway.check_payment_status(order.external_id)
        if status_res.is_paid:
            paid_order = await OrderService.mark_order_paid(
                session,
                order.id,
                external_id=order.external_id,
                paid_amount_rub=status_res.amount_rub,
            )
            if not paid_order or paid_order.status != "paid":
                await safe_callback_answer(callback, texts.ERROR_PAYMENT_SERVICE, show_alert=True)
                return

            if (paid_order.metadata_ or {}).get("settlement_held"):
                await safe_callback_answer(
                    callback,
                    texts.PAYMENT_DISPUTE_BLOCKED_NOTICE,
                    show_alert=True,
                )
                return

            if paid_order.service_type == "topup":
                from .balance_routes import _render_balance
                from bot.keyboards.payment import get_topup_credit_keyboard

                order_context = (
                    (paid_order.metadata_ or {}).get("context")
                    if paid_order.metadata_
                    else None
                )
                kb = (
                    get_topup_credit_keyboard(order_context)
                    if order_context
                    else None
                )
                await _render_balance(
                    callback.bot,
                    callback.message.chat.id,
                    session,
                    db_user,
                    notice=texts.TOPUP_CREDITED_NOTICE,
                    message_effect_id=EFFECT_CONFETTI,
                    force_new=True,
                    custom_keyboard=kb,
                )
                mark_notified(paid_order)
                await safe_callback_answer(callback)
                return

            balance = await get_account_balance(session, user_id=db_user.id)
            text, keyboard = build_tariff_success_card(paid_order, balance)
            await render_hub(
                callback.bot,
                callback.message.chat.id,
                text,
                keyboard,
                message_effect_id=EFFECT_CONFETTI,
                force_new=True,
            )
            mark_notified(paid_order)
            await safe_callback_answer(callback)
            return
        elif status_res.is_canceled:
            OrderService.mark_order_canceled(
                order,
                reason=status_res.cancellation_reason or "gateway_canceled",
            )
            await session.flush()
            await safe_callback_answer(
                callback,
                texts.PAYMENT_ORDER_PAYMENT_CANCELLED,
                show_alert=True,
            )
            return
        elif status_res.is_temporary_error or status_res.status_str in (
            "not_found",
            "server_error",
            "timeout",
            "network_error",
            "rate_limited",
            "unknown",
        ):
            await safe_callback_answer(
                callback,
                texts.ERROR_PAYMENT_SERVICE,
                show_alert=True,
            )
            return

    await safe_callback_answer(
        callback,
        texts.PAYMENT_ORDER_WAITING_PAYMENT,
        show_alert=True,
    )


@router.callback_query(F.data.startswith("order_cancel:"))
async def handle_order_cancel(
    callback: CallbackQuery,
    session: AsyncSession,
    db_user: User | None = None,
) -> None:
    order_id = _uuid_from_callback(callback.data)
    order = None
    if order_id and db_user:
        order = await session.get(Order, order_id, with_for_update=True)
        if order and order.user_id == db_user.id and order.status == "pending":
            OrderService.mark_order_canceled(order, reason="user_canceled")
            await session.flush()

    await safe_callback_answer(callback, show_alert=False)

    order_context = (
        (order.metadata_ or {}).get("context") if order and order.metadata_ else None
    )
    if (order_context or {}).get("source") == "white_internet":
        from .common import _show_hub

        await _show_hub(callback, session, db_user)
        return

    if order and order.service_type == "topup":
        from .balance_routes import _render_balance

        await _render_balance(
            callback.bot,
            callback.message.chat.id,
            session,
            db_user,
        )
        return

    from bot.handlers.start import _build_hub_text_and_kb

    text, kb = await _build_hub_text_and_kb(session, db_user)
    await render_hub(
        callback.bot,
        callback.message.chat.id,
        text,
        kb,
        session=session,
    )
