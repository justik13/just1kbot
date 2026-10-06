"""Telegram account-balance, top-up, and financial-history screens."""
from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
import logging

from utils.datetime_helpers import now_utc

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from bot import texts
from bot.keyboards import (
    get_back_button,
    get_balance_amounts_keyboard,
    get_balance_history_keyboard,
    get_balance_keyboard,
    get_order_invoice_keyboard,
)
from bot.states import BalanceStates
from config.settings import get_settings
from database.models import Order, User
from integrations.payment_gateways.base import PaymentCreationAmbiguousError
from database.repositories.account_ledger_repo import (
    get_account_balance,
    get_account_history,
    get_account_history_count,
)
from database.repositories.tariffs_repo import get_active_tariffs
from services.maintenance_service import MaintenanceService
from services.order_service import OrderService
from utils.callbacks import parse_callback_id
from utils.formatters import format_datetime
from utils.telegram import render_hub, safe_callback_answer

from .common import _render_maintenance

router = Router()
logger = logging.getLogger(__name__)


def _topup_errors(settings=None) -> dict[str, str]:
    cfg = settings or get_settings()
    return {
        "topup_amount_must_be_whole_rubles": texts.TOPUP_ERROR_WHOLE_RUBLES,
        "topup_below_minimum": texts.TOPUP_ERROR_MINIMUM.format(
            minimum=cfg.BALANCE_MIN_TOPUP_RUB
        ),
        "topup_above_maximum": texts.TOPUP_ERROR_MAXIMUM.format(
            maximum=cfg.BALANCE_MAX_CUSTOM_TOPUP_RUB
        ),
        "topup_balance_limit_exceeded": texts.TOPUP_ERROR_BALANCE_LIMIT,
        "too_many_unfinished_topups": texts.TOPUP_ERROR_UNFINISHED.format(
            limit=cfg.BALANCE_MAX_UNFINISHED_TOPUPS
        ),
        "topup_blocked": texts.TOPUP_ERROR_BLOCKED,
        "topup_user_banned": texts.TOPUP_ERROR_BANNED,
    }


HISTORY_LABELS = texts.BALANCE_ENTRY_LABELS


def topup_presets(tariffs: list, settings=None) -> list[int]:
    cfg = settings or get_settings()
    prices = {
        int(tariff.price_rub)
        for tariff in tariffs
        if tariff.is_active
        and int(tariff.price_rub) >= cfg.BALANCE_MIN_TOPUP_RUB
        and int(tariff.price_rub) <= cfg.BALANCE_MAX_PRESET_RUB
    }
    return sorted(prices)[: cfg.BALANCE_MAX_PRESET_OPTIONS]


def _history_lines(entries: list) -> str:
    if not entries:
        return texts.BALANCE_HISTORY_EMPTY
    lines = []
    for entry in entries:
        label = HISTORY_LABELS.get(
            entry.entry_type, texts.BALANCE_OPERATION_DEFAULT_LABEL
        )
        sign = "+" if entry.amount > 0 else texts.BALANCE_SIGN_MINUS
        amount = abs(int(entry.amount))
        lines.append(
            texts.BALANCE_HISTORY_ROW_FORMAT.format(
                value_0=format_datetime(entry.created_at),
                value_1=label,
                value_2=sign,
                value_3=amount,
            )
        )
    return "\n".join(lines)


def _pending_topup_conditions(user_id: int):
    """Single canonical predicate for fresh topup pendings.

    Both the visible-invoice lookup and the funnel-limit count must use
    the same definition, otherwise they drift (one blocks what the other
    cannot see). Fresh = has payment URL and created within 15 min:
    stale rows without URL (gateway failed) or older than the dedup
    window must not block the funnel forever — Order pendings are never
    auto-expired (cleanup covers only payments).
    """
    cutoff = now_utc() - timedelta(minutes=15)
    return (
        Order.user_id == user_id,
        Order.service_type == "topup",
        Order.status == "pending",
        Order.payment_url.is_not(None),
        Order.created_at >= cutoff,
    )


async def _get_pending_topup_order(
    session: AsyncSession, user_id: int
) -> Order | None:
    return await session.scalar(
        select(Order)
        .where(*_pending_topup_conditions(user_id))
        .order_by(Order.created_at.desc())
        .limit(1)
    )


