import logging
import re

from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardMarkup,
    Message,
    ReplyKeyboardRemove,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy.ext.asyncio import AsyncSession

from bot import texts
from bot.keyboards.common import get_hub_keyboard
from bot.middlewares.user_context import invalidate_user_cache
from config.settings import get_settings
from database.models import User
from database.repositories.account_ledger_repo import get_account_balance
from database.repositories.users_repo import (
    get_user_by_telegram_id,
    update_user,
)
from services.subscription import SubscriptionService
from utils.telegram import render_hub, safe

router = Router()
logger = logging.getLogger(__name__)


def parse_referral_id(command_args: str) -> int | None:
    if not command_args:
        return None

    match = re.match(r"^(?:ref_)?(\d{1,19})$", command_args.strip())
    if not match:
        return None

    try:
        val = int(match.group(1))
        if 0 < val <= 9_223_372_036_854_775_807:
            return val
    except (ValueError, OverflowError):
        pass

    return None


async def _update_user_profile_if_changed(
    session: AsyncSession,
    user: User,
    message: Message,
) -> User:
    updates = {}

    new_username = message.from_user.username
    new_first_name = message.from_user.first_name

    if new_username is not None and user.username != new_username:
        updates["username"] = new_username

    if new_first_name is not None and user.first_name != new_first_name:
        updates["first_name"] = new_first_name

    if not updates:
        return user

    updated_user = await update_user(session, user, **updates)

    invalidate_user_cache(user.telegram_id)

    logger.info(
        "User %s profile updated on /start: %s",
        user.telegram_id,
        ", ".join(updates.keys()),
    )

    return updated_user


async def _ensure_bot_unblocked(
    session: AsyncSession,
    telegram_id: int,
) -> None:
    """
    Если пользователь разблокировал бота и нажал /start или вернулся в меню,
    снимаем флаг is_bot_blocked, чтобы рассылки и уведомления снова работали.
    """
    user = await get_user_by_telegram_id(session, telegram_id)

    if not user:
        return

    if not user.is_bot_blocked:
        return

    await update_user(session, user, is_bot_blocked=False)
    invalidate_user_cache(telegram_id)

    logger.info(
        "User %s unblocked bot flag reset on user action",
        telegram_id,
    )


async def _build_hub_text_and_kb(session: AsyncSession, db_user: User) -> tuple[str, InlineKeyboardMarkup]:
    from database.repositories.profiles_repo import get_user_profiles
    from utils.formatters import format_datetime
    from bot.formatters import format_days_left

    is_active = await SubscriptionService.check_access(session, db_user.telegram_id)
    is_admin = db_user.telegram_id in get_settings().ADMIN_IDS
    name = safe(db_user.first_name or texts.USER_HUB_START)
    balance = await get_account_balance(session, user_id=db_user.id)
    profiles = await get_user_profiles(session, db_user.id)

    status_str = texts.STATUS_SUBSCRIPTION_ACTIVE if is_active else texts.STATUS_SUBSCRIPTION_INACTIVE
    valid_until_str = format_datetime(db_user.subscription_end) if db_user.subscription_end else texts.PLACEHOLDER_DASH
    days_left_str = format_days_left(db_user.subscription_end) if db_user.subscription_end else texts.ZERO_DAYS_LABEL

    inviter_line = ""
    if db_user.referred_by:
        referrer = await get_user_by_telegram_id(session, db_user.referred_by)
        if referrer:
            ref_name = safe(referrer.first_name) if referrer.first_name else ""
            ref_username = f" (@{safe(referrer.username)})" if referrer.username else ""
            if ref_name or ref_username:
                inviter_line = texts.INVITED_BY_NAMED_LINE.format(
                    name=f"{ref_name}{ref_username}",
                    referrer_id=referrer.telegram_id,
                )
            else:
                inviter_line = texts.INVITED_BY_ID_LINE.format(referrer_id=referrer.telegram_id)
        else:
            inviter_line = texts.INVITED_BY_ID_LINE.format(referrer_id=db_user.referred_by)

    bonus_line = (
        texts.HUB_BONUS_LINE_FORMAT.format(bonus_balance=int(balance.bonus_available))
        if balance.bonus_available > 0
        else ""
    )

    text = texts.HUB_HEADER.format(
        name=name,
        telegram_id=db_user.telegram_id,
        status=status_str,
        valid_until=valid_until_str,
        days_left=days_left_str,
        devices_count=len(profiles),
        device_limit=db_user.device_limit or 0,
        real_balance=int(balance.real_available),
        bonus_line=bonus_line,
        inviter_line=inviter_line,
    )

    from database.repositories.system_settings_repo import get_system_setting
    mtproto_url = await get_system_setting(session, "mtproto_proxy_url")

    kb = get_hub_keyboard(
        is_admin=is_admin,
        is_active=is_active,
        mtproto_url=mtproto_url,
    )

    return text, kb


