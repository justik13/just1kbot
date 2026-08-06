import logging
import re

from aiogram import Router, F
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
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

    match = re.match(r"ref_(\d+)", command_args)

    return int(match.group(1)) if match else None


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


@router.message(CommandStart())
async def cmd_start(
    message: Message,
    state: FSMContext,
    command: Command,
    session: AsyncSession,
):
    await state.clear()

    telegram_id = message.from_user.id

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

    builder = InlineKeyboardBuilder()
    builder.button(text="🏠 Главное меню", callback_data="back_to_main_menu")

    await render_hub(
        message.bot,
        message.chat.id,
        texts.WELCOME_TEXT,
        builder.as_markup(),
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

    await callback.answer(show_alert=False)

    if session is not None:
        await _ensure_bot_unblocked(
            session,
            db_user.telegram_id,
        )

    is_active = await SubscriptionService.check_access(
        session,
        db_user.telegram_id,
    )

    is_admin = db_user.telegram_id in get_settings().ADMIN_IDS

    name = safe(db_user.first_name or texts.RUNTIME_BOT_HANDLERS_START_L206_1)

    balance = await get_account_balance(session, user_id=db_user.id)
    text = texts.HUB_HEADER.format(
        name=name,
        real_balance=int(balance.real_available),
        bonus_balance=int(balance.bonus_available),
    )


    kb = get_hub_keyboard(
        is_admin=is_admin,
        is_active=is_active,
    )

    await render_hub(
        callback.bot,
        callback.message.chat.id,
        text,
        kb,
    )