async def _count_pending_topup_orders(
    session: AsyncSession, user_id: int
) -> int:
    return (
        await session.scalar(
            select(func.count(Order.id)).where(
                *_pending_topup_conditions(user_id)
            )
        )
        or 0
    )


async def _render_balance(
    bot,
    chat_id: int,
    session: AsyncSession,
    user: User,
    *,
    notice: str | None = None,
    trigger_message_id: int | None = None,
    message_effect_id: str | None = None,
    force_new: bool = False,
    custom_keyboard: InlineKeyboardMarkup | None = None,
) -> None:
    snapshot = await get_account_balance(session, user_id=user.id)
    history = await get_account_history(session, user_id=user.id, limit=5)
    pending_topup = await _get_pending_topup_order(session, user.id)

    details = [
        texts.BALANCE_BALANCE.format(
            int_snapshot_real_available=int(snapshot.real_available)
        ),
    ]
    if snapshot.bonus_available > 0:
        details.append(
            texts.BALANCE_BONUS_BALANCE.format(
                int_snapshot_bonus_available=int(snapshot.bonus_available)
            )
        )
    if snapshot.debt > 0:
        details.append(
            texts.BALANCE_INSUFFICIENT_FUNDS_DIFFERENCE.format(
                value_0=int(snapshot.debt)
            )
        )
    prefix = f"{notice}\n\n" if notice else ""
    text = (
        texts.BALANCE_TOPUP_CANCELLED_NO_DEBIT.format(value_0=prefix)
        + "\n".join(details)
        + texts.BALANCE_HISTORY_SECTION_TITLE
        + _history_lines(history)
    )

    await render_hub(
        bot,
        chat_id,
        text,
        custom_keyboard or get_balance_keyboard(has_visible_topup=pending_topup is not None),
        trigger_message_id=trigger_message_id,
        message_effect_id=message_effect_id,
        force_new=force_new,
    )


async def _create_and_render_topup(
    target,
    session: AsyncSession,
    user: User,
    amount: int,
    *,
    context: dict | None = None,
) -> None:
    if target is None:
        return
    bot = getattr(target, "bot", None)
    chat = getattr(target, "chat", None)
    chat_id = chat.id if chat else None
    if (bot is None or chat_id is None) and isinstance(target, CallbackQuery):
        bot = target.bot
        chat_id = target.message.chat.id if target.message else None

    if bot is None or chat_id is None:
        return

    # Capture primitives BEFORE any gateway IO: OrderService.create_order()
    # does session.rollback() on gateway failure, which expires all ORM
    # objects of this session. Touching user.id/user.telegram_id afterwards
    # raises MissingGreenlet (see prod 2026-09-29 YooKassa TIMEOUT).
    user_id = user.id
    telegram_id = user.telegram_id

    if not await MaintenanceService.can_user_perform_action(
        session, telegram_id
    ):
        await _render_maintenance(target, session, back_to="menu_balance")
        return

    back_to = (
        "white_internet"
        if (context or {}).get("source") == "white_internet"
        else "menu_balance"
    )

    # Funnel guards: choose_topup_amount() checks topup_blocked, but preset
    # callbacks and custom-amount messages arrive here directly, so enforce
    # here to close the bypass.
    if getattr(user, "topup_blocked", False):
        await render_hub(
            bot,
            chat_id,
            _topup_errors()["topup_blocked"],
            get_back_button(back_to),
        )
        return

    if getattr(user, "financial_hold", False):
        await render_hub(
            bot,
            chat_id,
            texts.PAYMENT_DISPUTE_BLOCKED_NOTICE,
            get_back_button(back_to),
        )
        return

    pending_count = await _count_pending_topup_orders(session, user_id)
    if pending_count >= get_settings().BALANCE_MAX_UNFINISHED_TOPUPS:
        await render_hub(
            bot,
            chat_id,
            _topup_errors()["too_many_unfinished_topups"],
            get_back_button(back_to),
        )
        return

    try:
        bot_username = getattr(getattr(bot, "_me", None), "username", None)
        order_meta = {"context": context} if context else None
        order = await OrderService.create_order(
            session,
            user_id=user_id,
            service_type="topup",
            amount_rub=Decimal(amount),
            payment_method="yookassa",
            description=texts.CHECKOUT_DESCRIPTION_DEFAULT,
            metadata=order_meta,
            bot_username=bot_username,
        )
    except PaymentCreationAmbiguousError:
        # Order is kept on purpose (webhook may still settle it) — tell the
        # user to wait rather than reporting a failed top-up.
        await render_hub(
            bot,
            chat_id,
            texts.PAYMENT_CREATION_STATUS_UNKNOWN,
            get_back_button(back_to),
        )
        return
    except Exception as exc:
        logger.exception("Failed to create topup order for user %s: %s", user_id, exc)
        await render_hub(
            bot,
            chat_id,
            texts.ERROR_PAYMENT_SERVICE,
            get_back_button(back_to),
        )
        return

    balance = await get_account_balance(session, user_id=user_id)
    text = texts.BALANCE_TOPUP_CARD.format(
        value_0=int(order.amount_rub), value_1=int(balance.available)
    )
    await render_hub(
        bot,
        chat_id,
        text,
        get_order_invoice_keyboard(
            payment_url=order.payment_url or "",
            order_id=str(order.id),
            price=int(order.amount_rub),
            back_callback=back_to,
        ),
    )
    if isinstance(target, CallbackQuery):
        await safe_callback_answer(target)


