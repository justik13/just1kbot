"""Balance and payment history handler for Just1k 2.0 Telegram Bot Hub."""

from __future__ import annotations

import logging
from decimal import Decimal

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy.ext.asyncio import AsyncSession

from bot import texts
from bot.keyboards.payment import (
    get_balance_amounts_keyboard,
    get_balance_history_keyboard,
    get_balance_keyboard,
)
from database.models import User
from database.repositories.account_ledger_repo import (
    get_account_balance,
    get_account_history,
)
from database.repositories.payments_repo import get_user_payments
from services.yookassa_service import YooKassaService
from utils.formatters import format_datetime
from utils.telegram import render_hub

router = Router()
logger = logging.getLogger(__name__)


@router.callback_query(F.data == "menu_balance")
async def show_balance_menu(
    callback: CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
    db_user: User | None = None,
):
    await callback.answer(show_alert=False)
    await state.clear()

    if not db_user:
        return

    balance = await get_account_balance(session, user_id=db_user.id)
    real_bal = int(balance.real_available)
    bonus_bal = int(balance.bonus_available)

    text = (
        "<b>Управление балансом</b>\n\n"
        f"<b>Реальный баланс:</b> {real_bal} ₽\n"
        f"<b>Бонусный баланс:</b> {bonus_bal} ₽\n\n"
        "<i>С баланса можно оплачивать и продлевать подписку. "
        "Бонусный баланс расходуется автоматически при оплате услуг.</i>"
    )

    await render_hub(
        callback.bot,
        callback.message.chat.id,
        text,
        get_balance_keyboard(),
    )


class BalanceStates(StatesGroup):
    waiting_for_custom_amount = State()


@router.callback_query(F.data == "balance_topup")
async def show_topup_presets(
    callback: CallbackQuery,
    state: FSMContext,
):
    await callback.answer(show_alert=False)
    await state.clear()

    text = (
        "<b>Пополнение счета</b>\n\n"
        "Выберите сумму пополнения или введите свою:"
    )
    await render_hub(
        callback.bot,
        callback.message.chat.id,
        text,
        get_balance_amounts_keyboard([99, 250, 500, 1000]),
    )


@router.callback_query(F.data == "balance_custom_amount")
async def ask_custom_amount(
    callback: CallbackQuery,
    state: FSMContext,
):
    await callback.answer(show_alert=False)
    await state.set_state(BalanceStates.waiting_for_custom_amount)
    builder = InlineKeyboardBuilder()
    builder.button(text=texts.BTN_BACK, callback_data="balance_topup")
    builder.adjust(1)
    text = (
        "<b>Пополнение на произвольную сумму</b>\n\n"
        "Введите желаемую сумму пополнения в рублях (от 50 до 15 000 ₽):"
    )
    await render_hub(
        callback.bot,
        callback.message.chat.id,
        text,
        builder.as_markup(),
    )


@router.message(BalanceStates.waiting_for_custom_amount, F.text)
async def process_custom_amount_text(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
):
    try:
        await message.delete()
    except Exception:
        pass

    raw = (message.text or "").strip()
    try:
        amount_rub = int(raw)
        if not (50 <= amount_rub <= 15000):
            raise ValueError()
    except ValueError:
        builder = InlineKeyboardBuilder()
        builder.button(text=texts.BTN_BACK, callback_data="balance_topup")
        builder.adjust(1)
        text = (
            "⚠️ <b>Некорректная сумма</b>\n\n"
            "Пожалуйста, введите целое число от 50 до 15 000 ₽:"
        )
        await render_hub(
            message.bot,
            message.chat.id,
            text,
            builder.as_markup(),
            session=session,
        )
        return

    await state.clear()
    from database.repositories.users_repo import get_user_by_telegram_id

    db_user = await get_user_by_telegram_id(session, message.from_user.id)
    if not db_user:
        return

    payment_url, payment_id = await YooKassaService.create_topup_payment(
        session=session,
        user=db_user,
        amount_rub=Decimal(amount_rub),
        description=f"Пополнение Just1k на {amount_rub} ₽",
    )

    builder = InlineKeyboardBuilder()
    if payment_url:
        builder.button(
            text=f"💳 Оплатить {amount_rub} ₽",
            url=payment_url,
        )
    builder.button(
        text=texts.BTN_BACK,
        callback_data="menu_balance",
    )
    builder.adjust(1)

    text = (
        f"<b>Счет на пополнение #{payment_id or 'новый'}</b>\n\n"
        f"<b>Сумма к оплате:</b> {amount_rub} ₽\n\n"
        "После успешной оплаты баланс обновится автоматически."
    )

    await render_hub(
        message.bot,
        message.chat.id,
        text,
        builder.as_markup(),
        session=session,
    )


