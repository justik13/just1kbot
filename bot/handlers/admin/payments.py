import logging
import math

from aiogram import Router, F
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from bot import texts
from bot.keyboards import get_back_button
from database.models import Payment
from database.repositories.payments_repo import (
    get_payment_by_id,
)
from services.audit_service import AuditService
from utils.admin import is_admin
from utils.callbacks import parse_callback_id
from utils.formatters import format_datetime
from utils.telegram import render_hub, safe
from utils.text_limits import truncate_button_text

router = Router()
logger = logging.getLogger(__name__)

PAYMENTS_PER_PAGE = 20


# ──────────────────────────────────────────────────────────────
# ИСПРАВЛЕНО: убрана кнопка «↩️ Вернуть (chargeback)».
# Возвраты делаются лично через кабинет YooKassa.
# ──────────────────────────────────────────────────────────────
def _get_payment_card_keyboard(
    payment_id: int,
    status: str,
    user_telegram_id: int | None,
) -> "InlineKeyboardBuilder":
    builder = InlineKeyboardBuilder()

    if status == "requires_manual_review":
        builder.button(
            text="✅ Выдать подписку",
            callback_data=f"admin_manual_grant:{payment_id}",
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
    payments, page: int, total_pages: int, total: int,
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
                payment.status, "❓",
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
    callback: CallbackQuery, session: AsyncSession, page: int = 1,
):
    total_payments = await session.scalar(
        select(func.count(Payment.id)),
    ) or 0
    total_pages = max(
        1, math.ceil(total_payments / PAYMENTS_PER_PAGE),
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
        payments, page, total_pages, total_payments,
    )
    try:
        await callback.message.edit_text(
            rendered, reply_markup=kb.as_markup(), parse_mode="HTML",
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
            texts.ERROR_ACCESS_DENIED, show_alert=True,
        )
        return
    await state.clear()
    await _show_payments_list(callback, session, page=1)
    await callback.answer()


@router.callback_query(F.data.startswith("admin_payments_page:"))
async def payments_pagination(
    callback: CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
):
    if not is_admin(callback.from_user.id):
        await callback.answer(
            texts.ERROR_ACCESS_DENIED, show_alert=True,
        )
        return
    page = parse_callback_id(callback.data, 1)
    if page is None or page < 1:
        await callback.answer(
            "Некорректный запрос", show_alert=True,
        )
        return
    await state.clear()
    await _show_payments_list(callback, session, page=page)
    await callback.answer()


@router.callback_query(F.data.startswith("admin_payment_card:"))
async def show_payment_card(
    callback: CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
):
    if not is_admin(callback.from_user.id):
        await callback.answer(
            texts.ERROR_ACCESS_DENIED, show_alert=True,
        )
        return
    payment_id = parse_callback_id(callback.data, 1)
    if payment_id is None:
        await callback.answer(
            "Некорректный запрос", show_alert=True,
        )
        return
    await state.clear()
    payment = await get_payment_by_id(session, payment_id)
    if not payment:
        await callback.answer(
            "Платёж не найден", show_alert=True,
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
        payment.status, payment.status,
    )
    status_icon = texts.PAYMENT_STATUS_ICONS.get(
        payment.status, "❓",
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

    rendered = (
        f"🛠 Админка › 💳 Платежи › "
        f"<b>Платёж #{payment.id}</b>\n"
        f"<b>ID:</b> {payment.id}\n"
        f"<b>Пользователь:</b> {user_label}\n"
        f"<b>Сумма:</b> {payment.amount} {payment.currency}\n"
        f"<b>Статус:</b> {status_icon} {status_name}\n"
        f"<b>Тариф:</b> {tariff_label}\n"
        f"<b>Создан:</b> {format_datetime(payment.created_at)}\n"
        f"<b>Оплачен:</b> {format_datetime(payment.paid_at)}\n"
        f"<b>External ID:</b> "
        f"<code>{safe(payment.external_id or '—')}</code>"
        f"{reason_line}"
    )

    kb = _get_payment_card_keyboard(
        payment.id, payment.status, user_telegram_id,
    )

    try:
        await callback.message.edit_text(
            rendered, reply_markup=kb.as_markup(), parse_mode="HTML",
        )
    except TelegramBadRequest as e:
        logger.debug("show_payment_card edit_text failed: %s", e)
    await callback.answer()