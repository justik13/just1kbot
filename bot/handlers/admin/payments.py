import logging
import math

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from bot import texts
from database.models import Payment
from database.repositories.account_ledger_repo import get_payment_refundable_amount
from database.repositories.payments_repo import get_payment_by_id
from services.provider_refunds import (
    BalanceRefundError,
    request_balance_topup_refund,
)
from utils.admin import is_admin
from utils.callbacks import parse_callback_id
from utils.formatters import format_datetime
from utils.telegram import safe
from utils.text_limits import truncate_button_text

router = Router()
logger = logging.getLogger(__name__)

PAYMENTS_PER_PAGE = 20


def _refund_available(payment: Payment) -> bool:
    return bool(
        payment.payment_kind == "balance_topup"
        and payment.provider_status == "succeeded"
        and payment.external_id
        and payment.currency == "RUB"
    )


def _get_payment_card_keyboard(
    payment: Payment,
    user_telegram_id: int | None,
) -> InlineKeyboardBuilder:
    builder = InlineKeyboardBuilder()

    if payment.status == "requires_manual_review":
        builder.button(
            text="✅ Выдать подписку",
            callback_data=f"admin_manual_grant:{payment.id}",
        )

    if _refund_available(payment):
        builder.button(
            text="↩️ Вернуть доступный остаток",
            callback_data=f"admin_payment_refund:{payment.id}",
        )

    if user_telegram_id:
        builder.button(
            text="👤 Профиль клиента",
            callback_data=f"admin_user_card:{user_telegram_id}",
        )

    builder.button(
        text="← К списку платежей",
        callback_data="admin_payments",
    )
    builder.adjust(1)
    return builder


async def _build_payments_list_text_and_kb(
    payments,
    page: int,
    total_pages: int,
    total: int,
) -> tuple[str, InlineKeyboardBuilder]:
    rendered = (
        f"🛠 Админка › 💳 <b>Платежи</b>\n"
        f"(стр. {page}/{total_pages}) · Всего: {total}\n"
    )
    builder = InlineKeyboardBuilder()
    if not payments:
        rendered += "<i>Платежей пока нет</i>\n"
    else:
        for payment in payments:
            status_icon = texts.PAYMENT_STATUS_ICONS.get(
                payment.status,
                "❓",
            )
            if payment.user and payment.user.username:
                user_label = f"@{payment.user.username}"
            elif payment.user:
                user_label = f"ID:{payment.user.telegram_id}"
            else:
                user_label = "—"
            button_text = truncate_button_text(
                f"{status_icon} #{payment.id} · "
                f"{user_label} · "
                f"{payment.amount}₽"
            )
            builder.button(
                text=button_text,
                callback_data=f"admin_payment_card:{payment.id}",
            )
    if page > 1:
        builder.button(
            text="⬅️",
            callback_data=f"admin_payments_page:{page - 1}",
        )
    if page < total_pages:
        builder.button(
            text="➡️",
            callback_data=f"admin_payments_page:{page + 1}",
        )
    builder.button(text="← В админку", callback_data="admin_menu")
    builder.adjust(1)
    return rendered, builder


async def _show_payments_list(
    callback: CallbackQuery,
    session: AsyncSession,
    page: int = 1,
):
    total_payments = await session.scalar(
        select(func.count(Payment.id)),
    ) or 0
    total_pages = max(
        1,
        math.ceil(total_payments / PAYMENTS_PER_PAGE),
    )
    if page > total_pages:
        page = total_pages
    offset = (page - 1) * PAYMENTS_PER_PAGE
    stmt = (
        select(Payment)
        .options(
            selectinload(Payment.user),
            selectinload(Payment.tariff),
        )
        .order_by(Payment.created_at.desc())
        .offset(offset)
        .limit(PAYMENTS_PER_PAGE)
    )
    result = await session.execute(stmt)
    payments = result.scalars().all()
    rendered, kb = await _build_payments_list_text_and_kb(
        payments,
        page,
        total_pages,
        total_payments,
    )
    try:
        await callback.message.edit_text(
            rendered,
            reply_markup=kb.as_markup(),
            parse_mode="HTML",
        )
    except TelegramBadRequest as e:
        logger.debug("_show_payments_list edit_text failed: %s", e)


@router.callback_query(F.data == "admin_payments")
async def show_payments_list(
    callback: CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
):
    if not is_admin(callback.from_user.id):
        await callback.answer(
            texts.ERROR_ACCESS_DENIED,
            show_alert=True,
        )
        return
    await state.clear()
    await _show_payments_list(callback, session, page=1)
    await callback.answer(show_alert=False)


@router.callback_query(F.data.startswith("admin_payments_page:"))
async def payments_pagination(
    callback: CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
):
    if not is_admin(callback.from_user.id):
        await callback.answer(
            texts.ERROR_ACCESS_DENIED,
            show_alert=True,
        )
        return
    page = parse_callback_id(callback.data, 1)
    if page is None or page < 1:
        await callback.answer(
            "Некорректный запрос",
            show_alert=True,
        )
        return
    await state.clear()
    await _show_payments_list(callback, session, page=page)
    await callback.answer(show_alert=False)


