import logging
import math

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bot import texts
from bot.keyboards import get_back_button
from bot.states import AdminStates
from database.models import Server, Tariff
from database.repositories.users_repo import (
    get_filtered_users_count,
    get_filtered_users_paginated,
)
from utils.admin import is_admin
from utils.callbacks import parse_callback_id
from utils.formatters import format_audit_details
from utils.telegram import render_hub, safe

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
        await callback.answer(texts.ERROR_ACCESS_DENIED, show_alert=True)
        return

    await callback.answer(show_alert=False)
    await state.clear()

    total_users = await get_filtered_users_count(session, filter_type="all")
    total_pages = max(1, math.ceil(total_users / USERS_PER_PAGE))
    users = await get_filtered_users_paginated(
        session, filter_type="all", filter_param=None, page=1, per_page=USERS_PER_PAGE
    )

    rendered, kb = await _build_users_list_text_and_kb(
        users, 1, total_pages, total_users, filter_type="all", filter_param="none"
    )

    try:
        await callback.message.edit_text(rendered, reply_markup=kb.as_markup(), parse_mode="HTML")
    except TelegramBadRequest as e:
        logger.debug(f"show_users_list edit_text failed: {e}")


@router.callback_query(F.data.startswith("admin_users_filter_menu:"))
async def show_extended_filter_menu(
    callback: CallbackQuery,
    session: AsyncSession,
):
    if not is_admin(callback.from_user.id):
        await callback.answer(texts.ERROR_ACCESS_DENIED, show_alert=True)
        return

    filter_type = callback.data.split(":", 1)[1]
    builder = InlineKeyboardBuilder()

    if filter_type == "server":
        rows = (await session.scalars(select(Server).order_by(Server.name))).all()
        if not rows:
            await callback.answer(texts.ADMIN_USERS_LIST_SERVEROV_NET, show_alert=True)
            return
        for server in rows:
            flag = server.country_flag or "🌐"
            builder.button(
                text=f"🖥 {flag} {server.name}",
                callback_data=f"admin_users_filter:server:{server.id}:1",
            )
        title = texts.ADMIN_USERS_LIST_SELECT_SERVER
    elif filter_type == "country":
        rows = (
            await session.execute(
                select(Server.country_flag)
                .where(Server.country_flag.is_not(None))
                .distinct()
                .order_by(Server.country_flag)
            )
        ).scalars().all()
        if not rows:
            await callback.answer(texts.ADMIN_USERS_LIST_STRAN_NET, show_alert=True)
            return
        for country in rows:
            builder.button(
                text=f"🌐 {country}",
                callback_data=f"admin_users_filter:country:{country}:1",
            )
        title = texts.ADMIN_USERS_LIST_SELECT_STRANU
    elif filter_type == "tariff":
        rows = (await session.scalars(select(Tariff).where(Tariff.is_active.is_(True)).order_by(Tariff.device_limit, Tariff.id))).all()
        if not rows:
            await callback.answer(texts.ADMIN_USERS_LIST_TARIFOV_NET, show_alert=True)
            return
        from utils.tariff_names import get_tariff_group_name
        seen_limits = set()
        for tariff in rows:
            limit = tariff.device_limit
            if limit in seen_limits:
                continue
            seen_limits.add(limit)
            label = get_tariff_group_name(limit)
            builder.button(
                text=f"💎 {label}",
                callback_data=f"admin_users_filter:tariff:{limit}:1",
            )
        title = texts.ADMIN_USERS_LIST_SELECT_TARIFF
    else:
        await callback.answer(texts.ADMIN_USERS_LIST_NEIZVESTNYY_FILTR, show_alert=True)
        return

    builder.button(text=texts.ADMIN_USERS_LIST_NAZAD, callback_data="admin_users")
    builder.adjust(1)
    await callback.answer(show_alert=False)
    try:
        await callback.message.edit_text(title, reply_markup=builder.as_markup(), parse_mode="HTML")
    except TelegramBadRequest as e:
        logger.debug(f"show_extended_filter_menu edit_text failed: {e}")


