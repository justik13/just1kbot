from datetime import datetime, timezone
import logging
import math
import uuid

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from bot import texts
from database.models import Order, Payment
from config.enums import (
    PaymentFulfillmentStatus,
    PaymentProviderStatus,
    PaymentReconciliationStatus,
)
from database.repositories.account_ledger_repo import get_payment_refundable_amount
from database.repositories.payments_repo import get_payment_by_id
from utils.admin import is_admin
from utils.callbacks import parse_callback_id
from utils.formatters import format_datetime
from utils.telegram import safe
from utils.text_limits import truncate_button_text

router = Router()
logger = logging.getLogger(__name__)

PAYMENTS_PER_PAGE = 20


def _normalize_dt(dt: datetime | None) -> datetime:
    if dt is None:
        return datetime.min.replace(tzinfo=timezone.utc)
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def payment_display_status(payment) -> str:
    if (
        payment.reconciliation_status in {PaymentReconciliationStatus.MISMATCH, PaymentReconciliationStatus.MANUAL_REVIEW}
        or payment.provider_status == PaymentProviderStatus.MANUAL_REVIEW
        or payment.fulfillment_status == PaymentFulfillmentStatus.MANUAL_REVIEW
    ):
        return "requires_manual_review"
    if (
        payment.provider_status == PaymentProviderStatus.REFUNDED
        or payment.fulfillment_status == PaymentFulfillmentStatus.REVERSED
    ):
        return "refunded"
    if payment.provider_status == PaymentProviderStatus.CANCELED:
        return "cancelled"
    if (
        payment.provider_status == PaymentProviderStatus.SUCCEEDED
        and payment.fulfillment_status == PaymentFulfillmentStatus.SUCCEEDED
    ):
        return "completed"
    if payment.provider_status == PaymentProviderStatus.SUCCEEDED:
        return "paid_processing"
    if payment.provider_status in {
        PaymentProviderStatus.NOT_CREATED,
        PaymentProviderStatus.CREATING,
        PaymentProviderStatus.PENDING,
        PaymentProviderStatus.WAITING_FOR_CAPTURE,
        PaymentProviderStatus.UNKNOWN,
    }:
        return "pending"
    return "failed"


def order_display_status(order: Order) -> str:
    if (order.metadata_ or {}).get("settlement_held"):
        return "requires_manual_review"
    if order.status == "paid":
        return "completed"
    if order.status == "canceled":
        return "cancelled"
    if order.status == "refunded":
        return "refunded"
    if order.status == "pending":
        return "pending"
    return "failed"



def _get_payment_card_keyboard(
    payment: Payment,
    user_telegram_id: int | None,
) -> InlineKeyboardBuilder:
    builder = InlineKeyboardBuilder()

    if user_telegram_id:
        builder.button(
            text=texts.ADMIN_CLIENT_CARD_BUTTON,
            callback_data=f"admin_user_card:{user_telegram_id}",
        )

    builder.button(
        text=texts.ADMIN_BTN_BACK_TO_PAYMENTS,
        callback_data="admin_payments",
    )
    builder.button(
        text=texts.BTN_BACK_TO_FINANCES,
        callback_data="admin_cat_finance",
    )
    builder.adjust(1)
    return builder


