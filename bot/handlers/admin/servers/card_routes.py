import logging

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from bot import texts
from bot.constants import AdminAuditAction
from bot.keyboards import get_back_button
from bot.keyboards.admin.servers import get_server_migration_targets_keyboard
from bot.keyboards.admin.users import get_admin_confirm_action_keyboard
from bot.states import AdminStates
from config.constants import XRAY_PROTOCOL
from config.enums import ServerHealthState, ServerLifecycleStatus
from database.models import Server, WhiteInternetSubscription
from database.repositories.servers_repo import (
    capacity_consuming_wl_condition,
    get_server_by_id,
    migrate_origin_subscriptions,
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
        if server.protocol == "amneziawg2":
            from services.amnezia_client import AmneziaClient

            client = AmneziaClient(server.api_url, server.api_key)
            is_healthy = await client.healthcheck()
        elif server.protocol == "xray":
            from services.xray_node_client import XrayNodeClient

            async with XrayNodeClient(timeout=10.0) as xclient:
                is_healthy, _epoch, _detail = await xclient.check_health(server.api_url, server.api_key)
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


@router.callback_query(F.data.startswith("admin_server_broadcast:"))
async def admin_server_broadcast_start(
    callback: CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
):
    if not is_admin(callback.from_user.id):
        await callback.answer(texts.ERROR_ACCESS_DENIED, show_alert=True)
        return

    server_id = parse_callback_id(callback.data, 1)
    if server_id is None:
        await callback.answer(texts.ERROR_INVALID_REQUEST, show_alert=True)
        return

    server = await get_server_by_id(session, server_id)
    if not server:
        await callback.answer(texts.ERROR_SERVER_NOT_FOUND, show_alert=True)
        return

    await callback.answer(show_alert=False)
    await state.clear()
    await state.update_data(target_audience=f"server_{server_id}")
    await state.set_state(AdminStates.entering_broadcast_message)

    try:
        await callback.message.edit_text(
            texts.BROADCAST_PROMPT,
            reply_markup=get_back_button(f"admin_server_card:{server_id}"),
        )
    except TelegramBadRequest as e:
        logger.debug(f"admin_server_broadcast_start edit_text failed: {e}")


@router.callback_query(F.data.startswith("admin_server_migrate:"))
async def admin_server_migrate_start(
    callback: CallbackQuery,
    session: AsyncSession,
):
    if not is_admin(callback.from_user.id):
        await callback.answer(texts.ERROR_ACCESS_DENIED, show_alert=True)
        return

    server_id = parse_callback_id(callback.data, 1)
    if server_id is None:
        await callback.answer(texts.ERROR_INVALID_REQUEST, show_alert=True)
        return

    source_server = await get_server_by_id(session, server_id)
    if not source_server:
        await callback.answer(texts.ERROR_SERVER_NOT_FOUND, show_alert=True)
        return

    source_count = (await session.scalar(
        select(func.count(WhiteInternetSubscription.id)).where(
            WhiteInternetSubscription.origin_node_id == server_id,
            capacity_consuming_wl_condition(),
        )
    )) or 0

    if source_count == 0:
        await callback.answer(texts.ADMIN_SERVER_MIGRATE_NO_SUBS, show_alert=True)
        return

    # Find candidate active Xray nodes (excluding source)
    stmt_targets = (
        select(Server)
        .where(
            Server.id != server_id,
            Server.protocol == XRAY_PROTOCOL,
            Server.is_active.is_(True),
            Server.lifecycle_status == ServerLifecycleStatus.ACTIVE,
            Server.health_state.in_([ServerHealthState.ONLINE, ServerHealthState.WAITING_CONFIRMATION]),
        )
        .order_by(Server.name)
    )
    targets = list((await session.execute(stmt_targets)).scalars().all())

    if not targets:
        await callback.answer(texts.ADMIN_SERVER_MIGRATE_NO_TARGETS, show_alert=True)
        return

    targets_info = []
    for t in targets:
        t_active = (await session.scalar(
            select(func.count(WhiteInternetSubscription.id)).where(
                WhiteInternetSubscription.origin_node_id == t.id,
                capacity_consuming_wl_condition(),
            )
        )) or 0
        targets_info.append((t.id, t.name, t.country_flag or "🌐", t_active, t.max_clients))

    text = texts.ADMIN_SERVER_MIGRATE_SELECT_TARGET_PROMPT.format(
        source_flag=source_server.country_flag or "🌐",
        source_name=safe(source_server.name),
        count=source_count,
    )
    await callback.message.edit_text(
        text,
        reply_markup=get_server_migration_targets_keyboard(server_id, targets_info),
        parse_mode="HTML",
    )


@router.callback_query(F.data.startswith("admin_server_migrate_to:"))
async def admin_server_migrate_to(
    callback: CallbackQuery,
    session: AsyncSession,
):
    if not is_admin(callback.from_user.id):
        await callback.answer(texts.ERROR_ACCESS_DENIED, show_alert=True)
        return

    parts = callback.data.split(":")
    if len(parts) < 3:
        await callback.answer(texts.ERROR_INVALID_REQUEST, show_alert=True)
        return

    source_id = int(parts[1])
    target_id = int(parts[2])

    source_server = await get_server_by_id(session, source_id)
    target_server = await get_server_by_id(session, target_id)
    if not source_server or not target_server:
        await callback.answer(texts.ERROR_SERVER_NOT_FOUND, show_alert=True)
        return

    source_count = (await session.scalar(
        select(func.count(WhiteInternetSubscription.id)).where(
            WhiteInternetSubscription.origin_node_id == source_id,
            capacity_consuming_wl_condition(),
        )
    )) or 0

    target_active = (await session.scalar(
        select(func.count(WhiteInternetSubscription.id)).where(
            WhiteInternetSubscription.origin_node_id == target_id,
            capacity_consuming_wl_condition(),
        )
    )) or 0

    free_slots = max(0, target_server.max_clients - target_active)
    text = texts.ADMIN_SERVER_MIGRATE_CONFIRM_PROMPT.format(
        source_count=source_count,
        source_flag=source_server.country_flag or "🌐",
        source_name=safe(source_server.name),
        target_flag=target_server.country_flag or "🌐",
        target_name=safe(target_server.name),
        free_slots=free_slots,
    )
    await callback.message.edit_text(
        text,
        reply_markup=get_admin_confirm_action_keyboard(
            confirm_callback=f"admin_server_migrate_confirm:{source_id}:{target_id}",
            cancel_callback=f"admin_server_migrate:{source_id}",
        ),
        parse_mode="HTML",
    )


@router.callback_query(F.data.startswith("admin_server_migrate_confirm:"))
async def admin_server_migrate_confirm(
    callback: CallbackQuery,
    session: AsyncSession,
):
    if not is_admin(callback.from_user.id):
        await callback.answer(texts.ERROR_ACCESS_DENIED, show_alert=True)
        return

    parts = callback.data.split(":")
    if len(parts) < 3:
        await callback.answer(texts.ERROR_INVALID_REQUEST, show_alert=True)
        return

    source_id = int(parts[1])
    target_id = int(parts[2])

    try:
        count = await migrate_origin_subscriptions(session, source_id, target_id, callback.from_user.id)
        await AuditService.log_action(
            session,
            admin_id=callback.from_user.id,
            action=AdminAuditAction.WHITE_INTERNET_ORIGIN_MIGRATED,
            target_type="server",
            target_id=source_id,
            details={
                "source_id": source_id,
                "target_id": target_id,
                "count": count,
            },
        )
        await session.commit()
        await callback.answer(texts.ADMIN_SERVER_MIGRATE_SUCCESS.format(count=count), show_alert=True)
    except Exception as exc:
        await session.rollback()
        await callback.answer(texts.ADMIN_SERVER_MIGRATE_FAILED.format(error=exc), show_alert=True)

    server = await get_server_by_id(session, source_id)
    if server:
        await _show_server_card(callback, session, server)