@router.callback_query(F.data.startswith("admin_users_filter:"))
async def users_filter_pagination(
    callback: CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
):
    if not is_admin(callback.from_user.id):
        await callback.answer(texts.ERROR_ACCESS_DENIED, show_alert=True)
        return

    parts = callback.data.split(":")
    if len(parts) == 3:
        filter_type = parts[1]
        filter_param = "none"
        try:
            page = int(parts[2])
        except ValueError:
            page = 1
    elif len(parts) >= 4:
        filter_type = parts[1]
        filter_param = parts[2]
        try:
            page = int(parts[3])
        except ValueError:
            page = 1
    else:
        filter_type = "all"
        filter_param = "none"
        page = 1

    param_val = None if filter_param == "none" else filter_param
    if filter_type in {"server", "tariff"} and param_val is not None and not str(param_val).isdigit():
        await callback.answer(texts.ADMIN_USERS_LIST_NEKORREKTNYY_PARAMETR_FILTRA, show_alert=True)
        return

    total_users = await get_filtered_users_count(
        session, filter_type=filter_type, filter_param=param_val
    )

    if total_users == 0:
        await callback.answer(texts.ADMIN_USERS_LIST_USERS_NE_NAYDENY_PO_FILT, show_alert=True)
    else:
        await callback.answer(show_alert=False)
    await state.clear()

    total_pages = max(1, math.ceil(total_users / USERS_PER_PAGE))
    page = min(max(1, page), total_pages)

    users = await get_filtered_users_paginated(
        session,
        filter_type=filter_type,
        filter_param=param_val,
        page=page,
        per_page=USERS_PER_PAGE,
    )

    rendered, kb = await _build_users_list_text_and_kb(
        users,
        page,
        total_pages,
        total_users,
        filter_type=filter_type,
        filter_param=filter_param,
    )

    try:
        await callback.message.edit_text(
            rendered,
            reply_markup=kb.as_markup(),
            parse_mode="HTML",
        )
    except TelegramBadRequest as e:
        logger.debug(f"users_filter_pagination edit_text failed: {e}")