async def _build_payments_list_text_and_kb(
    payments,
    page: int,
    total_pages: int,
    total: int,
) -> tuple[str, InlineKeyboardBuilder]:
    rendered = (
        texts.ADMIN_PAYMENTS_LIST_TITLE.format(page=page, total_pages=total_pages, total=total)
    )
    builder = InlineKeyboardBuilder()
    if not payments:
        rendered += texts.ADMIN_PAYMENTS_LIST_EMPTY
    else:
        for payment in payments:
            if isinstance(payment, Order):
                display_status = order_display_status(payment)
                status_icon = texts.PAYMENT_STATUS_ICONS.get(
                    display_status,
                    texts.ADMIN_PAYMENT_STATUS_FALLBACK_ICON,
                )
                if payment.user and payment.user.username:
                    user_label = f"@{payment.user.username}"
                elif payment.user:
                    user_label = texts.ADMIN_PAYMENT_USER_ID_COMPACT.format(user_id=payment.user.telegram_id)
                else:
                    user_label = texts.PLACEHOLDER_DASH
                button_text = truncate_button_text(
                    f"{status_icon} #{str(payment.id)[:8]} • {user_label} • {int(payment.amount_rub)} ₽"
                )
                builder.button(
                    text=button_text,
                    callback_data=f"admin_order_card:{payment.id}",
                )
            else:
                display_status = payment_display_status(payment)
                status_icon = texts.PAYMENT_STATUS_ICONS.get(
                    display_status,
                    texts.ADMIN_PAYMENT_STATUS_FALLBACK_ICON,
                )
                if payment.user and payment.user.username:
                    user_label = f"@{payment.user.username}"
                elif payment.user:
                    user_label = texts.ADMIN_PAYMENT_USER_ID_COMPACT.format(user_id=payment.user.telegram_id)
                else:
                    user_label = texts.PLACEHOLDER_DASH
                button_text = truncate_button_text(
                    texts.ADMIN_PAYMENTS_ROW_ENTRY.format(status_icon=status_icon, payment_id=payment.id, user_label=user_label, amount_rub=payment.amount)
                )
                builder.button(
                    text=button_text,
                    callback_data=f"admin_payment_card:{payment.id}",
                )
    if page > 1:
        builder.button(
            text=texts.ADMIN_BTN_PAGINATION_PREV,
            callback_data=f"admin_payments_page:{page - 1}",
        )
    if page < total_pages:
        builder.button(
            text=texts.ADMIN_BTN_PAGINATION_NEXT,
            callback_data=f"admin_payments_page:{page + 1}",
        )
    builder.button(text=texts.BTN_ADMIN_PURCHASES_LOGS, callback_data="admin_purchases")
    builder.button(text=texts.BTN_BACK_TO_FINANCES, callback_data="admin_cat_finance")
    builder.button(text=texts.ADMIN_BTN_BACK_TO_ADMIN, callback_data="admin_menu")
    builder.adjust(1)
    return rendered, builder


async def _show_payments_list(
    callback: CallbackQuery,
    session: AsyncSession,
    page: int = 1,
):
    total_orders = (
        await session.scalar(
            select(func.count(Order.id)).where(Order.payment_method == "yookassa")
        )
        or 0
    )
    total_legacy = (
        await session.scalar(
            select(func.count(Payment.id))
        )
        or 0
    )
    total_payments = total_orders + total_legacy
    total_pages = max(
        1,
        math.ceil(total_payments / PAYMENTS_PER_PAGE),
    )
    page = min(max(1, page), total_pages)
    offset = (page - 1) * PAYMENTS_PER_PAGE
    needed = offset + PAYMENTS_PER_PAGE

    order_stmt = (
        select(Order)
        .where(Order.payment_method == "yookassa")
        .options(selectinload(Order.user))
        .order_by(Order.created_at.desc())
        .limit(needed)
    )
    legacy_stmt = (
        select(Payment)
        .options(selectinload(Payment.user))
        .order_by(Payment.created_at.desc())
        .limit(needed)
    )
    orders = (await session.execute(order_stmt)).scalars().all()
    legacy_payments = (await session.execute(legacy_stmt)).scalars().all()

    combined = sorted(
        [*orders, *legacy_payments],
        key=lambda item: _normalize_dt(item.created_at),
        reverse=True,
    )
    page_items = combined[offset : offset + PAYMENTS_PER_PAGE]

    rendered, kb = await _build_payments_list_text_and_kb(
        page_items,
        page,
        total_pages,
        total_payments,
    )
    try:
        await callback.message.edit_text(
            rendered,
            reply_markup=kb.as_markup(),
            parse_mode="HTML",
        )
    except TelegramBadRequest as e:
        logger.debug("_show_payments_list edit_text failed: %s", e)


@router.callback_query(F.data == "admin_payments")
async def show_payments_list(
    callback: CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
):
    if not is_admin(callback.from_user.id):
        await callback.answer(
            texts.ERROR_ACCESS_DENIED,
            show_alert=True,
        )
        return
    await state.clear()
    await _show_payments_list(callback, session, page=1)
    await callback.answer(show_alert=False)


@router.callback_query(F.data.startswith("admin_payments_page:"))
async def payments_pagination(
    callback: CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
):
    if not is_admin(callback.from_user.id):
        await callback.answer(
            texts.ERROR_ACCESS_DENIED,
            show_alert=True,
        )
        return
    page = parse_callback_id(callback.data, 1)
    if page is None or page < 1:
        await callback.answer(
            texts.ERROR_INVALID_REQUEST,
            show_alert=True,
        )
        return
    await state.clear()
    await _show_payments_list(callback, session, page=page)
    await callback.answer(show_alert=False)


