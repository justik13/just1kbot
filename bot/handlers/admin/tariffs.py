import logging
import math

from aiogram import Router, F
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from bot import texts
from bot.keyboards import get_back_button
from bot.keyboards.admin.users import (
    get_admin_confirm_action_keyboard,
)
from bot.states import AdminStates
from database.models import Payment
from database.repositories.tariffs_repo import (
    get_tariff_by_id,
    get_tariff_count,
    get_tariffs_paginated,
    update_tariff,
)
from services.audit_service import AuditService
from utils.admin import is_admin
from utils.callbacks import parse_callback_id
from utils.telegram import render_hub, safe
from utils.text_limits import truncate_button_text

router = Router()
logger = logging.getLogger(__name__)

TARIFFS_PER_PAGE = 10
MAX_TARIFF_PRICE = 1_000_000


async def _build_tariffs_list_text_and_kb(
    tariffs,
    page: int,
    total_pages: int,
    total: int,
) -> tuple[str, InlineKeyboardBuilder]:
    rendered = (
        texts.RUNTIME_BOT_HANDLERS_ADMIN_TARIFFS_L45_1.format(value_0=page, value_1=total_pages, value_2=total)
    )

    builder = InlineKeyboardBuilder()

    if not tariffs:
        rendered += texts.RUNTIME_BOT_HANDLERS_ADMIN_TARIFFS_L52_1
    else:
        for tariff in tariffs:
            status = texts.RUNTIME_BOT_HANDLERS_ADMIN_TARIFFS_L55_1 if tariff.is_active else texts.STATUS_INACTIVE_ICON
            button_text = truncate_button_text(
                texts.RUNTIME_BOT_HANDLERS_ADMIN_TARIFFS_L57_1.format(value_0=status, value_1=tariff.name, value_2=tariff.duration_days, value_3=tariff.price_rub)
            )
            builder.button(
                text=button_text,
                callback_data=f"admin_tariff_card:{tariff.id}",
            )

    if page > 1:
        builder.button(
            text=texts.UI_BOT_HANDLERS_ADMIN_TARIFFS_L68_1,
            callback_data=f"admin_tariffs_page:{page - 1}",
        )
    if page < total_pages:
        builder.button(
            text=texts.UI_BOT_HANDLERS_ADMIN_TARIFFS_L73_1,
            callback_data=f"admin_tariffs_page:{page + 1}",
        )

    builder.button(
        text=texts.UI_BOT_HANDLERS_ADMIN_TARIFFS_L78_1,
        callback_data="admin_menu",
    )
    builder.adjust(1)

    return rendered, builder


async def _show_tariffs_list(
    callback: CallbackQuery,
    session: AsyncSession,
    page: int = 1,
):
    total_tariffs = await get_tariff_count(session)
    total_pages = max(
        1,
        math.ceil(total_tariffs / TARIFFS_PER_PAGE),
    )

    if page > total_pages:
        page = total_pages

    tariffs = await get_tariffs_paginated(
        session,
        page=page,
        per_page=TARIFFS_PER_PAGE,
    )

    rendered, kb = await _build_tariffs_list_text_and_kb(
        tariffs,
        page,
        total_pages,
        total_tariffs,
    )

    try:
        await callback.message.edit_text(
            rendered,
            reply_markup=kb.as_markup(),
            parse_mode="HTML",
        )
    except TelegramBadRequest as e:
        logger.debug(
            f"_show_tariffs_list edit_text failed: {e}"
        )


async def _get_pending_payments_count_for_tariff(
    session: AsyncSession,
    tariff_id: int,
) -> int:
    return 0



