import logging
from decimal import Decimal

from aiogram import Router, F
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy.ext.asyncio import AsyncSession

from bot import texts
from bot.keyboards import (
    get_back_button,
    get_payment_method_keyboard,
    get_payment_success_keyboard,
    get_yookassa_payment_keyboard,
)
from config.settings import get_settings
from database.repositories.payments_repo import (
    get_payment_by_id,
    get_payment_by_id_simple,
    mark_payment_as_cancelled,
)
from database.repositories.profiles_repo import get_user_profiles
from database.repositories.tariffs_repo import get_tariff_by_id
from database.repositories.users_repo import get_user_by_telegram_id
from services.maintenance_service import MaintenanceService
from services.payment_service import PaymentService
from services.payment_service.common import get_payment_tariff_name
from utils.callbacks import parse_callback_id, parse_callback_parts
from utils.formatters import format_datetime
from utils.tariff_names import get_tariff_display_name
from utils.telegram import render_hub, safe

from .common import (
    _check_tariff_change_allowed,
    _is_subscription_active,
    _render_maintenance,
    _show_hub,
    _show_showcase,
)

router = Router()
logger = logging.getLogger(__name__)


def _is_yookassa_configured() -> bool:
    settings = get_settings()
    return bool(
        settings.YOOKASSA_SHOP_ID and settings.YOOKASSA_SECRET_KEY
    )


async def _create_and_show_payment(
    target, session: AsyncSession, db_user, tariff, source: str,
    back_callback: str,
) -> None:
    bot_info = await target.bot.get_me()
    amount = Decimal(str(tariff.price_rub))
    payment, _ = await PaymentService.create_yookassa_payment(
        session=session,
        user_id=db_user.id,
        tariff_id=tariff.id,
        amount=amount,
        telegram_id=db_user.telegram_id,
        bot_username=bot_info.username,
    )
    if not payment or not payment.payment_url:
        await render_hub(
            target.bot, target.chat.id,
            texts.ERROR_PAYMENT_SERVICE,
            get_back_button(back_callback),
        )
        return
    text = texts.PAYMENT_YOOKASSA_INSTRUCTIONS.format(
        amount=tariff.price_rub,
        payment_url=safe(payment.payment_url),
    )
    await render_hub(
        target.bot, target.chat.id, text,
        get_yookassa_payment_keyboard(
            payment.payment_url, payment.id, tariff.id, source
        ),
        parse_mode="HTML",
    )


@router.callback_query(F.data.startswith("pay_yookassa:"))
async def pay_yookassa(
    callback: CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
) -> None:
    await state.clear()
    parts = parse_callback_parts(callback.data, 2)
    if parts is None:
        await callback.answer(
            "Некорректный запрос", show_alert=True,
        )
        return
    tariff_id = parse_callback_id(callback.data, 1)
    if tariff_id is None:
        await callback.answer(
            "Некорректный запрос", show_alert=True,
        )
        return
    source = parts[2] if len(parts) > 2 else "showcase"
    back_callback = {
        "change": "payment_change_tariff",
        "renew": "payment_quick_renew",
    }.get(source, f"select_tariff:{tariff_id}:{source}")

    if not _is_yookassa_configured():
        await callback.answer()
        await render_hub(
            callback.bot, callback.message.chat.id,
            texts.ERROR_PAYMENT_SERVICE,
            get_back_button(back_callback),
        )
        return

    if not await MaintenanceService.can_user_perform_action(
        session, callback.from_user.id
    ):
        await callback.answer()
        await _render_maintenance(
            callback, session, back_to=back_callback
        )
        return

    try:
        await callback.answer(texts.PAYMENT_CREATING)
        tariff = await get_tariff_by_id(session, tariff_id)
        if not tariff:
            await callback.answer(
                texts.ERROR_TARIFF_NOT_FOUND, show_alert=True,
            )
            return
        if not tariff.is_active:
            await render_hub(
                callback.bot, callback.message.chat.id,
                texts.ERROR_TARIFF_UNAVAILABLE,
                get_back_button(back_callback),
            )
            return
        db_user = await get_user_by_telegram_id(
            session, callback.from_user.id,
        )
        if not db_user:
            await callback.answer(
                texts.ERROR_USER_NOT_FOUND, show_alert=True,
            )
            return
        error_text = await _check_tariff_change_allowed(
            session, db_user, tariff,
        )
        if error_text:
            await render_hub(
                callback.bot, callback.message.chat.id,
                error_text, get_back_button(back_callback),
            )
            return
        await _create_and_show_payment(
            callback.message, session, db_user, tariff,
            source, back_callback,
        )
    except Exception as e:
        logger.error(f"pay_yookassa error: {e}", exc_info=True)
        await callback.answer(
            texts.PAYMENT_CREATE_ERROR, show_alert=True,
        )