@router.callback_query(F.data.startswith("admin_payments_filter:user:"))
async def show_user_payments_list(
    callback: CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
):
    if not is_admin(callback.from_user.id):
        await callback.answer(texts.ERROR_ACCESS_DENIED, show_alert=True)
        return

    parts = callback.data.split(":")
    if len(parts) < 4 or not parts[2].isdigit() or not parts[3].isdigit():
        await callback.answer(texts.ERROR_INVALID_REQUEST, show_alert=True)
        return

    telegram_id = int(parts[2])
    page = int(parts[3])

    from database.repositories.users_repo import get_user_by_telegram_id

    user = await get_user_by_telegram_id(session, telegram_id)
    if not user:
        await callback.answer(texts.ERROR_USER_NOT_FOUND, show_alert=True)
        return

    await state.clear()
    user_orders_count = (
        await session.scalar(
            select(func.count(Order.id)).where(
                Order.user_id == user.id,
                Order.payment_method == "yookassa",
            )
        )
        or 0
    )
    user_legacy_count = (
        await session.scalar(
            select(func.count(Payment.id)).where(Payment.user_id == user.id)
        )
        or 0
    )
    total_payments = user_orders_count + user_legacy_count

    total_pages = max(1, math.ceil(total_payments / PAYMENTS_PER_PAGE))
    page = min(max(1, page), total_pages)
    offset = (page - 1) * PAYMENTS_PER_PAGE
    needed = offset + PAYMENTS_PER_PAGE

    order_stmt = (
        select(Order)
        .where(Order.user_id == user.id, Order.payment_method == "yookassa")
        .options(selectinload(Order.user))
        .order_by(Order.created_at.desc())
        .limit(needed)
    )
    payment_stmt = (
        select(Payment)
        .where(Payment.user_id == user.id)
        .options(selectinload(Payment.user))
        .order_by(Payment.created_at.desc())
        .limit(needed)
    )
    orders = (await session.execute(order_stmt)).scalars().all()
    legacy = (await session.execute(payment_stmt)).scalars().all()
    combined = sorted(
        [*orders, *legacy],
        key=lambda item: _normalize_dt(item.created_at),
        reverse=True,
    )
    page_items = combined[offset : offset + PAYMENTS_PER_PAGE]

    user_label = user.username or str(user.telegram_id)
    header = texts.ADMIN_PAYMENTS_USER_TITLE.format(
        user_label=user_label,
        page=page,
        total_pages=total_pages,
        total_count=total_payments,
    ) + "\n\n"

    builder = InlineKeyboardBuilder()
    if not page_items:
        rendered = header + texts.ADMIN_PAYMENTS_USER_EMPTY
    else:
        rendered = header
        for item in page_items:
            if isinstance(item, Order):
                display_status = order_display_status(item)
                status_icon = texts.PAYMENT_STATUS_ICONS.get(
                    display_status,
                    texts.ADMIN_PAYMENT_STATUS_FALLBACK_ICON,
                )
                button_text = truncate_button_text(
                    f"{status_icon} #{str(item.id)[:8]} • {int(item.amount_rub)} ₽ ({format_datetime(item.created_at)})"
                )
                builder.button(
                    text=button_text,
                    callback_data=f"admin_order_card:{item.id}",
                )
            else:
                display_status = payment_display_status(item)
                status_icon = texts.PAYMENT_STATUS_ICONS.get(
                    display_status,
                    texts.ADMIN_PAYMENT_STATUS_FALLBACK_ICON,
                )
                button_text = truncate_button_text(
                    f"{status_icon} #{item.id} • {item.amount} ₽ ({format_datetime(item.created_at)})"
                )
                builder.button(
                    text=button_text,
                    callback_data=f"admin_payment_card:{item.id}",
                )

    if page > 1:
        builder.button(
            text=texts.ADMIN_BTN_PAGINATION_PREV,
            callback_data=f"admin_payments_filter:user:{telegram_id}:{page - 1}",
        )
    if page < total_pages:
        builder.button(
            text=texts.ADMIN_BTN_PAGINATION_NEXT,
            callback_data=f"admin_payments_filter:user:{telegram_id}:{page + 1}",
        )

    builder.button(
        text=texts.ADMIN_CLIENT_CARD_BUTTON,
        callback_data=f"admin_user_card:{telegram_id}",
    )
    builder.adjust(1)

    try:
        await callback.message.edit_text(
            rendered,
            reply_markup=builder.as_markup(),
            parse_mode="HTML",
        )
    except TelegramBadRequest as e:
        logger.debug("show_user_payments_list edit_text failed: %s", e)

    await callback.answer(show_alert=False)


