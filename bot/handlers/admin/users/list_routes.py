import logging
import math

from aiogram import Router, F
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from bot import texts
from bot.keyboards import get_back_button
from bot.states import AdminStates
from database.repositories.users_repo import (
    get_user_by_telegram_id,
    get_user_count,
    get_users_paginated_with_profiles,
)
from utils.admin import is_admin
from utils.callbacks import parse_callback_id
from utils.telegram import render_hub

from .common import (
    USERS_PER_PAGE,
    _build_users_list_text_and_kb,
    _get_user_with_profiles,
    _render_user_card,
    _show_user_card_edit,
)

router = Router()
logger = logging.getLogger(__name__)


@router.callback_query(F.data == "admin_users")
async def show_users_list(
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

    await callback.answer(show_alert=False)
    await state.clear()

    total_users = await get_user_count(session)

    total_pages = max(
        1,
        math.ceil(total_users / USERS_PER_PAGE),
    )

    users = await get_users_paginated_with_profiles(
        session,
        page=1,
        per_page=USERS_PER_PAGE,
    )

    rendered, kb = await _build_users_list_text_and_kb(
        users,
        1,
        total_pages,
        total_users,
    )

    try:
        await callback.message.edit_text(
            rendered,
            reply_markup=kb.as_markup(),
            parse_mode="HTML",
        )
    except TelegramBadRequest as e:
        logger.debug(f"show_users_list edit_text failed: {e}")


@router.callback_query(F.data.startswith("admin_users_page:"))
async def users_pagination(
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
            texts.UI_BOT_HANDLERS_ADMIN_USERS_LIST_ROUTES_L97_1,
            show_alert=True,
        )
        return

    await callback.answer(show_alert=False)
    await state.clear()

    total_users = await get_user_count(session)

    total_pages = max(
        1,
        math.ceil(total_users / USERS_PER_PAGE),
    )

    if page > total_pages:
        page = total_pages

    users = await get_users_paginated_with_profiles(
        session,
        page=page,
        per_page=USERS_PER_PAGE,
    )

    rendered, kb = await _build_users_list_text_and_kb(
        users,
        page,
        total_pages,
        total_users,
    )

    try:
        await callback.message.edit_text(
            rendered,
            reply_markup=kb.as_markup(),
            parse_mode="HTML",
        )
    except TelegramBadRequest as e:
        logger.debug(f"users_pagination edit_text failed: {e}")


@router.callback_query(F.data == "admin_users_search")
async def start_search_user(
    callback: CallbackQuery,
    state: FSMContext,
):
    if not is_admin(callback.from_user.id):
        await callback.answer(
            texts.ERROR_ACCESS_DENIED,
            show_alert=True,
        )
        return

    await callback.answer(show_alert=False)
    await state.clear()

    try:
        await callback.message.edit_text(
            texts.ADMIN_USER_SEARCH_PROMPT,
            reply_markup=get_back_button("admin_users"),
        )
    except TelegramBadRequest as e:
        logger.debug(f"start_search_user edit_text failed: {e}")

    await state.set_state(AdminStates.searching_user)


@router.message(AdminStates.searching_user)
async def process_search_user(
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
            texts.ERROR_NUMERIC_ID,
            get_back_button("admin_users"),
        )
        return

    if message.text.startswith("/"):
        await state.clear()
        return

    try:
        telegram_id = int(message.text.strip())
    except ValueError:
        await render_hub(
            message.bot,
            message.chat.id,
            texts.ERROR_NUMERIC_ID,
            get_back_button("admin_users"),
        )
        return

    user = await get_user_by_telegram_id(session, telegram_id)

    if not user:
        await render_hub(
            message.bot,
            message.chat.id,
            texts.UI_BOT_HANDLERS_ADMIN_USERS_LIST_ROUTES_L204_1.format(value_0=telegram_id),
            get_back_button("admin_users"),
        )

        await state.clear()

        return

    await _show_user_card_edit(message, user, session)

    await state.clear()


@router.callback_query(F.data.startswith("admin_user_card:"))
async def show_user_card(
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

    telegram_id = parse_callback_id(callback.data, 1)

    if telegram_id is None:
        await callback.answer(
            texts.UI_BOT_HANDLERS_ADMIN_USERS_LIST_ROUTES_L234_1,
            show_alert=True,
        )
        return

    await callback.answer(show_alert=False)
    await state.clear()

    user = await _get_user_with_profiles(session, telegram_id)

    if not user:
        await callback.answer(
            texts.ERROR_USER_NOT_FOUND,
            show_alert=True,
        )
        return

    await _render_user_card(callback, user, session)