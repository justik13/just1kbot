import asyncio
import logging
from decimal import Decimal

from aiogram import Router, F
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession

from bot import texts
from bot.keyboards import (
    get_back_button,
    get_payment_method_keyboard,
    get_payment_success_keyboard,
    get_yookassa_payment_keyboard,
)
from config.settings import get_settings
from database.connection import queue_post_commit_task, session_scope
from database.repositories.payments_repo import (
    get_payment_by_id,
    get_payment_by_id_simple,
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

_PAYMENT_LINK_POLL_SECONDS = 0.5
_PAYMENT_LINK_WAIT_SECONDS = 20.0

_PAYMENT_ERROR_MESSAGES = {
    "current_tariff_deleted": "Текущий тариф удалён. Обратитесь в поддержку для проверки подписки.",
    "active_tariff_change_temporarily_unavailable": "Безопасная смена тарифа временно недоступна. Продление текущего тарифа продолжает работать.",
    "active_tariff_change_quote_exists": "У вас есть незавершённая смена тарифа. Завершите или отмените её перед созданием нового платежа.",
    "unfinished_checkout_exists": "У вас есть незавершённый платёж. Завершите или отмените его перед созданием нового.",
    "active_checkout_quote_conflict": "Конфликт котировок. Попробуйте позже или обратитесь в поддержку.",
    "checkout_user_missing": texts.ERROR_USER_NOT_FOUND,
}


def _is_yookassa_configured() -> bool:
    settings = get_settings()
    return bool(settings.YOOKASSA_SHOP_ID and settings.YOOKASSA_SECRET_KEY)


async def _send_payment_url_to_user(
    bot,
    chat_id: int,
    payment_url: str,
    payment_id: int,
    tariff_id: int,
    tariff_price: int,
    source: str,
) -> None:
    text = texts.PAYMENT_YOOKASSA_INSTRUCTIONS.format(
        amount=tariff_price,
        payment_url=safe(payment_url),
    )
    await render_hub(
        bot,
        chat_id,
        text,
        get_yookassa_payment_keyboard(payment_url, payment_id, tariff_id, source),
        parse_mode="HTML",
    )


async def _wait_and_show_payment_url(
    bot,
    chat_id: int,
    payment_id: int,
    tariff_id: int,
    tariff_price: int,
    source: str,
) -> None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + _PAYMENT_LINK_WAIT_SECONDS

    while loop.time() < deadline:
        async with session_scope() as poll_session:
            payment = await get_payment_by_id_simple(poll_session, payment_id)

        if payment is None:
            return
        if payment.payment_url and payment.checkout_status == "active":
            await _send_payment_url_to_user(
                bot,
                chat_id,
                payment.payment_url,
                payment.id,
                tariff_id,
                tariff_price,
                source,
            )
            return
        if payment.checkout_status == "abandoned" or payment.provider_status in {
            "succeeded",
            "canceled",
            "refunded",
            "manual_review",
        }:
            return
        await asyncio.sleep(_PAYMENT_LINK_POLL_SECONDS)


async def _create_and_show_payment(
    target,
    session: AsyncSession,
    db_user,
    tariff,
    source: str,
    back_callback: str,
) -> None:
    bot_info = await target.bot.get_me()
    amount = Decimal(str(tariff.price_rub))
    payment, error_code = await PaymentService.create_yookassa_payment(
        session=session,
        user_id=db_user.id,
        tariff_id=tariff.id,
        amount=amount,
        telegram_id=db_user.telegram_id,
        bot_username=bot_info.username,
    )
    if not payment:
        error_text = (
            _PAYMENT_ERROR_MESSAGES.get(
                error_code,
                texts.ERROR_PAYMENT_SERVICE,
            )
            if error_code
            else texts.ERROR_PAYMENT_SERVICE
        )
        await render_hub(
            target.bot,
            target.chat.id,
            error_text,
            get_back_button(back_callback),
        )
        return
    if not payment.payment_url:
        builder = InlineKeyboardBuilder()
        builder.button(text="Обновить", callback_data=f"check_payment:{payment.id}")
        builder.button(text="Назад", callback_data=back_callback)
        builder.adjust(1)
        await render_hub(
            target.bot,
            target.chat.id,
            (
                "⏳ Создаём ссылку на оплату.\n"
                "Обычно это занимает несколько секунд — "
                "страница обновится автоматически."
            ),
            builder.as_markup(),
        )
        queue_post_commit_task(
            session,
            lambda b=target.bot, cid=target.chat.id, pid=payment.id, tid=tariff.id, tp=tariff.price_rub, s=source: (
                _wait_and_show_payment_url(b, cid, pid, tid, tp, s)
            ),
        )
        return

    queue_post_commit_task(
        session,
        lambda b=target.bot, cid=target.chat.id, purl=payment.payment_url, pid=payment.id, tid=tariff.id, tp=tariff.price_rub, s=source: (
            _send_payment_url_to_user(
                b,
                cid,
                purl,
                pid,
                tid,
                tp,
                s,
            )
        ),
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
            "Некорректный запрос",
            show_alert=True,
        )
        return
    tariff_id = parse_callback_id(callback.data, 1)
    if tariff_id is None:
        await callback.answer(
            "Некорректный запрос",
            show_alert=True,
        )
        return
    source = parts[2] if len(parts) > 2 else "showcase"
    back_callback = {
        "change": "payment_change_tariff",
        "renew": "payment_quick_renew",
    }.get(source, f"select_tariff:{tariff_id}:{source}")

    if not _is_yookassa_configured():
        await callback.answer(show_alert=False)
        await render_hub(
            callback.bot,
            callback.message.chat.id,
            texts.ERROR_PAYMENT_SERVICE,
            get_back_button(back_callback),
        )
        return

    if not await MaintenanceService.can_user_perform_action(
        session, callback.from_user.id
    ):
        await callback.answer(show_alert=False)
        await _render_maintenance(callback, session, back_to=back_callback)
        return

    try:
        await callback.answer(texts.PAYMENT_CREATING, show_alert=False)
        tariff = await get_tariff_by_id(session, tariff_id)
        if not tariff:
            await callback.answer(
                texts.ERROR_TARIFF_NOT_FOUND,
                show_alert=True,
            )
            return
        if not tariff.is_active:
            await render_hub(
                callback.bot,
                callback.message.chat.id,
                texts.ERROR_TARIFF_UNAVAILABLE,
                get_back_button(back_callback),
            )
            return
        db_user = await get_user_by_telegram_id(
            session,
            callback.from_user.id,
        )
        if not db_user:
            await callback.answer(
                texts.ERROR_USER_NOT_FOUND,
                show_alert=True,
            )
            return
        error_text = await _check_tariff_change_allowed(
            session,
            db_user,
            tariff,
        )
        if error_text:
            await render_hub(
                callback.bot,
                callback.message.chat.id,
                error_text,
                get_back_button(back_callback),
            )
            return
        await _create_and_show_payment(
            callback.message,
            session,
            db_user,
            tariff,
            source,
            back_callback,
        )
    except Exception as e:
        logger.error(f"pay_yookassa error: {e}", exc_info=True)
        await callback.answer(
            texts.PAYMENT_CREATE_ERROR,
            show_alert=True,
        )


@router.callback_query(F.data.startswith("check_payment:"))
async def check_payment_status(
    callback: CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
    db_user=None,
) -> None:
    await callback.answer(texts.PAYMENT_CHECKING_STATUS, show_alert=False)
    payment_id = parse_callback_id(callback.data, 1)
    if payment_id is None:
        await callback.answer(
            texts.PAYMENT_INVALID,
            show_alert=True,
        )
        return
    if not db_user:
        await callback.answer(
            texts.ERROR_ACCESS_DENIED,
            show_alert=True,
        )
        return
    payment_simple = await get_payment_by_id_simple(session, payment_id)
    if not payment_simple:
        await callback.answer(
            texts.PAYMENT_NOT_FOUND_SHORT,
            show_alert=True,
        )
        return
    if payment_simple.user_id != db_user.id:
        await callback.answer(
            texts.ERROR_ACCESS_DENIED,
            show_alert=True,
        )
        return

    success, result_code = await PaymentService.check_yookassa_payment(
        session,
        payment_id,
        notify_user=False,
    )

    if success and isinstance(result_code, dict):
        provider = result_code["provider_status"]
        fulfillment = result_code["fulfillment_status"]
        if fulfillment == "succeeded":
            message = "✅ Доступ активирован."
        elif provider == "succeeded":
            message = "✅ Оплата подтверждена.\n⏳ Обновляем подписку и доступ."
        elif (
            payment_simple.payment_url
            and payment_simple.checkout_status == "active"
            and provider not in {"canceled", "refunded"}
        ):
            message = texts.PAYMENT_YOOKASSA_INSTRUCTIONS.format(
                amount=payment_simple.amount,
                payment_url=safe(payment_simple.payment_url),
            )
            keyboard = get_yookassa_payment_keyboard(
                payment_simple.payment_url,
                payment_simple.id,
                payment_simple.tariff_id,
                "refresh",
            )
            await render_hub(
                callback.bot,
                callback.message.chat.id,
                message,
                keyboard,
                parse_mode="HTML",
            )
            return
        elif payment_simple.checkout_status == "abandoned" or provider in {
            "canceled",
            "refunded",
        }:
            message = "Этот платёж больше не доступен для оплаты."
        else:
            message = "⏳ Создаём ссылку на оплату.\nНажмите «Обновить», чтобы проверить готовность."
        await render_hub(
            callback.bot,
            callback.message.chat.id,
            message,
            get_back_button("back_to_main_menu"),
        )
        return
    if success and result_code in ("success", "already_processed"):
        payment = await get_payment_by_id(session, payment_id)
        user = await get_user_by_telegram_id(
            session,
            callback.from_user.id,
        )
        profiles = await get_user_profiles(session, user.id) if user else []
        valid_until = (
            format_datetime(user.subscription_end)
            if user and user.subscription_end
            else "—"
        )
        tariff_name = get_payment_tariff_name(payment)
        text = (
            texts.PAYMENT_SUCCESS_RENEW.format(
                tariff_name=tariff_name,
                valid_until=valid_until,
            )
            if profiles
            else texts.PAYMENT_SUCCESS_NEW.format(
                tariff_name=tariff_name,
                valid_until=valid_until,
            )
        )
        await render_hub(
            callback.bot,
            callback.message.chat.id,
            text,
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
            callback.bot,
            callback.message.chat.id,
            text,
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
            callback.bot,
            callback.message.chat.id,
            texts.PAYMENT_MANUAL_REVIEW_TEXT,
            builder.as_markup(),
        )
    elif result_code == "api_error":
        await callback.answer(
            texts.PAYMENT_API_ERROR,
            show_alert=True,
        )
    elif result_code == "refunded":
        await callback.answer(
            texts.PAYMENT_REFUNDED_SHORT,
            show_alert=True,
        )
    elif result_code == "cancelled":
        await callback.answer(
            texts.PAYMENT_CANCELLED_SHORT,
            show_alert=True,
        )
    else:
        await callback.answer(
            texts.PAYMENT_NOT_RECEIVED,
            show_alert=True,
        )


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
            texts.PAYMENT_INVALID,
            show_alert=True,
        )
        return
    payment_id = parse_callback_id(callback.data, 1)
    if payment_id is None:
        await callback.answer(
            texts.PAYMENT_INVALID,
            show_alert=True,
        )
        return
    tariff_id = parse_callback_id(callback.data, 2)
    if tariff_id is None:
        await callback.answer(
            texts.PAYMENT_INVALID,
            show_alert=True,
        )
        return
    source = parts[3] if len(parts) > 3 else "showcase"

    if not db_user:
        await callback.answer(
            texts.ERROR_ACCESS_DENIED,
            show_alert=True,
        )
        return

    payment = await get_payment_by_id_simple(session, payment_id)
    if not payment:
        await callback.answer(
            texts.PAYMENT_NOT_FOUND_SHORT,
            show_alert=True,
        )
        return
    if payment.user_id != db_user.id:
        await callback.answer(
            texts.ERROR_ACCESS_DENIED,
            show_alert=True,
        )
        return
    if payment.status == "completed":
        await callback.answer(
            texts.PAYMENT_ALREADY_PROCESSED,
            show_alert=True,
        )
        return

    if payment.provider_status == "pending" and payment.external_id:
        await state.clear()
        await callback.answer(
            ("Платёж сохранён. Вы сможете вернуться к нему через раздел оплаты."),
            show_alert=False,
        )
    else:
        try:
            queued = await PaymentService.cancel_payment_via_api(session, payment_id)
        except (OperationalError, OSError, TimeoutError) as e:
            logger.warning("Temporary error cancelling payment %s: %s", payment_id, e)
            await callback.answer(
                "⚠️ Временная ошибка. Попробуйте позже.", show_alert=True
            )
            return
        except Exception as e:
            logger.warning("Failed to queue cancellation %s: %s", payment_id, e)
            queued = False
        await state.clear()
        await callback.answer(
            "Запрос на отмену поставлен в очередь"
            if queued
            else texts.PAYMENT_ALREADY_PROCESSED,
            show_alert=not queued,
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
            callback.bot,
            callback.message.chat.id,
            text,
            get_payment_method_keyboard(
                tariff.id,
                device_limit,
                source=source,
            ),
        )
        return

    user = await get_user_by_telegram_id(
        session,
        callback.from_user.id,
    )
    if user and await _is_subscription_active(user):
        await _show_hub(callback, user, session)
    else:
        await _show_showcase(callback, session)
