import logging
from uuid import uuid4

from aiogram import F, Router
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
    AccountLedgerInvariantError,
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
            f"📉 <b>Списание бонусных средств</b>\n\n"
            f"Введите сумму списания бонусных рублей (целое число от 1 до {MAX_BALANCE_ADJUSTMENT}):",
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

    await state.update_data(amount=amount, action_type="topup")
    await state.set_state(AdminStates.entering_user_balance_reason)

    await render_hub(
        message.bot,
        message.chat.id,
        f"📝 <b>Причина начисления (+{amount} ₽)</b>\n\n"
        f"Введите текстовое примечание (причину начисления) для лога аудита:\n"
        f"<i>(Или отправьте <code>-</code> дефис для абстрактного описания)</i>",
        get_back_button(f"admin_user_balance:{telegram_id}"),
        trigger_message_id=message.message_id,
    )


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

    balance_info = await get_account_balance(session, user_id=user.id)
    if balance_info.bonus_available < amount:
        await render_hub(
            message.bot,
            message.chat.id,
            f"⚠️ <b>У пользователя недостаточно бонусных средств.</b>\n"
            f"Доступно для списания бонусных средств: <b>{int(balance_info.bonus_available)} ₽</b>",
            get_back_button(f"admin_user_balance:{telegram_id}"),
            trigger_message_id=message.message_id,
        )
        await state.clear()
        return

    await state.update_data(amount=amount, action_type="deduct")
    await state.set_state(AdminStates.entering_user_balance_reason)

    await render_hub(
        message.bot,
        message.chat.id,
        f"📝 <b>Причина списания (-{amount} ₽)</b>\n\n"
        f"Введите текстовое примечание (причину списания) для лога аудита:\n"
        f"<i>(Или отправьте <code>-</code> дефис для абстрактного описания)</i>",
        get_back_button(f"admin_user_balance:{telegram_id}"),
        trigger_message_id=message.message_id,
    )


@router.message(AdminStates.entering_user_balance_reason)
async def process_balance_reason(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
):
    if not is_admin(message.from_user.id):
        await state.clear()
        return

    data = await state.get_data()
    telegram_id = data.get("target_telegram_id")
    amount = data.get("amount")
    action_type = data.get("action_type")

    if not telegram_id or not amount or not action_type:
        await state.clear()
        return

    reason = message.text.strip() if message.text and message.text.strip() != "-" else "Корректировка администратором"
    adjustment_id = uuid4().hex
    await state.update_data(reason=reason, adjustment_id=adjustment_id)

    user = await get_user_by_telegram_id(session, telegram_id)
    if not user:
        await state.clear()
        return

    username_str = f"@{user.username}" if user.username else f"ID: {user.telegram_id}"
    change_str = f"+{amount} ₽" if action_type == "topup" else f"-{amount} ₽"

    from utils.formatters import format_admin_breadcrumbs
    header = format_admin_breadcrumbs("👥 Пользователи", f"ID {user.telegram_id}", "Баланс")

    text = (
        f"{header}"
        f"⚠️ <b>Подтверждение изменения баланса:</b>\n\n"
        f"Пользователь: <b>{safe(username_str)}</b>\n"
        f"Тип счета: <b>🎁 Бонусный баланс (RUB)</b>\n"
        f"Изменение: <b>{change_str}</b>\n"
        f"Причина: <b>{safe(reason)}</b>\n\n"
        f"Вы уверены, что хотите применить данное изменение?"
    )

    builder = InlineKeyboardBuilder()
    builder.button(
        text="✅ Подтвердить и применить",
        callback_data="confirm_admin_balance_apply",
    )
    builder.button(
        text="❌ Отмена",
        callback_data=f"admin_user_balance:{telegram_id}",
    )
    builder.adjust(1)

    await state.set_state(AdminStates.confirming_user_balance)

    await render_hub(
        message.bot,
        message.chat.id,
        text,
        builder.as_markup(),
        trigger_message_id=message.message_id,
        parse_mode="HTML",
    )


