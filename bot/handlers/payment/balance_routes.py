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
)
from database.repositories.tariffs_repo import get_active_tariffs
from services.account_topup import (
    AccountTopupError,
    create_balance_topup,
    get_visible_balance_topup,
    hide_balance_topup,
    request_topup_status_refresh,
)
from utils.callbacks import parse_callback_id
from utils.datetime_helpers import now_utc
from utils.formatters import format_datetime
from utils.telegram import render_hub


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
        "topup_creation_rate_limited": texts.TOPUP_ERROR_RATE_LIMIT,
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
        and int(tariff.price_rub) > 0
        and int(tariff.price_rub) <= cfg.BALANCE_MAX_PRESET_RUB
    }
    return sorted(prices)[: cfg.BALANCE_MAX_PRESET_OPTIONS]


def _history_lines(entries: list) -> str:
    if not entries:
        return texts.BALANCE_HISTORY_EMPTY
    lines = []
    for entry in entries:
        label = HISTORY_LABELS.get(entry.entry_type, texts.RUNTIME_BOT_HANDLERS_PAYMENT_BALANCE_ROUTES_L83_1)
        sign = "+" if entry.amount > 0 else texts.RUNTIME_BOT_HANDLERS_PAYMENT_BALANCE_ROUTES_L84_1
        amount = abs(int(entry.amount))
        lines.append(
            texts.RUNTIME_BOT_HANDLERS_PAYMENT_BALANCE_ROUTES_L87_1.format(value_0=format_datetime(entry.created_at), value_1=label, value_2=sign, value_3=amount)
        )
    return "\n".join(lines)


async def _render_balance(
    bot,
    chat_id: int,
    session: AsyncSession,
    user: User,
    *,
    notice: str | None = None,
) -> None:
    snapshot = await get_account_balance(session, user_id=user.id)
    history = await get_account_history(session, user_id=user.id, limit=5)
    visible = await get_visible_balance_topup(session, user_id=user.id)
    details = [
        f"💰 Реальный баланс: <b>{int(snapshot.real_available)} ₽</b>",
        f"🎁 Бонусный баланс: <b>{int(snapshot.bonus_available)} ₽</b>",
    ]
    if snapshot.reserved > 0:
        details.append(texts.RUNTIME_BOT_HANDLERS_PAYMENT_BALANCE_ROUTES_L106_1.format(value_0=int(snapshot.reserved)))
    if snapshot.debt > 0:
        details.append(texts.RUNTIME_BOT_HANDLERS_PAYMENT_BALANCE_ROUTES_L108_1.format(value_0=int(snapshot.debt)))
    prefix = f"{notice}\n\n" if notice else ""
    text = (
        texts.RUNTIME_BOT_HANDLERS_PAYMENT_BALANCE_ROUTES_L111_1.format(value_0=prefix)
        + "\n".join(details)
        + texts.RUNTIME_BOT_HANDLERS_PAYMENT_BALANCE_ROUTES_L113_1
        + _history_lines(history)
    )

    await render_hub(
        bot,
        chat_id,
        text,
        get_balance_keyboard(has_visible_topup=visible is not None),
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
            texts.RUNTIME_BOT_HANDLERS_PAYMENT_BALANCE_ROUTES_L151_1.format(value_0=int(payment.amount), value_1=int(balance.available))
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
        texts.RUNTIME_BOT_HANDLERS_PAYMENT_BALANCE_ROUTES_L172_1.format(value_0=int(payment.amount), value_1=int(balance.available))
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
    bot_info = await target.bot.get_me()
    try:
        result = await create_balance_topup(
            session,
            user_id=user.id,
            amount=amount,
            bot_username=bot_info.username,
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
            target.bot,
            target.chat.id,
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
        target.bot, target.chat.id, session, user, result.payment
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
        await callback.answer(texts.UI_BOT_HANDLERS_PAYMENT_BALANCE_ROUTES_L246_1, show_alert=True)
        return
    await _render_balance(callback.bot, callback.message.chat.id, session, db_user)


@router.callback_query(F.data == "balance_history")
async def show_balance_history(
    callback: CallbackQuery,
    session: AsyncSession,
    db_user: User | None = None,
) -> None:
    await callback.answer(show_alert=False)
    if db_user is None:
        return
    entries = await get_account_history(session, user_id=db_user.id, limit=30)
    await render_hub(
        callback.bot,
        callback.message.chat.id,
        texts.BALANCE_HISTORY_TITLE.format(history=_history_lines(entries)),
        get_back_button("menu_balance"),
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
    amounts = topup_presets(tariffs)
    balance = await get_account_balance(session, user_id=db_user.id)
    text = (
        f"➕ <b>Пополнение баланса</b>\n\n"
        f"💰 Реальный баланс: <b>{int(balance.real_available)} ₽</b>\n"
        f"🎁 Бонусный баланс: <b>{int(balance.bonus_available)} ₽</b>\n\n"
        f"Выберите сумму или укажите другую целую сумму в рублях."
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
    await callback.answer(texts.TOPUP_CREATING_ALERT, show_alert=False)
    amount = parse_callback_id(callback.data, 1)
    if db_user is None or amount is None:
        return
    await _create_and_render_topup(callback.message, session, db_user, amount)


@router.callback_query(F.data == "balance_custom_amount")
async def request_custom_amount(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    await callback.answer(show_alert=False)
    await state.set_state(BalanceStates.enter_custom_amount)
    await state.set_data({})
    await render_hub(
        callback.bot,
        callback.message.chat.id,
        texts.TOPUP_CUSTOM_AMOUNT_PROMPT.format(
            minimum=get_settings().BALANCE_MIN_TOPUP_RUB,
            maximum=get_settings().BALANCE_MAX_CUSTOM_TOPUP_RUB,
        ),
        get_back_button("menu_balance"),
    )


@router.message(BalanceStates.enter_custom_amount)
async def accept_custom_amount(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
    db_user: User | None = None,
) -> None:
    raw = (message.text or "").strip()
    if not raw.isascii() or not raw.isdigit():
        await render_hub(
            message.bot,
            message.chat.id,
            texts.TOPUP_INVALID_AMOUNT,
            get_back_button("menu_balance"),
        )
        return
    if db_user is None:
        await state.clear()
        return
    data = await state.get_data()
    minimum = int(data.get("balance_minimum") or get_settings().BALANCE_MIN_TOPUP_RUB)
    amount = int(raw)
    if amount < minimum:
        await render_hub(
            message.bot,
            message.chat.id,
            texts.TOPUP_OPERATION_MINIMUM.format(minimum=minimum),
            get_back_button("menu_balance"),
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
        await callback.answer(texts.TOPUP_NOT_FOUND_ALERT, show_alert=True)
        return
    try:
        payment = await request_topup_status_refresh(
            session, payment_id=payment.id
        )
    except AccountTopupError:
        await callback.answer(texts.TOPUP_NOT_FOUND_ALERT, show_alert=True)
        return
    if payment.fulfillment_status == "succeeded":
        await _render_balance(
            callback.bot,
            callback.message.chat.id,
            session,
            db_user,
            notice=texts.TOPUP_CREDITED_NOTICE,
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
        notice=f"Отменено {count} ссылок." if count > 0 else None,
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
