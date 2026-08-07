import logging
import math

from aiogram import Router, F
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from bot import texts
from bot.keyboards import get_back_button
from bot.states import AdminStates
from database.repositories.users_repo import (
    get_user_by_telegram_id,
    get_user_count,
    get_users_paginated_with_profiles,
)
from utils.admin import is_admin
from utils.callbacks import parse_callback_id
from utils.telegram import render_hub

from .common import (
    USERS_PER_PAGE,
    _build_users_list_text_and_kb,
    _get_user_with_profiles,
    _render_user_card,
    _show_user_card_edit,
)

router = Router()
logger = logging.getLogger(__name__)


@router.callback_query(F.data == "admin_users")
async def show_users_list(
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

    await callback.answer(show_alert=False)
    await state.clear()

    total_users = await get_user_count(session)

    total_pages = max(
        1,
        math.ceil(total_users / USERS_PER_PAGE),
    )

    users = await get_users_paginated_with_profiles(
        session,
        page=1,
        per_page=USERS_PER_PAGE,
    )

    rendered, kb = await _build_users_list_text_and_kb(
        users,
        1,
        total_pages,
        total_users,
    )

    try:
        await callback.message.edit_text(
            rendered,
            reply_markup=kb.as_markup(),
            parse_mode="HTML",
        )
    except TelegramBadRequest as e:
        logger.debug(f"show_users_list edit_text failed: {e}")


@router.callback_query(F.data.startswith("admin_users_page:"))
async def users_pagination(
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
            texts.UI_BOT_HANDLERS_ADMIN_USERS_LIST_ROUTES_L97_1,
            show_alert=True,
        )
        return

    await callback.answer(show_alert=False)
    await state.clear()

    total_users = await get_user_count(session)

    total_pages = max(
        1,
        math.ceil(total_users / USERS_PER_PAGE),
    )

    if page > total_pages:
        page = total_pages

    users = await get_users_paginated_with_profiles(
        session,
        page=page,
        per_page=USERS_PER_PAGE,
    )

    rendered, kb = await _build_users_list_text_and_kb(
        users,
        page,
        total_pages,
        total_users,
    )

    try:
        await callback.message.edit_text(
            rendered,
            reply_markup=kb.as_markup(),
            parse_mode="HTML",
        )
    except TelegramBadRequest as e:
        logger.debug(f"users_pagination edit_text failed: {e}")


@router.callback_query(F.data == "admin_users_search")
async def start_search_user(
    callback: CallbackQuery,
    state: FSMContext,
):
    if not is_admin(callback.from_user.id):
        await callback.answer(
            texts.ERROR_ACCESS_DENIED,
            show_alert=True,
        )
        return

    await callback.answer(show_alert=False)
    await state.clear()

    try:
        await callback.message.edit_text(
            texts.ADMIN_USER_SEARCH_PROMPT,
            reply_markup=get_back_button("admin_users"),
        )
    except TelegramBadRequest as e:
        logger.debug(f"start_search_user edit_text failed: {e}")

    await state.set_state(AdminStates.searching_user)


@router.message(AdminStates.searching_user)
async def process_search_user(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
):
    if not is_admin(message.from_user.id):
        await state.clear()
        return

    if not message.text:
        await render_hub(
            message.bot,
            message.chat.id,
            texts.ERROR_NUMERIC_ID,
            get_back_button("admin_users"),
        )
        return

    if message.text.startswith("/"):
        await state.clear()
        return

    try:
        telegram_id = int(message.text.strip())
    except ValueError:
        await render_hub(
            message.bot,
            message.chat.id,
            texts.ERROR_NUMERIC_ID,
            get_back_button("admin_users"),
        )
        return

    user = await get_user_by_telegram_id(session, telegram_id)

    if not user:
        await render_hub(
            message.bot,
            message.chat.id,
            texts.UI_BOT_HANDLERS_ADMIN_USERS_LIST_ROUTES_L204_1.format(value_0=telegram_id),
            get_back_button("admin_users"),
        )

        await state.clear()

        return

    await _show_user_card_edit(message, user, session)

    await state.clear()


@router.callback_query(F.data.startswith("admin_user_card:"))
async def show_user_card(
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

    telegram_id = parse_callback_id(callback.data, 1)

    if telegram_id is None:
        await callback.answer(
            texts.UI_BOT_HANDLERS_ADMIN_USERS_LIST_ROUTES_L234_1,
            show_alert=True,
        )
        return

    await callback.answer(show_alert=False)
    await state.clear()

    user = await _get_user_with_profiles(session, telegram_id)
    if not user:
        await callback.answer(
            texts.ERROR_USER_NOT_FOUND,
            show_alert=True,
        )
        return

    await _render_user_card(callback, user, session)


@router.callback_query(F.data.startswith("admin_user_audit:"))
async def show_user_audit(
    callback: CallbackQuery,
    session: AsyncSession,
):
    if not is_admin(callback.from_user.id):
        await callback.answer(texts.ERROR_ACCESS_DENIED, show_alert=True)
        return

    parts = callback.data.split(":")
    if len(parts) < 2:
        await callback.answer(texts.ERROR_USER_NOT_FOUND, show_alert=True)
        return

    telegram_id = int(parts[1]) if parts[1].isdigit() else None
    page = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 1

    if telegram_id is None:
        await callback.answer(texts.ERROR_USER_NOT_FOUND, show_alert=True)
        return

    await callback.answer(show_alert=False)
    user = await _get_user_with_profiles(session, telegram_id)
    if not user:
        await callback.answer(texts.ERROR_USER_NOT_FOUND, show_alert=True)
        return

    import math
    from aiogram.utils.keyboard import InlineKeyboardBuilder
    from database.repositories.audit_repo import (
        get_user_audit_logs,
        get_user_audit_logs_count,
    )
    from utils.formatters import format_datetime

    page_size = 10
    offset = (page - 1) * page_size
    total_count = await get_user_audit_logs_count(session, user_id=user.id)
    total_pages = max(1, math.ceil(total_count / page_size))
    page = min(max(1, page), total_pages)
    offset = (page - 1) * page_size

    logs = await get_user_audit_logs(
        session, user_id=user.id, offset=offset, limit=page_size
    )

    action_map = {
        "ACCOUNT_TARIFF_CHANGE_SETTLED": "🔄 Смена тарифа",
        "ACCOUNT_PURCHASE_SETTLED": "🛒 Покупка тарифа",
        "TOPUP_USER_BALANCE": "💳 Начисление баланса",
        "ADMIN_BALANCE_TOPUP": "➕ Начисление баланса админом",
        "ADMIN_BALANCE_DEDUCT": "➖ Списание баланса админом",
        "ADMIN_SUB_GRANT": "🎁 Выдача подписки админом",
        "ADMIN_SUB_EXTEND": "⏳ Продление подписки админом",
        "ADMIN_SUB_REDUCE": "✂️ Сокращение подписки админом",
        "ADMIN_SUB_CHANGE": "⚙️ Изменение подписки админом",
        "BAN_USER": "🚫 Блокировка пользователя",
        "UNBAN_USER": "✅ Разблокировка пользователя",
        "DEVICE_CREATE": "📱 Создание устройства",
        "DEVICE_DELETE": "🗑 Удаление устройства",
        "PAYMENT_SUCCESS": "✅ Оплата (YooKassa)",
        "PAYMENT_FAILED": "❌ Ошибка оплаты",
        "REFUND": "↩️ Возврат средств",
        "ADMIN_DIRECT_MESSAGE_SENT": "✉️ Сообщение от админа",
    }

    lines = [
        f"📜 <b>История действий пользователя ID {user.telegram_id}:</b>",
        f"<i>Всего записей: {total_count}</i>\n",
    ]
    if not logs:
        lines.append("<i>История действий пуста.</i>")
    else:
        for item in logs:
            dt = format_datetime(item.created_at)
            action_text = action_map.get(item.action, item.action or "Действие")
            details_text = ""
            if item.details:
                if "debit=" in item.details and "conversion=" in item.details:
                    details_text = ""
                else:
                    details_text = f" — {item.details}"
            lines.append(f"• <code>[{dt}]</code> {action_text}{details_text}")

    builder = InlineKeyboardBuilder()
    if total_pages > 1:
        if page > 1:
            builder.button(
                text="◀️ Назад",
                callback_data=f"admin_user_audit:{telegram_id}:{page - 1}",
            )
        else:
            builder.button(text=" ⏹ ", callback_data="ignore")

        builder.button(
            text=f"Стр {page}/{total_pages}",
            callback_data="ignore",
        )

        if page < total_pages:
            builder.button(
                text="Вперед ▶️",
                callback_data=f"admin_user_audit:{telegram_id}:{page + 1}",
            )
        else:
            builder.button(text=" ⏹ ", callback_data="ignore")

        builder.adjust(3, 1)
    else:
        builder.adjust(1)

    builder.button(
        text="🔙 К карточке пользователя",
        callback_data=f"admin_user_card:{telegram_id}",
    )

    try:
        await callback.message.edit_text(
            "\n".join(lines),
            reply_markup=builder.as_markup(),
            parse_mode="HTML",
        )
    except TelegramBadRequest:
        pass