@router.callback_query(F.data.startswith("admin_payment_card:"))
async def show_payment_card(
    callback: CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
):
    if not is_admin(callback.from_user.id):
        await callback.answer(
            texts.ERROR_ACCESS_DENIED,
            show_alert=True,
        )
        return
    payment_id = parse_callback_id(callback.data, 1)
    if payment_id is None:
        await callback.answer(
            texts.ERROR_INVALID_REQUEST,
            show_alert=True,
        )
        return
    await state.clear()
    payment = await get_payment_by_id(session, payment_id)
    if not payment:
        await callback.answer(
            texts.ADMIN_PAYMENT_NOT_FOUND_ALERT,
            show_alert=True,
        )
        return

    if payment.user and payment.user.username:
        user_label = texts.ADMIN_PAYMENT_USER_WITH_ID.format(
            username=safe(payment.user.username),
            user_id=payment.user.telegram_id,
        )
        user_telegram_id = payment.user.telegram_id
    elif payment.user:
        user_label = texts.ADMIN_PAYMENT_USER_ID.format(user_id=payment.user.telegram_id)
        user_telegram_id = payment.user.telegram_id
    else:
        user_label = texts.PLACEHOLDER_DASH
        user_telegram_id = None

    display_status = payment_display_status(payment)
    status_name = texts.PAYMENT_STATUS_NAMES.get(
        display_status,
        display_status,
    )
    status_icon = texts.PAYMENT_STATUS_ICONS.get(
        display_status,
        texts.ADMIN_PAYMENT_STATUS_FALLBACK_ICON,
    )

    reason_line = ""
    if (
        display_status == "requires_manual_review"
        and payment.manual_review_reason
    ):
        reason_line = (
            texts.ADMIN_PAYMENT_MANUAL_REVIEW_LINE.format(reason=safe(payment.manual_review_reason))
        )

    refundable_line = ""
    if payment.provider_status == "succeeded" and payment.currency == "RUB":
        refundable = await get_payment_refundable_amount(
            session,
            payment_id=payment.id,
        )
        if refundable > 0:
            refundable_line = texts.ADMIN_PAYMENT_REFUNDABLE_LINE.format(amount_rub=int(refundable))

    rendered = (
        texts.ADMIN_PAYMENT_CARD_TEMPLATE.format(
            payment_id=payment.id,
            user_label=user_label,
            amount_rub=payment.amount,
            currency=payment.currency,
            status_icon=status_icon,
            status_name=status_name,
            provider_status=safe(payment.provider_status),
            fulfillment_status=safe(payment.fulfillment_status),
            created_at=format_datetime(payment.created_at),
            paid_at=format_datetime(payment.paid_at),
            external_id=safe(payment.external_id or texts.PLACEHOLDER_DASH),
            refundable_line=refundable_line,
            reason_line=reason_line,
        )
    )

    kb = _get_payment_card_keyboard(
        payment,
        user_telegram_id,
    )

    try:
        await callback.message.edit_text(
            rendered,
            reply_markup=kb.as_markup(),
            parse_mode="HTML",
        )
    except TelegramBadRequest as e:
        logger.debug("show_payment_card edit_text failed: %s", e)
    await callback.answer(show_alert=False)


