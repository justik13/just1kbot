import logging
import math

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from bot import texts
from database.models import Payment
from database.repositories.account_ledger_repo import get_payment_refundable_amount
from database.repositories.payments_repo import get_payment_by_id
from services.payment_status import payment_display_status
from services.provider_refunds import (
    BalanceRefundError,
    request_balance_topup_refund,
)
from utils.admin import is_admin
from utils.callbacks import parse_callback_id
from utils.formatters import format_datetime
from utils.telegram import safe
from utils.text_limits import truncate_button_text

router = Router()
logger = logging.getLogger(__name__)

PAYMENTS_PER_PAGE = 20


def _refund_available(payment: Payment) -> bool:
    return bool(
        payment.provider_status == "succeeded"
        and payment.external_id
        and payment.currency == "RUB"
    )


def _get_payment_card_keyboard(
    payment: Payment,
    user_telegram_id: int | None,
) -> InlineKeyboardBuilder:
    builder = InlineKeyboardBuilder()

    if _refund_available(payment):
        builder.button(
            text=texts.UI_BOT_HANDLERS_ADMIN_PAYMENTS_L50_1,
            callback_data=f"admin_payment_refund:{payment.id}",
        )

    if user_telegram_id:
        builder.button(
            text=texts.UI_BOT_HANDLERS_ADMIN_PAYMENTS_L56_1,
            callback_data=f"admin_user_card:{user_telegram_id}",
        )

    builder.button(
        text=texts.UI_BOT_HANDLERS_ADMIN_PAYMENTS_L61_1,
        callback_data="admin_payments",
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
        texts.RUNTIME_BOT_HANDLERS_ADMIN_PAYMENTS_L75_1.format(value_0=page, value_1=total_pages, value_2=total)
    )
    builder = InlineKeyboardBuilder()
    if not payments:
        rendered += texts.RUNTIME_BOT_HANDLERS_ADMIN_PAYMENTS_L80_1
    else:
        for payment in payments:
            display_status = payment_display_status(payment)
            status_icon = texts.PAYMENT_STATUS_ICONS.get(
                display_status,
                texts.RUNTIME_BOT_HANDLERS_ADMIN_PAYMENTS_L86_1,
            )
            if payment.user and payment.user.username:
                user_label = f"@{payment.user.username}"
            elif payment.user:
                user_label = texts.ADMIN_PAYMENT_USER_ID_COMPACT.format(user_id=payment.user.telegram_id)
            else:
                user_label = texts.RUNTIME_BOT_HANDLERS_ADMIN_PAYMENTS_L93_1
            button_text = truncate_button_text(
                texts.RUNTIME_BOT_HANDLERS_ADMIN_PAYMENTS_L95_1.format(value_0=status_icon, value_1=payment.id, value_2=user_label, value_3=payment.amount)
            )
            builder.button(
                text=button_text,
                callback_data=f"admin_payment_card:{payment.id}",
            )
    if page > 1:
        builder.button(
            text=texts.UI_BOT_HANDLERS_ADMIN_PAYMENTS_L105_1,
            callback_data=f"admin_payments_page:{page - 1}",
        )
    if page < total_pages:
        builder.button(
            text=texts.UI_BOT_HANDLERS_ADMIN_PAYMENTS_L110_1,
            callback_data=f"admin_payments_page:{page + 1}",
        )
    builder.button(text="🛒 К логам покупок", callback_data="admin_purchases")
    builder.button(text=texts.UI_BOT_HANDLERS_ADMIN_PAYMENTS_L113_1, callback_data="admin_menu")
    builder.adjust(1)
    return rendered, builder


