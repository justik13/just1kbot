import logging

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery
from sqlalchemy.ext.asyncio import AsyncSession

from bot import texts
from bot.keyboards import (
    get_history_keyboard,
    get_referral_keyboard,
    get_referrals_list_keyboard,
)
from database.models import User
from database.repositories.payments_repo import get_user_payments
from database.repositories.users_repo import (
    get_user_referrals_count,
    get_user_referrals_paginated,
)
from services.payment_status import payment_display_status
from services.referral_bonus import get_referral_bonus_balance
from utils.formatters import format_datetime
from utils.telegram import render_hub, safe

router = Router()
logger = logging.getLogger(__name__)


async def _get_inviter_line(session: AsyncSession, user: User) -> str:
    if not user.referred_by:
        return ""
    from database.repositories.users_repo import get_user_by_telegram_id
    referrer = await get_user_by_telegram_id(session, user.referred_by)
    if referrer:
        name = safe(referrer.first_name) if referrer.first_name else ""
        username_str = f" (@{safe(referrer.username)})" if referrer.username else ""
        if name or username_str:
            return f"\n🤝 Вас пригласил: {name}{username_str} (ID: <code>{referrer.telegram_id}</code>)"
        return f"\n🤝 Вас пригласил: ID <code>{referrer.telegram_id}</code>"
    return f"\n🤝 Вас пригласил: ID <code>{user.referred_by}</code>"


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
            date = format_datetime(payment.paid_at or payment.created_at)
            currency = texts.RUNTIME_BOT_HANDLERS_PROFILE_L213_1
            rendered += (
                f"{status_icon} {date} | "
                f"{payment.amount} {currency}\n"
            )

        if len(payments) > 10:
            rendered += texts.HISTORY_LIMIT_NOTE.format(count=len(payments))

    await render_hub(
        callback.bot,
        callback.message.chat.id,
        rendered,
        get_history_keyboard(),
        trigger_message_id=callback.message.message_id if callback.message else None,
    )


@router.callback_query(F.data.in_({"referral", "menu_referral"}))
async def show_referral(
    callback: CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
    db_user: User | None = None,
):
    await callback.answer(show_alert=False)
    await state.clear()

    if not db_user:
        await callback.answer(texts.ERROR_USER_NOT_FOUND, show_alert=True)
        return

    invited_count = await get_user_referrals_count(session, db_user.telegram_id)
    bonus_balance = await get_referral_bonus_balance(session, user_id=db_user.id)

    bot_info = await callback.bot.get_me()
    referral_link = f"https://t.me/{bot_info.username}?start=ref_{db_user.telegram_id}"

    inviter_line = await _get_inviter_line(session, db_user)
    if inviter_line:
        inviter_line = f"\n{inviter_line}\n"

    await render_hub(
        callback.bot,
        callback.message.chat.id,
        texts.REFERRAL_TEXT_BALANCE.format(
            referral_link=referral_link,
            invited_count=invited_count,
            bonus_balance=int(bonus_balance),
            inviter_line=inviter_line,
        ),
        get_referral_keyboard(referral_link),
        trigger_message_id=callback.message.message_id if callback.message else None,
    )


@router.callback_query(F.data.startswith("referrals_list"))
async def show_referrals_list(
    callback: CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
    db_user: User | None = None,
):
    await callback.answer(show_alert=False)
    await state.clear()

    if not db_user:
        await callback.answer(texts.ERROR_USER_NOT_FOUND, show_alert=True)
        return

    page = 1
    if ":" in callback.data:
        try:
            page = int(callback.data.split(":")[1])
        except (ValueError, IndexError):
            page = 1

    page_size = 10
    page_referrals, total_count, page = await get_user_referrals_paginated(
        session, db_user.telegram_id, page=page, per_page=page_size
    )

    if total_count == 0:
        rendered = texts.REFERRAL_LIST_EMPTY
        total_pages = 1
    else:
        total_pages = max(1, (total_count + page_size - 1) // page_size)

        start_idx = (page - 1) * page_size
        rendered = texts.REFERRAL_LIST_HEADER
        for idx, referral in enumerate(page_referrals, start=start_idx + 1):
            safe_user = (
                f"@{safe(referral.username)}"
                if referral.username
                else texts.USER_ID_LABEL.format(user_id=referral.telegram_id)
            )
            created_str = referral.created_at.strftime("%d.%m.%Y") if referral.created_at else ""
            rendered += f"\n{idx}. <b>{safe_user}</b> ({created_str})"

        rendered += "\n" + texts.REFERRAL_LIST_FOOTER.format(count=total_count)

    await render_hub(
        callback.bot,
        callback.message.chat.id,
        rendered,
        get_referrals_list_keyboard(page=page, total_pages=total_pages),
        trigger_message_id=callback.message.message_id if callback.message else None,
    )
