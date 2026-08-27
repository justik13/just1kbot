import logging
import math
from datetime import timedelta

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from bot import texts
from bot.handlers.admin.disputes import router as disputes_router
from bot.keyboards import (
    get_admin_cat_finance_keyboard,
    get_admin_cat_infra_keyboard,
    get_admin_cat_system_keyboard,
    get_admin_cat_users_keyboard,
    get_admin_menu,
    get_audit_keyboard,
    get_back_button,
    get_maintenance_confirm_keyboard,
)
from bot.states import AdminStates
from database.models import Payment
from database.repositories.audit_repo import (
    get_all_audit_logs_paginated,
    get_total_audit_logs_count,
)
from database.repositories.servers_repo import get_total_free_ips
from database.repositories.system_settings_repo import (
    get_system_setting,
    set_system_setting,
)
from database.repositories.users_repo import get_dashboard_stats
from services.audit_service import AuditService
from services.maintenance_service import MaintenanceService
from utils.admin import is_admin
from utils.datetime_helpers import now_utc
from utils.formatters import (
    format_admin_breadcrumbs,
    format_audit_details,
    format_datetime,
)
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
    from database.repositories.servers_repo import (
        get_active_servers,
        get_server_peer_counts,
    )
    from services.slots_cache import get_cached_peer_count

    servers = await get_active_servers(session)
    if not servers:
        return texts.UI_DASHBOARD_SERVEROV_POKA_NET_119

    db_counts = await get_server_peer_counts(session)
    lines = []
    for s in servers:
        flag = s.country_flag or "🌐"
        used = get_cached_peer_count(s.id)
        if used is None:
            used = db_counts.get(s.id, 0)
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

    header = format_admin_breadcrumbs(texts.UI_DASHBOARD_GLAVNYY_DASHBORD_148)
    text = (
        f"{header}"+
        texts.UI_DASHBOARD_POLZOVATELI_I_PODPISKI_151.format()+
        texts.UI_DASHBOARD_VSEGO_POLZOVATELEY_TOTAL_152.format(stats__total=stats['total'])+
        texts.UI_DASHBOARD_AKTIVNYKH_PODPISOK_ACTIVE_153.format(stats__active=stats['active'])+
        texts.UI_DASHBOARD_NOVYKH_ZA_24_CHASA_NEW_24H_154.format(stats__new_24h=stats['new_24h'])+
        texts.UI_DASHBOARD_FINANSOVYE_POKAZATELI_155.format()+
        texts.UI_DASHBOARD_VYRUCHKA_ZA_24CH_PRODAZH_REV_2_156.format(fin_stats__rev_24h=fin_stats['rev_24h'], fin_stats__count_24h=fin_stats['count_24h'])+
        texts.UI_DASHBOARD_VYRUCHKA_ZA_7D_REV_7D_157.format(fin_stats__rev_7d=fin_stats['rev_7d'])+
        texts.UI_DASHBOARD_VYRUCHKA_ZA_30D_REV_30D_158.format(fin_stats__rev_30d=fin_stats['rev_30d'])+
        texts.UI_DASHBOARD_SREDNIY_CHEK_30D_AVG_CHECK_159.format(fin_stats__avg_check=fin_stats['avg_check'])+
        texts.UI_DASHBOARD_VPN_SERVERY_I_PUL_IP_160.format()+
        texts.UI_DASHBOARD_SVOBODNYKH_IP_V_PULE_161.format(free_ips=free_ips)+
        f"{servers_summary}\n\n"
    )

    if dead_queues_count > 0 or disputes_count > 0:
        text += (
            texts.UI_DASHBOARD_TREBUET_VNIMANIYA_167.format()+
            texts.UI_DASHBOARD_ZAVISSHIKH_ZADACH_V_OCHEREDYAK_168.format(dead_queues_count=dead_queues_count)+
            texts.UI_DASHBOARD_OTKRYTYKH_PLATEZHNYKH_SPOROV_169.format(disputes_count=disputes_count)
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


@router.callback_query(F.data == "admin_cat_users")
async def show_admin_cat_users(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer(texts.ERROR_ACCESS_DENIED, show_alert=True)
        return
    await state.clear()
    header = format_admin_breadcrumbs(texts.UI_DASHBOARD_POLZOVATELI_I_RASSYLKI_224)
    text = texts.UI_DASHBOARD_UPRAVLENIE_POLZOVATELYAMI_I_RA_225.format(header=header)
    try:
        await callback.message.edit_text(text, reply_markup=get_admin_cat_users_keyboard(), parse_mode="HTML")
    except TelegramBadRequest:
        pass
    await callback.answer(show_alert=False)


@router.callback_query(F.data == "admin_cat_infra")
async def show_admin_cat_infra(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer(texts.ERROR_ACCESS_DENIED, show_alert=True)
        return
    await state.clear()
    header = format_admin_breadcrumbs(texts.UI_DASHBOARD_SERVERY_I_TARIFY_239)
    text = texts.UI_DASHBOARD_UPRAVLENIE_VPN_SERVERAMI_I_TAR_240.format(header=header)
    try:
        await callback.message.edit_text(text, reply_markup=get_admin_cat_infra_keyboard(), parse_mode="HTML")
    except TelegramBadRequest:
        pass
    await callback.answer(show_alert=False)


@router.callback_query(F.data == "admin_cat_finance")
async def show_admin_cat_finance(callback: CallbackQuery, state: FSMContext, session: AsyncSession):
    if not is_admin(callback.from_user.id):
        await callback.answer(texts.ERROR_ACCESS_DENIED, show_alert=True)
        return
    await state.clear()
    disputes_count = await _get_disputes_count(session)
    dead_queues_count = await _get_dead_queues_count(session)
    header = format_admin_breadcrumbs(texts.UI_DASHBOARD_FINANSY_I_OCHEREDI_256)
    text = texts.UI_DASHBOARD_FINANSY_OCHEREDI_I_PLATEZHNYE__257.format(header=header)
    try:
        await callback.message.edit_text(text, reply_markup=get_admin_cat_finance_keyboard(dead_queues_count, disputes_count), parse_mode="HTML")
    except TelegramBadRequest:
        pass
    await callback.answer(show_alert=False)


@router.callback_query(F.data == "admin_cat_system")
async def show_admin_cat_system(callback: CallbackQuery, state: FSMContext, session: AsyncSession):
    if not is_admin(callback.from_user.id):
        await callback.answer(texts.ERROR_ACCESS_DENIED, show_alert=True)
        return
    await state.clear()
    maintenance_enabled = await MaintenanceService.is_enabled(session)
    header = format_admin_breadcrumbs(texts.UI_DASHBOARD_SISTEMA_I_NASTROYKI_272)
    text = texts.UI_DASHBOARD_SISTEMNYE_NASTROYKI_I_LOGI_VYB_273.format(header=header)
    try:
        await callback.message.edit_text(text, reply_markup=get_admin_cat_system_keyboard(maintenance_enabled), parse_mode="HTML")
    except TelegramBadRequest:
        pass
    await callback.answer(show_alert=False)


@router.callback_query(F.data.startswith("admin_audit"))
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

    page = 1
    if ":" in callback.data:
        try:
            page = int(callback.data.split(":")[1])
        except (ValueError, IndexError):
            page = 1

    per_page = 10
    total_count = await get_total_audit_logs_count(session)
    total_pages = max(1, math.ceil(total_count / per_page))
    page = min(max(1, page), total_pages)
    offset = (page - 1) * per_page

    logs = await get_all_audit_logs_paginated(session, offset=offset, limit=per_page)

    header = format_admin_breadcrumbs(texts.UI_DASHBOARD_SISTEMA_311, texts.UI_DASHBOARD_AUDIT_LOG_311)
    text = texts.UI_DASHBOARD_AUDIT_LOG_DEYSTVIY_ADMINISTRAT_312.format(header=header, page=page, total_pages=total_pages, total_count=total_count)

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
                target = f" ({safe(log.target_type)}"
                if log.target_id:
                    target += f" #{log.target_id}"
                target += ")"

            details = ""
            if log.details:
                formatted_d = format_audit_details(log.details)
                details = f"\n{safe(truncate_details(formatted_d, 300))}"

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
            reply_markup=get_audit_keyboard(page=page, total_pages=total_pages),
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
    header = format_admin_breadcrumbs(texts.UI_DASHBOARD_NASTROYKI_BOTA_441)

    text = (
        f"{header}"+
        texts.UI_DASHBOARD_SISTEMNYE_NASTROYKI_BOTA_445.format()+
        "🚀 <b>MTProto Proxy URL:</b>\n"+
        texts.UI_DASHBOARD_NE_ZADANO_SSYLKA_SKRYTA_OT_POL_447.format(safe_mtproto_url_or=safe(mtproto_url or 'Не задано (ссылка скрыта от пользователей)'))+
        texts.UI_DASHBOARD_VY_MOZHETE_IZMENIT_SSYLKU_NA_M_448.format()
    )

    builder = InlineKeyboardBuilder()
    builder.button(
        text=texts.UI_DASHBOARD_IZMENIT_SSYLKU_MTPROTO_PROXY_453,
        callback_data="admin_edit_mtproto",
    )
    builder.button(
        text=texts.UI_DASHBOARD_V_ADMIN_MENYU_457,
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
    header = format_admin_breadcrumbs(texts.UI_DASHBOARD_NASTROYKI_483, "MTProto Proxy")
    text = (
        f"{header}"+
        texts.UI_DASHBOARD_VVOD_SSYLKI_MTPROTO_PROXY_486.format()+
        texts.UI_DASHBOARD_OTPRAVTE_NOVUYU_SSYLKU_NA_MTPR_487.format()+
        texts.UI_DASHBOARD_ILI_OTPRAVTE_DEFIS_CHTOBY_UDAL_488.format()
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
            trigger_message_id=message.message_id,
        )
        return

    input_text = message.text.strip()
    if input_text == "-":
        new_val = None
    else:
        new_val = input_text

    await set_system_setting(session, "mtproto_proxy_url", new_val, updated_by=message.from_user.id)
    await state.clear()

    header = format_admin_breadcrumbs(texts.UI_DASHBOARD_NASTROYKI_533, "🚀 MTProto Proxy")

    status_msg = texts.UI_DASHBOARD_SSYLKA_MTPROTO_PROXY_USPESHNO__535 if not new_val else texts.UI_DASHBOARD_SSYLKA_MTPROTO_PROXY_OBNOVLENA_535.format(safe_new_val=safe(new_val))

    await render_hub(
        message.bot,
        message.chat.id,
        f"{header}{status_msg}",
        get_back_button("admin_settings"),
        parse_mode="HTML",
        trigger_message_id=message.message_id,
    )