@router.callback_query(F.data == "confirm_admin_balance_apply")
async def apply_user_balance_change(
    callback: CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
):
    if not is_admin(callback.from_user.id):
        await callback.answer(texts.ERROR_ACCESS_DENIED, show_alert=True)
        return

    data = await state.get_data()
    telegram_id = data.get("target_telegram_id")
    amount = data.get("amount")
    action_type = data.get("action_type")
    reason = data.get("reason", "Корректировка администратором")
    adjustment_id = data.get("adjustment_id")

    if not telegram_id or not amount or not action_type or not adjustment_id:
        await callback.answer("Ошибка: данные устарели.", show_alert=True)
        await state.clear()
        return

    user = await get_user_by_telegram_id(session, telegram_id)
    if not user:
        await callback.answer(texts.ERROR_USER_NOT_FOUND, show_alert=True)
        await state.clear()
        return

    signed_amount = amount if action_type == "topup" else -amount
    idempotency_key = f"admin_adj:{adjustment_id}"

    try:
        await create_admin_adjustment(
            session,
            user_id=user.id,
            signed_amount=signed_amount,
            idempotency_key=idempotency_key,
            metadata={"admin_id": callback.from_user.id, "reason": reason},
        )
    except AccountLedgerInvariantError:
        # Authoritative under-lock rejection (TOCTOU-safe): the step-one
        # preview may have gone stale while the admin was typing the reason.
        await session.rollback()
        fresh = await get_account_balance(session, user_id=user.id)
        await callback.answer(
            f"⚠️ Недостаточно бонусных средств. Доступно: {int(fresh.bonus_available)} ₽",
            show_alert=True,
        )
        await state.clear()
        return
    except Exception as exc:
        logger.error("Failed to apply admin balance adjustment for user %s: %s", user.id, exc)
        await callback.answer("⚠️ Ошибка применения баланса.", show_alert=True)
        await state.clear()
        return

    audit_action = "ADMIN_BALANCE_TOPUP" if action_type == "topup" else "ADMIN_BALANCE_DEDUCT"
    await AuditService.log_action(
        session,
        admin_id=callback.from_user.id,
        action=audit_action,
        target_type="user",
        target_id=user.id,
        details={
            "amount": abs(int(signed_amount)),
            "reason": reason,
            "signed_amount": int(signed_amount),
        },
    )

    try:
        builder = InlineKeyboardBuilder()
        builder.button(text="💳 В баланс", callback_data="menu_balance")
        builder.button(text="✅ Прочитано", callback_data="dismiss_notification")
        builder.adjust(2)

        msg_text = (
            f"🎁 <b>Вам начислен бонусный баланс: +{amount} ₽!</b>\n"
            f"Причина: <i>{safe(reason)}</i>"
            if action_type == "topup"
            else f"💳 <b>С вашего бонусного баланса списано: -{amount} ₽.</b>\nПричина: <i>{safe(reason)}</i>"
        )
        await callback.bot.send_message(
            user.telegram_id,
            msg_text,
            reply_markup=builder.as_markup(),
            parse_mode="HTML",
        )
    except Exception as e:
        logger.debug("Failed to notify user %s about balance change: %s", user.telegram_id, e)

    await state.clear()
    await callback.answer("✅ Успешно приведено в действие!", show_alert=True)

    from utils.formatters import format_admin_breadcrumbs
    header = format_admin_breadcrumbs("👥 Пользователи", f"ID {user.telegram_id}", "Баланс")
    change_formatted = f"+{amount} ₽" if action_type == "topup" else f"-{amount} ₽"

    await render_hub(
        callback.bot,
        callback.message.chat.id,
        f"{header}✅ <b>Успешно!</b> Бонусный баланс пользователя {user.telegram_id} изменен на <b>{change_formatted}</b>.\nПричина: <i>{safe(reason)}</i>",
        get_back_button(f"admin_user_card:{user.telegram_id}"),
        parse_mode="HTML",
    )
