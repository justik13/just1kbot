import logging

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery
from sqlalchemy.ext.asyncio import AsyncSession

from bot import texts
from bot.constants import AdminAuditAction
from bot.keyboards.admin.users import get_admin_confirm_action_keyboard
from database.repositories.servers_repo import (
    get_server_by_id,
    update_server,
)
from services.audit_service import AuditService
from utils.admin import is_admin
from utils.callbacks import parse_callback_id
from utils.telegram import safe

from .common import _show_server_card

router = Router()
logger = logging.getLogger(__name__)


@router.callback_query(F.data.startswith("admin_server_card:"))
async def show_server_card(
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

    server_id = parse_callback_id(callback.data, 1)

    if server_id is None:
        await callback.answer(
            texts.ERROR_INVALID_REQUEST,
            show_alert=True,
        )
        return

    await callback.answer(show_alert=False)
    await state.clear()

    server = await get_server_by_id(session, server_id)

    if not server:
        await callback.answer(
            texts.ERROR_SERVER_NOT_FOUND,
            show_alert=True,
        )
        return

    await _show_server_card(callback, session, server)


@router.callback_query(F.data.startswith("admin_server_toggle:"))
async def toggle_server_confirm(
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

    server_id = parse_callback_id(callback.data, 1)

    if server_id is None:
        await callback.answer(
            texts.ERROR_INVALID_REQUEST,
            show_alert=True,
        )
        return

    await callback.answer(show_alert=False)
    await state.clear()

    server = await get_server_by_id(session, server_id)

    if not server:
        await callback.answer(
            texts.ERROR_SERVER_NOT_FOUND,
            show_alert=True,
        )
        return

    new_status = not server.is_active

    flag = server.country_flag or texts.EMOJI_GLOBE

    if new_status:
        text = texts.ADMIN_SERVER_TOGGLE_ENABLE_CONFIRM.format(
            flag=flag,
            name=safe(server.name),
        )
    else:
        text = texts.ADMIN_SERVER_TOGGLE_DISABLE_CONFIRM.format(
            flag=flag,
            name=safe(server.name),
        )

    try:
        await callback.message.edit_text(
            text,
            reply_markup=get_admin_confirm_action_keyboard(
                confirm_callback=f"admin_server_toggle_apply:{server_id}",
                cancel_callback=f"admin_server_card:{server_id}",
            ),
            parse_mode="HTML",
        )
    except TelegramBadRequest as e:
        logger.debug(f"toggle_server_confirm edit_text failed: {e}")


@router.callback_query(F.data.startswith("admin_server_toggle_apply:"))
async def toggle_server_apply(
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

    server_id = parse_callback_id(callback.data, 1)

    if server_id is None:
        await callback.answer(
            texts.ERROR_INVALID_REQUEST,
            show_alert=True,
        )
        return

    await callback.answer(show_alert=False)
    await state.clear()

    server = await get_server_by_id(session, server_id)

    if not server:
        await callback.answer(
            texts.ERROR_SERVER_NOT_FOUND,
            show_alert=True,
        )
        return

    new_status = not server.is_active

    from config.constants import ServerHealthState
    from services.amnezia_client import cleanup_server_circuit_breakers
    from services.workers.node_monitor import reset_server_monitor_state
    from utils.datetime_helpers import now_utc

    if new_status:
        await update_server(
            session,
            server,
            is_active=True,
            disabled_reason=None,
            disabled_at=None,
            health_state=ServerHealthState.ONLINE,
            consecutive_fails=0,
            consecutive_successes=0,
            problem_started_at=None,
            next_check_at=None,
            recovery_notice_sent=False,
            last_alert_sent_state=None,
        )
        reset_server_monitor_state(server_id, ServerHealthState.ONLINE)
    else:
        await update_server(
            session,
            server,
            is_active=False,
            disabled_reason="MANUAL",
            disabled_at=now_utc(),
            health_state=ServerHealthState.MANUAL_DISABLED,
            problem_started_at=None,
            next_check_at=None,
        )
        reset_server_monitor_state(server_id, ServerHealthState.MANUAL_DISABLED)
        cleanup_server_circuit_breakers(server.api_url)

    await AuditService.log_action(
        session,
        callback.from_user.id,
        AdminAuditAction.TOGGLE_SERVER,
        "Server",
        server_id,
        "enabled" if new_status else "disabled",
    )

    status_text = (
        texts.ADMIN_SERVER_STATE_ENABLED
        if new_status
        else texts.ADMIN_SERVER_STATE_DISABLED
    )

    await callback.answer(
        texts.ADMIN_SERVER_TOGGLE_SUCCESS.format(status=status_text),
        show_alert=True,
    )

    logger.info(
        f"Admin {callback.from_user.id} toggled server {server_id} "
        f"to {new_status}"
    )

    refreshed = await get_server_by_id(session, server_id)

    await _show_server_card(callback, session, refreshed)


@router.callback_query(F.data.startswith("admin_server_ping:"))
async def ping_server(
    callback: CallbackQuery,
    session: AsyncSession,
):
    if not is_admin(callback.from_user.id):
        await callback.answer(texts.ERROR_ACCESS_DENIED, show_alert=True)
        return

    server_id = parse_callback_id(callback.data, 1)
    if server_id is None:
        await callback.answer(texts.ERROR_SERVER_ID_REQUIRED, show_alert=True)
        return

    server = await get_server_by_id(session, server_id)
    if not server:
        await callback.answer(texts.ERROR_SERVER_NOT_FOUND, show_alert=True)
        return

    await callback.answer(texts.ADMIN_SERVER_PING_CHECKING, show_alert=False)

    import time

    start_t = time.monotonic()
    try:
        if "xray_origin" in (server.capabilities or []) or server.protocol == "xray":
            from services.xray_node_client import XrayNodeClient

            async with XrayNodeClient(timeout=10.0) as xclient:
                is_healthy, _epoch, _detail = await xclient.check_health(server.api_url, server.api_key)
        elif server.protocol == "amneziawg2":
            from services.amnezia_client import AmneziaClient

            client = AmneziaClient(server.api_url, server.api_key)
            is_healthy = await client.healthcheck()
        else:
            is_healthy = False

        duration_ms = int((time.monotonic() - start_t) * 1000)
        if is_healthy:
            ping_res = texts.ADMIN_SERVER_PING_ONLINE.format(latency_ms=duration_ms)
        else:
            ping_res = texts.ADMIN_SERVER_PING_NO_HEALTHZ
    except Exception as exc:
        ping_res = texts.ADMIN_SERVER_PING_ERROR.format(error=type(exc).__name__)

    await _show_server_card(callback, session, server, ping_result=ping_res)


@router.callback_query(F.data.startswith("admin_dismiss_alert"))
async def dismiss_admin_alert(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer(texts.ERROR_ACCESS_DENIED, show_alert=True)
        return
    await callback.answer(texts.ADMIN_SERVER_DELETED_BADGE, show_alert=False)
    try:
        await callback.message.delete()
    except Exception as e:
        logger.debug(f"Failed to delete alert message: {e}")
