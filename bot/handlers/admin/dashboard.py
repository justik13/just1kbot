import logging
from datetime import timedelta

from aiogram import Router, F
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from bot import texts
from bot.handlers.admin.disputes import router as disputes_router
from bot.keyboards import (
    get_admin_menu,
    get_audit_keyboard,
    get_back_button,
    get_maintenance_confirm_keyboard,
)
from bot.states import AdminStates
from database.models import Payment
from database.repositories.audit_repo import get_recent_audit_logs
from database.repositories.servers_repo import get_total_free_ips
from database.repositories.system_settings_repo import get_system_setting, set_system_setting
from database.repositories.users_repo import get_dashboard_stats
from services.audit_service import AuditService
from services.maintenance_service import MaintenanceService
from utils.admin import is_admin
from utils.datetime_helpers import now_utc
from utils.formatters import format_admin_breadcrumbs, format_datetime
from utils.telegram import render_hub, safe
from utils.text_limits import truncate_details

router = Router()
router.include_router(disputes_router)
logger = logging.getLogger(__name__)


async def _get_financial_stats(session: AsyncSession) -> dict:
    now = now_utc()
    since_24h = now - timedelta(hours=24)
    since_7d = now - timedelta(days=7)
    since_30d = now - timedelta(days=30)

    stmt_24h = select(func.coalesce(func.sum(Payment.amount), 0), func.count(Payment.id)).where(
        Payment.provider_status == "succeeded",
        Payment.created_at >= since_24h,
    )
    stmt_7d = select(func.coalesce(func.sum(Payment.amount), 0)).where(
        Payment.provider_status == "succeeded",
        Payment.created_at >= since_7d,
    )
    stmt_30d = select(func.coalesce(func.sum(Payment.amount), 0), func.count(Payment.id)).where(
        Payment.provider_status == "succeeded",
        Payment.created_at >= since_30d,
    )

    res_24h = (await session.execute(stmt_24h)).one()
    res_7d = (await session.execute(stmt_7d)).scalar_one()
    res_30d = (await session.execute(stmt_30d)).one()

    rev_24h = int(res_24h[0])
    count_24h = int(res_24h[1])
    rev_7d = int(res_7d)
    rev_30d = int(res_30d[0])
    count_30d = int(res_30d[1])
    avg_check = int(rev_30d / count_30d) if count_30d > 0 else 0

    return {
        "rev_24h": rev_24h,
        "count_24h": count_24h,
        "rev_7d": rev_7d,
        "rev_30d": rev_30d,
        "avg_check": avg_check,
    }


async def _get_disputes_count(session: AsyncSession) -> int:
    from database.dispute_models import PaymentDispute
    stmt = select(func.count(PaymentDispute.id)).where(
        PaymentDispute.status.in_(["open", "manual_review"])
    )
    return int((await session.scalar(stmt)) or 0)


async def _get_dead_queues_count(session: AsyncSession) -> int:
    from services.payment_queue_health import get_payment_queue_health_snapshot
    try:
        snapshot = await get_payment_queue_health_snapshot(session)
        return sum(q.dead for q in snapshot.queues)
    except Exception:
        return 0


async def _get_servers_capacity_summary(session: AsyncSession) -> str:
    from database.repositories.servers_repo import get_all_active_servers
    servers = await get_all_active_servers(session)
    if not servers:
        return "<i>Серверов пока нет</i>"
    lines = []
    for s in servers:
        flag = s.country_flag or "🌐"
        used = getattr(s, "current_clients_count", 0) or 0
        total = s.max_clients or 240
        pct = int((used / total) * 100) if total > 0 else 0
        status_icon = "🟢" if pct < 80 else ("🟡" if pct < 90 else "🔴")
        lines.append(f"{status_icon} {flag} <b>{safe(s.name)}</b>: {used}/{total} ({pct}%)")
    return "\n".join(lines)