@router.callback_query(F.data.startswith("admin_users_page:"))
async def users_pagination(
    callback: CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
):
    if not is_admin(callback.from_user.id):
        await callback.answer(texts.ERROR_ACCESS_DENIED, show_alert=True)
        return

    page = parse_callback_id(callback.data, 1) or 1
    await callback.answer(show_alert=False)
    await state.clear()

    total_users = await get_filtered_users_count(session, filter_type="all")
    total_pages = max(1, math.ceil(total_users / USERS_PER_PAGE))
    page = min(max(1, page), total_pages)

    users = await get_filtered_users_paginated(
        session, filter_type="all", page=page, per_page=USERS_PER_PAGE
    )

    rendered, kb = await _build_users_list_text_and_kb(
        users, page, total_pages, total_users, filter_type="all", filter_param="none"
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
        await callback.answer(texts.ERROR_ACCESS_DENIED, show_alert=True)
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
            texts.ADMIN_USERS_LIST_ENTER_USERNAME_TELEGRAM_ID_I,
            get_back_button("admin_users"),
        )
        return

    if message.text.startswith("/"):
        await state.clear()
        return

    from database.repositories.users_repo import search_user_flexible
    user = await search_user_flexible(session, message.text)

    if not user:
        await render_hub(
            message.bot,
            message.chat.id,
            texts.ADMIN_USERS_LIST_USER_PO_ZAPROSU_NE_NAYDE.format(safe_message_text=safe(message.text)),
            get_back_button("admin_users"),
            parse_mode="HTML",
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
        await callback.answer(texts.ERROR_ACCESS_DENIED, show_alert=True)
        return

    telegram_id = parse_callback_id(callback.data, 1)

    if telegram_id is None:
        await callback.answer(texts.ERROR_INVALID_REQUEST, show_alert=True)
        return

    await callback.answer(show_alert=False)
    await state.clear()

    user = await _get_user_with_profiles(session, telegram_id)
    if not user:
        await callback.answer(texts.ERROR_USER_NOT_FOUND, show_alert=True)
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

    from database.repositories.audit_repo import (
        get_user_audit_logs,
        get_user_audit_logs_count,
    )
    from utils.formatters import format_datetime

    page_size = 10
    total_count = await get_user_audit_logs_count(session, user_id=user.id, telegram_id=user.telegram_id)
    total_pages = max(1, math.ceil(total_count / page_size))
    page = min(max(1, page), total_pages)
    offset = (page - 1) * page_size
    logs = await get_user_audit_logs(session, user_id=user.id, telegram_id=user.telegram_id, offset=offset, limit=page_size)

    action_map = {
        # User lifecycle
        "USER_REGISTER": "👋 Регистрация",
        "REFERRAL_ATTACHED": "🤝 Привязка реферала",
        "USER_RESTORED": "♻️ Восстановление аккаунта",
        # Balance & payments
        "PAYMENT_SUCCESS": "💳 Пополнение баланса",
        "TOPUP_USER_BALANCE": "💳 Начисление баланса",
        "ADMIN_BALANCE_TOPUP": "➕ Начисление баланса админом",
        "ADMIN_BALANCE_DEDUCT": "➖ Списание баланса админом",
        "DEDUCT_USER_BALANCE": "➖ Списание баланса админом",
        "MASS_BONUS_GRANTED": "🎁 Массовый бонус",
        "REFERRAL_BONUS_GRANTED": "🎁 Реферальный бонус",
        "WELCOME_BONUS_GRANTED": "🎁 Приветственный бонус",
        # Subscriptions
        "ACCOUNT_PURCHASE_SETTLED": "🛒 Покупка тарифа",
        "ACCOUNT_TARIFF_CHANGE_SETTLED": "🔄 Смена тарифа",
        "ADMIN_SUB_GRANT": "🎁 Выдача подписки админом",
        "GRANT": "🎁 Выдача подписки админом",
        "ADMIN_SUB_EXTEND": "⏳ Продление подписки админом",
        "EXTEND": "⏳ Продление подписки админом",
        "ADMIN_SUB_REDUCE": "✂️ Сокращение подписки админом",
        "REDUCE": "✂️ Сокращение подписки админом",
        "ADMIN_SUB_CHANGE": "⚙️ Изменение тарифа админом",
        "CHANGE_TARIFF": "⚙️ Изменение тарифа",
        "SUB_EXPIRED": "⌛ Истечение срока подписки",
        # Devices
        "DEVICE_CREATE": "📱 Создание устройства",
        "DEVICE_CREATED": "📱 Создание устройства",
        "DEVICE_DELETE": "🗑 Удаление устройства",
        "DEVICE_DELETED": "🗑 Удаление устройства",
        "DEVICE_RENAME": "✏️ Переименование устройства",
        "ADMIN_DEVICE_DELETE": "🗑 Удаление устройства админом",
        "CLEANUP_DEVICE_DELETE": "🧹 Автоудаление устройства",
        # Moderation
        "BAN": "🚫 Блокировка пользователя",
        "BAN_USER": "🚫 Блокировка пользователя",
        "UNBAN": "✅ Разблокировка пользователя",
        "UNBAN_USER": "✅ Разблокировка пользователя",
        "ADMIN_DIRECT_MESSAGE_SENT": "✉️ Сообщение от админа",
        "ADMIN_DIRECT_MESSAGE": "✉️ Сообщение от админа",
        # Disputes & refunds
        "PAYMENT_DISPUTE_OPENED": "⚠️ Открыт спор по платежу",
        "PAYMENT_DISPUTE_RESOLVED": "⚖️ Спор по платежу разрешён",
        "PAYMENT_DISPUTE_MANUAL_REVIEW": "🧪 Спор на проверке",
        "BALANCE_REFUND_REQUESTED": "↩️ Запрос возврата средств",
        "PAYMENT_FAILED": "❌ Ошибка оплаты",
        "REFUND": "↩️ Возврат средств",
    }

    lines = [
        texts.ADMIN_USERS_LIST_HISTORY_DEYSTVIY_POLZOVATELYA.format(user_telegram_id=user.telegram_id),
        texts.ADMIN_USERS_LIST_TOTAL_ZAPISEY.format(total_count=total_count),
    ]
    if not logs:
        lines.append(texts.ADMIN_USERS_LIST_HISTORY_DEYSTVIY_PUSTA)
    else:
        for item in logs:
            dt = format_datetime(item.created_at)
            action_text = safe(action_map.get(item.action, item.action or texts.ADMIN_USERS_LIST_ACTION))
            details_text = safe(format_audit_details(item.details))
            lines.append(f"• <code>[{dt}]</code> {action_text}{details_text}")

    builder = InlineKeyboardBuilder()
    if total_pages > 1:
        if page > 1:
            builder.button(text=texts.BTN_BACK, callback_data=f"admin_user_audit:{telegram_id}:{page - 1}")
        else:
            builder.button(text=" ⏹ ", callback_data="ignore")
        builder.button(text=texts.ADMIN_USERS_LIST_STR.format(page=page, total_pages=total_pages), callback_data="ignore")
        if page < total_pages:
            builder.button(text=texts.BTN_PAGINATION_NEXT, callback_data=f"admin_user_audit:{telegram_id}:{page + 1}")
        else:
            builder.button(text=" ⏹ ", callback_data="ignore")
    builder.button(text=texts.ADMIN_USERS_LIST_K_KARTOCHKE_POLZOVATELYA, callback_data=f"admin_user_card:{telegram_id}")
    if total_pages > 1:
        builder.adjust(3, 1)
    else:
        builder.adjust(1)

    try:
        await callback.message.edit_text(
            "\n".join(lines),
            reply_markup=builder.as_markup(),
            parse_mode="HTML",
        )
    except TelegramBadRequest:
        pass
