import logging

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from bot import texts
from bot.constants import AdminAuditAction
from bot.keyboards import get_back_button
from bot.keyboards.admin.servers import get_server_delete_confirm_keyboard
from bot.states import AdminStates
from config.enums import WhiteInternetStatus
from database.models import APIOperation, Server, VPNProfile, WhiteInternetQuotaGrant, WhiteInternetSubscription
from database.repositories.servers_repo import (
    delete_profiles_by_server_id,
    delete_server,
    get_server_by_id,
)
from services.amnezia_client import cleanup_server_circuit_breakers
from services.api_operations_queue import (
    classify_create_side_effect_risk,
    ensure_delete_operation,
)
from services.audit_service import AuditService
from utils.admin import is_admin
from utils.callbacks import parse_callback_id
from utils.datetime_helpers import now_utc
from utils.telegram import safe

from .common import _show_servers_list

router = Router()
logger = logging.getLogger(__name__)

def _has_unfinished_create_cleanup(profiles, operations) -> bool:
    cleanup_profile_ids = {
        profile.id for profile in profiles
        if profile.provisioning_status == "create_cleanup_pending"
    }
    unsafe_create = any(
        op.operation_type == "create_peer" and op.status != "succeeded"
        and (op.profile_id in cleanup_profile_ids or op.peer_id is not None
             or classify_create_side_effect_risk(op) in {"may_have_created_peer", "cleanup_required"})
        and not (op.status == "cancelled" and op.profile_id is None
                 and op.peer_id is None and op.last_error_code not in {
                    "create_ambiguous_reconcile", "invalid_created_config_cleanup",
                    "create_compensation_required", "cleanup_peer_identity_mismatch"})
        for op in operations
    )
    return bool(cleanup_profile_ids) or unsafe_create


