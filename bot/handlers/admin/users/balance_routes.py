import logging
from uuid import uuid4

from aiogram import Router, F
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy.ext.asyncio import AsyncSession

from bot import texts
from bot.keyboards import get_back_button
from bot.keyboards.admin.users import get_admin_user_balance_keyboard
from bot.states import AdminStates
from database.repositories.account_ledger_repo import (
    create_admin_adjustment,
    get_account_balance,
)
from database.repositories.users_repo import get_user_by_telegram_id
from services.audit_service import AuditService
from utils.admin import is_admin
from utils.callbacks import parse_callback_id
from utils.telegram import render_hub, safe

router = Router()
logger = logging.getLogger(__name__)

MAX_BALANCE_ADJUSTMENT = 1_000_000


@router.callback_query(F.data.startswith("admin_user_balance:"))
async def show_user_balance_menu(
    callback: CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
):
    if not is_admin(callback.from_user.id):
        await callback.answer(texts.ERROR_ACCESS_DENIED, show_alert=True)
        return

    telegram_id = parse_callback_id(callback.data, 1)
    if telegram_id is None:
        await callback.answer(texts.ERROR_USER_NOT_FOUND, show_alert=True)
        return

    await state.clear()

    user = await get_user_by_telegram_id(session, telegram_id)
    if not user:
        await callback.answer(texts.ERROR_USER_NOT_FOUND, show_alert=True)
        return

    balance_info = await get_account_balance(session, user_id=user.id)
    real_rub = int(balance_info.real_available)
    bonus_rub = int(balance_info.bonus_available)

    username_str = f"@{user.username}" if user.username else f"ID: {user.telegram_id}"
    text = (
        f"💳 <b>Управление балансом пользователя</b>\n\n"
        f"Пользователь: <b>{safe(username_str)}</b>\n"
        f"💰 Реальный баланс: <b>{real_rub} ₽</b>\n"
        f"🎁 Бонусный баланс: <b>{bonus_rub} ₽</b>\n\n"
        f"Выберите действие ниже:"
    )


    try:
        await callback.message.edit_text(
            text,
            reply_markup=get_admin_user_balance_keyboard(telegram_id),
            parse_mode="HTML",
        )
    except TelegramBadRequest as e:
        logger.debug(f"show_user_balance_menu edit_text failed: {e}")

    await callback.answer(show_alert=False)


@router.callback_query(F.data.startswith("admin_balance_topup:"))
async def start_balance_topup(
    callback: CallbackQuery,
    state: FSMContext,
):
    if not is_admin(callback.from_user.id):
        await callback.answer(texts.ERROR_ACCESS_DENIED, show_alert=True)
        return

    telegram_id = parse_callback_id(callback.data, 1)
    if telegram_id is None:
        await callback.answer(texts.ERROR_USER_NOT_FOUND, show_alert=True)
        return

    await state.clear()
    await state.update_data(target_telegram_id=telegram_id)
    await state.set_state(AdminStates.entering_user_balance_topup)

    try:
        await callback.message.edit_text(
            f"💰 <b>Начисление бонусного баланса</b>\n\n"
            f"Введите сумму начисления в рублях (целое число от 1 до {MAX_BALANCE_ADJUSTMENT}):",
            reply_markup=get_back_button(f"admin_user_balance:{telegram_id}"),
            parse_mode="HTML",
        )
    except TelegramBadRequest as e:
        logger.debug(f"start_balance_topup edit_text failed: {e}")

    await callback.answer(show_alert=False)


@router.callback_query(F.data.startswith("admin_balance_deduct:"))
async def start_balance_deduct(
    callback: CallbackQuery,
    state: FSMContext,
):
    if not is_admin(callback.from_user.id):
        await callback.answer(texts.ERROR_ACCESS_DENIED, show_alert=True)
        return

    telegram_id = parse_callback_id(callback.data, 1)
    if telegram_id is None:
        await callback.answer(texts.ERROR_USER_NOT_FOUND, show_alert=True)
        return

    await state.clear()
    await state.update_data(target_telegram_id=telegram_id)
    await state.set_state(AdminStates.entering_user_balance_deduct)

    try:
        await callback.message.edit_text(
            f"📉 <b>Списание средств с баланса</b>\n\n"
            f"Введите сумму списания в рублях (целое число от 1 до {MAX_BALANCE_ADJUSTMENT}):",
            reply_markup=get_back_button(f"admin_user_balance:{telegram_id}"),
            parse_mode="HTML",
        )
    except TelegramBadRequest as e:
        logger.debug(f"start_balance_deduct edit_text failed: {e}")

    await callback.answer(show_alert=False)


