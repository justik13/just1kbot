import logging

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery
from sqlalchemy.ext.asyncio import AsyncSession

from bot import texts
from bot.formatters import format_plural, pluralize
from bot.keyboards import (
    get_history_keyboard,
    get_referral_keyboard,
    get_referral_leaderboard_keyboard,
    get_referrals_list_keyboard,
)
from database.models import User
from database.repositories.payments_repo import get_user_payments
from database.repositories.users_repo import (
    get_referral_leaderboard,
    get_user_active_referrals_count,
    get_user_referral_rank,
    get_user_referrals_count,
    get_user_referrals_paginated,
    get_users_by_telegram_ids,
    update_user,
)
from services.referral_bonus import get_referral_bonus_balance, get_referral_tier
from utils.formatters import format_datetime
from utils.telegram import (
    make_rich_button,
    make_rich_button_row,
    make_rich_nav_row,
    render_hub,
    safe,
)

router = Router()
logger = logging.getLogger(__name__)


def payment_display_status(payment: object) -> str:
    status = (
        getattr(payment, "status", None)
        or getattr(payment, "provider_status", None)
        or "pending"
    ).lower()
    if status in {"succeeded", "success", "paid"}:
        return "succeeded"
    if status in {"canceled", "cancelled"}:
        return "canceled"
    if status in {"refunded", "partially_refunded"}:
        return "refunded"
    return "pending"


