import logging

from aiogram import Router, F
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery
from sqlalchemy.ext.asyncio import AsyncSession

from bot import texts
from bot.keyboards import (
    get_admin_menu,
    get_audit_keyboard,
    get_maintenance_confirm_keyboard,
)
from database.repositories.audit_repo import get_recent_audit_logs
from database.repositories.servers_repo import get_total_free_ips
from database.repositories.users_repo import get_dashboard_stats
from services.audit_service import AuditService
from services.maintenance_service import MaintenanceService
from utils.admin import is_admin
from utils.formatters import format_datetime
from utils.telegram import safe
from utils.text_limits import truncate_details

from bot.keyboards.admin.dashboard import (  # noqa: F401
    get_admin_menu as get_admin_menu_keyboard,
    get_audit_keyboard as get_audit_keyboard_keyboard,
    get_maintenance_confirm_keyboard as get_maintenance_confirm_keyboard_keyboard,
)

router = Router()
logger = logging.getLogger(__name__)


async def _show_admin_dashboard(
    callback: CallbackQuery,
    session: AsyncSession,
) -> None:
    stats = await get_dashboard_stats(session)
    free_ips = await get_total_free_ips(session)
    maintenance_enabled = await MaintenanceService.is_enabled(session)

    text = texts.DASHBOARD_HEADER + texts.DASHBOARD_STATS.format(
        total_users=stats["total"],
        active_subs=stats["active"],
        new_users_24h=stats["new_24h"],
        free_ips=free_ips,
    )

    if maintenance_enabled:
        text += texts.DASHBOARD_MAINTENANCE_ON
    else:
        text += texts.DASHBOARD_MAINTENANCE_OFF

    try:
        await callback.message.edit_text(
            text,
            reply_markup=get_admin_menu(
                maintenance_enabled=maintenance_enabled
            ),
            parse_mode="HTML",
        )
    except TelegramBadRequest as e:
        logger.debug("admin dashboard edit_text failed: %s", e)


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
    await callback.answer()


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
                target = f" · {safe(log.target_type)}"
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

    await callback.answer()


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

    await callback.answer()


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