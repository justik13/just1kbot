"""Telegram account-balance, top-up, and financial-history screens."""

import logging

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from bot import texts
from bot.keyboards import (
    get_back_button,
    get_balance_amounts_keyboard,
    get_balance_history_keyboard,
    get_balance_keyboard,
    get_topup_payment_keyboard,
    get_topup_waiting_keyboard,
)
from bot.states import BalanceStates
from config.settings import get_settings
from database.models import Payment, User
from database.repositories.account_ledger_repo import (
    get_account_balance,
    get_account_history,
    get_account_history_count,
)
from database.repositories.tariffs_repo import get_active_tariffs
from services.account_topup import (
    AccountTopupError,
    create_balance_topup,
    get_visible_balance_topup,
    hide_balance_topup,
    request_topup_status_refresh,
)
from services.maintenance_service import MaintenanceService
from utils.callbacks import parse_callback_id
from utils.datetime_helpers import now_utc
from utils.formatters import format_datetime
from utils.telegram import EFFECT_CONFETTI, render_hub

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
        label = HISTORY_LABELS.get(entry.entry_type, texts.BALANCE_OPERATION_DEFAULT_LABEL)
        sign = "+" if entry.amount > 0 else texts.BALANCE_SIGN_MINUS
        amount = abs(int(entry.amount))
        lines.append(
            texts.BALANCE_HISTORY_ROW_FORMAT.format(value_0=format_datetime(entry.created_at), value_1=label, value_2=sign, value_3=amount)
        )
    return "\n".join(lines)


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
) -> None:
    snapshot = await get_account_balance(session, user_id=user.id)
    history = await get_account_history(session, user_id=user.id, limit=5)
    visible = await get_visible_balance_topup(session, user_id=user.id)
    details = [
        texts.BALANCE_BALANCE.format(int_snapshot_real_available=int(snapshot.real_available)),
    ]
    if snapshot.bonus_available > 0:
        details.append(
            texts.BALANCE_BONUS_BALANCE.format(int_snapshot_bonus_available=int(snapshot.bonus_available))
        )
    if snapshot.reserved > 0:
        details.append(texts.PAYMENT_BALANCE.format(value_0=int(snapshot.reserved)))
    if snapshot.debt > 0:
        details.append(texts.BALANCE_INSUFFICIENT_FUNDS_DIFFERENCE.format(value_0=int(snapshot.debt)))
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
        get_balance_keyboard(has_visible_topup=visible is not None),
        trigger_message_id=trigger_message_id,
        message_effect_id=message_effect_id,
        force_new=force_new,
    )


async def _store_presentation(
    session: AsyncSession,
    payment: Payment,
    *,
    chat_id: int,
    message_id: int,
    auto_show: bool,
) -> None:
    payment.topup_context = {
        **(payment.topup_context or {}),
        "chat_id": chat_id,
        "message_id": message_id,
        "auto_show": auto_show,
    }
    await session.flush()


