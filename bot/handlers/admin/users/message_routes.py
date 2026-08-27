"""Admin direct messaging routes for user card panel."""

import logging

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy.ext.asyncio import AsyncSession

from bot import texts
from bot.states import AdminStates
from database.repositories.users_repo import get_user_by_telegram_id
from services.audit_service import AuditService
from utils.admin import is_admin
from utils.callbacks import parse_callback_id
from utils.telegram import render_hub, safe

from .common import _show_user_card_edit

router = Router()
logger = logging.getLogger(__name__)


@router.callback_query(F.data.startswith("admin_send_msg:"))
async def prompt_send_user_message(
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

    user = await get_user_by_telegram_id(session, telegram_id)
    if not user:
        await callback.answer(texts.ERROR_USER_NOT_FOUND, show_alert=True)
        return

    await state.set_state(AdminStates.sending_user_message)
    await state.update_data(
        target_telegram_id=user.telegram_id,
        target_user_db_id=user.id,
    )

    builder = InlineKeyboardBuilder()
    builder.button(
        text=texts.UI_MESSAGE_ROUTES_OTMENA_54,
        callback_data=f"admin_user_card:{user.telegram_id}",
    )

    text = (
        texts.UI_MESSAGE_ROUTES_OTPRAVKA_SOOBSHCHENIYA_POLZOVA_59.format()+
        texts.UI_MESSAGE_ROUTES_POLZOVATEL_ID_60.format(safe_user_username_or_str_user_telegram_id=safe(user.username or str(user.telegram_id)), user_telegram_id=user.telegram_id)+
        texts.UI_MESSAGE_ROUTES_VVEDITE_TEKST_SOOBSHCHENIYA_KO_61.format()+
        texts.UI_MESSAGE_ROUTES_PODDERZHIVAETSYA_HTML_RAZMETKA_62.format()
    )

    try:
        await callback.message.edit_text(
            text,
            reply_markup=builder.as_markup(),
            parse_mode="HTML",
        )
    except TelegramBadRequest:
        await render_hub(
            callback.bot,
            callback.message.chat.id,
            text,
            builder.as_markup(),
        )


@router.message(AdminStates.sending_user_message)
async def process_send_user_message(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
):
    if not is_admin(message.from_user.id):
        await state.clear()
        return

    data = await state.get_data()
    target_telegram_id = data.get("target_telegram_id")
    target_user_db_id = data.get("target_user_db_id")

    if not target_telegram_id or not target_user_db_id:
        await state.clear()
        from bot.keyboards.common import get_back_button
        await render_hub(
            message.bot,
            message.chat.id,
            texts.UI_MESSAGE_ROUTES_OSHIBKA_NE_NAYDEN_TSELEVOY_POL_100,
            get_back_button("admin_users"),
            trigger_message_id=message.message_id,
        )
        return

    user = await get_user_by_telegram_id(session, target_telegram_id)
    if not user:
        await state.clear()
        from bot.keyboards.common import get_back_button
        await render_hub(
            message.bot,
            message.chat.id,
            texts.ERROR_USER_NOT_FOUND,
            get_back_button("admin_users"),
            trigger_message_id=message.message_id,
        )
        return

    text_to_send = message.text or message.caption
    if not text_to_send and not message.photo and not message.document:
        from bot.keyboards.common import get_back_button
        await render_hub(
            message.bot,
            message.chat.id,
            texts.UI_MESSAGE_ROUTES_POZHALUYSTA_OTPRAVTE_TEKSTOVOE_125,
            get_back_button(f"admin_user_card:{target_telegram_id}"),
            trigger_message_id=message.message_id,
        )
        return

    await state.clear()

    msg_sent = False
    error_reason = None
    from aiogram.utils.keyboard import InlineKeyboardBuilder
    dismiss_builder = InlineKeyboardBuilder()
    dismiss_builder.button(text=texts.UI_MESSAGE_ROUTES_PROCHITANO_137, callback_data="dismiss_notification")
    reply_markup = dismiss_builder.as_markup()

    try:
        if message.photo:
            photo_id = message.photo[-1].file_id
            try:
                await message.bot.send_photo(
                    target_telegram_id,
                    photo=photo_id,
                    caption=texts.UI_MESSAGE_ROUTES_SOOBSHCHENIE_OT_ADMINISTRATSII_147.format(text_to_send_or=text_to_send or ''),
                    reply_markup=reply_markup,
                    parse_mode="HTML",
                )
            except TelegramBadRequest:
                await message.bot.send_photo(
                    target_telegram_id,
                    photo=photo_id,
                    caption=texts.UI_MESSAGE_ROUTES_SOOBSHCHENIE_OT_ADMINISTRATSII_155.format(text_to_send_or=text_to_send or ''),
                    reply_markup=reply_markup,
                )
        elif message.document:
            doc_id = message.document.file_id
            try:
                await message.bot.send_document(
                    target_telegram_id,
                    document=doc_id,
                    caption=texts.UI_MESSAGE_ROUTES_SOOBSHCHENIE_OT_ADMINISTRATSII_164.format(text_to_send_or=text_to_send or ''),
                    reply_markup=reply_markup,
                    parse_mode="HTML",
                )
            except TelegramBadRequest:
                await message.bot.send_document(
                    target_telegram_id,
                    document=doc_id,
                    caption=texts.UI_MESSAGE_ROUTES_SOOBSHCHENIE_OT_ADMINISTRATSII_172.format(text_to_send_or=text_to_send or ''),
                    reply_markup=reply_markup,
                )
        else:
            try:
                header = texts.UI_MESSAGE_ROUTES_SOOBSHCHENIE_OT_ADMINISTRATSII_177
                await message.bot.send_message(
                    target_telegram_id,
                    f"{header}{text_to_send}",
                    reply_markup=reply_markup,
                    parse_mode="HTML",
                )
            except TelegramBadRequest:
                header = texts.UI_MESSAGE_ROUTES_SOOBSHCHENIE_OT_ADMINISTRATSII_185
                await message.bot.send_message(
                    target_telegram_id,
                    f"{header}{text_to_send}",
                    reply_markup=reply_markup,
                )
        msg_sent = True
    except TelegramForbiddenError:
        error_reason = texts.UI_MESSAGE_ROUTES_POLZOVATEL_ZABLOKIROVAL_BOTA_193
    except Exception as exc:
        error_reason = str(exc)
        logger.warning(f"Failed to send admin direct message to {target_telegram_id}: {exc}")

    if msg_sent:
        import json
        await AuditService.log_action(
            session,
            admin_id=message.from_user.id,
            action="ADMIN_DIRECT_MESSAGE_SENT",
            target_type="user",
            target_id=user.id,
            details=json.dumps({
                "target_telegram_id": target_telegram_id,
                "text": text_to_send[:500] if text_to_send else "",
            }, ensure_ascii=False),
        )
        notice = texts.UI_MESSAGE_ROUTES_SOOBSHCHENIE_POLZOVATELYU_ID_U_211.format(target_telegram_id=target_telegram_id)
    else:
        notice = texts.UI_MESSAGE_ROUTES_NE_UDALOS_OTPRAVIT_SOOBSHCHENI_213.format(safe_error_reason=safe(error_reason))

    await _show_user_card_edit(message, user, session, notice=notice)


