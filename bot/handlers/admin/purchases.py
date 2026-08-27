import logging
import math

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy.ext.asyncio import AsyncSession

from bot import texts
from database.repositories.purchases_repo import (
    get_purchase_log_by_id,
    get_purchase_logs_paginated,
)
from utils.admin import is_admin
from utils.callbacks import parse_callback_id
from utils.formatters import format_admin_breadcrumbs, format_datetime
from utils.telegram import safe
from utils.text_limits import truncate_button_text

router = Router()
logger = logging.getLogger(__name__)

PURCHASES_PER_PAGE = 10


async def _show_purchases_list(
    callback: CallbackQuery,
    session: AsyncSession,
    page: int = 1,
):
    entries, total = await get_purchase_logs_paginated(
        session, page=page, per_page=PURCHASES_PER_PAGE
    )
    total_pages = max(1, math.ceil(total / PURCHASES_PER_PAGE))
    page = min(max(1, page), total_pages)

    header = format_admin_breadcrumbs(texts.ADMIN_PURCHASES_TAB_TITLE, texts.ADMIN_PURCHASES_PAGE_INDICATOR.format(page=page, total_pages=total_pages))
    rendered = (
        f"{header}"+
        texts.ADMIN_PURCHASES_LOGS_TITLE.format(page=page, total_pages=total_pages, total=total)
    )

    builder = InlineKeyboardBuilder()

    if not entries:
        rendered += texts.ADMIN_PURCHASES_EMPTY_NOTICE
    else:
        for idx, entry in enumerate(entries, start=1):
            dt_str = format_datetime(entry.created_at)
            amount_str = f"{int(entry.amount_rub)} ₽" if entry.amount_rub > 0 else texts.ADMIN_PURCHASES_AMOUNT_ZERO_BONUS
            rendered += (
                texts.ADMIN_PURCHASES_ROW_FORMAT.format(
                    idx=idx, 
                    user_label=safe(entry.user_label), 
                    operation_title=safe(entry.operation_title), 
                    tariff_info=texts.ADMIN_PURCHASES_TARIFF_ROW.format(safe_entry_tariff_name=safe(entry.tariff_name), entry_duration_days=entry.duration_days, entry_device_limit=entry.device_limit, amount_str=amount_str), 
                    dt_str=dt_str
                )
            )


            button_text = truncate_button_text(
                texts.ADMIN_PURCHASES_ROW_BUTTON_TEMPLATE.format(
                    numeric_id=entry.numeric_id,
                    user_label=entry.user_label,
                    amount=amount_str,
                )
            )
            builder.button(
                text=button_text,
                callback_data=f"admin_purchase_card:{entry.id}",
            )

    nav_buttons = 0
    if page > 1:
        builder.button(
            text=texts.BTN_BACK,
            callback_data=f"admin_purchases_page:{page - 1}",
        )
        nav_buttons += 1

    if page < total_pages:
        builder.button(
            text=texts.BTN_PAGINATION_NEXT,
            callback_data=f"admin_purchases_page:{page + 1}",
        )
        nav_buttons += 1

    builder.button(text=texts.ADMIN_PURCHASES_PAYMENTS_BUTTON, callback_data="admin_payments")
    builder.button(text=texts.BTN_ADMIN_MENU, callback_data="admin_menu")

    adjust_pattern = [1] * len(entries)
    if nav_buttons > 0:
        adjust_pattern.append(nav_buttons)
    adjust_pattern.extend([1, 1])

    builder.adjust(*adjust_pattern)

    try:
        await callback.message.edit_text(
            rendered,
            reply_markup=builder.as_markup(),
            parse_mode="HTML",
        )
    except TelegramBadRequest as e:
        logger.debug(f"_show_purchases_list edit_text failed: {e}")


@router.callback_query(F.data == "admin_purchases")
async def show_purchases(
    callback: CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
):
    if not is_admin(callback.from_user.id):
        await callback.answer(texts.ERROR_ACCESS_DENIED, show_alert=True)
        return

    await state.clear()
    await callback.answer(show_alert=False)
    await _show_purchases_list(callback, session, page=1)


@router.callback_query(F.data.startswith("admin_purchases_page:"))
async def purchases_pagination(
    callback: CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
):
    if not is_admin(callback.from_user.id):
        await callback.answer(texts.ERROR_ACCESS_DENIED, show_alert=True)
        return

    page = parse_callback_id(callback.data, 1) or 1
    await state.clear()
    await callback.answer(show_alert=False)
    await _show_purchases_list(callback, session, page=page)


@router.callback_query(F.data.startswith("admin_purchase_card:"))
async def show_purchase_card(
    callback: CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
):
    if not is_admin(callback.from_user.id):
        await callback.answer(texts.ERROR_ACCESS_DENIED, show_alert=True)
        return

    parts = callback.data.split(":", 1)
    if len(parts) < 2:
        await callback.answer(texts.ADMIN_PURCHASES_RECORD_NOT_FOUND_ALERT, show_alert=True)
        return

    entry_id = parts[1]
    entry = await get_purchase_log_by_id(session, entry_id)
    if not entry:
        await callback.answer(texts.ADMIN_PURCHASES_NOT_FOUND_ALERT, show_alert=True)
        return

    await state.clear()
    await callback.answer(show_alert=False)

    header = format_admin_breadcrumbs(texts.ADMIN_PURCHASES_TAB_TITLE, texts.ADMIN_PURCHASES_DETAILS_LINK.format(entry_numeric_id=entry.numeric_id))
    amount_str = f"{int(entry.amount_rub)} ₽" if entry.amount_rub > 0 else texts.ADMIN_PURCHASES_AMOUNT_ZERO_BONUS_GRANT
    dt_str = format_datetime(entry.created_at)

    rendered = (
        f"{header}"+
        texts.ADMIN_PURCHASES_ENTRY_TITLE.format(entry_numeric_id=entry.numeric_id)+
        texts.ADMIN_PURCHASES_ENTRY_USER_LINE.format(safe_entry_user_label=safe(entry.user_label), entry_telegram_id=entry.telegram_id)+
        texts.ADMIN_PURCHASES_ENTRY_TARIFF_LINE.format(safe_entry_tariff_name=safe(entry.tariff_name))+
        texts.ADMIN_PURCHASES_ENTRY_DEVICE_LIMIT_LINE.format(entry_device_limit=entry.device_limit)+
        texts.ADMIN_PURCHASES_ENTRY_DURATION_LINE.format(entry_duration_days=entry.duration_days)+
        texts.ADMIN_PURCHASES_ENTRY_SUMMARY_LINE.format(amount_str=amount_str)+
        texts.ADMIN_PURCHASES_ENTRY_OPERATION_TYPE_LINE.format(safe_entry_operation_title=safe(entry.operation_title))+
        texts.ADMIN_PURCHASES_ENTRY_DATETIME_LINE.format(dt_str=dt_str)

    )

    builder = InlineKeyboardBuilder()
    if entry.telegram_id:
        builder.button(
            text=texts.ADMIN_PURCHASES_USER_CARD_LINK,
            callback_data=f"admin_user_card:{entry.telegram_id}",
        )
    builder.button(text=texts.ADMIN_PURCHASES_LIST_BUTTON, callback_data="admin_purchases")
    builder.button(text=texts.BTN_ADMIN_MENU, callback_data="admin_menu")
    builder.adjust(1)

    try:
        await callback.message.edit_text(
            rendered,
            reply_markup=builder.as_markup(),
            parse_mode="HTML",
        )
    except TelegramBadRequest as e:
        logger.debug(f"show_purchase_card edit_text failed: {e}")