@router.callback_query(F.data.startswith("admin_server_delete:"))
async def request_delete_server(
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

    server = await get_server_by_id(session, server_id)

    if not server:
        await callback.answer(
            texts.ERROR_SERVER_NOT_FOUND,
            show_alert=True,
        )
        return

    result = await session.execute(
        select(VPNProfile.id).where(
            VPNProfile.server_id == server.id
        ),
    )
    profiles_count = len(result.all())

    wl_subs_count = await session.scalar(
        select(func.count(WhiteInternetSubscription.id)).where(
            WhiteInternetSubscription.origin_node_id == server.id
        )
    ) or 0

    flag = server.country_flag or texts.EMOJI_GLOBE

    await state.update_data(delete_server_id=server_id)
    await state.set_state(AdminStates.confirming_server_delete)

    confirm_msg = texts.ADMIN_SERVER_DELETE_CONFIRM.format(
        flag=flag,
        name=safe(server.name),
        profiles_count=profiles_count,
    )
    if wl_subs_count > 0:
        confirm_msg += texts.ADMIN_SERVER_DELETE_WL_SUBS_NOTE.format(count=wl_subs_count)

    await callback.message.edit_text(
        confirm_msg,
        reply_markup=get_server_delete_confirm_keyboard(server_id),
        parse_mode="HTML",
    )


@router.callback_query(F.data.startswith("confirm_server_delete:"))
async def confirm_delete_server(
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

    current_state = await state.get_state()

    if current_state != AdminStates.confirming_server_delete:
        await callback.answer(
            texts.ADMIN_SERVER_CONFIRMATION_EXPIRED,
            show_alert=True,
        )
        return

    server_id = parse_callback_id(callback.data, 1)
    state_data = await state.get_data()
    expected_server_id = state_data.get("delete_server_id")

    if server_id is None or server_id != expected_server_id:
        await callback.answer(
            texts.ERROR_INVALID_REQUEST,
            show_alert=True,
        )
        await state.clear()
        return

    await callback.answer(show_alert=False)
    await state.clear()

    # First database action in the destructive confirmation transaction: this
    # serializes against DeviceService.create_device's identical server lock.
    server = (await session.execute(select(Server).where(
        Server.id == server_id).with_for_update())).scalar_one_or_none()

    if not server:
        await callback.answer(
            texts.ERROR_SERVER_NOT_FOUND,
            show_alert=True,
        )

        await _show_servers_list(callback, session, page=1)

        return

    server_name = server.name
    api_url = server.api_url
    api_key = server.api_key

    profiles = list((await session.execute(select(VPNProfile).where(
        VPNProfile.server_id == server.id).with_for_update())).scalars().all())
    operations = list((await session.execute(select(APIOperation).where(
        APIOperation.server_id == server.id,
        APIOperation.status.in_(("pending", "retry", "processing")),
    ).with_for_update())).scalars().all())
    processing_update = any(
        op.status == "processing" and op.operation_type == "update_peer"
        for op in operations
    )

    active_wl_subs = list((await session.execute(
        select(WhiteInternetSubscription).where(
            WhiteInternetSubscription.origin_node_id == server.id,
            WhiteInternetSubscription.status.in_([
                WhiteInternetStatus.PENDING,
                WhiteInternetStatus.ACTIVE,
                WhiteInternetStatus.EXHAUSTED,
            ]),
        ).with_for_update()
    )).scalars().all())

    if active_wl_subs:
        await session.rollback()
        await callback.answer(
            texts.ADMIN_SERVER_DELETE_BLOCKED_ACTIVE_WL_ALERT.format(count=len(active_wl_subs)),
            show_alert=True,
        )
        try:
            await callback.message.edit_text(
                texts.ADMIN_SERVER_DELETE_BLOCKED_ACTIVE_WL_TEXT.format(
                    name=safe(server_name),
                    count=len(active_wl_subs),
                ),
                reply_markup=get_back_button(f"admin_server_card:{server.id}"),
                parse_mode="HTML",
            )
        except TelegramBadRequest:
            pass
        return

    if _has_unfinished_create_cleanup(profiles, operations) or processing_update:
        await session.rollback()
        await callback.answer(
            texts.ADMIN_SERVER_DELETE_BLOCKED_PENDING_CLIENT,
            show_alert=True,
        )
        try:
            await callback.message.edit_text(
                texts.ADMIN_SERVER_DELETE_BLOCKED_PENDING,
                reply_markup=get_back_button(f"admin_server_card:{server.id}"),
                parse_mode="HTML",
            )
        except TelegramBadRequest:
            pass
        return
    for operation in operations:
        if operation.operation_type in {"create_peer", "update_peer"} and operation.status in {
            "pending", "retry"
        }:
            operation.status = "cancelled"
            operation.completed_at = now_utc()
            operation.locked_at = operation.locked_by = None
            operation.last_error_code = "server_deleting"
    for profile in profiles:
        if profile.peer_id:
            await ensure_delete_operation(session,
                idempotency_key=f"delete-peer:{profile.id}:{profile.peer_id}",
                server_id=server.id, profile_id=None,
                server_name_snapshot=server_name, api_url_snapshot=api_url,
                api_key_snapshot=api_key, peer_id=profile.peer_id,
                client_name=profile.client_name, audit_reason="server_delete")

    for profile in profiles:
        await session.delete(profile)

    deleted_profiles = await delete_profiles_by_server_id(
        session,
        server_id,
    )

    inactive_wl_subs = list((await session.execute(
        select(WhiteInternetSubscription).where(
            WhiteInternetSubscription.origin_node_id == server.id
        ).with_for_update()
    )).scalars().all())
    for wl_sub in inactive_wl_subs:
        await session.execute(delete(WhiteInternetQuotaGrant).where(WhiteInternetQuotaGrant.subscription_id == wl_sub.id))
        await session.delete(wl_sub)

    await delete_server(session, server)
    session.expire_all()


    cleanup_server_circuit_breakers(api_url)
    from services.slots_cache import invalidate_server_cache
    invalidate_server_cache(server_id)
    from services.workers.node_monitor import clear_server_monitor_state
    clear_server_monitor_state(server_id)

    await AuditService.log_action(
        session,
        callback.from_user.id,
        AdminAuditAction.DELETE_SERVER,
        "Server",
        server_id,
        texts.ADMIN_AUDIT_LOG_DETAILS_DELETE_SERVER.format(server_name=server_name, count=deleted_profiles),
    )

    await callback.answer(
        texts.ADMIN_SERVER_DELETE_SUCCESS_NOTICE.format(v0=server_name, v1=deleted_profiles),
        show_alert=True,
    )

    logger.info(
        f"Admin {callback.from_user.id} fully deleted server {server_id} "
        f"({server_name}) with {deleted_profiles} profiles"
    )

    await _show_servers_list(callback, session, page=1)