async def _show_payments_list(
    callback: CallbackQuery,
    session: AsyncSession,
    page: int = 1,
):
    total_payments = await session.scalar(
        select(func.count(Payment.id)),
    ) or 0
    total_pages = max(
        1,
        math.ceil(total_payments / PAYMENTS_PER_PAGE),
    )
    page = min(page, total_pages)
    offset = (page - 1) * PAYMENTS_PER_PAGE
    stmt = (
        select(Payment)
        .options(
            selectinload(Payment.user),
        )
        .order_by(Payment.created_at.desc())
        .offset(offset)
        .limit(PAYMENTS_PER_PAGE)
    )
    result = await session.execute(stmt)
    payments = result.scalars().all()
    rendered, kb = await _build_payments_list_text_and_kb(
        payments,
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
            texts.UI_BOT_HANDLERS_ADMIN_PAYMENTS_L192_1,
            show_alert=True,
        )
        return
    await state.clear()
    await _show_payments_list(callback, session, page=page)
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
            texts.UI_BOT_HANDLERS_ADMIN_PAYMENTS_L216_1,
            show_alert=True,
        )
        return
    await state.clear()
    payment = await get_payment_by_id(session, payment_id)
    if not payment:
        await callback.answer(
            texts.UI_BOT_HANDLERS_ADMIN_PAYMENTS_L224_1,
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
        user_label = texts.RUNTIME_BOT_HANDLERS_ADMIN_PAYMENTS_L239_1
        user_telegram_id = None

    display_status = payment_display_status(payment)
    status_name = texts.PAYMENT_STATUS_NAMES.get(
        display_status,
        display_status,
    )
    status_icon = texts.PAYMENT_STATUS_ICONS.get(
        display_status,
        texts.RUNTIME_BOT_HANDLERS_ADMIN_PAYMENTS_L249_1,
    )

    reason_line = ""
    if (
        display_status == "requires_manual_review"
        and payment.manual_review_reason
    ):
        reason_line = (
            texts.RUNTIME_BOT_HANDLERS_ADMIN_PAYMENTS_L258_1.format(value_0=safe(payment.manual_review_reason))
        )

    refundable_line = ""
    if _refund_available(payment):
        refundable = await get_payment_refundable_amount(
            session,
            payment_id=payment.id,
        )
        refundable_line = texts.RUNTIME_BOT_HANDLERS_ADMIN_PAYMENTS_L268_1.format(value_0=int(refundable))

    rendered = (
        texts.RUNTIME_BOT_HANDLERS_ADMIN_PAYMENTS_L271_1.format(value_0=payment.id, value_1=payment.id, value_2=user_label, value_3=payment.amount, value_4=payment.currency, value_5=status_icon, value_6=status_name, value_7=safe(payment.provider_status), value_8=safe(payment.fulfillment_status), value_9=format_datetime(payment.created_at), value_10=format_datetime(payment.paid_at), value_11=safe(payment.external_id or texts.PLACEHOLDER_DASH), value_12=refundable_line, value_13=reason_line)
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


@router.callback_query(F.data.startswith("admin_payment_refund:"))
async def confirm_payment_refund(
    callback: CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
):
    if not is_admin(callback.from_user.id):
        await callback.answer(texts.ERROR_ACCESS_DENIED, show_alert=True)
        return
    payment_id = parse_callback_id(callback.data, 1)
    payment = (
        await get_payment_by_id(session, payment_id)
        if payment_id is not None
        else None
    )
    if payment is None or not _refund_available(payment):
        await callback.answer(texts.UI_BOT_HANDLERS_ADMIN_PAYMENTS_L319_1, show_alert=True)
        return
    refundable = await get_payment_refundable_amount(
        session,
        payment_id=payment.id,
    )
    if refundable <= 0:
        await callback.answer(texts.UI_BOT_HANDLERS_ADMIN_PAYMENTS_L326_1, show_alert=True)
        return
    await state.clear()
    builder = InlineKeyboardBuilder()
    builder.button(
        text=texts.UI_BOT_HANDLERS_ADMIN_PAYMENTS_L331_1.format(value_0=int(refundable)),
        callback_data=f"admin_payment_refund_confirm:{payment.id}",
    )
    builder.button(
        text=texts.UI_BOT_HANDLERS_ADMIN_PAYMENTS_L335_1,
        callback_data=f"admin_payment_card:{payment.id}",
    )
    builder.adjust(1)
    await callback.message.edit_text(
        texts.UI_BOT_HANDLERS_ADMIN_PAYMENTS_L340_1.format(value_0=payment.id, value_1=safe(payment.external_id), value_2=int(refundable)),
        reply_markup=builder.as_markup(),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data.startswith("admin_payment_refund_confirm:"))
async def enqueue_payment_refund(
    callback: CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
):
    if not is_admin(callback.from_user.id):
        await callback.answer(texts.ERROR_ACCESS_DENIED, show_alert=True)
        return
    payment_id = parse_callback_id(callback.data, 1)
    if payment_id is None:
        await callback.answer(texts.UI_BOT_HANDLERS_ADMIN_PAYMENTS_L363_1, show_alert=True)
        return
    try:
        request = await request_balance_topup_refund(
            session,
            payment_id=payment_id,
            requested_by_admin_id=callback.from_user.id,
        )
    except BalanceRefundError as exc:
        messages = {
            "payment_not_found": texts.RUNTIME_BOT_HANDLERS_ADMIN_PAYMENTS_L368_1,
            "refund_requires_balance_topup": texts.RUNTIME_BOT_HANDLERS_ADMIN_PAYMENTS_L369_1,
            "payment_not_refundable": texts.RUNTIME_BOT_HANDLERS_ADMIN_PAYMENTS_L370_1,
            "provider_payment_id_missing": texts.RUNTIME_BOT_HANDLERS_ADMIN_PAYMENTS_L371_1,
            "no_refundable_balance": texts.RUNTIME_BOT_HANDLERS_ADMIN_PAYMENTS_L372_1,
            "active_refund_reservation_missing": texts.RUNTIME_BOT_HANDLERS_ADMIN_PAYMENTS_L373_1,
        }
        await callback.answer(
            messages.get(exc.code, texts.RUNTIME_BOT_HANDLERS_ADMIN_PAYMENTS_L376_1),
            show_alert=True,
        )
        return

    await state.clear()
    builder = InlineKeyboardBuilder()
    builder.button(
        text=texts.UI_BOT_HANDLERS_ADMIN_PAYMENTS_L389_1,
        callback_data=f"admin_payment_card:{payment_id}",
    )
    builder.button(text=texts.UI_BOT_HANDLERS_ADMIN_PAYMENTS_L392_1, callback_data="admin_payments")
    builder.adjust(1)
    status_text = (
        texts.RUNTIME_BOT_HANDLERS_ADMIN_PAYMENTS_L390_1
        if request.created
        else texts.RUNTIME_BOT_HANDLERS_ADMIN_PAYMENTS_L392_1
    )
    await callback.message.edit_text(
        texts.UI_BOT_HANDLERS_ADMIN_PAYMENTS_L400_1.format(value_0=status_text, value_1=int(request.operation.amount), value_2=request.operation.operation_id, value_3=safe(request.operation.status)),
        reply_markup=builder.as_markup(),
        parse_mode="HTML",
    )
    await callback.answer()