async def _get_inviter_line(session: AsyncSession, user: User) -> str:
    if not user.referred_by:
        return ""
    from database.repositories.users_repo import get_user_by_telegram_id
    referrer = await get_user_by_telegram_id(session, user.referred_by)
    if referrer:
        name = safe(referrer.first_name) if referrer.first_name else ""
        username_str = f" (@{safe(referrer.username)})" if referrer.username else ""
        if name or username_str:
            return texts.INVITED_BY_NAMED_LINE.format(
                name=f"{name}{username_str}",
                referrer_id=referrer.telegram_id,
            )
        return texts.INVITED_BY_ID_LINE.format(referrer_id=referrer.telegram_id)
    return texts.INVITED_BY_ID_LINE.format(referrer_id=user.referred_by)


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
        # Already answered above: a second answer on the same query raises
        # TelegramBadRequest, which must not bubble into the error handler.
        try:
            await callback.answer(
                texts.ERROR_USER_NOT_FOUND,
                show_alert=True,
            )
        except Exception:
            pass
        return

    payments = await get_user_payments(session, db_user.id, limit=10)

    if not payments:
        rendered = texts.HISTORY_HEADER + texts.HISTORY_EMPTY
    else:
        rows = []
        for payment in payments[:10]:
            display_status = payment_display_status(payment)
            status_icon = texts.PAYMENT_STATUS_ICONS.get(
                display_status,
                texts.PAYMENT_STATUS_PENDING_ICON,
            )
            date = format_datetime(getattr(payment, "paid_at", None) or getattr(payment, "created_at", None))
            currency = texts.CURRENCY_RUB_SYMBOL
            amount = (
                int(payment.amount_rub)
                if hasattr(payment, "amount_rub")
                else int(getattr(payment, "amount", 0))
            )
            rows.append(
                f"<tr><td>{status_icon}</td><td>{date}</td><td><b>{amount} {currency}</b></td></tr>"
            )
        table = texts.REFERRAL_PAYMENTS_TABLE_TEMPLATE.format(rows=''.join(rows))
        rendered = texts.HISTORY_HEADER + table

        if len(payments) > 10:
            rendered += texts.HISTORY_LIMIT_NOTE.format(count=len(payments))

    rendered += f"\n{make_rich_nav_row('menu_referral')}"

    await render_hub(
        callback.bot,
        callback.message.chat.id,
        rendered,
        reply_markup=None,
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
        try:
            await callback.answer(texts.ERROR_USER_NOT_FOUND, show_alert=True)
        except Exception:
            pass
        return

    active_count = await get_user_active_referrals_count(session, db_user.telegram_id)
    invited_count = await get_user_referrals_count(session, db_user.telegram_id)
    bonus_balance = await get_referral_bonus_balance(session, user_id=db_user.id)
    user_rank, _ = await get_user_referral_rank(session, db_user.telegram_id)
    tier_info = get_referral_tier(active_count)

    rate_pct = int(tier_info.rate * 100)
    if (
        tier_info.needed_for_next is not None
        and tier_info.next_tier_name
        and tier_info.next_rate
    ):
        next_rate_pct = int(tier_info.next_rate * 100)
        needed_count = format_plural(tier_info.needed_for_next, texts.NOUN_USERS)
        tier_progress_line = texts.REFERRAL_TIER_PROGRESS_NEXT.format(
            next_tier_name=tier_info.next_tier_name,
            next_rate_pct=next_rate_pct,
            needed_count=needed_count,
        )
    else:
        tier_progress_line = texts.REFERRAL_TIER_PROGRESS_MAX

    if user_rank is not None and active_count > 0:
        rank_line = texts.REFERRAL_MAIN_RANK_RANKED.format(rank=user_rank)
    else:
        rank_line = texts.REFERRAL_MAIN_RANK_NOT_RANKED

    bot_info = await callback.bot.get_me()
    referral_link = f"https://t.me/{bot_info.username}?start=ref_{db_user.telegram_id}"

    inviter_line = await _get_inviter_line(session, db_user)
    if inviter_line:
        inviter_line = f"\n\n{inviter_line}"
    else:
        inviter_line = ""

    nav_row = make_rich_nav_row(include_menu=True)

    body_text = (
        texts.REFERRAL_TEXT_BALANCE.format(
            bonus_balance=int(bonus_balance),
            tier_name=tier_info.name,
            rate_pct=rate_pct,
            rank_line=rank_line,
            active_count=active_count,
            invited_count=invited_count,
            tier_progress_line=tier_progress_line,
            referral_link=referral_link,
            inviter_line=inviter_line,
        )
        + f"\n{nav_row}"
    )

    await render_hub(
        callback.bot,
        callback.message.chat.id,
        body_text,
        reply_markup=None,
        trigger_message_id=callback.message.message_id if callback.message else None,
    )


async def _render_referral_leaderboard(
    callback: CallbackQuery,
    session: AsyncSession,
    db_user: User,
) -> None:
    top_leaders = await get_referral_leaderboard(session, limit=5)
    user_rank, user_active_count = await get_user_referral_rank(
        session, db_user.telegram_id
    )

    from bot.texts.user.referral import mask_telegram_id

    if not top_leaders:
        rendered = texts.REFERRAL_LEADERBOARD_TITLE + "\n" + texts.REFERRAL_LEADERBOARD_EMPTY
    else:
        leader_ids = [leader_tg_id for leader_tg_id, _ in top_leaders]
        leader_users = await get_users_by_telegram_ids(session, leader_ids)

        rendered = f"<p>{texts.REFERRAL_LEADERBOARD_TITLE.strip()}</p>\n"
        rows = []
        for pos, (leader_tg_id, count) in enumerate(top_leaders, start=1):
            medal = texts.REFERRAL_LEADERBOARD_MEDALS.get(pos, "")
            is_me = leader_tg_id == db_user.telegram_id
            leader_user = leader_users.get(leader_tg_id)

            if leader_user and getattr(leader_user, "show_in_leaderboard", False):
                if leader_user.username and leader_user.username.strip():
                    display_name = f"@{safe(leader_user.username.strip())}"
                elif leader_user.first_name and leader_user.first_name.strip():
                    fname = leader_user.first_name.strip()
                    if len(fname) > 20:
                        fname = fname[:19] + "…"
                    display_name = safe(fname)
                else:
                    display_name = mask_telegram_id(leader_tg_id)
            else:
                display_name = mask_telegram_id(leader_tg_id)

            user_label = (
                texts.REFERRAL_LEADERBOARD_USER_YOU.format(masked=display_name)
                if is_me
                else texts.REFERRAL_LEADERBOARD_USER_OTHER.format(masked=display_name)
            )
            rows.append(
                f"<tr><td>{medal.strip() or pos}</td><td>{user_label}</td><td><b>{count}</b></td></tr>"
            )
        table = texts.REFERRAL_LEADERBOARD_TABLE_TEMPLATE.format(rows=''.join(rows))
        rendered += table

    if user_rank is not None and user_active_count > 0:
        noun = pluralize(user_active_count, texts.NOUN_USERS)
        rendered += texts.REFERRAL_LEADERBOARD_YOUR_RANK.format(
            rank=user_rank,
            count=user_active_count,
            noun=noun,
        )
    else:
        rendered += texts.REFERRAL_LEADERBOARD_NOT_RANKED

    is_anonymous = not bool(getattr(db_user, "show_in_leaderboard", False))
    anon_text = texts.BTN_REFERRAL_SHOW_NAME if is_anonymous else texts.BTN_REFERRAL_HIDE_NAME
    anon_btn = make_rich_button(anon_text, callback_data="toggle_ref_anon", style="primary")
    nav_row = make_rich_nav_row("menu_referral")
    rendered += f"\n{make_rich_button_row(anon_btn)}\n{nav_row}"

    await render_hub(
        callback.bot,
        callback.message.chat.id,
        rendered,
        reply_markup=None,
        trigger_message_id=callback.message.message_id if callback.message else None,
    )


@router.callback_query(F.data == "referral_leaderboard")
async def show_referral_leaderboard(
    callback: CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
    db_user: User | None = None,
) -> None:
    await callback.answer(show_alert=False)
    await state.clear()

    if not db_user:
        try:
            await callback.answer(texts.ERROR_USER_NOT_FOUND, show_alert=True)
        except Exception:
            pass
        return

    await _render_referral_leaderboard(callback, session, db_user)


@router.callback_query(F.data == "referral_toggle_anonymity")
async def referral_toggle_anonymity(
    callback: CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
    db_user: User | None = None,
) -> None:
    await state.clear()

    if not db_user:
        try:
            await callback.answer(texts.ERROR_USER_NOT_FOUND, show_alert=True)
        except Exception:
            pass
        return

    current_val = bool(getattr(db_user, "show_in_leaderboard", False))
    new_val = not current_val

    update_kwargs: dict[str, object] = {"show_in_leaderboard": new_val}
    if callback.from_user:
        raw_username = callback.from_user.username
        raw_first_name = callback.from_user.first_name
        if raw_username and raw_username != getattr(db_user, "username", None):
            update_kwargs["username"] = raw_username
        if raw_first_name and raw_first_name != getattr(db_user, "first_name", None):
            update_kwargs["first_name"] = raw_first_name

    await update_user(session, db_user, **update_kwargs)
    await session.commit()

    try:
        await callback.answer(texts.LEADERBOARD_ANONYMITY_TOGGLED, show_alert=False)
    except Exception:
        pass

    await _render_referral_leaderboard(callback, session, db_user)


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
        try:
            await callback.answer(texts.ERROR_USER_NOT_FOUND, show_alert=True)
        except Exception:
            pass
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
            rendered += texts.REFERRAL_LIST_ITEM_FORMAT.format(idx=idx, user=safe_user, date=created_str)

        rendered += "\n" + texts.REFERRAL_LIST_FOOTER.format(
            count=format_plural(total_count, texts.NOUN_USERS)
        )

    pag_btns = []
    if total_pages > 1:
        if page > 1:
            pag_btns.append(make_rich_button(texts.BTN_PAGINATION_PREV, callback_data=f"referrals_list:{page - 1}"))
        pag_btns.append(make_rich_button(texts.BTN_PAGINATION_PAGE.format(page=page, total_pages=total_pages), callback_data="ignore"))
        if page < total_pages:
            pag_btns.append(make_rich_button(texts.BTN_PAGINATION_NEXT, callback_data=f"referrals_list:{page + 1}"))

    pag_row = make_rich_button_row(*pag_btns) if pag_btns else ""
    nav_row = make_rich_nav_row("menu_referral")
    full_text = rendered + (f"\n{pag_row}" if pag_row else "") + f"\n{nav_row}"

    await render_hub(
        callback.bot,
        callback.message.chat.id,
        full_text,
        reply_markup=None,
        trigger_message_id=callback.message.message_id if callback.message else None,
    )