@router.callback_query(F.data.startswith("check_payment:"))
async def check_payment_status(
    callback: CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
    db_user=None,
) -> None:
    await callback.answer(texts.PAYMENT_CHECKING_STATUS)
    payment_id = parse_callback_id(callback.data, 1)
    if payment_id is None:
        await callback.answer(
            texts.PAYMENT_INVALID, show_alert=True,
        )
        return
    if not db_user:
        await callback.answer(
            texts.ERROR_ACCESS_DENIED, show_alert=True,
        )
        return
    payment_simple = await get_payment_by_id_simple(
        session, payment_id
    )
    if not payment_simple:
        await callback.answer(
            texts.PAYMENT_NOT_FOUND_SHORT, show_alert=True,
        )
        return
    if payment_simple.user_id != db_user.id:
        await callback.answer(
            texts.ERROR_ACCESS_DENIED, show_alert=True,
        )
        return

    success, result_code = (
        await PaymentService.check_yookassa_payment(
            session, payment_id, notify_user=False,
        )
    )

    if success and result_code in ("success", "already_processed"):
        payment = await get_payment_by_id(session, payment_id)
        user = await get_user_by_telegram_id(
            session, callback.from_user.id,
        )
        profiles = (
            await get_user_profiles(session, user.id)
            if user
            else []
        )
        valid_until = (
            format_datetime(user.subscription_end)
            if user and user.subscription_end
            else "—"
        )
        tariff_name = get_payment_tariff_name(payment)
        text = (
            texts.PAYMENT_SUCCESS_RENEW.format(
                tariff_name=tariff_name, valid_until=valid_until,
            )
            if profiles
            else texts.PAYMENT_SUCCESS_NEW.format(
                tariff_name=tariff_name, valid_until=valid_until,
            )
        )
        await render_hub(
            callback.bot, callback.message.chat.id, text,
            get_payment_success_keyboard(),
        )
    elif result_code == "paid_after_cancel":
        settings = get_settings()
        support_username = settings.SUPPORT_USERNAME.lstrip("@")
        payment = await get_payment_by_id(session, payment_id)
        tariff_name = get_payment_tariff_name(payment)
        text = texts.PAYMENT_PAID_AFTER_CANCEL.format(
            amount=payment.amount if payment else "—",
            currency=payment.currency if payment else "—",
            tariff_name=tariff_name,
            payment_id=payment_id,
        )
        builder = InlineKeyboardBuilder()
        builder.button(
            text="💬 Написать в поддержку",
            url=f"https://t.me/{support_username}",
        )
        builder.button(
            text="🏠 В главное меню",
            callback_data="back_to_main_menu",
        )
        builder.adjust(1, 1)
        await render_hub(
            callback.bot, callback.message.chat.id, text,
            builder.as_markup(),
        )
    elif result_code == "manual_review":
        settings = get_settings()
        support_username = settings.SUPPORT_USERNAME.lstrip("@")
        builder = InlineKeyboardBuilder()
        builder.button(
            text="💬 Написать в поддержку",
            url=f"https://t.me/{support_username}",
        )
        builder.button(
            text="🏠 В главное меню",
            callback_data="back_to_main_menu",
        )
        builder.adjust(1, 1)
        await render_hub(
            callback.bot, callback.message.chat.id,
            texts.PAYMENT_MANUAL_REVIEW_TEXT,
            builder.as_markup(),
        )
    elif result_code == "api_error":
        await callback.answer(
            texts.PAYMENT_API_ERROR, show_alert=True,
        )
    elif result_code == "refunded":
        await callback.answer(
            texts.PAYMENT_REFUNDED_SHORT, show_alert=True,
        )
    elif result_code == "cancelled":
        await callback.answer(
            texts.PAYMENT_CANCELLED_SHORT, show_alert=True,
        )
    else:
        await callback.answer(
            texts.PAYMENT_NOT_RECEIVED, show_alert=True,
        )


