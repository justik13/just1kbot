import logging
from datetime import timedelta

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from bot import texts
from bot.constants import (
    PERMANENT_END_DATE,
    PERMANENT_SUBSCRIPTION_DAYS,
)
from bot.keyboards import get_back_button
from bot.keyboards.admin.users import (
    get_admin_confirm_action_keyboard,
    get_admin_extend_days_new_keyboard,
)
from bot.middlewares.user_context import invalidate_user_cache
from bot.states import AdminStates
from database.repositories.users_repo import get_user_by_telegram_id
from services.audit_service import AuditService
from services.subscription import SubscriptionService
from utils.admin import is_admin
from utils.callbacks import (
    parse_callback_id,
    parse_callback_int,
    parse_callback_parts,
)
from utils.datetime_helpers import now_utc
from utils.formatters import format_datetime
from utils.telegram import render_hub

from .common import _validate_positive_int

router = Router()
logger = logging.getLogger(__name__)


@router.callback_query(F.data.startswith("admin_sub_extend:"))
async def admin_sub_extend(
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
            texts.ERROR_INVALID_REQUEST,
            show_alert=True,
        )
        return

    await callback.answer(show_alert=False)

    user = await get_user_by_telegram_id(session, telegram_id)

    if not user or not user.subscription_end:
        await callback.answer(
            texts.ADMIN_SUB_NO_SUBSCRIPTION,
            show_alert=True,
        )
        return

    valid_until = format_datetime(user.subscription_end)

    text = texts.ADMIN_SUB_EXTEND_HEADER.format(
        telegram_id=telegram_id,
        valid_until=valid_until,
    )

    try:
        await callback.message.edit_text(
            text,
            reply_markup=get_admin_extend_days_new_keyboard(
                telegram_id
            ),
            parse_mode="HTML",
        )
    except TelegramBadRequest as e:
        logger.debug(f"admin_sub_extend edit_text failed: {e}")


@router.callback_query(F.data.startswith("admin_sub_confirm_extend:"))
async def admin_sub_confirm_extend(
    callback: CallbackQuery,
    session: AsyncSession,
):
    if not is_admin(callback.from_user.id):
        await callback.answer(
            texts.ERROR_ACCESS_DENIED,
            show_alert=True,
        )
        return

    parts = parse_callback_parts(callback.data, 3)

    if parts is None:
        await callback.answer(
            texts.ERROR_INVALID_REQUEST,
            show_alert=True,
        )
        return

    telegram_id = parse_callback_int(parts, 1)
    days = parse_callback_int(parts, 2)

    if (
        telegram_id is None
        or days is None
        or days < 1
        or days > PERMANENT_SUBSCRIPTION_DAYS
    ):
        await callback.answer(
            texts.ERROR_INVALID_DAYS_COUNT,
            show_alert=True,
        )
        return

    await callback.answer(show_alert=False)

    user = await get_user_by_telegram_id(session, telegram_id)

    if not user:
        await callback.message.edit_text(
            texts.ERROR_USER_NOT_FOUND
        )
        return

    current_time = now_utc()

    current_end = (
        user.subscription_end
        if (
            user.subscription_end
            and user.subscription_end > current_time
        )
        else current_time
    )

    new_end = (
        PERMANENT_END_DATE
        if days >= PERMANENT_SUBSCRIPTION_DAYS
        else current_end + timedelta(days=days)
    )

    days_text = (
        texts.ADMIN_SUB_PERMANENT_LABEL
        if days >= PERMANENT_SUBSCRIPTION_DAYS
        else texts.TIME_DAYS_FORMAT.format(days=days)
    )

    text = texts.ADMIN_SUB_CONFIRM_EXTEND.format(
        telegram_id=telegram_id,
        current_end=format_datetime(current_end),
        days_text=days_text,
        new_end=format_datetime(new_end),
    )

    try:
        await callback.message.edit_text(
            text,
            reply_markup=get_admin_confirm_action_keyboard(
                confirm_callback=(
                    f"admin_sub_apply_extend:"
                    f"{telegram_id}:{days}"
                ),
                cancel_callback=(
                    f"admin_sub_extend:{telegram_id}"
                ),
            ),
            parse_mode="HTML",
        )
    except TelegramBadRequest as e:
        logger.debug(
            f"admin_sub_confirm_extend edit_text failed: {e}"
        )