@router.callback_query(F.data == "menu_balance")
async def show_balance(
    callback: CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
    db_user: User | None = None,
) -> None:
    await callback.answer(show_alert=False)
    await state.clear()
    if db_user is None:
        try:
            await callback.answer(
                texts.BALANCE_HISTORY_LIMIT_REACHED_NOTE, show_alert=True
            )
        except Exception:
            pass
        return
    await _render_balance(
        callback.bot,
        callback.message.chat.id,
        session,
        db_user,
        trigger_message_id=callback.message.message_id
        if callback.message
        else None,
    )


@router.callback_query(F.data.startswith("balance_history"))
async def show_balance_history(
    callback: CallbackQuery,
    session: AsyncSession,
    db_user: User | None = None,
) -> None:
    await callback.answer(show_alert=False)
    if db_user is None:
        return

    page = 1
    if ":" in callback.data:
        try:
            page = int(callback.data.split(":")[1])
        except (ValueError, IndexError):
            page = 1

    total_count = await get_account_history_count(session, user_id=db_user.id)
    page_size = 10
    total_pages = max(1, (total_count + page_size - 1) // page_size)
    page = max(1, min(page, total_pages))
    offset = (page - 1) * page_size

    entries = await get_account_history(
        session, user_id=db_user.id, limit=page_size, offset=offset
    )

    await render_hub(
        callback.bot,
        callback.message.chat.id,
        texts.BALANCE_HISTORY_TITLE.format(history=_history_lines(entries)),
        get_balance_history_keyboard(page=page, total_pages=total_pages),
    )


@router.callback_query(F.data == "balance_topup")
async def choose_topup_amount(
    callback: CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
    db_user: User | None = None,
) -> None:
    await callback.answer(show_alert=False)
    await state.clear()
    if db_user is None:
        return
    if not await MaintenanceService.can_user_perform_action(
        session, callback.from_user.id
    ):
        await _render_maintenance(callback, session, back_to="menu_balance")
        return
    if db_user.topup_blocked:
        await render_hub(
            callback.bot,
            callback.message.chat.id,
            _topup_errors()["topup_blocked"],
            get_back_button("menu_balance"),
        )
        return
    if getattr(db_user, "financial_hold", False):
        await render_hub(
            callback.bot,
            callback.message.chat.id,
            texts.PAYMENT_DISPUTE_BLOCKED_NOTICE,
            get_back_button("menu_balance"),
        )
        return

    pending_topup = await _get_pending_topup_order(session, db_user.id)
    if pending_topup and pending_topup.payment_url:
        balance = await get_account_balance(session, user_id=db_user.id)
        text = texts.BALANCE_TOPUP_CARD.format(
            value_0=int(pending_topup.amount_rub), value_1=int(balance.available)
        )
        await render_hub(
            callback.bot,
            callback.message.chat.id,
            text,
            get_order_invoice_keyboard(
                payment_url=pending_topup.payment_url,
                order_id=str(pending_topup.id),
                price=int(pending_topup.amount_rub),
                back_callback="menu_balance",
            ),
        )
        return

    tariffs = await get_active_tariffs(session)
    cfg = get_settings()
    preset_defaults = [100, 250, 500, 1000]
    valid_defaults = [
        amt
        for amt in preset_defaults
        if cfg.BALANCE_MIN_TOPUP_RUB <= amt <= cfg.BALANCE_MAX_PRESET_RUB
    ]
    tariff_amounts = topup_presets(tariffs, cfg)
    amounts = sorted(list(set(valid_defaults + tariff_amounts)))[
        : cfg.BALANCE_MAX_PRESET_OPTIONS
    ]
    balance = await get_account_balance(session, user_id=db_user.id)
    balance_lines = texts.BALANCE_TOTAL_AVAILABLE_LABEL.format(
        int_balance_real_available=int(balance.real_available)
    )
    if balance.bonus_available > 0:
        balance_lines += texts.BALANCE_BONUS_REMAINING_LABEL.format(
            int_balance_bonus_available=int(balance.bonus_available)
        )

    from services.referral_bonus import (
        calculate_referral_bonus,
        get_referral_tier,
        is_first_topup_eligible,
    )

    is_first_eligible = await is_first_topup_eligible(
        session, user_id=db_user.id
    )

    bonus_notice = ""
    if is_first_eligible:
        # Preview the inviter's actual tier rate (15-30%), not a fixed 20%.
        # The bonus accrues to the inviter; hide the notice if the inviter
        # is gone/banned since no bonus would be granted.
        from database.repositories.users_repo import (
            get_user_active_referrals_count,
            get_user_by_telegram_id,
        )

        referrer = None
        if getattr(db_user, "referred_by", None):
            referrer = await get_user_by_telegram_id(session, db_user.referred_by)
        if (
            referrer is not None
            and not getattr(referrer, "is_deleted", False)
            and not getattr(referrer, "is_banned", False)
        ):
            ref_active = await get_user_active_referrals_count(
                session, referrer.telegram_id
            )
            ref_rate = get_referral_tier(ref_active).rate
            rate_pct = int(ref_rate * 100)
            bonus_lines = "\n".join(
                texts.BALANCE_NA_BONUS_BALANCE.format(
                    amt=amt,
                    amt____10=int(calculate_referral_bonus(amt, rate=ref_rate)),
                )
                for amt in amounts
            )
            bonus_notice = (
                texts.BALANCE_BONUS_NA_PERVOE_TOPUP.format()
                + texts.BALANCE_VY_POLUCHITE_20_OT_SUMMY_POPOL.format(
                    rate_pct=rate_pct
                )
                + texts.BALANCE_PODROBNEE_V_MENYU_PRIGLASIT_DR.format()
                + texts.BALANCE_RASCHET_BONUSA_K_SUMME.format(bonus_lines=bonus_lines)
            )

    text = (
        texts.BALANCE_TOPUP_BALANCE.format()
        + f"{balance_lines}\n"
        + f"{bonus_notice}\n"
        + texts.BALANCE_SELECT_AMOUNT_ILI_UKAZHITE_DR.format()
    )
    await render_hub(
        callback.bot,
        callback.message.chat.id,
        text,
        get_balance_amounts_keyboard(amounts),
    )


@router.callback_query(F.data.startswith("balance_create:"))
async def create_preset_topup(
    callback: CallbackQuery,
    session: AsyncSession,
    db_user: User | None = None,
) -> None:
    amount = parse_callback_id(callback.data, 1)
    if db_user is None or amount is None:
        return
    if not await MaintenanceService.can_user_perform_action(
        session, callback.from_user.id
    ):
        await _render_maintenance(callback, session, back_to="menu_balance")
        return
    cfg = get_settings()
    if amount < cfg.BALANCE_MIN_TOPUP_RUB or amount > cfg.BALANCE_MAX_CUSTOM_TOPUP_RUB:
        await safe_callback_answer(callback, texts.ERROR_INVALID_REQUEST, show_alert=True)
        return
    await _create_and_render_topup(callback, session, db_user, amount)


@router.callback_query(F.data == "balance_custom_amount")
async def request_custom_amount(
    callback: CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
) -> None:
    await callback.answer(show_alert=False)
    if not await MaintenanceService.can_user_perform_action(
        session, callback.from_user.id
    ):
        await _render_maintenance(callback, session, back_to="menu_balance")
        return
    await state.set_state(BalanceStates.enter_custom_amount)
    await state.set_data({})
    await render_hub(
        callback.bot,
        callback.message.chat.id,
        texts.TOPUP_CUSTOM_AMOUNT_PROMPT.format(
            minimum=get_settings().BALANCE_MIN_TOPUP_RUB,
            maximum=get_settings().BALANCE_MAX_CUSTOM_TOPUP_RUB,
        ),
        get_back_button("menu_balance", text=texts.BTN_PAYMENT_CANCEL),
    )


@router.message(BalanceStates.enter_custom_amount)
async def accept_custom_amount(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
    db_user: User | None = None,
) -> None:
    try:
        await message.delete()
    except Exception:
        pass

    if not await MaintenanceService.can_user_perform_action(
        session, message.from_user.id
    ):
        await state.clear()
        await _render_maintenance(message, session, back_to="menu_balance")
        return

    raw = (message.text or "").strip()
    if not raw or raw.startswith("/"):
        await state.clear()
        return

    if not raw.isascii() or not raw.isdigit():
        prompt = texts.TOPUP_CUSTOM_AMOUNT_PROMPT.format(
            minimum=get_settings().BALANCE_MIN_TOPUP_RUB,
            maximum=get_settings().BALANCE_MAX_CUSTOM_TOPUP_RUB,
        )
        await render_hub(
            message.bot,
            message.chat.id,
            f"{texts.TOPUP_INVALID_AMOUNT}\n\n{prompt}",
            get_back_button("menu_balance", text=texts.BTN_PAYMENT_CANCEL),
        )
        return
    if db_user is None:
        await state.clear()
        return

    data = await state.get_data()
    minimum = int(data.get("balance_minimum") or get_settings().BALANCE_MIN_TOPUP_RUB)
    maximum = get_settings().BALANCE_MAX_CUSTOM_TOPUP_RUB
    prompt = texts.TOPUP_CUSTOM_AMOUNT_PROMPT.format(
        minimum=minimum,
        maximum=maximum,
    )

    amount = int(raw)
    if amount < minimum:
        err = texts.TOPUP_OPERATION_MINIMUM.format(minimum=minimum)
        await render_hub(
            message.bot,
            message.chat.id,
            f"{err}\n\n{prompt}",
            get_back_button("menu_balance", text=texts.BTN_PAYMENT_CANCEL),
        )
        return

    if amount > maximum:
        err = texts.TOPUP_ERROR_MAXIMUM.format(maximum=maximum)
        await render_hub(
            message.bot,
            message.chat.id,
            f"{err}\n\n{prompt}",
            get_back_button("menu_balance", text=texts.BTN_PAYMENT_CANCEL),
        )
        return

    await state.clear()
    await _create_and_render_topup(
        message,
        session,
        db_user,
        amount,
        context=data.get("balance_context"),
    )


@router.callback_query(F.data == "balance_resume_topup")
async def resume_topup(
    callback: CallbackQuery,
    session: AsyncSession,
    db_user: User | None = None,
) -> None:
    await callback.answer(show_alert=False)
    if db_user is None:
        return
    # Same funnel policy as creation: blocked/held users must not be
    # handed a live invoice. Settlement holds the credit anyway
    # (mark_order_paid), this is the UX-layer reject.
    if getattr(db_user, "topup_blocked", False):
        await render_hub(
            callback.bot,
            callback.message.chat.id,
            _topup_errors()["topup_blocked"],
            get_back_button("menu_balance"),
        )
        return
    if getattr(db_user, "financial_hold", False):
        await render_hub(
            callback.bot,
            callback.message.chat.id,
            texts.PAYMENT_DISPUTE_BLOCKED_NOTICE,
            get_back_button("menu_balance"),
        )
        return
    pending_topup = await _get_pending_topup_order(session, db_user.id)
    if pending_topup is None or not pending_topup.payment_url:
        await _render_balance(
            callback.bot,
            callback.message.chat.id,
            session,
            db_user,
            notice=texts.TOPUP_MISSING_NOTICE,
        )
        return
    balance = await get_account_balance(session, user_id=db_user.id)
    text = texts.BALANCE_TOPUP_CARD.format(
        value_0=int(pending_topup.amount_rub), value_1=int(balance.available)
    )
    await render_hub(
        callback.bot,
        callback.message.chat.id,
        text,
        get_order_invoice_keyboard(
            payment_url=pending_topup.payment_url,
            order_id=str(pending_topup.id),
            price=int(pending_topup.amount_rub),
            back_callback="menu_balance",
        ),
    )
