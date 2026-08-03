import logging

from aiogram import Router, F
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy.ext.asyncio import AsyncSession

from bot import texts
from bot.keyboards import (
    get_back_button,
    get_history_keyboard,
    get_profile_keyboard,
    get_referral_keyboard,
)
from database.models import User
from database.repositories.account_ledger_repo import get_account_balance
from database.repositories.payments_repo import get_user_payments
from database.repositories.profiles_repo import get_user_profiles
from database.repositories.tariffs_repo import get_tariff_by_id
from database.repositories.users_repo import (
    get_user_referrals,
    get_user_with_referrals,
)
from services.payment_status import payment_display_status
from services.subscription import SubscriptionService
from utils.formatters import (
    format_datetime,
    format_traffic,
)
from utils.tariff_names import get_tariff_display_name
from utils.telegram import render_hub, safe

router = Router()
logger = logging.getLogger(__name__)


async def _render_profile(
    target,
    user: User,
    session: AsyncSession,
):
    profiles = await get_user_profiles(session, user.id)
    profiles_count = len(profiles)
    total_traffic = sum(
        p.traffic_down + p.traffic_up
        for p in profiles
    )

    has_access = await SubscriptionService.check_access(
        session,
        user.telegram_id,
    )

    referrals_count = len(
        await get_user_referrals(session, user.telegram_id)
    )
    balance = await get_account_balance(session, user_id=user.id)

    if has_access:
        device_limit = user.device_limit or 0
        tariff_name = (
            texts.RUNTIME_BOT_HANDLERS_PROFILE_L63_1.format(value_0=get_tariff_display_name(device_limit), value_1=device_limit)
        ) if device_limit else texts.RUNTIME_BOT_HANDLERS_PROFILE_L65_1

        if user.current_tariff_id:
            tariff = await get_tariff_by_id(
                session,
                user.current_tariff_id,
            )
            if tariff:
                device_limit = tariff.device_limit
                tariff_name = (
                    texts.RUNTIME_BOT_HANDLERS_PROFILE_L75_1.format(value_0=get_tariff_display_name(device_limit), value_1=device_limit)
                )

        rendered = texts.PROFILE_TEXT_ACTIVE.format(
            name=safe(user.first_name or texts.RUNTIME_BOT_HANDLERS_PROFILE_L80_1),
            username_line=(f" (@{safe(user.username)})" if user.username else ""),
            telegram_id=user.telegram_id,
            tariff_name=tariff_name,
            devices_count=profiles_count,
            total_traffic=format_traffic(total_traffic),
            referrals_count=referrals_count,
            referral_days=user.referral_days,
            balance=int(balance.available),
        )
        kb = get_profile_keyboard()
    else:
        rendered = texts.PROFILE_TEXT_INACTIVE.format(
            name=safe(user.first_name or texts.RUNTIME_BOT_HANDLERS_PROFILE_L93_1),
            username_line=(f" (@{safe(user.username)})" if user.username else ""),
            telegram_id=user.telegram_id,
            referrals_count=referrals_count,
            referral_days=user.referral_days,
            balance=int(balance.available),
        )

        builder = InlineKeyboardBuilder()
        builder.button(
            text=texts.UI_BOT_HANDLERS_PROFILE_L103_1,
            callback_data="menu_buy",
        )
        builder.button(
            text=texts.UI_BOT_HANDLERS_PROFILE_L107_1,
            callback_data="menu_balance",
        )
        builder.button(
            text=texts.UI_BOT_HANDLERS_PROFILE_L111_1,
            callback_data="referral",
        )
        builder.button(
            text=texts.UI_BOT_HANDLERS_PROFILE_L115_1,
            callback_data="user_history",
        )
        builder.button(
            text=texts.UI_BOT_HANDLERS_PROFILE_L119_1,
            callback_data="back_to_main_menu",
        )
        builder.adjust(1, 1, 1, 1, 1)
        kb = builder.as_markup()

    await render_hub(
        target.bot,
        target.chat.id,
        rendered,
        kb,
    )