@router.callback_query(F.data == "admin_tariffs")
async def show_tariffs_list(
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
    await _show_tariffs_list(callback, session, page=1)
    await callback.answer(show_alert=False)


@router.callback_query(F.data.startswith("admin_tariffs_page:"))
async def tariffs_pagination(
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
            texts.UI_BOT_HANDLERS_ADMIN_TARIFFS_L176_1,
            show_alert=True,
        )
        return

    await state.clear()
    await _show_tariffs_list(callback, session, page=page)
    await callback.answer(show_alert=False)


async def _show_tariff_card(
    callback: CallbackQuery,
    tariff,
):
    status = (
        texts.RUNTIME_BOT_HANDLERS_ADMIN_TARIFFS_L191_1
        if tariff.is_active
        else texts.RUNTIME_BOT_HANDLERS_ADMIN_TARIFFS_L193_1
    )

    rendered = (
        texts.RUNTIME_BOT_HANDLERS_ADMIN_TARIFFS_L197_1.format(value_0=tariff.id, value_1=safe(tariff.name), value_2=safe(tariff.description or texts.PLACEHOLDER_DASH), value_3=tariff.duration_days, value_4=tariff.device_limit, value_5=tariff.price_rub, value_6=status)
    )

    builder = InlineKeyboardBuilder()
    builder.button(
        text=texts.UI_BOT_HANDLERS_ADMIN_TARIFFS_L209_1,
        callback_data=f"admin_tariff_edit_rub:{tariff.id}",
    )

    if tariff.is_active:
        builder.button(
            text=texts.UI_BOT_HANDLERS_ADMIN_TARIFFS_L215_1,
            callback_data=f"admin_tariff_toggle:{tariff.id}",
        )
    else:
        builder.button(
            text=texts.UI_BOT_HANDLERS_ADMIN_TARIFFS_L220_1,
            callback_data=f"admin_tariff_toggle:{tariff.id}",
        )

    builder.button(
        text=texts.UI_BOT_HANDLERS_ADMIN_TARIFFS_L225_1,
        callback_data="admin_tariffs",
    )
    builder.adjust(1)

    try:
        await callback.message.edit_text(
            rendered,
            reply_markup=builder.as_markup(),
            parse_mode="HTML",
        )
    except TelegramBadRequest as e:
        logger.debug(
            f"_show_tariff_card edit_text failed: {e}"
        )


@router.callback_query(F.data.startswith("admin_tariff_card:"))
async def show_tariff_card(
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

    tariff_id = parse_callback_id(callback.data, 1)
    if tariff_id is None:
        await callback.answer(
            texts.UI_BOT_HANDLERS_ADMIN_TARIFFS_L258_1,
            show_alert=True,
        )
        return

    await state.clear()

    tariff = await get_tariff_by_id(session, tariff_id)
    if not tariff:
        await callback.answer(
            texts.ERROR_TARIFF_NOT_FOUND,
            show_alert=True,
        )
        return

    await _show_tariff_card(callback, tariff)
    await callback.answer(show_alert=False)


@router.callback_query(F.data.startswith("admin_tariff_toggle:"))
async def toggle_tariff_confirm(
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

    tariff_id = parse_callback_id(callback.data, 1)
    if tariff_id is None:
        await callback.answer(
            texts.UI_BOT_HANDLERS_ADMIN_TARIFFS_L293_1,
            show_alert=True,
        )
        return

    await state.clear()

    tariff = await get_tariff_by_id(session, tariff_id)
    if not tariff:
        await callback.answer(
            texts.ERROR_TARIFF_NOT_FOUND,
            show_alert=True,
        )
        return

    new_status = not tariff.is_active

    if new_status:
        text = (
            texts.RUNTIME_BOT_HANDLERS_ADMIN_TARIFFS_L312_1.format(value_0=safe(tariff.name), value_1=tariff.duration_days, value_2=tariff.device_limit)
        )
    else:
        text = (
            texts.RUNTIME_BOT_HANDLERS_ADMIN_TARIFFS_L323_1.format(value_0=safe(tariff.name), value_1=tariff.duration_days, value_2=tariff.device_limit)
        )

    try:
        await callback.message.edit_text(
            text,
            reply_markup=get_admin_confirm_action_keyboard(
                confirm_callback=(
                    f"admin_tariff_toggle_apply:{tariff_id}"
                ),
                cancel_callback=(
                    f"admin_tariff_card:{tariff_id}"
                ),
            ),
            parse_mode="HTML",
        )
    except TelegramBadRequest as e:
        logger.debug(
            f"toggle_tariff_confirm edit_text failed: {e}"
        )

    await callback.answer(show_alert=False)


@router.callback_query(
    F.data.startswith("admin_tariff_toggle_apply:")
)
async def toggle_tariff_apply(
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

    tariff_id = parse_callback_id(callback.data, 1)
    if tariff_id is None:
        await callback.answer(
            texts.UI_BOT_HANDLERS_ADMIN_TARIFFS_L372_1,
            show_alert=True,
        )
        return

    await state.clear()

    tariff = await get_tariff_by_id(session, tariff_id)
    if not tariff:
        await callback.answer(
            texts.ERROR_TARIFF_NOT_FOUND,
            show_alert=True,
        )
        return

    new_status = not tariff.is_active

    if not new_status:
        pending_count = (
            await _get_pending_payments_count_for_tariff(
                session,
                tariff_id,
            )
        )
        if pending_count > 0:
            await callback.answer(
                texts.ADMIN_TARIFF_TOGGLE_BLOCKED_PENDING,
                show_alert=True,
            )
            return

    await update_tariff(
        session,
        tariff,
        is_active=new_status,
    )

    await AuditService.log_action(
        session,
        callback.from_user.id,
        "EDIT_TARIFF",
        "Tariff",
        tariff_id,
        f"toggled to {'active' if new_status else 'inactive'}",
    )

    if new_status:
        await callback.answer(
            texts.ADMIN_TARIFF_TOGGLE_SUCCESS_ENABLED,
            show_alert=True,
        )
    else:
        await callback.answer(
            texts.ADMIN_TARIFF_TOGGLE_SUCCESS_DISABLED,
            show_alert=True,
        )

    refreshed = await get_tariff_by_id(session, tariff_id)
    await _show_tariff_card(callback, refreshed)


@router.callback_query(
    F.data.startswith("admin_tariff_edit_rub:")
)
async def start_edit_tariff_rub(
    callback: CallbackQuery,
    state: FSMContext,
):
    if not is_admin(callback.from_user.id):
        await callback.answer(
            texts.ERROR_ACCESS_DENIED,
            show_alert=True,
        )
        return

    tariff_id = parse_callback_id(callback.data, 1)
    if tariff_id is None:
        await callback.answer(
            texts.UI_BOT_HANDLERS_ADMIN_TARIFFS_L450_1,
            show_alert=True,
        )
        return

    await state.clear()
    await state.update_data(tariff_id=tariff_id)
    await state.set_state(AdminStates.editing_tariff_rub)

    try:
        await callback.message.edit_text(
            texts.ADMIN_TARIFF_EDIT_RUB_PROMPT,
            reply_markup=get_back_button("admin_tariffs"),
        )
    except TelegramBadRequest as e:
        logger.debug(
            f"start_edit_tariff_rub edit_text failed: {e}"
        )

    await callback.answer(show_alert=False)


@router.message(AdminStates.editing_tariff_rub)
async def process_edit_tariff_rub(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
):
    if not is_admin(message.from_user.id):
        await state.clear()
        return

    if not message.text:
        await render_hub(
            message.bot,
            message.chat.id,
            texts.ERROR_POSITIVE_NUMBER,
            get_back_button("admin_tariffs"),
        )
        return

    if message.text.startswith("/"):
        await state.clear()
        await render_hub(
            message.bot,
            message.chat.id,
            texts.ERROR_OPERATION_CANCELLED,
            get_back_button("admin_tariffs"),
        )
        return

    try:
        new_value = int(message.text.strip())
    except ValueError:
        await render_hub(
            message.bot,
            message.chat.id,
            texts.ERROR_POSITIVE_NUMBER,
            get_back_button("admin_tariffs"),
        )
        return

    if new_value <= 0 or new_value > MAX_TARIFF_PRICE:
        await render_hub(
            message.bot,
            message.chat.id,
            texts.UI_BOT_HANDLERS_ADMIN_TARIFFS_L516_1.format(value_0=MAX_TARIFF_PRICE),
            get_back_button("admin_tariffs"),
        )
        return

    data = await state.get_data()
    tariff_id = data["tariff_id"]

    tariff = await get_tariff_by_id(session, tariff_id)
    if not tariff:
        await render_hub(
            message.bot,
            message.chat.id,
            texts.ERROR_TARIFF_NOT_FOUND,
            get_back_button("admin_tariffs"),
        )
        await state.clear()
        return

    old_value = tariff.price_rub

    await update_tariff(
        session,
        tariff,
        price_rub=new_value,
    )

    await AuditService.log_action(
        session,
        message.from_user.id,
        "EDIT_TARIFF",
        "Tariff",
        tariff_id,
        f"RUB: {old_value} -> {new_value}",
    )

    await render_hub(
        message.bot,
        message.chat.id,
        texts.ADMIN_TARIFF_EDIT_RUB_SUCCESS.format(
            value=new_value
        ),
        get_back_button("admin_tariffs"),
    )

    logger.info(
        f"Admin {message.from_user.id} updated tariff "
        f"{tariff_id} price_rub: {old_value} -> {new_value}"
    )

    await state.clear()