async def _show_admin_dashboard(
    callback: CallbackQuery,
    session: AsyncSession,
) -> None:
    stats = await get_dashboard_stats(session)
    free_ips = await get_total_free_ips(session)
    fin_stats = await _get_financial_stats(session)
    disputes_count = await _get_disputes_count(session)
    dead_queues_count = await _get_dead_queues_count(session)
    servers_summary = await _get_servers_capacity_summary(session)
    maintenance_enabled = await MaintenanceService.is_enabled(session)

    header = format_admin_breadcrumbs("Главный Дашборд")
    text = (
        f"{header}"
        f"📊 <b>Пользователи и Подписки:</b>\n"
        f"• Всего пользователей: <b>{stats['total']}</b>\n"
        f"• Активных подписок: <b>{stats['active']}</b>\n"
        f"• Новых за 24 часа: <b>{stats['new_24h']}</b>\n\n"
        f"💰 <b>Финансовые показатели:</b>\n"
        f"• Выручка за 24ч: <b>{fin_stats['rev_24h']} ₽</b> ({fin_stats['count_24h']} продаж)\n"
        f"• Выручка за 7д: <b>{fin_stats['rev_7d']} ₽</b>\n"
        f"• Выручка за 30д: <b>{fin_stats['rev_30d']} ₽</b>\n"
        f"• Средний чек (30д): <b>{fin_stats['avg_check']} ₽</b>\n\n"
        f"🖥 <b>VPN Серверы и Пул IP:</b>\n"
        f"• Свободных IP в пуле: <b>{free_ips}</b>\n"
        f"{servers_summary}\n\n"
    )

    if dead_queues_count > 0 or disputes_count > 0:
        text += (
            f"⚠️ <b>Требует внимания:</b>\n"
            f"• Зависших задач в очередях: <b>{dead_queues_count}</b>\n"
            f"• Открытых платежных споров: <b>{disputes_count}</b>\n\n"
        )

    if maintenance_enabled:
        text += texts.DASHBOARD_MAINTENANCE_ON
    else:
        text += texts.DASHBOARD_MAINTENANCE_OFF

    kb = get_admin_menu(
        maintenance_enabled=maintenance_enabled,
        dead_queues_count=dead_queues_count,
        disputes_count=disputes_count,
    )

    try:
        await callback.message.edit_text(
            text,
            reply_markup=kb,
            parse_mode="HTML",
        )
    except TelegramBadRequest as e:
        logger.debug("admin dashboard edit_text failed: %s", e)
        await render_hub(
            callback.bot,
            callback.message.chat.id,
            text,
            kb,
        )