@router.callback_query(F.data.startswith("admin_sub_apply_extend:"))
async def admin_sub_apply_extend(
    callback: CallbackQuery,
    session: AsyncSession,
):
    if not is_admin(callback.from_user.id):
        await callback.answer(
            texts.ERROR_ACCESS_DENIED,
            show_alert=True,
        )
        return

    parts = parse_callback_parts(callback.data, 3)

    if parts is None:
        await callback.answer(
            texts.ERROR_INVALID_REQUEST,
            show_alert=True,
        )
        return

    telegram_id = parse_callback_int(parts, 1)
    days = parse_callback_int(parts, 2)

    if (
        telegram_id is None
        or days is None
        or days < 1
        or days > PERMANENT_SUBSCRIPTION_DAYS
    ):
        await callback.answer(
            texts.ERROR_INVALID_DAYS_COUNT,
            show_alert=True,
        )
        return

    await callback.answer(show_alert=False)

    try:
        user = await get_user_by_telegram_id(
            session,
            telegram_id,
        )

        if not user:
            await callback.message.edit_text(
                texts.ERROR_USER_NOT_FOUND
            )
            return

        await SubscriptionService.extend_subscription(
            session,
            telegram_id,
            days,
            new_device_limit=None,
            new_tariff_id=None,
        )

        invalidate_user_cache(telegram_id)

        user = await get_user_by_telegram_id(
            session,
            telegram_id,
        )

        days_text = (
            texts.ADMIN_SUB_PERMANENT_LABEL
            if days >= PERMANENT_SUBSCRIPTION_DAYS
            else texts.TIME_DAYS_FORMAT.format(days=days)
        )

        await AuditService.log_action(
            session,
            admin_id=callback.from_user.id,
            action="ADMIN_SUB_EXTEND",
            target_type="user",
            target_id=user.id,
            details={"days": days_text},
        )

        new_end_str = (
            format_datetime(user.subscription_end)
            if user and user.subscription_end
            else texts.PLACEHOLDER_DASH
        )

        text = texts.ADMIN_SUB_EXTEND_SUCCESS.format(
            telegram_id=telegram_id,
            days_text=days_text,
            new_end=new_end_str,
        )

        try:
            await callback.message.edit_text(
                text,
                reply_markup=get_back_button(
                    f"admin_user_card:{telegram_id}"
                ),
                parse_mode="HTML",
            )
        except TelegramBadRequest as e:
            logger.debug(
                f"admin_sub_apply_extend edit_text failed: {e}"
            )

    except Exception as e:
        logger.error(
            f"admin_sub_apply_extend error: {e}",
            exc_info=True,
        )

        await session.rollback()

        await callback.answer(
            texts.ADMIN_SUB_EXTEND_FAILED,
            show_alert=True,
        )


@router.callback_query(F.data.startswith("admin_sub_extend_custom:"))
async def admin_sub_extend_custom_start(
    callback: CallbackQuery,
    state: FSMContext,
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
            texts.ERROR_INVALID_REQUEST,
            show_alert=True,
        )
        return

    await callback.answer(show_alert=False)
    await state.clear()

    await state.set_state(AdminStates.admin_extending_custom)
    await state.update_data(admin_telegram_id=telegram_id)

    text = texts.ADMIN_SUB_EXTEND_PROMPT.format(
        telegram_id=telegram_id
    )

    try:
        await callback.message.edit_text(
            text,
            reply_markup=get_back_button(
                f"admin_sub_extend:{telegram_id}"
            ),
            parse_mode="HTML",
        )
    except TelegramBadRequest as e:
        logger.debug(
            "admin_sub_extend_custom_start "
            f"edit_text failed: {e}"
        )


@router.message(AdminStates.admin_extending_custom)
async def admin_sub_extend_custom_process(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
):
    if not is_admin(message.from_user.id):
        return

    data = await state.get_data()

    telegram_id = data.get("admin_telegram_id")

    if not telegram_id:
        await state.clear()
        return

    days = _validate_positive_int(message.text)

    if days is None:
        await render_hub(
            message.bot,
            message.chat.id,
            texts.ERROR_DAYS_OVERFLOW,
            get_back_button(f"admin_sub_extend:{telegram_id}"),
            parse_mode="HTML",
        )
        return

    await state.clear()

    user = await get_user_by_telegram_id(session, telegram_id)

    if not user:
        await render_hub(
            message.bot,
            message.chat.id,
            texts.ERROR_USER_NOT_FOUND,
            get_back_button(f"admin_sub_extend:{telegram_id}"),
        )
        return

    current_time = now_utc()

    current_end = (
        user.subscription_end
        if (
            user.subscription_end
            and user.subscription_end > current_time
        )
        else current_time
    )

    new_end = (
        PERMANENT_END_DATE
        if days >= PERMANENT_SUBSCRIPTION_DAYS
        else current_end + timedelta(days=days)
    )

    days_text = (
        texts.ADMIN_SUB_PERMANENT_LABEL
        if days >= PERMANENT_SUBSCRIPTION_DAYS
        else texts.TIME_DAYS_FORMAT.format(days=days)
    )

    confirm_text = texts.ADMIN_SUB_CONFIRM_EXTEND.format(
        telegram_id=telegram_id,
        current_end=format_datetime(current_end),
        days_text=days_text,
        new_end=format_datetime(new_end),
    )

    await render_hub(
        message.bot,
        message.chat.id,
        confirm_text,
        get_admin_confirm_action_keyboard(
            confirm_callback=(
                f"admin_sub_apply_extend:{telegram_id}:{days}"
            ),
            cancel_callback=(
                f"admin_sub_extend:{telegram_id}"
            ),
        ),
    )