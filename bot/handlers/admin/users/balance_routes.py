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

    username_str = f"@{user.username}" if user.username else texts.ADMIN_USER_ID_FORMAT.format(telegram_id=user.telegram_id)
    text = (
        texts.ADMIN_USERS_BALANCE_MANAGE_BALANCE_USER.format()+
        texts.ADMIN_USERS_BALANCE_USER.format(safe_username_str=safe(username_str))+
        texts.ADMIN_USERS_BALANCE_REAL_BALANCE.format(real_rub=real_rub)+
        texts.ADMIN_USERS_BALANCE_BONUS_BALANCE.format(bonus_rub=bonus_rub)+
        texts.ADMIN_USERS_BALANCE_SELECT_ACTION_BELOW.format()
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
            texts.ADMIN_USERS_BALANCE_GRANT_BONUS_BALANCE.format()+
            texts.ADMIN_USERS_BALANCE_ENTER_AMOUNT_GRANT_V_R.format(MAX_BALANCE_ADJUSTMENT=MAX_BALANCE_ADJUSTMENT),
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
            texts.ADMIN_USERS_BALANCE_REDUCE_BONUS_FUNDS.format()+
            texts.ADMIN_USERS_BALANCE_ENTER_AMOUNT_REDUCE_BONUSN.format(MAX_BALANCE_ADJUSTMENT=MAX_BALANCE_ADJUSTMENT),
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
            texts.ADMIN_USERS_BALANCE_AMOUNT_DOLZHNA_BYT_BOLSHE_0_I_N.format(MAX_BALANCE_ADJUSTMENT=MAX_BALANCE_ADJUSTMENT),
            get_back_button(f"admin_user_balance:{telegram_id}"),
            trigger_message_id=message.message_id,
        )
        return

    await state.update_data(amount=amount, action_type="topup")
    await state.set_state(AdminStates.entering_user_balance_reason)

    await render_hub(
        message.bot,
        message.chat.id,
        texts.ADMIN_USERS_BALANCE_REASON_GRANT.format(amount=amount)+
        texts.ADMIN_USERS_BALANCE_ENTER_TEKSTOVOE_NOTE.format()+
        texts.ADMIN_USERS_BALANCE_ILI_OTPRAVTE_DEFIS_FOR_ABSTRA.format(),
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
            texts.ADMIN_USERS_BALANCE_AMOUNT_DOLZHNA_BYT_BOLSHE_0_I_N.format(MAX_BALANCE_ADJUSTMENT=MAX_BALANCE_ADJUSTMENT),
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
            texts.ADMIN_USER_BALANCE_INSUFFICIENT_FOR_DEBIT.format()+
            texts.ADMIN_USERS_BALANCE_DOSTUPNO_FOR_REDUCE_BONUSN.format(int_balance_info_bonus_available=int(balance_info.bonus_available)),
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
        texts.ADMIN_USERS_BALANCE_REASON_REDUCE.format(amount=amount)+
        texts.ADMIN_USER_DEBIT_REASON_PROMPT.format()+
        texts.ADMIN_USERS_BALANCE_ILI_OTPRAVTE_DEFIS_FOR_ABSTRA.format(),
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

    reason = message.text.strip() if message.text and message.text.strip() != "-" else texts.ADMIN_USERS_BALANCE_KORREKTIROVKA_ADMINISTRATOROM
    adjustment_id = uuid4().hex
    await state.update_data(reason=reason, adjustment_id=adjustment_id)

    user = await get_user_by_telegram_id(session, telegram_id)
    if not user:
        await state.clear()
        return

    username_str = f"@{user.username}" if user.username else texts.ADMIN_USER_ID_FORMAT.format(telegram_id=user.telegram_id)
    change_str = f"+{amount} ₽" if action_type == "topup" else f"-{amount} ₽"

    from bot.formatters import format_admin_breadcrumbs
    header = format_admin_breadcrumbs(texts.BTN_USERS, texts.ADMIN_USER_ID_NO_COLON_FORMAT.format(telegram_id=user.telegram_id), texts.ADMIN_USERS_BALANCE_BALANCE)

    text = (
        f"{header}"+
        texts.ADMIN_USERS_BALANCE_CONFIRM_CHANGE_BALA.format()+
        texts.ADMIN_USERS_BALANCE_USER.format(safe_username_str=safe(username_str))+
        texts.ADMIN_USERS_BALANCE_TYPE_ACCOUNT_BONUS_BALANCE_RUB.format()+
        texts.ADMIN_USERS_BALANCE_CHANGE.format(change_str=change_str)+
        texts.ADMIN_USERS_BALANCE_REASON.format(safe_reason=safe(reason))+
        texts.ADMIN_USERS_BALANCE_VY_UVERENY_CHTO_KHOTITE_PRIMEN.format()
    )

    builder = InlineKeyboardBuilder()
    builder.button(
        text=texts.ADMIN_USERS_BALANCE_PODTVERDIT_I_PRIMENIT,
        callback_data="confirm_admin_balance_apply",
    )
    builder.button(
        text=texts.BTN_CANCEL,
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
    reason = data.get("reason", texts.ADMIN_USERS_BALANCE_KORREKTIROVKA_ADMINISTRATOROM)
    adjustment_id = data.get("adjustment_id")

    if not telegram_id or not amount or not action_type or not adjustment_id:
        await callback.answer(texts.ADMIN_USERS_BALANCE_ERROR_DANNYE_USTARELI, show_alert=True)
        await state.clear()
        return

    user = await get_user_by_telegram_id(session, telegram_id)
    if not user:
        await callback.answer(texts.ERROR_USER_NOT_FOUND, show_alert=True)
        await state.clear()
        return

    signed_amount = amount if action_type == "topup" else -amount
    idempotency_key = f"admin_adj:{adjustment_id}"
    # Captured before the call: rollback() below expires ORM instances, so
    # touching user.id inside an except block would trigger a sync load
    # (MissingGreenlet) in async context.
    target_user_id = user.id

    try:
        await create_admin_adjustment(
            session,
            user_id=target_user_id,
            signed_amount=signed_amount,
            idempotency_key=idempotency_key,
            metadata={"admin_id": callback.from_user.id, "reason": reason},
        )
    except AccountLedgerInvariantError:
        # Authoritative under-lock rejection (TOCTOU-safe): the step-one
        # preview may have gone stale while the admin was typing the reason.
        await session.rollback()
        fresh = await get_account_balance(session, user_id=target_user_id)
        await callback.answer(
            texts.ADMIN_USERS_BALANCE_NEDOSTATOCHNO_BONUS_SREDST.format(int_fresh_bonus_available=int(fresh.bonus_available)),
            show_alert=True,
        )
        await state.clear()
        return
    except Exception as exc:
        logger.error("Failed to apply admin balance adjustment for user %s: %s", target_user_id, exc)
        await callback.answer(texts.ADMIN_USERS_BALANCE_ERROR_PRIMENENIYA_BALANCE, show_alert=True)
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
        builder.button(text=texts.ADMIN_USERS_BALANCE_V_BALANCE, callback_data="menu_balance")
        builder.button(text=texts.BTN_DISMISS, callback_data="dismiss_notification")
        builder.adjust(2)

        msg_text = (
            texts.ADMIN_USER_BONUS_ACCREDITED_NOTIFICATION.format(amount=amount)+
            texts.ADMIN_BONUS_REASON_LINE_FORMAT.format(safe_reason=safe(reason))
            if action_type == "topup"
            else texts.ADMIN_USERS_BALANCE_S_VASHEGO_BONUS_BALANCE_SP.format(amount=amount, safe_reason=safe(reason))
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
    await callback.answer(texts.ADMIN_USERS_BALANCE_SUCCESS_PRIVEDENO_V_ACTION, show_alert=True)

    from bot.formatters import format_admin_breadcrumbs
    header = format_admin_breadcrumbs(texts.BTN_USERS, texts.ADMIN_USER_ID_NO_COLON_FORMAT.format(telegram_id=user.telegram_id), texts.ADMIN_USERS_BALANCE_BALANCE)
    change_formatted = f"+{amount} ₽" if action_type == "topup" else f"-{amount} ₽"

    await render_hub(
        callback.bot,
        callback.message.chat.id,
        texts.ADMIN_USERS_BALANCE_SUCCESS_BONUS_BALANCE_POLZO.format(header=header, user_telegram_id=user.telegram_id, change_formatted=change_formatted, safe_reason=safe(reason)),
        get_back_button(f"admin_user_card:{user.telegram_id}"),
        parse_mode="HTML",
    )