@router.message(CommandStart())
async def cmd_start(
    message: Message,
    state: FSMContext,
    command: Command,
    session: AsyncSession,
):
    await state.clear()

    try:
        await message.delete()
    except Exception:
        pass

    # Clear legacy ReplyKeyboard if present from previous bot versions
    try:
        tmp_msg = await message.answer("🧹", reply_markup=ReplyKeyboardRemove())
        await message.bot.delete_message(message.chat.id, tmp_msg.message_id)
    except Exception:
        pass

    telegram_id = message.from_user.id
    existing_user = await get_user_by_telegram_id(session, telegram_id)
    is_new = existing_user is None or getattr(existing_user, "is_deleted", False)

    ref_id = parse_referral_id(command.args) if command.args else None

    user = await SubscriptionService.process_onboarding(
        session,
        telegram_id,
        message.from_user.username,
        message.from_user.first_name,
        ref_id,
    )

    if user is None:
        logger.error(
            "cmd_start: user is still None after onboarding "
            "for telegram_id=%s",
            telegram_id,
        )

        await message.answer(texts.ERROR_TECHNICAL_MESSAGE)

        return

    user = await _update_user_profile_if_changed(
        session,
        user,
        message,
    )

    await _ensure_bot_unblocked(session, telegram_id)

    if is_new:
        builder = InlineKeyboardBuilder()
        builder.button(text=texts.BTN_MAIN_MENU, callback_data="back_to_main_menu")

        await render_hub(
            message.bot,
            message.chat.id,
            texts.WELCOME_TEXT,
            builder.as_markup(),
            force_new=True,
        )
    else:
        text, kb = await _build_hub_text_and_kb(session, user)
        await render_hub(
            message.bot,
            message.chat.id,
            text,
            kb,
            force_new=True,
        )


@router.message(F.text & F.text.in_(texts.LEGACY_REPLY_BUTTON_TRIGGER_TEXTS))
async def handle_legacy_reply_keyboard(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
):
    await state.clear()
    try:
        tmp_msg = await message.answer("🧹", reply_markup=ReplyKeyboardRemove())
        await message.bot.delete_message(message.chat.id, tmp_msg.message_id)
        await message.bot.delete_message(message.chat.id, message.message_id)
    except Exception:
        pass

    db_user = await SubscriptionService.process_onboarding(
        session,
        message.from_user.id,
        message.from_user.username,
        message.from_user.first_name,
        None,
    )
    if not db_user:
        await message.answer(texts.ERROR_USER_NOT_FOUND)
        return

    text, kb = await _build_hub_text_and_kb(session, db_user)

    await render_hub(
        message.bot,
        message.chat.id,
        text,
        kb,
        force_new=True,
    )


@router.callback_query(F.data == "back_to_main_menu")
async def back_to_main_menu(
    callback: CallbackQuery,
    state: FSMContext,
    db_user: User | None = None,
    session: AsyncSession = None,
):
    await state.clear()

    if not db_user:
        if session is None:
            await callback.answer(
                texts.ERROR_USER_NOT_FOUND,
                show_alert=True,
            )
            return

        db_user = await SubscriptionService.process_onboarding(
            session,
            callback.from_user.id,
            callback.from_user.username,
            callback.from_user.first_name,
            None,
        )

        invalidate_user_cache(callback.from_user.id)

    if not db_user:
        await callback.answer(
            texts.ERROR_USER_NOT_FOUND,
            show_alert=True,
        )
        return

    if session is None:
        await callback.answer(
            texts.ERROR_USER_NOT_FOUND,
            show_alert=True,
        )
        return

    await callback.answer(show_alert=False)
    await _ensure_bot_unblocked(
        session,
        db_user.telegram_id,
    )

    text, kb = await _build_hub_text_and_kb(session, db_user)

    await render_hub(
        callback.bot,
        callback.message.chat.id,
        text,
        kb,
        trigger_message_id=callback.message.message_id,
    )


@router.callback_query(F.data == "ignore")
async def ignore_callback(callback: CallbackQuery) -> None:
    await callback.answer()
