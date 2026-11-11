import logging

from config.constants import AdminAuditAction
from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from bot import texts
from bot.keyboards.admin.servers import get_server_delete_confirm_keyboard
from bot.states import AdminStates
from config.enums import (
    ServerHealthState,
    ServerLifecycleStatus,
    WhiteInternetProvisioningStatus,
    WhiteInternetStatus,
)
from database.models import (
    Server,
    WhiteInternetOrphanCleanup,
    WhiteInternetSubscription,
)
from database.repositories.servers_repo import (
    delete_server,
    get_server_by_id,
)
from services.audit_service import AuditService
from utils.admin import is_admin
from utils.callbacks import parse_callback_id
from utils.datetime_helpers import now_utc
from utils.telegram import safe

from .common import _show_servers_list

router = Router()
logger = logging.getLogger(__name__)


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

    wl_subs_count = await session.scalar(
        select(func.count(WhiteInternetSubscription.id)).where(
            WhiteInternetSubscription.origin_node_id == server.id,
            WhiteInternetSubscription.status.in_([
                WhiteInternetStatus.PENDING,
                WhiteInternetStatus.ACTIVE,
                WhiteInternetStatus.EXHAUSTED,
            ]),
        )
    ) or 0

    flag = server.country_flag or texts.EMOJI_GLOBE

    await state.update_data(delete_server_id=server_id)
    await state.set_state(AdminStates.confirming_server_delete)

    from bot.formatters import get_server_protocol_badge

    proto = get_server_protocol_badge(server)
    server_display_name = f"{proto} {safe(server.name)}" if proto else safe(server.name)
    confirm_msg = texts.ADMIN_SERVER_DELETE_CONFIRM.format(
        flag=flag,
        name=server_display_name,
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

    pending_orphans_count = (await session.scalar(
        select(func.count(WhiteInternetOrphanCleanup.id)).where(
            WhiteInternetOrphanCleanup.server_id == server.id,
            WhiteInternetOrphanCleanup.status == "pending",
        )
    )) or 0

    pending_trial_resets_count = (await session.scalar(
        select(func.count(WhiteInternetSubscription.id)).where(
            WhiteInternetSubscription.origin_node_id == server.id,
            WhiteInternetSubscription.pending_hard_delete.is_(True),
        )
    )) or 0

    if active_wl_subs or pending_orphans_count > 0 or pending_trial_resets_count > 0:
        total_blocked = len(active_wl_subs) + pending_orphans_count + pending_trial_resets_count
        await session.rollback()
        await callback.answer(
            texts.ADMIN_SERVER_DELETE_BLOCKED_ACTIVE_WL_ALERT.format(count=total_blocked),
            show_alert=True,
        )
        try:
            from aiogram.utils.keyboard import InlineKeyboardBuilder
            builder = InlineKeyboardBuilder()
            builder.button(text=texts.ADMIN_SERVER_BTN_MIGRATE, callback_data=f"admin_server_migrate:{server.id}")
            builder.button(text=texts.ADMIN_SERVER_BTN_FORCE_PURGE, callback_data=f"admin_server_purge:{server.id}")
            builder.button(text=texts.ADMIN_SERVER_BTN_BACK_TO_CARD, callback_data=f"admin_server_card:{server.id}")
            builder.adjust(1)
            await callback.message.edit_text(
                texts.ADMIN_SERVER_DELETE_BLOCKED_ACTIVE_WL_TEXT.format(
                    name=safe(server_name),
                    count=total_blocked,
                ),
                reply_markup=builder.as_markup(),
                parse_mode="HTML",
            )
        except TelegramBadRequest:
            pass
        return

    # Phase 1: Deactivate server under Server lock.
    # Committing Phase 1 marks the server inactive immediately so create_device / migrate_device
    # cannot allocate it, and releases the Server lock to eliminate lock graph cycles.
    server.is_active = False
    server.lifecycle_status = ServerLifecycleStatus.DECOMMISSIONING
    await session.commit()

    # Phase 2: Final server removal and cache invalidation.
    server_to_delete = (await session.execute(
        select(Server).where(Server.id == server_id).with_for_update()
    )).scalar_one_or_none()

    if server_to_delete:
        await session.execute(
            update(WhiteInternetSubscription)
            .where(WhiteInternetSubscription.origin_node_id == server_id)
            .values(
                origin_node_id=None,
                provisioning_status=WhiteInternetProvisioningStatus.SYNCED_INACTIVE,
                last_reconciled_node_epoch=None,
            )
        )
        await delete_server(session, server_to_delete)
        await session.commit()

    session.expire_all()

    from services.workers.node_monitor import clear_server_monitor_state
    clear_server_monitor_state(server_id)

    await AuditService.log_action(
        session,
        callback.from_user.id,
        AdminAuditAction.DELETE_SERVER,
        "Server",
        server_id,
        texts.ADMIN_AUDIT_LOG_DETAILS_DELETE_SERVER.format(server_name=server_name, count=0),
    )

    await callback.answer(
        texts.ADMIN_SERVER_DELETE_SUCCESS_NOTICE.format(v0=server_name, v1=0),
        show_alert=True,
    )

    logger.info(
        f"Admin {callback.from_user.id} fully deleted server {server_id} "
        f"({server_name})"
    )

    await _show_servers_list(callback, session, page=1)


@router.callback_query(F.data.startswith("admin_server_purge:"))
async def request_purge_server(
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

    server = await get_server_by_id(session, server_id)
    if not server:
        await callback.answer(texts.ERROR_SERVER_NOT_FOUND, show_alert=True)
        return

    wl_subs_count = (await session.scalar(
        select(func.count(WhiteInternetSubscription.id)).where(
            WhiteInternetSubscription.origin_node_id == server.id,
        )
    )) or 0

    from bot.keyboards.admin.users import get_admin_confirm_action_keyboard
    from bot.formatters import get_server_protocol_badge

    proto = get_server_protocol_badge(server)
    server_display_name = f"{proto} {safe(server.name)}" if proto else safe(server.name)
    text = texts.ADMIN_SERVER_FORCE_PURGE_CONFIRM.format(name=server_display_name, count=wl_subs_count)
    await callback.message.edit_text(
        text,
        reply_markup=get_admin_confirm_action_keyboard(
            confirm_callback=f"confirm_server_purge:{server.id}",
            cancel_callback=f"admin_server_card:{server.id}",
        ),
        parse_mode="HTML",
    )


@router.callback_query(F.data.startswith("confirm_server_purge:"))
async def confirm_purge_server(
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

    server = (await session.execute(
        select(Server).where(Server.id == server_id).with_for_update()
    )).scalar_one_or_none()
    if not server:
        await callback.answer(texts.ERROR_SERVER_NOT_FOUND, show_alert=True)
        return

    # 1. Mark pending orphans as done with error
    await session.execute(
        update(WhiteInternetOrphanCleanup)
        .where(
            WhiteInternetOrphanCleanup.server_id == server.id,
            WhiteInternetOrphanCleanup.status == "pending",
        )
        .values(
            status="done",
            last_error="node_destroyed_force_purge",
            updated_at=now_utc(),
        )
    )

    # 2. Update all subscriptions on this dead node to consistent inactive state
    subs = list((await session.execute(
        select(WhiteInternetSubscription).where(
            WhiteInternetSubscription.origin_node_id == server.id,
        ).with_for_update()
    )).scalars().all())

    for sub in subs:
        sub.status = WhiteInternetStatus.DISABLED
        sub.provisioning_status = WhiteInternetProvisioningStatus.SYNCED_INACTIVE
        sub.origin_node_id = None
        sub.pending_hard_delete = False
        sub.status_reason = "force_purged_node_destroyed"

    # Decommission the purged server so it is excluded from allocation and monitoring
    server.is_active = False
    server.lifecycle_status = ServerLifecycleStatus.DECOMMISSIONED
    server.health_state = ServerHealthState.MANUAL_DISABLED

    # 3. Log structured audit
    await AuditService.log_action(
        session,
        admin_id=callback.from_user.id,
        action=AdminAuditAction.SERVER_FORCE_PURGE,
        target_type="server",
        target_id=server.id,
        details={
            "server_id": server.id,
            "server_name": server.name,
            "affected_subscriptions": len(subs),
        },
    )

    await session.commit()
    await callback.answer(texts.ADMIN_SERVER_FORCE_PURGE_SUCCESS.format(name=server.name, count=len(subs)), show_alert=True)
    await _show_servers_list(callback, session, page=1)
