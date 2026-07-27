import logging

from aiogram import Router, F
from aiogram.exceptions import (
    TelegramBadRequest,
    TelegramForbiddenError,
)
from aiogram.types import CallbackQuery
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy.ext.asyncio import AsyncSession

from bot import texts
from bot.keyboards.admin.users import (
    get_admin_confirm_action_keyboard,
)
from database.connection import session_scope
from database.repositories.payments_repo import get_payment_by_id
from database.repositories.users_repo import mark_user_bot_blocked
from services.payment_service import PaymentService
from services.payment_service.common import get_payment_tariff_name
from services.workers.heartbeat import get_bot_ref
from utils.admin import is_admin
from utils.callbacks import parse_callback_id
from utils.formatters import format_datetime
from utils.telegram import render_hub

from .common import (
    MANUAL_GRANT_ALLOWED_STATUSES,
)

router = Router()
logger = logging.getLogger(__name__)


@router.callback_query(F.data.startswith("admin_manual_grant:"))
async def admin_manual_grant(
    callback: CallbackQuery,
    session: AsyncSession,
):
    if not is_admin(callback.from_user.id):
        await callback.answer(
            texts.ERROR_ACCESS_DENIED,
            show_alert=True,
        )
        return

    payment_id = parse_callback_id(callback.data, 1)

    if payment_id is None:
        await callback.answer(
            "Некорректный запрос",
            show_alert=True,
        )
        return

    await callback.answer(show_alert=False)

    payment = await get_payment_by_id(session, payment_id)

    if not payment:
        await callback.answer(
            texts.ADMIN_MANUAL_GRANT_PAYMENT_NOT_FOUND,
            show_alert=True,
        )
        return

    if payment.status == "completed":
        await callback.answer(
            texts.ADMIN_MANUAL_GRANT_ALREADY_COMPLETED,
            show_alert=True,
        )
        return

    if payment.status == "refunded":
        await callback.answer(
            texts.ADMIN_MANUAL_GRANT_REFUNDED,
            show_alert=True,
        )
        return

    if payment.status not in MANUAL_GRANT_ALLOWED_STATUSES:
        await callback.answer(
            texts.ADMIN_MANUAL_GRANT_INVALID_STATUS,
            show_alert=True,
        )
        return

    user = payment.user

    if not user:
        await callback.answer(
            texts.ADMIN_MANUAL_GRANT_USER_NOT_FOUND,
            show_alert=True,
        )
        return

    if user.is_deleted:
        await callback.answer(
            texts.ADMIN_MANUAL_GRANT_USER_DELETED,
            show_alert=True,
        )
        return

    if user.is_banned:
        await callback.answer(
            texts.ADMIN_MANUAL_GRANT_USER_BANNED,
            show_alert=True,
        )
        return

    tariff_name = get_payment_tariff_name(payment)

    status_name = texts.PAYMENT_STATUS_NAMES.get(
        payment.status,
        payment.status,
    )

    text = texts.ADMIN_MANUAL_GRANT_CONFIRM.format(
        payment_id=payment.id,
        user_telegram_id=user.telegram_id,
        tariff_name=tariff_name,
        amount=payment.amount,
        currency=payment.currency,
        status_name=status_name,
    )

    try:
        await callback.message.edit_text(
            text,
            reply_markup=get_admin_confirm_action_keyboard(
                confirm_callback=(
                    f"admin_manual_grant_apply:{payment.id}"
                ),
                cancel_callback="admin_menu",
            ),
            parse_mode="HTML",
        )
    except TelegramBadRequest as e:
        logger.debug(f"admin_manual_grant edit_text failed: {e}")


@router.callback_query(F.data.startswith("admin_manual_grant_apply:"))
async def admin_manual_grant_apply(
    callback: CallbackQuery,
    session: AsyncSession,
):
    if not is_admin(callback.from_user.id):
        await callback.answer(
            texts.ERROR_ACCESS_DENIED,
            show_alert=True,
        )
        return

    payment_id = parse_callback_id(callback.data, 1)

    if payment_id is None:
        await callback.answer(
            "Некорректный запрос",
            show_alert=True,
        )
        return

    await callback.answer(show_alert=False)

    try:
        success, result = await PaymentService.force_grant_payment(
            session,
            payment_id,
            callback.from_user.id,
        )

        if success:
            payment = await get_payment_by_id(
                session,
                payment_id,
            )

            user_tg_id = (
                payment.user.telegram_id
                if payment and payment.user
                else "—"
            )

            await callback.answer(
                texts.ADMIN_MANUAL_GRANT_SUCCESS_ANSWER.format(
                    user_telegram_id=user_tg_id,
                ),
                show_alert=True,
            )

            try:
                await callback.message.edit_text(
                    texts.ADMIN_MANUAL_GRANT_SUCCESS_MESSAGE.format(
                        payment_id=payment_id,
                        user_telegram_id=user_tg_id,
                        admin_id=callback.from_user.id,
                    ),
                    parse_mode="HTML",
                )
            except TelegramBadRequest as e:
                logger.debug(
                    f"admin_manual_grant_apply edit_text failed: {e}"
                )

            try:
                bot = get_bot_ref()

                if bot and payment and payment.user:
                    user = payment.user

                    tariff_name = get_payment_tariff_name(payment)

                    valid_until = format_datetime(
                        user.subscription_end
                    )

                    client_msg = texts.USER_MANUAL_GRANT_NOTIFICATION.format(
                        tariff_name=tariff_name,
                        valid_until=valid_until,
                    )

                    builder = InlineKeyboardBuilder()

                    builder.button(
                        text="🔌 Подключить устройство",
                        callback_data="menu_connections",
                    )

                    builder.button(
                        text="🏠 В главное меню",
                        callback_data="back_to_main_menu",
                    )

                    builder.button(
                        text="✅ Прочитано (убрать)",
                        callback_data="dismiss_notification",
                    )

                    builder.adjust(1, 1, 1)

                    await render_hub(
                        bot,
                        user.telegram_id,
                        client_msg,
                        builder.as_markup(),
                    )

            except TelegramForbiddenError:
                logger.info(
                    "Manual grant notification: user %s "
                    "blocked the bot",
                    user.telegram_id
                    if payment and payment.user
                    else "?",
                )

                try:
                    if payment and payment.user:
                        async with session_scope() as notify_session:
                            await mark_user_bot_blocked(
                                notify_session,
                                payment.user.telegram_id,
                            )
                except Exception as block_error:
                    logger.error(
                        "Failed to mark user as bot_blocked "
                        "after manual grant notification: %s",
                        block_error,
                    )

            except Exception as notify_error:
                logger.error(
                    "Failed to notify client after manual grant: "
                    f"{notify_error}"
                )

        else:
            await callback.answer(
                texts.ADMIN_BAN_FAILED.format(message=result),
                show_alert=True,
            )

    except Exception as e:
        logger.error(
            f"admin_manual_grant_apply error: {e}",
            exc_info=True,
        )

        await callback.answer(
            texts.ADMIN_MANUAL_GRANT_FAILED,
            show_alert=True,
        )