@router.callback_query(F.data.startswith("admin_payment_card:"))
async def show_payment_card(
    callback: CallbackQuery,
    state: FSMContext,
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
    await state.clear()
    payment = await get_payment_by_id(session, payment_id)
    if not payment:
        await callback.answer(
            "Платёж не найден",
            show_alert=True,
        )
        return

    if payment.user and payment.user.username:
        user_label = (
            f"@{safe(payment.user.username)} "
            f"(ID: <code>{payment.user.telegram_id}</code>)"
        )
        user_telegram_id = payment.user.telegram_id
    elif payment.user:
        user_label = f"ID: <code>{payment.user.telegram_id}</code>"
        user_telegram_id = payment.user.telegram_id
    else:
        user_label = "—"
        user_telegram_id = None

    status_name = texts.PAYMENT_STATUS_NAMES.get(
        payment.status,
        payment.status,
    )
    status_icon = texts.PAYMENT_STATUS_ICONS.get(
        payment.status,
        "❓",
    )

    if payment.tariff:
        tariff_label = (
            f"{payment.tariff.duration_days} дн. / "
            f"{payment.tariff.device_limit} устр."
        )
    else:
        tariff_label = "—"

    reason_line = ""
    if (
        payment.status == "requires_manual_review"
        and payment.manual_review_reason
    ):
        reason_line = (
            f"\n<b>Причина:</b> "
            f"{safe(payment.manual_review_reason)}"
        )

    refundable_line = ""
    if _refund_available(payment):
        refundable = await get_payment_refundable_amount(
            session,
            payment_id=payment.id,
        )
        refundable_line = f"\n<b>Можно вернуть:</b> {int(refundable)} RUB"

    rendered = (
        f"🛠 Админка › 💳 Платежи › "
        f"<b>Платёж #{payment.id}</b>\n"
        f"<b>ID:</b> {payment.id}\n"
        f"<b>Пользователь:</b> {user_label}\n"
        f"<b>Сумма:</b> {payment.amount} {payment.currency}\n"
        f"<b>Статус:</b> {status_icon} {status_name}\n"
        f"<b>Provider:</b> {safe(payment.provider_status)}\n"
        f"<b>Исполнение:</b> {safe(payment.fulfillment_status)}\n"
        f"<b>Тип:</b> {safe(payment.payment_kind)}\n"
        f"<b>Тариф:</b> {tariff_label}\n"
        f"<b>Создан:</b> {format_datetime(payment.created_at)}\n"
        f"<b>Оплачен:</b> {format_datetime(payment.paid_at)}\n"
        f"<b>External ID:</b> "
        f"<code>{safe(payment.external_id or '—')}</code>"
        f"{refundable_line}"
        f"{reason_line}"
    )

    kb = _get_payment_card_keyboard(
        payment,
        user_telegram_id,
    )

    try:
        await callback.message.edit_text(
            rendered,
            reply_markup=kb.as_markup(),
            parse_mode="HTML",
        )
    except TelegramBadRequest as e:
        logger.debug("show_payment_card edit_text failed: %s", e)
    await callback.answer(show_alert=False)


@router.callback_query(F.data.startswith("admin_payment_refund:"))
async def confirm_payment_refund(
    callback: CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
):
    if not is_admin(callback.from_user.id):
        await callback.answer(texts.ERROR_ACCESS_DENIED, show_alert=True)
        return
    payment_id = parse_callback_id(callback.data, 1)
    payment = (
        await get_payment_by_id(session, payment_id)
        if payment_id is not None
        else None
    )
    if payment is None or not _refund_available(payment):
        await callback.answer("Платёж недоступен для возврата", show_alert=True)
        return
    refundable = await get_payment_refundable_amount(
        session,
        payment_id=payment.id,
    )
    if refundable <= 0:
        await callback.answer("Возвращаемого остатка уже нет", show_alert=True)
        return
    await state.clear()
    builder = InlineKeyboardBuilder()
    builder.button(
        text=f"✅ Подтвердить возврат {int(refundable)} ₽",
        callback_data=f"admin_payment_refund_confirm:{payment.id}",
    )
    builder.button(
        text="← Назад к платежу",
        callback_data=f"admin_payment_card:{payment.id}",
    )
    builder.adjust(1)
    await callback.message.edit_text(
        "⚠️ <b>Подтверждение возврата</b>\n\n"
        f"Платёж: <code>#{payment.id}</code>\n"
        f"YooKassa ID: <code>{safe(payment.external_id)}</code>\n"
        f"Будет возвращено: <b>{int(refundable)} RUB</b>\n\n"
        "Сумма сначала будет заморожена на внутреннем балансе, "
        "затем durable worker отправит идемпотентный запрос в YooKassa.",
        reply_markup=builder.as_markup(),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data.startswith("admin_payment_refund_confirm:"))
async def enqueue_payment_refund(
    callback: CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
):
    if not is_admin(callback.from_user.id):
        await callback.answer(texts.ERROR_ACCESS_DENIED, show_alert=True)
        return
    payment_id = parse_callback_id(callback.data, 1)
    if payment_id is None:
        await callback.answer("Некорректный запрос", show_alert=True)
        return
    try:
        request = await request_balance_topup_refund(
            session,
            payment_id=payment_id,
            requested_by_admin_id=callback.from_user.id,
        )
    except BalanceRefundError as exc:
        messages = {
            "payment_not_found": "Платёж не найден",
            "refund_requires_balance_topup": "Можно вернуть только пополнение баланса",
            "payment_not_refundable": "Платёж ещё не подтверждён или уже возвращён",
            "provider_payment_id_missing": "У платежа нет YooKassa ID",
            "no_refundable_balance": "Возвращаемого остатка уже нет",
            "active_refund_reservation_missing": "Возврат требует ручной проверки",
        }
        await callback.answer(
            messages.get(exc.code, "Не удалось поставить возврат в очередь"),
            show_alert=True,
        )
        return

    if request.created:
        message = (
            f"Возврат {int(request.operation.amount)} ₽ поставлен в durable-очередь. "
            f"Operation: {request.operation.operation_id}"
        )
    else:
        message = (
            f"Этот возврат уже обрабатывается. "
            f"Operation: {request.operation.operation_id}"
        )
    await callback.answer(message, show_alert=True)
    callback.data = f"admin_payment_card:{payment_id}"
    await show_payment_card(callback, state, session)