async def _render_topup(
    bot,
    chat_id: int,
    session: AsyncSession,
    user: User,
    payment: Payment,
) -> None:
    balance = await get_account_balance(session, user_id=user.id)
    if payment.payment_url:
        text = (
            texts.BALANCE_TOPUP_CARD.format(value_0=int(payment.amount), value_1=int(balance.available))
        )
        message_id = await render_hub(
            bot,
            chat_id,
            text,
            get_topup_payment_keyboard(payment.payment_url, payment.id),
        )
        payment.payment_url_notified_at = payment.payment_url_notified_at or now_utc()
        await _store_presentation(
            session,
            payment,
            chat_id=chat_id,
            message_id=message_id,
            auto_show=False,
        )
        return
    text = (
        texts.BALANCE_TOPUP_CREATING_LINK_CARD.format(value_0=int(payment.amount), value_1=int(balance.available))
    )
    message_id = await render_hub(
        bot,
        chat_id,
        text,
        get_topup_waiting_keyboard(payment.id),
    )
    await _store_presentation(
        session,
        payment,
        chat_id=chat_id,
        message_id=message_id,
        auto_show=True,
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

    if not await MaintenanceService.can_user_perform_action(
        session, user.telegram_id
    ):
        await _render_maintenance(target, session, back_to="menu_balance")
        return

    bot_info = await bot.get_me()
    bot_username = bot_info.username if bot_info else ""
    try:
        result = await create_balance_topup(
            session,
            user_id=user.id,
            amount=amount,
            bot_username=bot_username,
            context=context,
        )
    except AccountTopupError as exc:
        from bot.keyboards.payment import get_back_or_cancel_topups_keyboard
        keyboard = (
            get_back_or_cancel_topups_keyboard() 
            if exc.code == "too_many_unfinished_topups" 
            else get_back_button("menu_balance")
        )
        await render_hub(
            bot,
            chat_id,
            _topup_errors().get(exc.code, texts.ERROR_PAYMENT_SERVICE),
            keyboard,
        )
        return
    if context:
        result.payment.topup_context = {
            **(result.payment.topup_context or {}),
            **context,
        }
    await _render_topup(
        bot, chat_id, session, user, result.payment
    )


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
            await callback.answer(texts.BALANCE_HISTORY_LIMIT_REACHED_NOTE, show_alert=True)
        except Exception:
            pass
        return
    await _render_balance(
        callback.bot,
        callback.message.chat.id,
        session,
        db_user,
        trigger_message_id=callback.message.message_id if callback.message else None,
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
    visible = await get_visible_balance_topup(session, user_id=db_user.id)
    if visible:
        await _render_topup(
            callback.bot,
            callback.message.chat.id,
            session,
            db_user,
            visible,
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
    amounts = sorted(list(set(valid_defaults + tariff_amounts)))[: cfg.BALANCE_MAX_PRESET_OPTIONS]
    balance = await get_account_balance(session, user_id=db_user.id)
    balance_lines = texts.BALANCE_TOTAL_AVAILABLE_LABEL.format(int_balance_real_available=int(balance.real_available))
    if balance.bonus_available > 0:
        balance_lines += texts.BALANCE_BONUS_REMAINING_LABEL.format(int_balance_bonus_available=int(balance.bonus_available))

    from services.referral_bonus import is_first_topup_eligible
    is_first_eligible = await is_first_topup_eligible(session, user_id=db_user.id)

    bonus_notice = ""
    if is_first_eligible:
        bonus_lines = "\n".join(
            texts.BALANCE_NA_BONUS_BALANCE.format(amt=amt, amt____10=amt // 10)
            for amt in amounts
        )
        bonus_notice = (
            texts.BALANCE_BONUS_NA_PERVOE_TOPUP.format()+
            texts.BALANCE_VY_POLUCHITE_10_OT_SUMMY_POPOL.format()+
            texts.BALANCE_PODROBNEE_V_MENYU_PRIGLASIT_DR.format()+
            texts.BALANCE_RASCHET_BONUSA_K_SUMME.format(bonus_lines=bonus_lines)
        )

    text = (
        texts.BALANCE_TOPUP_BALANCE.format()+
        f"{balance_lines}\n"+
        f"{bonus_notice}\n"+
        texts.BALANCE_SELECT_AMOUNT_ILI_UKAZHITE_DR.format()
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
    await callback.answer(texts.PAYMENT_CREATING_LINK_NOTICE, show_alert=False)
    amount = parse_callback_id(callback.data, 1)
    if db_user is None or amount is None:
        return
    if not await MaintenanceService.can_user_perform_action(
        session, callback.from_user.id
    ):
        await _render_maintenance(callback, session, back_to="menu_balance")
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
    payment = await get_visible_balance_topup(session, user_id=db_user.id)
    if payment is None:
        await _render_balance(
            callback.bot,
            callback.message.chat.id,
            session,
            db_user,
            notice=texts.TOPUP_MISSING_NOTICE,
        )
        return
    await _render_topup(
        callback.bot,
        callback.message.chat.id,
        session,
        db_user,
        payment,
    )


async def _owned_topup(
    session: AsyncSession, user: User, payment_id: int
) -> Payment | None:
    payment = await session.get(Payment, payment_id)
    if (
        payment is None
        or payment.user_id != user.id
    ):
        return None
    return payment


@router.callback_query(F.data.startswith("balance_check:"))
async def check_topup(
    callback: CallbackQuery,
    session: AsyncSession,
    db_user: User | None = None,
) -> None:
    await callback.answer(texts.TOPUP_CHECKING_ALERT, show_alert=False)
    payment_id = parse_callback_id(callback.data, 1)
    if db_user is None or payment_id is None:
        return
    payment = await _owned_topup(session, db_user, payment_id)
    if payment is None:
        # Already answered above: never let a second answer bubble up.
        try:
            await callback.answer(texts.TOPUP_NOT_FOUND_ALERT, show_alert=True)
        except Exception:
            pass
        return
    try:
        payment = await request_topup_status_refresh(
            session, payment_id=payment.id, bot=callback.bot
        )
    except AccountTopupError:
        try:
            await callback.answer(texts.TOPUP_NOT_FOUND_ALERT, show_alert=True)
        except Exception:
            pass
        return
    if payment.fulfillment_status == "succeeded":
        ctx = payment.topup_context or {}
        is_auto_fulfilled = ctx.get("auto_fulfill_status") == "succeeded"
        ui_notified = bool(ctx.get("ui_confetti_shown"))

        if (payment.credit_notified_at and not is_auto_fulfilled) or (
            is_auto_fulfilled and ui_notified
        ):
            # Already notified, just render without effect/force_new
            await _render_balance(
                callback.bot,
                callback.message.chat.id,
                session,
                db_user,
                notice=texts.TOPUP_CREDITED_NOTICE,
            )
        else:
            # Mark notified state immediately before network render to prevent concurrent duplicates
            if not is_auto_fulfilled and not payment.credit_notified_at:
                payment.credit_notified_at = now_utc()
            if is_auto_fulfilled and not ui_notified:
                payment.topup_context = {
                    **ctx,
                    "ui_confetti_shown": True,
                }
            await session.flush()

            await _render_balance(
                callback.bot,
                callback.message.chat.id,
                session,
                db_user,
                notice=texts.TOPUP_CREDITED_NOTICE,
                message_effect_id=EFFECT_CONFETTI,
                force_new=True,
            )
    elif payment.provider_status == "succeeded":
        await render_hub(
            callback.bot,
            callback.message.chat.id,
            texts.TOPUP_CONFIRMED_NOTICE,
            get_topup_waiting_keyboard(payment.id),
        )
    elif payment.provider_status == "canceled":
        await _render_balance(
            callback.bot,
            callback.message.chat.id,
            session,
            db_user,
            notice=texts.TOPUP_PROVIDER_CANCELLED_NOTICE,
        )
    else:
        await _render_topup(
            callback.bot,
            callback.message.chat.id,
            session,
            db_user,
            payment,
        )


@router.callback_query(F.data.startswith("balance_cancel:"))
async def cancel_topup_ui(
    callback: CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
    db_user: User | None = None,
) -> None:
    await callback.answer(show_alert=False)
    await state.clear()
    payment_id = parse_callback_id(callback.data, 1)
    if db_user is None or payment_id is None:
        return
    try:
        await hide_balance_topup(
            session, user_id=db_user.id, payment_id=payment_id
        )
    except AccountTopupError:
        await callback.answer(texts.TOPUP_ALREADY_FINISHED_ALERT, show_alert=True)
        return
    await _render_balance(
        callback.bot,
        callback.message.chat.id,
        session,
        db_user,
        notice=texts.TOPUP_HIDE_NOTICE,
    )


@router.callback_query(F.data == "balance_cancel_all")
async def cancel_all_topups_ui(
    callback: CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
    db_user: User | None = None,
) -> None:
    await callback.answer(show_alert=False)
    await state.clear()
    if db_user is None:
        return
    from services.account_topup import cancel_all_unfinished_topups
    count = await cancel_all_unfinished_topups(session, user_id=db_user.id)
    if count == 0:
        await callback.answer(texts.TOPUP_ALREADY_FINISHED_ALERT, show_alert=True)
    await _render_balance(
        callback.bot,
        callback.message.chat.id,
        session,
        db_user,
        notice=texts.BALANCE_OTMENENO_SSYLOK.format(count=count) if count > 0 else None,
    )


@router.callback_query(F.data.startswith("balance_later:"))
async def return_later(
    callback: CallbackQuery,
    session: AsyncSession,
    db_user: User | None = None,
) -> None:
    await callback.answer(show_alert=False)
    payment_id = parse_callback_id(callback.data, 1)
    if db_user is None or payment_id is None:
        return
    payment = await _owned_topup(session, db_user, payment_id)
    if payment:
        payment.topup_context = {
            **(payment.topup_context or {}),
            "auto_show": False,
        }
    await _render_balance(
        callback.bot,
        callback.message.chat.id,
        session,
        db_user,
        notice=texts.TOPUP_SAVED_NOTICE,
    )
