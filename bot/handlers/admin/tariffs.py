import logging
import math

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy.ext.asyncio import AsyncSession

from bot import texts
from bot.constants import AdminAuditAction
from bot.keyboards import get_back_button
from bot.keyboards.admin.users import (
    get_admin_confirm_action_keyboard,
)
from bot.states import AdminStates
from database.repositories.payments_repo import (
    get_pending_payments_count_for_tariff as _get_pending_payments_count_for_tariff,
)
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
        texts.ADMIN_TARIFF_LIST_TITLE.format(page=page, total_pages=total_pages, total=total)
    )

    builder = InlineKeyboardBuilder()

    if not tariffs:
        rendered += texts.ADMIN_TARIFF_LIST_EMPTY
    else:
        for tariff in tariffs:
            status = texts.STATUS_ACTIVE_ICON if tariff.is_active else texts.STATUS_INACTIVE_ICON
            button_text = truncate_button_text(
                texts.ADMIN_TARIFF_ROW_FORMAT.format(status=status, name=tariff.name, duration_days=tariff.duration_days, price_rub=tariff.price_rub)
            )
            builder.button(
                text=button_text,
                callback_data=f"admin_tariff_card:{tariff.id}",
            )

    if page > 1:
        builder.button(
            text=texts.ADMIN_BTN_PAGINATION_PREV,
            callback_data=f"admin_tariffs_page:{page - 1}",
        )
    if page < total_pages:
        builder.button(
            text=texts.ADMIN_BTN_PAGINATION_NEXT,
            callback_data=f"admin_tariffs_page:{page + 1}",
        )

    builder.button(
        text=texts.ADMIN_BTN_BACK_TO_ADMIN,
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

    page = min(page, total_pages)

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
            texts.ERROR_INVALID_REQUEST,
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
        texts.STATUS_ACTIVE_BADGE
        if tariff.is_active
        else texts.ADMIN_TARIFF_STATUS_DISABLED_BADGE
    )

    rendered = (
        texts.ADMIN_TARIFF_CARD_TEMPLATE.format(
            tariff_id=tariff.id,
            name=safe(tariff.name),
            description=safe(tariff.description or texts.PLACEHOLDER_DASH),
            duration_days=tariff.duration_days,
            device_limit=tariff.device_limit,
            price_rub=tariff.price_rub,
            status=status,
        )
    )

    builder = InlineKeyboardBuilder()
    builder.button(
        text=texts.ADMIN_TARIFF_BTN_EDIT_PRICE,
        callback_data=f"admin_tariff_edit_rub:{tariff.id}",
    )

    if tariff.is_active:
        builder.button(
            text=texts.BTN_DISABLE_SERVER,
            callback_data=f"admin_tariff_toggle:{tariff.id}",
        )
    else:
        builder.button(
            text=texts.BTN_ENABLE_SERVER_CARD,
            callback_data=f"admin_tariff_toggle:{tariff.id}",
        )

    builder.button(
        text=texts.ADMIN_TARIFF_BTN_BACK_TO_LIST,
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
            texts.ERROR_INVALID_REQUEST,
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
            texts.ERROR_INVALID_REQUEST,
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
            texts.ADMIN_TARIFF_ENABLE_CONFIRM.format(name=safe(tariff.name), duration_days=tariff.duration_days, device_limit=tariff.device_limit)
        )
    else:
        text = (
            texts.ADMIN_TARIFF_DISABLE_CONFIRM.format(name=safe(tariff.name), duration_days=tariff.duration_days, device_limit=tariff.device_limit)
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
            texts.ERROR_INVALID_REQUEST,
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
        AdminAuditAction.EDIT_TARIFF,
        "Tariff",
        tariff_id,
        texts.ADMIN_AUDIT_LOG_DETAILS_TARIFF_TOGGLED.format(status='active' if new_status else 'inactive'),
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
            texts.ERROR_INVALID_REQUEST,
            show_alert=True,
        )
        return

    await state.clear()
    await state.update_data(tariff_id=tariff_id)
    await state.set_state(AdminStates.editing_tariff_rub)

    try:
        await callback.message.edit_text(
            texts.ADMIN_TARIFF_EDIT_PRICE_PROMPT,
            reply_markup=get_back_button(f"admin_tariff_card:{tariff_id}"),
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
    try:
        await message.delete()
    except Exception:
        pass

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
            texts.ADMIN_TARIFF_ERR_PRICE_RANGE.format(max_price=MAX_TARIFF_PRICE),
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
        AdminAuditAction.EDIT_TARIFF,
        "Tariff",
        tariff_id,
        texts.ADMIN_AUDIT_LOG_DETAILS_TARIFF_EDIT_RUB.format(old_value=old_value, new_value=new_value),
    )

    await render_hub(
        message.bot,
        message.chat.id,
        texts.ADMIN_TARIFF_EDIT_PRICE_SUCCESS.format(
            price_rub=new_value
        ),
        get_back_button("admin_tariffs"),
    )

    logger.info(
        f"Admin {message.from_user.id} updated tariff "
        f"{tariff_id} price_rub: {old_value} -> {new_value}"
    )

    await state.clear()