@router.callback_query(F.data.in_({"menu_admin", "admin_menu"}))
async def show_admin_menu(
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
    await _show_admin_dashboard(callback, session)
    await callback.answer(show_alert=False)


@router.callback_query(F.data == "admin_audit")
async def show_admin_audit(
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

    logs = await get_recent_audit_logs(session, limit=10)

    text = texts.AUDIT_LOG_HEADER

    if not logs:
        text += texts.AUDIT_LOG_EMPTY
    else:
        for log in logs:
            action_label = texts.AUDIT_ACTIONS.get(
                log.action,
                log.action,
            )

            target = ""
            if log.target_type:
                target = texts.RUNTIME_BOT_HANDLERS_ADMIN_DASHBOARD_L111_1.format(value_0=safe(log.target_type))
                if log.target_id:
                    target += f" #{log.target_id}"

            details = ""
            if log.details:
                details = f"\n{safe(truncate_details(log.details, 300))}"

            text += texts.AUDIT_ENTRY.format(
                date=format_datetime(log.created_at),
                admin_id=log.admin_id,
                action=action_label,
                target=target,
                details=details,
            )

    try:
        await callback.message.edit_text(
            text,
            reply_markup=get_audit_keyboard(),
            parse_mode="HTML",
        )
    except TelegramBadRequest as e:
        logger.debug("admin audit edit_text failed: %s", e)

    await callback.answer(show_alert=False)


@router.callback_query(F.data == "admin_maintenance")
async def show_admin_maintenance(
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

    enabled = await MaintenanceService.is_enabled(session)

    text = (
        texts.ADMIN_MAINTENANCE_MENU_ENABLED
        if enabled
        else texts.ADMIN_MAINTENANCE_MENU_DISABLED
    )

    try:
        await callback.message.edit_text(
            text,
            reply_markup=get_maintenance_confirm_keyboard(),
            parse_mode="HTML",
        )
    except TelegramBadRequest as e:
        logger.debug("admin maintenance edit_text failed: %s", e)

    await callback.answer(show_alert=False)


@router.callback_query(F.data == "admin_maintenance_toggle_apply")
async def toggle_admin_maintenance(
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

    enabled = await MaintenanceService.toggle(
        session,
        callback.from_user.id,
    )

    await AuditService.log_action(
        session,
        callback.from_user.id,
        "TOGGLE_MAINTENANCE",
        "MaintenanceMode",
        1,
        "enabled" if enabled else "disabled",
    )

    await callback.answer(
        texts.ADMIN_MAINTENANCE_ENABLED_ANSWER
        if enabled
        else texts.ADMIN_MAINTENANCE_DISABLED_ANSWER,
        show_alert=True,
    )

    await _show_admin_dashboard(callback, session)


@router.callback_query(F.data == "admin_settings")
async def show_admin_settings(
    callback: CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
):
    if not is_admin(callback.from_user.id):
        await callback.answer(texts.ERROR_ACCESS_DENIED, show_alert=True)
        return

    await state.clear()
    mtproto_url = await get_system_setting(session, "mtproto_proxy_url", "")
    header = format_admin_breadcrumbs("⚙️ Настройки бота")

    text = (
        f"{header}"
        f"⚙️ <b>Системные настройки бота:</b>\n\n"
        f"🚀 <b>MTProto Proxy URL:</b>\n"
        f"<code>{safe(mtproto_url or 'Не задано (ссылка скрыта от пользователей)')}</code>\n\n"
        f"Вы можете изменить ссылку на MTProto Proxy в 1 клик прямо из бота без перезапуска сервера."
    )

    builder = InlineKeyboardBuilder()
    builder.button(
        text="🚀 Изменить ссылку MTProto Proxy",
        callback_data="admin_edit_mtproto",
    )
    builder.button(
        text="🔙 В админ-меню",
        callback_data="admin_menu",
    )
    builder.adjust(1)

    try:
        await callback.message.edit_text(
            text,
            reply_markup=builder.as_markup(),
            parse_mode="HTML",
        )
    except TelegramBadRequest:
        pass
    await callback.answer(show_alert=False)


@router.callback_query(F.data == "admin_edit_mtproto")
async def start_edit_mtproto(
    callback: CallbackQuery,
    state: FSMContext,
):
    if not is_admin(callback.from_user.id):
        await callback.answer(texts.ERROR_ACCESS_DENIED, show_alert=True)
        return

    await state.clear()
    header = format_admin_breadcrumbs("⚙️ Настройки", "MTProto Proxy")
    text = (
        f"{header}"
        f"🚀 <b>Ввод ссылки MTProto Proxy:</b>\n\n"
        f"Отправьте новую ссылку на MTProto Proxy (например, <code>https://t.me/proxy?server=...</code>)\n"
        f"Или отправьте <code>-</code> (дефис), чтобы удалить ссылку и скрыть кнопку у пользователей:"
    )

    try:
        await callback.message.edit_text(
            text,
            reply_markup=get_back_button("admin_settings"),
            parse_mode="HTML",
        )
    except TelegramBadRequest:
        pass

    await state.set_state(AdminStates.editing_mtproto_proxy_url)
    await callback.answer(show_alert=False)


@router.message(AdminStates.editing_mtproto_proxy_url)
async def process_edit_mtproto(
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
            texts.ERROR_TEXT_REQUIRED,
            get_back_button("admin_settings"),
        )
        return

    input_text = message.text.strip()
    if input_text == "-":
        new_val = None
    else:
        new_val = input_text

    await set_system_setting(session, "mtproto_proxy_url", new_val, updated_by=message.from_user.id)
    await state.clear()

    header = format_admin_breadcrumbs("⚙️ Настройки", "🚀 MTProto Proxy")

    status_msg = "✅ Ссылка MTProto Proxy успешно удалена." if not new_val else f"✅ Ссылка MTProto Proxy обновлена на:\n<code>{safe(new_val)}</code>"

    await render_hub(
        message.bot,
        message.chat.id,
        f"{header}{status_msg}",
        get_back_button("admin_settings"),
        parse_mode="HTML",
    )