@router.callback_query(F.data.startswith("balance_create:"))
async def process_balance_create(
    callback: CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
    db_user: User | None = None,
):
    await callback.answer(show_alert=False)
    await state.clear()

    if not db_user:
        return

    parts = callback.data.split(":")
    try:
        amount_rub = int(parts[1])
    except (IndexError, ValueError):
        amount_rub = 100

    payment_url, payment_id = await YooKassaService.create_topup_payment(
        session=session,
        user=db_user,
        amount_rub=Decimal(amount_rub),
        description=f"Пополнение Just1k на {amount_rub} ₽",
    )

    builder = InlineKeyboardBuilder()
    if payment_url:
        builder.button(
            text=f"💳 Оплатить {amount_rub} ₽",
            url=payment_url,
        )
    builder.button(
        text=texts.BTN_BACK,
        callback_data="menu_balance",
    )
    builder.adjust(1)

    text = (
        f"<b>Счет на пополнение #{payment_id or 'новый'}</b>\n\n"
        f"<b>Сумма к оплате:</b> {amount_rub} ₽\n\n"
        "После успешной оплаты баланс обновится автоматически."
    )

    await render_hub(
        callback.bot,
        callback.message.chat.id,
        text,
        builder.as_markup(),
    )


@router.callback_query(F.data.in_({"balance_history_topups"}) | F.data.startswith("balance_topups_page:"))
async def show_topups_history(
    callback: CallbackQuery,
    session: AsyncSession,
    db_user: User | None = None,
):
    await callback.answer(show_alert=False)

    if not db_user:
        return

    page = 0
    if ":" in callback.data:
        try:
            page = max(0, int(callback.data.split(":")[1]))
        except (ValueError, IndexError):
            page = 0

    per_page = 5
    offset = page * per_page
    payments = await get_user_payments(session, db_user.id, limit=per_page + 1, offset=offset)

    has_next = len(payments) > per_page
    page_payments = payments[:per_page]
    total_pages = page + 2 if has_next else page + 1

    if not page_payments:
        text = "<b>История пополнений (реальные деньги):</b>\n\nПополнений счета пока не было."
    else:
        text = f"<b>История пополнений (страница {page + 1}):</b>\n\n"
        for p in page_payments:
            amount = getattr(p, "amount_rub", getattr(p, "amount", 0))
            status = getattr(p, "status", "pending")
            status_text = texts.PAYMENT_STATUS_NAMES.get(status, status)
            status_icon = texts.PAYMENT_STATUS_ICONS.get(status, "⏳")
            dt = format_datetime(getattr(p, "created_at", None))
            text += f"{status_icon} <b>+{amount} ₽</b> — {status_text} ({dt})\n"

    await render_hub(
        callback.bot,
        callback.message.chat.id,
        text,
        get_balance_history_keyboard(
            page=page,
            total_pages=total_pages,
            back_target="menu_balance",
            prefix="balance_topups_page",
        ),
    )


@router.callback_query(F.data.in_({"balance_history_ops"}) | F.data.startswith("balance_ops_page:"))
async def show_operations_history(
    callback: CallbackQuery,
    session: AsyncSession,
    db_user: User | None = None,
):
    await callback.answer(show_alert=False)

    if not db_user:
        return

    page = 0
    if ":" in callback.data:
        try:
            page = max(0, int(callback.data.split(":")[1]))
        except (ValueError, IndexError):
            page = 0

    per_page = 5
    offset = page * per_page
    entries = await get_account_history(session, user_id=db_user.id, limit=per_page + 1, offset=offset)

    has_next = len(entries) > per_page
    page_entries = entries[:per_page]
    total_pages = page + 2 if has_next else page + 1

    if not page_entries:
        text = "<b>История операций:</b>\n\nИстория операций пуста."
    else:
        text = f"<b>История операций (страница {page + 1}):</b>\n\n"
        for e in page_entries:
            amount = e.amount_rub
            sign = "+" if amount >= 0 else ""
            dt = format_datetime(e.created_at)
            desc = e.description or e.entry_type
            text += f"• <b>{sign}{amount} ₽</b> — {desc} ({dt})\n"

    await render_hub(
        callback.bot,
        callback.message.chat.id,
        text,
        get_balance_history_keyboard(
            page=page,
            total_pages=total_pages,
            back_target="menu_balance",
            prefix="balance_ops_page",
        ),
    )