@router.callback_query(F.data.startswith("admin_order_card:"))
async def show_order_card(
    callback: CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
):
    if not is_admin(callback.from_user.id):
        await callback.answer(texts.ERROR_ACCESS_DENIED, show_alert=True)
        return
    try:
        order_id_str = callback.data.split(":")[1]
        order_uuid = uuid.UUID(order_id_str)
    except (IndexError, ValueError, TypeError):
        await callback.answer(texts.ERROR_INVALID_REQUEST, show_alert=True)
        return
    await state.clear()
    order = await session.scalar(
        select(Order)
        .where(Order.id == order_uuid)
        .options(selectinload(Order.user), selectinload(Order.tariff))
    )
    if not order:
        await callback.answer(texts.ADMIN_PAYMENT_NOT_FOUND_ALERT, show_alert=True)
        return
    user = order.user
    if user and user.username:
        user_label = texts.ADMIN_PAYMENT_USER_WITH_ID.format(
            username=safe(user.username),
            user_id=user.telegram_id,
        )
        user_telegram_id = user.telegram_id
    elif user:
        user_label = texts.ADMIN_PAYMENT_USER_ID.format(user_id=user.telegram_id)
        user_telegram_id = user.telegram_id
    else:
        user_label = texts.PLACEHOLDER_DASH
        user_telegram_id = None

    display_status = order_display_status(order)
    status_name = texts.PAYMENT_STATUS_NAMES.get(display_status, display_status)
    status_icon = texts.PAYMENT_STATUS_ICONS.get(
        display_status, texts.ADMIN_PAYMENT_STATUS_FALLBACK_ICON
    )

    tariff_label = order.tariff.name if order.tariff else order.service_type
    paid_at_line = (
        texts.ADMIN_ORDER_PAID_AT_LINE.format(paid_at=format_datetime(order.paid_at))
        if order.paid_at
        else ""
    )
    refunded_at_line = (
        texts.ADMIN_ORDER_REFUNDED_AT_LINE.format(
            refunded_at=format_datetime(order.refunded_at)
        )
        if order.refunded_at
        else ""
    )
    external_id_line = (
        texts.ADMIN_ORDER_EXTERNAL_ID_LINE.format(external_id=safe(order.external_id))
        if order.external_id
        else ""
    )
    description_line = (
        texts.ADMIN_ORDER_DESCRIPTION_LINE.format(description=safe(order.description))
        if order.description
        else ""
    )
    held_meta = order.metadata_ or {}
    held_line = (
        texts.ADMIN_ORDER_HELD_LINE.format(
            reason=safe(str(held_meta.get("settlement_hold_reason", "hold")))
        )
        if held_meta.get("settlement_held")
        else ""
    )
    payment_url_line = (
        texts.ADMIN_ORDER_PAYMENT_URL_LINE.format(payment_url=safe(order.payment_url))
        if (order.payment_url and order.status == "pending")
        else ""
    )
    diagnostics_line = ""
    now = datetime.now(timezone.utc)
    if order.status == "pending":
        if order.created_at and (now - _normalize_dt(order.created_at)).total_seconds() > 900:
            diagnostics_line += texts.ADMIN_ORDER_DIAGNOSTICS_PENDING
    if held_meta.get("payment_creation_ambiguous"):
        diagnostics_line += texts.ADMIN_ORDER_DIAGNOSTICS_AMBIGUOUS

    payment_method_label = "ЮKassa" if order.payment_method == "yookassa" else safe(order.payment_method)

    rendered = texts.ADMIN_ORDER_CARD_TEMPLATE.format(
        short_id=str(order.id)[:8],
        user_label=user_label,
        amount_rub=int(order.amount_rub),
        tariff_label=safe(tariff_label),
        service_type=safe(order.service_type),
        status_icon=status_icon,
        status_name=status_name,
        status=order.status,
        payment_method=payment_method_label,
        created_at=format_datetime(order.created_at),
        paid_at_line=paid_at_line,
        refunded_at_line=refunded_at_line,
        external_id_line=external_id_line,
        payment_url_line=payment_url_line,
        description_line=description_line,
        held_line=held_line,
        diagnostics_line=diagnostics_line,
    )
    builder = InlineKeyboardBuilder()
    if user_telegram_id:
        builder.button(
            text=texts.ADMIN_CLIENT_CARD_BUTTON,
            callback_data=f"admin_user_card:{user_telegram_id}",
        )
    builder.button(
        text=texts.ADMIN_BTN_BACK_TO_PAYMENTS,
        callback_data="admin_payments",
    )
    builder.button(
        text=texts.BTN_BACK_TO_FINANCES,
        callback_data="admin_cat_finance",
    )
    builder.adjust(1)
    try:
        await callback.message.edit_text(
            rendered,
            reply_markup=builder.as_markup(),
            parse_mode="HTML",
        )
    except TelegramBadRequest as e:
        logger.debug("show_order_card edit_text failed: %s", e)
    await callback.answer(show_alert=False)
