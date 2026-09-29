"""Order payment, confirmation, and status check routes."""
from __future__ import annotations

from decimal import Decimal
import logging
import uuid

from aiogram import F, Router
from aiogram.types import CallbackQuery
from sqlalchemy.ext.asyncio import AsyncSession

from bot import texts
from bot.formatters import get_tariff_display_name
from bot.keyboards import (
    get_order_invoice_keyboard,
    get_payment_success_keyboard,
)
from database.models import Order, User
from database.repositories.account_ledger_repo import get_account_balance
from database.repositories.tariffs_repo import get_tariff_by_id
from integrations.payment_gateways.factory import get_payment_gateway
from services.maintenance_service import MaintenanceService
from services.order_service import (
    AccountDebtBlockedError,
    FinancialHoldBlockedError,
    InsufficientBalanceError,
    OrderService,
)
from utils.datetime_helpers import now_utc
from utils.telegram import EFFECT_CONFETTI, render_hub

from .common import _render_maintenance

router = Router()
logger = logging.getLogger(__name__)


def _uuid_from_callback(data: str) -> uuid.UUID | None:
    try:
        return uuid.UUID(data.split(":", 1)[1])
    except (ValueError, IndexError, AttributeError):
        return None


@router.callback_query(F.data.startswith("order_pay_wallet:"))
async def handle_order_pay_wallet(
    callback: CallbackQuery,
    session: AsyncSession,
    db_user: User | None = None,
) -> None:
    if not db_user:
        await callback.answer(texts.ERROR_USER_NOT_FOUND, show_alert=True)
        return
    # Capture primitive before DB/gateway IO: on failure the session may be
    # rolled back (expired ORM -> MissingGreenlet if db_user.id is touched).
    db_user_id = db_user.id
    if not await MaintenanceService.can_user_perform_action(
        session, callback.from_user.id
    ):
        await _render_maintenance(callback, session, back_to="payment_showcase")
        return

    try:
        tariff_id = int(callback.data.split(":")[1])
    except (IndexError, ValueError):
        await callback.answer(texts.ERROR_INVALID_REQUEST, show_alert=True)
        return

    tariff = await get_tariff_by_id(session, tariff_id)
    if not tariff or not tariff.is_active:
        await callback.answer(texts.PAYMENT_TARIFF_UNAVAILABLE_NOTICE, show_alert=True)
        return

    await callback.answer(texts.PAYMENT_PURCHASE_PROCESSING_NOTICE, show_alert=False)

    try:
        order = await OrderService.pay_from_wallet(
            session,
            user_id=db_user_id,
            service_type="awg",
            tariff_id=tariff.id,
        )
    except InsufficientBalanceError:
        await callback.answer(texts.PAYMENT_INSUFFICIENT_FUNDS_ALERT, show_alert=True)
        return
    except FinancialHoldBlockedError:
        await callback.answer(texts.PAYMENT_DISPUTE_BLOCKED_NOTICE, show_alert=True)
        return
    except Exception as exc:
        # pay_from_wallet() may have flushed a partial paid order before
        # failing (debit/fulfill). Roll back instead of committing it:
        # session_scope() commits on clean return.
        logger.exception(
            "Wallet payment failed for user %s, tariff %s: %s",
            db_user_id,
            tariff_id,
            exc,
        )
        try:
            await session.rollback()
        except Exception:
            pass
        await callback.answer(texts.PAYMENT_PURCHASE_OPEN_FAILED, show_alert=True)
        return

    balance = await get_account_balance(session, user_id=db_user_id)
    tariff_name = get_tariff_display_name(order.device_limit or 2)
    is_change = bool(order.metadata_ and order.metadata_.get("is_tariff_change"))
    operation = texts.PAYMENT_OP_TITLE_CHANGE if is_change else texts.PURCHASE_COMPLETED

    await render_hub(
        callback.bot,
        callback.message.chat.id,
        texts.PAYMENT_PURCHASE_SUCCESS_CARD.format(
            operation_title=operation,
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


@router.callback_query(F.data.startswith("order_pay_card:"))
async def handle_order_pay_card(
    callback: CallbackQuery,
    session: AsyncSession,
    db_user: User | None = None,
) -> None:
    if not db_user:
        await callback.answer(texts.ERROR_USER_NOT_FOUND, show_alert=True)
        return
    # Capture primitive before gateway IO: OrderService.create_order() does
    # session.rollback() on gateway failure, expiring db_user (MissingGreenlet).
    db_user_id = db_user.id
    if not await MaintenanceService.can_user_perform_action(
        session, callback.from_user.id
    ):
        await _render_maintenance(callback, session, back_to="payment_showcase")
        return
    # Mirror select_tariff(): old order_pay_card buttons must not bypass it.
    # Authoritative enforcement lives at settlement (mark_order_paid);
    # this is an early UX reject on (possibly stale) middleware state.
    if getattr(db_user, "financial_hold", False):
        await callback.answer(texts.PAYMENT_DISPUTE_BLOCKED_NOTICE, show_alert=True)
        return

    try:
        tariff_id = int(callback.data.split(":")[1])
    except (IndexError, ValueError):
        await callback.answer(texts.ERROR_INVALID_REQUEST, show_alert=True)
        return

    tariff = await get_tariff_by_id(session, tariff_id)
    if not tariff or not tariff.is_active:
        await callback.answer(texts.PAYMENT_TARIFF_UNAVAILABLE_NOTICE, show_alert=True)
        return

    # Check if this is a zero-cost change: if so, route to wallet payment
    current_tid = getattr(db_user, "current_tariff_id", None)
    sub_end = getattr(db_user, "subscription_end", None)
    now = now_utc()
    if (
        current_tid
        and current_tid != tariff.id
        and sub_end
        and sub_end > now
    ):
        current_tariff = await get_tariff_by_id(session, current_tid)
        due_rub, _ = OrderService.calculate_tariff_change(
            current_tariff, tariff, sub_end, now=now
        )
        if due_rub <= Decimal("0.00"):
            # NOTE: only pre-mutation domain errors are caught here
            # (hold/debt/insufficient are raised before any DB change).
            # Unexpected failures must roll back the partial paid order
            # instead of committing it (session_scope commits on return).
            try:
                order = await OrderService.pay_from_wallet(
                    session,
                    user_id=db_user_id,
                    service_type="awg",
                    tariff_id=tariff.id,
                )
            except FinancialHoldBlockedError:
                await callback.answer(texts.PAYMENT_DISPUTE_BLOCKED_NOTICE, show_alert=True)
                return
            except (AccountDebtBlockedError, InsufficientBalanceError):
                await callback.answer(texts.PAYMENT_INSUFFICIENT_FUNDS_ALERT, show_alert=True)
                return
            except Exception as exc:
                logger.exception(
                    "Zero-cost tariff change failed for user %s, tariff %s: %s",
                    db_user_id,
                    tariff.id,
                    exc,
                )
                try:
                    await session.rollback()
                except Exception:
                    pass
                await callback.answer(texts.PAYMENT_PURCHASE_OPEN_FAILED, show_alert=True)
                return
            balance = await get_account_balance(session, user_id=db_user_id)
            tariff_name = get_tariff_display_name(order.device_limit or 2)
            await render_hub(
                callback.bot,
                callback.message.chat.id,
                texts.PAYMENT_PURCHASE_SUCCESS_CARD.format(
                    operation_title=texts.PAYMENT_OP_TITLE_CHANGE,
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
            return

    await callback.answer(texts.PAYMENT_CREATING_LINK_NOTICE, show_alert=False)

    try:
        bot_username = getattr(getattr(callback.bot, "_me", None), "username", None)
        order = await OrderService.create_order(
            session,
            user_id=db_user_id,
            service_type="awg",
            tariff_id=tariff.id,
            payment_method="yookassa",
            bot_username=bot_username,
        )
    except Exception as exc:
        logger.exception(
            "Failed to create YooKassa order for user %s: %s", db_user_id, exc
        )
        await callback.answer(texts.ERROR_PAYMENT_SERVICE, show_alert=True)
        return

    tariff_name = get_tariff_display_name(order.device_limit or 2)
    price = int(order.amount_rub)
    text = texts.PAYMENT_ORDER_INVOICE_CARD.format(
        tariff_name=tariff_name,
        duration_days=order.duration_days,
        price=price,
    )

    from .common import _is_subscription_active

    is_active = await _is_subscription_active(db_user)
    back_target = "menu_subscription" if is_active else "payment_showcase"

    await render_hub(
        callback.bot,
        callback.message.chat.id,
        text,
        get_order_invoice_keyboard(
            payment_url=order.payment_url or "",
            order_id=str(order.id),
            price=price,
            back_callback=back_target,
        ),
    )


@router.callback_query(F.data.startswith("order_check:"))
async def handle_order_check(
    callback: CallbackQuery,
    session: AsyncSession,
    db_user: User | None = None,
) -> None:
    if not db_user:
        await callback.answer(texts.ERROR_USER_NOT_FOUND, show_alert=True)
        return

    order_id = _uuid_from_callback(callback.data)
    if not order_id:
        await callback.answer(texts.ERROR_INVALID_REQUEST, show_alert=True)
        return

    order = await session.get(Order, order_id, with_for_update=True)
    if not order or order.user_id != db_user.id:
        await callback.answer(texts.PAYMENT_PURCHASE_INVALID_OPERATION, show_alert=True)
        return

    if order.status == "paid":
        if (order.metadata_ or {}).get("settlement_held"):
            # Benefits were withheld at settlement (hold/block): never
            # present them as credited/granted.
            await callback.answer(
                texts.PAYMENT_DISPUTE_BLOCKED_NOTICE, show_alert=True
            )
            return
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
            return

        balance = await get_account_balance(session, user_id=db_user.id)
        tariff_name = get_tariff_display_name(order.device_limit or 2)
        is_change = bool(order.metadata_ and order.metadata_.get("is_tariff_change"))
        operation = texts.PAYMENT_OP_TITLE_CHANGE if is_change else texts.PURCHASE_COMPLETED
        await render_hub(
            callback.bot,
            callback.message.chat.id,
            texts.PAYMENT_PURCHASE_SUCCESS_CARD.format(
                operation_title=operation,
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
        return

    if order.external_id:
        gateway = get_payment_gateway(order.payment_method)
        status_res = await gateway.check_payment_status(order.external_id)
        if status_res.is_paid:
            paid_order = await OrderService.mark_order_paid(
                session, order.id, external_id=order.external_id
            )
            if not paid_order:
                await callback.answer(texts.ERROR_PAYMENT_SERVICE, show_alert=True)
                return

            if (paid_order.metadata_ or {}).get("settlement_held"):
                # Credit/fulfillment were withheld (hold/block at settlement):
                # never show a success/credited card for withheld benefits.
                await callback.answer(
                    texts.PAYMENT_DISPUTE_BLOCKED_NOTICE, show_alert=True
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
                return

            balance = await get_account_balance(session, user_id=db_user.id)
            tariff_name = get_tariff_display_name(paid_order.device_limit or 2)
            is_change = bool(
                paid_order.metadata_ and paid_order.metadata_.get("is_tariff_change")
            )
            operation = texts.PAYMENT_OP_TITLE_CHANGE if is_change else texts.PURCHASE_COMPLETED
            await render_hub(
                callback.bot,
                callback.message.chat.id,
                texts.PAYMENT_PURCHASE_SUCCESS_CARD.format(
                    operation_title=operation,
                    tariff_name=tariff_name,
                    duration_days=paid_order.duration_days,
                    charged=int(paid_order.amount_rub),
                    real_balance=int(balance.real_available),
                    bonus_balance=int(balance.bonus_available),
                ),
                get_payment_success_keyboard(),
                message_effect_id=EFFECT_CONFETTI,
                force_new=True,
            )
            return
        elif status_res.is_canceled:
            OrderService.mark_order_canceled(order, reason="gateway_canceled")
            await session.flush()
            await callback.answer(
                texts.PAYMENT_ORDER_PAYMENT_CANCELLED, show_alert=True
            )
            return

    await callback.answer(
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

    await callback.answer(show_alert=False)

    order_context = (
        (order.metadata_ or {}).get("context") if order and order.metadata_ else None
    )
    if (order_context or {}).get("source") == "white_internet":
        from bot.handlers.white_internet import show_white_internet_menu

        await show_white_internet_menu(callback, session)
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

    from .common import _is_subscription_active, _show_hub, render_tariff_showcase

    is_change = bool(order and order.metadata_ and order.metadata_.get("is_tariff_change"))
    if is_change or (db_user and await _is_subscription_active(db_user)):
        await _show_hub(callback, db_user, session)
        return

    await render_tariff_showcase(callback.bot, callback.message.chat.id, session)