@router.message(AdminStates.entering_user_balance_topup)
async def process_balance_topup(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
):
    if not is_admin(message.from_user.id):
        await state.clear()
        return

    data = await state.get_data()
    telegram_id = data.get("target_telegram_id")
    if not telegram_id:
        await state.clear()
        return

    if message.text and message.text.startswith("/"):
        await state.clear()
        await render_hub(
            message.bot,
            message.chat.id,
            texts.ERROR_OPERATION_CANCELLED,
            get_back_button(f"admin_user_balance:{telegram_id}"),
            trigger_message_id=message.message_id,
        )
        return

    try:
        amount = int(message.text.strip())
    except (ValueError, AttributeError):
        await render_hub(
            message.bot,
            message.chat.id,
            texts.ERROR_POSITIVE_NUMBER,
            get_back_button(f"admin_user_balance:{telegram_id}"),
            trigger_message_id=message.message_id,
        )
        return

    if amount <= 0 or amount > MAX_BALANCE_ADJUSTMENT:
        await render_hub(
            message.bot,
            message.chat.id,
            f"⚠️ Сумма должна быть больше 0 и не превышать {MAX_BALANCE_ADJUSTMENT} ₽",
            get_back_button(f"admin_user_balance:{telegram_id}"),
            trigger_message_id=message.message_id,
        )
        return

    user = await get_user_by_telegram_id(session, telegram_id)
    if not user:
        await render_hub(
            message.bot,
            message.chat.id,
            texts.ERROR_USER_NOT_FOUND,
            get_back_button("admin_users"),
            trigger_message_id=message.message_id,
        )
        await state.clear()
        return

    idempotency_key = f"admin_topup_{message.from_user.id}_{user.id}_{uuid4().hex[:10]}"
    await create_admin_adjustment(
        session,
        user_id=user.id,
        signed_amount=amount,
        idempotency_key=idempotency_key,
        metadata={"admin_id": message.from_user.id, "reason": "manual_topup"},
    )

    await AuditService.log_action(
        session,
        message.from_user.id,
        "TOPUP_USER_BALANCE",
        "User",
        user.id,
        f"Added +{amount} RUB to user {user.telegram_id}",
    )

    # Notify target user if possible
    try:
        builder = InlineKeyboardBuilder()
        builder.button(
            text="💳 В баланс",
            callback_data="menu_balance",
        )
        builder.button(
            text="✅ Прочитано",
            callback_data="dismiss_notification",
        )
        builder.adjust(2)

        await message.bot.send_message(
            user.telegram_id,
            f"🎁 <b>Вам начислен бонусный баланс: +{amount} ₽!</b>\n"
            f"Вы можете использовать его для покупки или продления подписки.",
            reply_markup=builder.as_markup(),
            parse_mode="HTML",
        )
    except Exception as e:
        logger.debug(f"Failed to notify user {user.telegram_id} about topup: {e}")

    await render_hub(
        message.bot,
        message.chat.id,
        f"✅ <b>Успешно!</b> Пользователю {user.telegram_id} начислено <b>+{amount} ₽</b>.",
        get_back_button(f"admin_user_card:{user.telegram_id}"),
        trigger_message_id=message.message_id,
    )

    await state.clear()


@router.message(AdminStates.entering_user_balance_deduct)
async def process_balance_deduct(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
):
    if not is_admin(message.from_user.id):
        await state.clear()
        return

    data = await state.get_data()
    telegram_id = data.get("target_telegram_id")
    if not telegram_id:
        await state.clear()
        return

    if message.text and message.text.startswith("/"):
        await state.clear()
        await render_hub(
            message.bot,
            message.chat.id,
            texts.ERROR_OPERATION_CANCELLED,
            get_back_button(f"admin_user_balance:{telegram_id}"),
            trigger_message_id=message.message_id,
        )
        return

    try:
        amount = int(message.text.strip())
    except (ValueError, AttributeError):
        await render_hub(
            message.bot,
            message.chat.id,
            texts.ERROR_POSITIVE_NUMBER,
            get_back_button(f"admin_user_balance:{telegram_id}"),
            trigger_message_id=message.message_id,
        )
        return

    if amount <= 0 or amount > MAX_BALANCE_ADJUSTMENT:
        await render_hub(
            message.bot,
            message.chat.id,
            f"⚠️ Сумма должна быть больше 0 и не превышать {MAX_BALANCE_ADJUSTMENT} ₽",
            get_back_button(f"admin_user_balance:{telegram_id}"),
            trigger_message_id=message.message_id,
        )
        return

    user = await get_user_by_telegram_id(session, telegram_id)
    if not user:
        await render_hub(
            message.bot,
            message.chat.id,
            texts.ERROR_USER_NOT_FOUND,
            get_back_button("admin_users"),
            trigger_message_id=message.message_id,
        )
        await state.clear()
        return

    idempotency_key = f"admin_deduct_{message.from_user.id}_{user.id}_{uuid4().hex[:10]}"
    await create_admin_adjustment(
        session,
        user_id=user.id,
        signed_amount=-amount,
        idempotency_key=idempotency_key,
        metadata={"admin_id": message.from_user.id, "reason": "manual_deduction"},
    )

    await AuditService.log_action(
        session,
        message.from_user.id,
        "DEDUCT_USER_BALANCE",
        "User",
        user.id,
        f"Deducted -{amount} RUB from user {user.telegram_id}",
    )

    # Notify target user if possible
    try:
        builder = InlineKeyboardBuilder()
        builder.button(
            text="💳 В баланс",
            callback_data="menu_balance",
        )
        builder.button(
            text="✅ Прочитано",
            callback_data="dismiss_notification",
        )
        builder.adjust(2)

        await message.bot.send_message(
            user.telegram_id,
            f"💳 <b>С вашего баланса списано: -{amount} ₽.</b>",
            reply_markup=builder.as_markup(),
            parse_mode="HTML",
        )
    except Exception as e:
        logger.debug(f"Failed to notify user {user.telegram_id} about deduction: {e}")

    await render_hub(
        message.bot,
        message.chat.id,
        f"✅ <b>Успешно!</b> С баланса пользователя {user.telegram_id} списано <b>-{amount} ₽</b>.",
        get_back_button(f"admin_user_card:{user.telegram_id}"),
        trigger_message_id=message.message_id,
    )

    await state.clear()