@router.callback_query(F.data == "menu_profile")
async def hub_menu_profile(
    callback: CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
    db_user: User | None = None,
):
    await callback.answer(show_alert=False)
    await state.clear()

    if not db_user:
        await callback.answer(
            texts.ERROR_USER_NOT_FOUND,
            show_alert=True,
        )
        return

    await _render_profile(
        callback.message,
        db_user,
        session,
    )


@router.callback_query(F.data == "back_to_profile")
async def back_to_profile(
    callback: CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
    db_user: User | None = None,
):
    await callback.answer(show_alert=False)
    await state.clear()

    if not db_user:
        await callback.answer(
            texts.ERROR_USER_NOT_FOUND,
            show_alert=True,
        )
        return

    await _render_profile(
        callback.message,
        db_user,
        session,
    )


@router.callback_query(F.data == "user_history")
async def show_history(
    callback: CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
    db_user: User | None = None,
):
    await callback.answer(show_alert=False)
    await state.clear()

    if not db_user:
        await callback.answer(
            texts.ERROR_USER_NOT_FOUND,
            show_alert=True,
        )
        return

    payments = await get_user_payments(session, db_user.id)

    if not payments:
        rendered = texts.HISTORY_HEADER + texts.HISTORY_EMPTY
    else:
        rendered = texts.HISTORY_HEADER
        for payment in payments[:10]:
            display_status = payment_display_status(payment)
            status_icon = texts.PAYMENT_STATUS_ICONS.get(
                display_status,
                texts.RUNTIME_BOT_HANDLERS_PROFILE_L208_1,
            )
            date = format_datetime(
                payment.paid_at or payment.created_at
            )
            currency = texts.RUNTIME_BOT_HANDLERS_PROFILE_L213_1
            rendered += (
                f"{status_icon} {date} | "
                f"{payment.amount} {currency}\n"
            )

        if len(payments) > 10:
            rendered += texts.HISTORY_LIMIT_NOTE.format(
                count=len(payments),
            )

    await render_hub(
        callback.bot,
        callback.message.chat.id,
        rendered,
        get_history_keyboard(),
    )


@router.callback_query(F.data == "referral")
async def show_referral(
    callback: CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
    db_user: User | None = None,
):
    await callback.answer(show_alert=False)
    await state.clear()

    if not db_user:
        await callback.answer(
            texts.ERROR_USER_NOT_FOUND,
            show_alert=True,
        )
        return

    _, referrals = await get_user_with_referrals(
        session,
        db_user.telegram_id,
    )

    bot_info = await callback.bot.get_me()
    referral_link = (
        f"https://t.me/{bot_info.username}"
        f"?start=ref_{db_user.telegram_id}"
    )

    invited_count = len(referrals)

    await render_hub(
        callback.bot,
        callback.message.chat.id,
        texts.REFERRAL_TEXT.format(
            referral_link=referral_link,
            invited_count=invited_count,
            bonus_total=db_user.referral_days,
        ),
        get_referral_keyboard(referral_link),
    )


@router.callback_query(F.data == "referrals_list")
async def show_referrals_list(
    callback: CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
    db_user: User | None = None,
):
    await callback.answer(show_alert=False)
    await state.clear()

    if not db_user:
        await callback.answer(
            texts.ERROR_USER_NOT_FOUND,
            show_alert=True,
        )
        return

    _, referrals = await get_user_with_referrals(
        session,
        db_user.telegram_id,
    )

    if not referrals:
        rendered = texts.REFERRAL_LIST_EMPTY
    else:
        rendered = texts.REFERRAL_LIST_HEADER
        for referral in referrals[:20]:
            safe_user = (
                f"@{safe(referral.username)}"
                if referral.username
                else texts.USER_ID_LABEL.format(user_id=referral.telegram_id)
            )
            rendered += texts.RUNTIME_BOT_HANDLERS_PROFILE_L306_1.format(value_0=safe_user)

        if len(referrals) > 20:
            rendered += (
                texts.RUNTIME_BOT_HANDLERS_PROFILE_L310_1.format(value_0=len(referrals) - 20)
            )

        rendered += texts.REFERRAL_LIST_FOOTER.format(
            count=len(referrals),
        )

    await render_hub(
        callback.bot,
        callback.message.chat.id,
        rendered,
        get_back_button("referral"),
    )
