import logging
import math

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy.ext.asyncio import AsyncSession

from bot import texts
from database.repositories.purchases_repo import (
    get_purchase_log_by_id,
    get_purchase_logs_paginated,
)
from utils.admin import is_admin
from utils.callbacks import parse_callback_id
from utils.formatters import format_admin_breadcrumbs, format_datetime
from utils.telegram import safe
from utils.text_limits import truncate_button_text

router = Router()
logger = logging.getLogger(__name__)

PURCHASES_PER_PAGE = 10


async def _show_purchases_list(
    callback: CallbackQuery,
    session: AsyncSession,
    page: int = 1,
):
    entries, total = await get_purchase_logs_paginated(
        session, page=page, per_page=PURCHASES_PER_PAGE
    )
    total_pages = max(1, math.ceil(total / PURCHASES_PER_PAGE))
    page = min(max(1, page), total_pages)

    header = format_admin_breadcrumbs("🛒 Покупки", f"Стр. {page}/{total_pages}")
    rendered = (
        f"{header}"
        f"🛒 <b>Логи покупок пользователей</b> (Стр. {page}/{total_pages}, всего: {total})\n\n"
    )

    builder = InlineKeyboardBuilder()

    if not entries:
        rendered += "<i>Покупки не найдены.</i>"
    else:
        for idx, entry in enumerate(entries, start=1):
            dt_str = format_datetime(entry.created_at)
            amount_str = f"{int(entry.amount_rub)} ₽" if entry.amount_rub > 0 else "0 ₽ (Бонус)"
            rendered += (
                f"<b>{idx}. {entry.user_label}</b> | {entry.operation_title}\n"
                f"   💎 {entry.tariff_name} ({entry.duration_days} дн., {entry.device_limit} устр.) — <b>{amount_str}</b>\n"
                f"   🕒 {dt_str}\n\n"
            )

            button_text = truncate_button_text(
                f"🛒 #{entry.numeric_id} | {entry.user_label} | {amount_str}"
            )
            builder.button(
                text=button_text,
                callback_data=f"admin_purchase_card:{entry.id}",
            )

    nav_buttons = 0
    if page > 1:
        builder.button(
            text="◀️ Назад",
            callback_data=f"admin_purchases_page:{page - 1}",
        )
        nav_buttons += 1

    if page < total_pages:
        builder.button(
            text="Вперед ▶️",
            callback_data=f"admin_purchases_page:{page + 1}",
        )
        nav_buttons += 1

    builder.button(text="💳 К платежам", callback_data="admin_payments")
    builder.button(text="🔙 В админ-меню", callback_data="admin_menu")

    adjust_pattern = [1] * len(entries)
    if nav_buttons > 0:
        adjust_pattern.append(nav_buttons)
    adjust_pattern.extend([1, 1])

    builder.adjust(*adjust_pattern)

    try:
        await callback.message.edit_text(
            rendered,
            reply_markup=builder.as_markup(),
            parse_mode="HTML",
        )
    except TelegramBadRequest as e:
        logger.debug(f"_show_purchases_list edit_text failed: {e}")


@router.callback_query(F.data == "admin_purchases")
async def show_purchases(
    callback: CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
):
    if not is_admin(callback.from_user.id):
        await callback.answer(texts.ERROR_ACCESS_DENIED, show_alert=True)
        return

    await state.clear()
    await callback.answer(show_alert=False)
    await _show_purchases_list(callback, session, page=1)


@router.callback_query(F.data.startswith("admin_purchases_page:"))
async def purchases_pagination(
    callback: CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
):
    if not is_admin(callback.from_user.id):
        await callback.answer(texts.ERROR_ACCESS_DENIED, show_alert=True)
        return

    page = parse_callback_id(callback.data, 1) or 1
    await state.clear()
    await callback.answer(show_alert=False)
    await _show_purchases_list(callback, session, page=page)


@router.callback_query(F.data.startswith("admin_purchase_card:"))
async def show_purchase_card(
    callback: CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
):
    if not is_admin(callback.from_user.id):
        await callback.answer(texts.ERROR_ACCESS_DENIED, show_alert=True)
        return

    parts = callback.data.split(":", 1)
    if len(parts) < 2:
        await callback.answer("Запись покупки не найдена", show_alert=True)
        return

    entry_id = parts[1]
    entry = await get_purchase_log_by_id(session, entry_id)
    if not entry:
        await callback.answer("Покупка не найдена", show_alert=True)
        return

    await state.clear()
    await callback.answer(show_alert=False)

    header = format_admin_breadcrumbs("🛒 Покупки", f"Детали #{entry.numeric_id}")
    amount_str = f"{int(entry.amount_rub)} ₽" if entry.amount_rub > 0 else "0 ₽ (Бонус/Выдача)"
    dt_str = format_datetime(entry.created_at)

    rendered = (
        f"{header}"
        f"🛒 <b>Детали покупки / транзакции #{entry.numeric_id}</b>\n\n"
        f"👤 <b>Пользователь:</b> {entry.user_label} (Telegram ID: <code>{entry.telegram_id}</code>)\n"
        f"💎 <b>Тариф:</b> {safe(entry.tariff_name)}\n"
        f"📱 <b>Лимит устройств:</b> {entry.device_limit} шт.\n"
        f"⏳ <b>Длительность:</b> {entry.duration_days} дней\n"
        f"💳 <b>Сумма:</b> <b>{amount_str}</b>\n"
        f"⚙️ <b>Тип операции:</b> {entry.operation_title}\n"
        f"🕒 <b>Дата и время:</b> {dt_str}\n"
    )

    builder = InlineKeyboardBuilder()
    if entry.telegram_id:
        builder.button(
            text="👤 Карточка пользователя",
            callback_data=f"admin_user_card:{entry.telegram_id}",
        )
    builder.button(text="🛒 К списку покупок", callback_data="admin_purchases")
    builder.button(text="🔙 В админ-меню", callback_data="admin_menu")
    builder.adjust(1)

    try:
        await callback.message.edit_text(
            rendered,
            reply_markup=builder.as_markup(),
            parse_mode="HTML",
        )
    except TelegramBadRequest as e:
        logger.debug(f"show_purchase_card edit_text failed: {e}")