# ──────────────────────────────────────────────────────────────
# ИСПРАВЛЕНО: после mark_payment_as_cancelled перечитываем
# платёж и показываем реальный статус.
# ──────────────────────────────────────────────────────────────
@router.callback_query(F.data.startswith("cancel_invoice:"))
async def cancel_invoice(
    callback: CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
    db_user=None,
) -> None:
    parts = parse_callback_parts(callback.data, 3)
    if parts is None:
        await callback.answer(
            texts.PAYMENT_INVALID, show_alert=True,
        )
        return
    payment_id = parse_callback_id(callback.data, 1)
    if payment_id is None:
        await callback.answer(
            texts.PAYMENT_INVALID, show_alert=True,
        )
        return
    tariff_id = parse_callback_id(callback.data, 2)
    if tariff_id is None:
        await callback.answer(
            texts.PAYMENT_INVALID, show_alert=True,
        )
        return
    source = parts[3] if len(parts) > 3 else "showcase"

    if not db_user:
        await callback.answer(
            texts.ERROR_ACCESS_DENIED, show_alert=True,
        )
        return

    payment = await get_payment_by_id_simple(session, payment_id)
    if not payment:
        await callback.answer(
            texts.PAYMENT_NOT_FOUND_SHORT, show_alert=True,
        )
        return
    if payment.user_id != db_user.id:
        await callback.answer(
            texts.ERROR_ACCESS_DENIED, show_alert=True,
        )
        return
    if payment.status == "completed":
        await callback.answer(
            texts.PAYMENT_ALREADY_PROCESSED, show_alert=True,
        )
        return

    try:
        api_cancelled = await PaymentService.cancel_payment_via_api(
            session, payment_id,
        )
        if not api_cancelled:
            logger.warning(
                "cancel_invoice: API cancel returned False "
                "for payment %s.",
                payment_id,
            )
        was_cancelled = await mark_payment_as_cancelled(
            session, payment_id,
        )
    except Exception as e:
        logger.warning(
            f"Failed to cancel payment {payment_id}: {e}"
        )
        was_cancelled = False

    await state.clear()

    # ── ИСПРАВЛЕНО: показываем реальный статус ──
    if was_cancelled:
        await callback.answer(texts.PAYMENT_INVOICE_CANCELLED)
    else:
        refreshed = await get_payment_by_id_simple(
            session, payment_id,
        )
        if refreshed and refreshed.status == "completed":
            await callback.answer(
                texts.PAYMENT_ALREADY_PROCESSED,
                show_alert=True,
            )
        elif (
            refreshed
            and refreshed.status == "requires_manual_review"
        ):
            await callback.answer(
                "Платёж на проверке. Обратитесь в поддержку.",
                show_alert=True,
            )
        else:
            await callback.answer(
                texts.PAYMENT_INVOICE_CANCELLED,
            )

    tariff = await get_tariff_by_id(session, tariff_id)
    if tariff and tariff.is_active:
        device_limit = getattr(tariff, "device_limit", 2)
        tariff_name = get_tariff_display_name(device_limit)
        text = texts.PAYMENT_CHECKOUT_TEXT.format(
            tariff_name=tariff_name,
            duration_days=tariff.duration_days,
            price_rub=tariff.price_rub,
        )
        await render_hub(
            callback.bot, callback.message.chat.id, text,
            get_payment_method_keyboard(
                tariff.id, device_limit, source=source,
            ),
        )
        return

    user = await get_user_by_telegram_id(
        session, callback.from_user.id,
    )
    if user and await _is_subscription_active(user):
        await _show_hub(callback, user, session)
    else:
        await _show_showcase(callback, session)