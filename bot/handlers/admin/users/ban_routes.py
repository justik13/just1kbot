import logging

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery
from sqlalchemy.ext.asyncio import AsyncSession

from bot import texts
from bot.keyboards.admin.users import get_admin_confirm_action_keyboard
from config.settings import get_settings
from services.ban_service import BanService
from utils.admin import is_admin
from utils.callbacks import parse_callback_id

from .common import (
    _get_user_with_profiles,
    _render_user_card,
)

router = Router()
logger = logging.getLogger(__name__)


@router.callback_query(F.data.startswith("admin_ban:"))
async def admin_ban_confirm(
    callback: CallbackQuery,
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
            texts.UI_BOT_HANDLERS_ADMIN_USERS_BAN_ROUTES_L40_1,
            show_alert=True,
        )
        return

    await callback.answer(show_alert=False)

    settings = get_settings()

    if telegram_id in settings.ADMIN_IDS:
        await callback.answer(
            texts.ERROR_ADMIN_BAN_FORBIDDEN,
            show_alert=True,
        )
        return

    text = texts.ADMIN_BAN_CONFIRM.format(telegram_id=telegram_id)

    try:
        await callback.message.edit_text(
            text,
            reply_markup=get_admin_confirm_action_keyboard(
                confirm_callback=f"admin_ban_apply:{telegram_id}",
                cancel_callback=f"admin_user_card:{telegram_id}",
            ),
            parse_mode="HTML",
        )
    except TelegramBadRequest as e:
        logger.debug(f"admin_ban_confirm edit_text failed: {e}")


@router.callback_query(F.data.startswith("admin_ban_apply:"))
async def admin_ban_apply(
    callback: CallbackQuery,
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
            texts.UI_BOT_HANDLERS_ADMIN_USERS_BAN_ROUTES_L87_1,
            show_alert=True,
        )
        return

    await callback.answer(show_alert=False)

    settings = get_settings()

    if telegram_id in settings.ADMIN_IDS:
        await callback.answer(
            texts.ERROR_ADMIN_BAN_FORBIDDEN,
            show_alert=True,
        )
        return

    success, message = await BanService.ban_user(
        session,
        callback.from_user.id,
        telegram_id,
    )

    if not success:
        await callback.answer(
            texts.ADMIN_BAN_FAILED.format(message=message),
            show_alert=True,
        )
        return

    await callback.answer(
        texts.ADMIN_BAN_SUCCESS.format(message=message),
        show_alert=True,
    )

    user = await _get_user_with_profiles(session, telegram_id)

    if user:
        await _render_user_card(callback, user, session)


@router.callback_query(F.data.startswith("admin_unban:"))
async def admin_unban_confirm(
    callback: CallbackQuery,
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
            texts.UI_BOT_HANDLERS_ADMIN_USERS_BAN_ROUTES_L143_1,
            show_alert=True,
        )
        return

    await callback.answer(show_alert=False)

    text = texts.ADMIN_UNBAN_CONFIRM.format(telegram_id=telegram_id)

    try:
        await callback.message.edit_text(
            text,
            reply_markup=get_admin_confirm_action_keyboard(
                confirm_callback=f"admin_unban_apply:{telegram_id}",
                cancel_callback=f"admin_user_card:{telegram_id}",
            ),
            parse_mode="HTML",
        )
    except TelegramBadRequest as e:
        logger.debug(f"admin_unban_confirm edit_text failed: {e}")


@router.callback_query(F.data.startswith("admin_unban_apply:"))
async def admin_unban_apply(
    callback: CallbackQuery,
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
            texts.UI_BOT_HANDLERS_ADMIN_USERS_BAN_ROUTES_L181_1,
            show_alert=True,
        )
        return

    await callback.answer(show_alert=False)

    success, message = await BanService.unban_user(
        session,
        callback.from_user.id,
        telegram_id,
    )

    if not success:
        await callback.answer(
            texts.ADMIN_BAN_FAILED.format(message=message),
            show_alert=True,
        )
        return

    await callback.answer(
        texts.ADMIN_BAN_SUCCESS.format(message=message),
        show_alert=True,
    )

    user = await _get_user_with_profiles(session, telegram_id)

    if user:
        await _render_user_card(callback, user, session)
