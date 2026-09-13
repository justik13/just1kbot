"""Telegram UI routes for AmneziaWG Unified Subscription System."""

from __future__ import annotations

from datetime import datetime, timezone
import logging

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import BufferedInputFile, CallbackQuery
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from bot import texts
from bot.constants import AMNEZIA_PROTOCOL
from config.enums import ServerHealthState, ServerLifecycleStatus
from database.models import User, VPNProfile
from database.repositories import users_repo
from database.repositories.servers_repo import (
    get_available_servers,
    get_server_by_id,
)
from database.repositories.users_repo import get_user_by_telegram_id
from services.device_service import (
    DeviceLimitExceeded,
    DeviceService,
    RESERVING_STATUSES,
    ServerUnavailable,
)
from services.maintenance_service import MaintenanceService
from services.slots_cache import capture_server_peer_snapshot
from services.subscription import SubscriptionService
from utils.callbacks import parse_callback_id
from utils.datetime_helpers import now_utc
from utils.formatters import format_datetime, format_traffic
from utils.telegram import render_hub, safe
from utils.vpn_parser import build_conf_file

from .common import (
    _get_effective_device_limit,
    _render_connections,
    _render_maintenance,
)
from .device_create_routes import _await_profile_ready

logger = logging.getLogger(__name__)
router = Router(name="awg_subscription")


@router.callback_query(F.data == "awg_download_conf_menu")
async def awg_download_conf_menu(
    callback: CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
    db_user: User | None = None,
):
    """Show country selection menu for downloading .conf file."""
    await callback.answer(show_alert=False)
    await state.clear()

    telegram_user_id = callback.from_user.id
    if not await MaintenanceService.can_user_perform_action(session, telegram_user_id):
        await _render_maintenance(callback.message, session, back_to="back_to_connections")
        return

    user = db_user or await get_user_by_telegram_id(session, telegram_user_id)
    if not user or not await SubscriptionService.check_access(session, user.telegram_id):
        await render_hub(
            callback.bot,
            callback.message.chat.id,
            texts.ERROR_NO_SUBSCRIPTION,
            InlineKeyboardBuilder()
            .button(text=texts.BTN_BUY_ACCESS, callback_data="menu_buy")
            .as_markup(),
        )
        return

    servers = await get_available_servers(session)
    if not servers:
        await render_hub(
            callback.bot,
            callback.message.chat.id,
            texts.ERROR_NO_FREE_SLOTS,
            InlineKeyboardBuilder()
            .button(text=texts.BTN_BACK, callback_data="awg_manage_devices")
            .as_markup(),
        )
        return

    builder = InlineKeyboardBuilder()
    for server in servers:
        flag = server.country_flag or texts.EMOJI_GLOBE
        builder.button(
            text=f"{flag} {server.name}",
            callback_data=f"awg_get_conf:{server.id}",
        )
    builder.button(text=texts.BTN_BACK, callback_data="awg_manage_devices")
    builder.adjust(1)

    await render_hub(
        callback.bot,
        callback.message.chat.id,
        texts.AWG_DOWNLOAD_CONF_SELECT_SERVER,
        builder.as_markup(),
    )


@router.callback_query(F.data.startswith("awg_get_conf:"))
async def awg_get_conf(
    callback: CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
    db_user: User | None = None,
):
    """Generate or retrieve .conf file for chosen server and send as Telegram document."""
    await callback.answer(show_alert=False)
    await state.clear()

    telegram_user_id = callback.from_user.id
    if not await MaintenanceService.can_user_perform_action(session, telegram_user_id):
        await _render_maintenance(callback.message, session, back_to="back_to_connections")
        return

    server_id = parse_callback_id(callback.data, 1)
    if server_id is None:
        await callback.answer(texts.ERROR_LOCATION_NOT_FOUND, show_alert=True)
        return

    user = db_user or await get_user_by_telegram_id(session, telegram_user_id)
    if not user or not await SubscriptionService.check_access(session, user.telegram_id):
        await render_hub(
            callback.bot,
            callback.message.chat.id,
            texts.ERROR_NO_SUBSCRIPTION,
            InlineKeyboardBuilder()
            .button(text=texts.BTN_BUY_ACCESS, callback_data="menu_buy")
            .as_markup(),
        )
        return

    server = await get_server_by_id(session, server_id)
    if (
        not server
        or not server.is_active
        or server.protocol != AMNEZIA_PROTOCOL
        or (getattr(server, "health_state", None) or ServerHealthState.ONLINE)
        != ServerHealthState.ONLINE
        or (getattr(server, "lifecycle_status", None) or ServerLifecycleStatus.ACTIVE)
        != ServerLifecycleStatus.ACTIVE
        or "xray_origin" in (getattr(server, "capabilities", None) or [])
    ):
        await render_hub(
            callback.bot,
            callback.message.chat.id,
            texts.ERROR_SERVER_DISABLED,
            InlineKeyboardBuilder()
            .button(text=texts.BTN_BACK, callback_data="awg_download_conf_menu")
            .as_markup(),
        )
        return

    # Check if user already has an existing active manual profile on this server (idempotency)
    existing_profile_stmt = select(VPNProfile).where(
        VPNProfile.user_id == user.id,
        VPNProfile.server_id == server_id,
        VPNProfile.device_type == "manual",
        VPNProfile.provisioning_status.in_(["active", "pending_create", "pending_update"]),
    )
    profile = (await session.execute(existing_profile_stmt)).scalar_one_or_none()

    limit = await _get_effective_device_limit(session, user)

    if not profile:
        # Check quota before creating
        active_sub_devices = dict(getattr(user, "active_sub_devices", None) or {})
        manual_count = (
            await session.execute(
                select(func.count(VPNProfile.id)).where(
                    VPNProfile.user_id == user.id,
                    VPNProfile.device_type == "manual",
                    VPNProfile.provisioning_status.in_(RESERVING_STATUSES),
                )
            )
        ).scalar_one()

        total_active = len(active_sub_devices) + manual_count
        if total_active >= limit:
            builder = InlineKeyboardBuilder()
            builder.button(text=texts.BTN_MANAGE_DEVICES, callback_data="awg_manage_devices")
            builder.button(text=texts.BTN_BACK, callback_data="awg_download_conf_menu")
            builder.adjust(1)
            await render_hub(
                callback.bot,
                callback.message.chat.id,
                texts.AWG_CONF_LIMIT_EXCEEDED.format(active_count=total_active, limit=limit),
                builder.as_markup(),
            )
            return

        try:
            snapshot = await capture_server_peer_snapshot(server_id)
            new_profile = await DeviceService.create_device(
                session,
                user_id=user.id,
                server_id=server_id,
                device_name=None,
                snapshot=snapshot,
                device_type="manual",
            )
            await session.commit()
            profile = await _await_profile_ready(new_profile.id, timeout_seconds=5.0)
        except DeviceLimitExceeded:
            builder = InlineKeyboardBuilder()
            builder.button(text=texts.BTN_MANAGE_DEVICES, callback_data="awg_manage_devices")
            builder.button(text=texts.BTN_BACK, callback_data="awg_download_conf_menu")
            builder.adjust(1)
            await render_hub(
                callback.bot,
                callback.message.chat.id,
                texts.AWG_CONF_LIMIT_EXCEEDED.format(active_count=total_active, limit=limit),
                builder.as_markup(),
            )
            return
        except ServerUnavailable:
            await callback.answer(texts.ERROR_SERVER_UNAVAILABLE, show_alert=True)
            return
        except Exception:
            logger.exception(
                "Failed to create manual profile for user %s on server %s", user.id, server_id
            )
            await callback.answer(texts.ERROR_TECHNICAL_MESSAGE, show_alert=True)
            return

    if not profile or not profile.raw_config:
        profile = await _await_profile_ready(profile.id if profile else 0, timeout_seconds=4.0)

    if not profile or not profile.raw_config:
        await callback.answer(texts.DEVICE_CONFIG_UNAVAILABLE, show_alert=True)
        await _render_connections(callback.message, user, session)
        return

    conf_text = build_conf_file(profile.raw_config)
    if not conf_text:
        await callback.answer(texts.DEVICE_CONFIG_UNAVAILABLE, show_alert=True)
        await _render_connections(callback.message, user, session)
        return

    # Calculate active devices for caption
    active_sub_devices = dict(getattr(user, "active_sub_devices", None) or {})
    manual_count = (
        await session.execute(
            select(func.count(VPNProfile.id)).where(
                VPNProfile.user_id == user.id,
                VPNProfile.device_type == "manual",
                VPNProfile.provisioning_status.in_(RESERVING_STATUSES),
            )
        )
    ).scalar_one()
    total_active = len(active_sub_devices) + manual_count

    country_label = f"{server.country_flag or ''} {server.name or 'Server'}".strip()
    caption = texts.AWG_CONF_READY_CAPTION.format(
        country=country_label,
        active_count=total_active,
        limit=limit,
    )

    clean_filename = f"{server.name or 'server'}_AmneziaWG.conf".replace(" ", "_")
    doc = BufferedInputFile(conf_text.encode("utf-8"), filename=clean_filename)

    builder = InlineKeyboardBuilder()
    builder.button(text=texts.BTN_BACK_TO_DEVICES, callback_data="awg_manage_devices")
    builder.button(text=texts.BTN_MAIN_MENU_NAV, callback_data="back_to_main_menu")
    builder.adjust(1)

    await callback.message.answer_document(
        document=doc,
        caption=caption,
        reply_markup=builder.as_markup(),
        parse_mode="HTML",
    )


async def _render_manage_devices(
    message,
    user: User,
    session: AsyncSession,
):
    """Render unified device management screen showing sub devices and manual .conf profiles."""
    active_sub_devices = dict(getattr(user, "active_sub_devices", None) or {})

    stmt = (
        select(VPNProfile)
        .options(selectinload(VPNProfile.server))
        .where(
            VPNProfile.user_id == user.id,
            VPNProfile.device_type == "manual",
            VPNProfile.provisioning_status.in_(RESERVING_STATUSES),
        )
    )
    res = await session.execute(stmt)
    manual_profiles = res.scalars().all()

    total_active = len(active_sub_devices) + len(manual_profiles)
    limit = await _get_effective_device_limit(session, user)

    items_text = []
    builder = InlineKeyboardBuilder()

    if total_active < limit:
        builder.button(
            text=texts.BTN_DOWNLOAD_CONF,
            callback_data="awg_download_conf_menu",
        )

    now = now_utc()
    # 1. Sub devices
    for hwid_hash, data in active_sub_devices.items():
        label = data.get("label") or texts.AWG_SUB_DEVICE_LABEL_TEMPLATE.format(
            index=data.get("device_index", 1)
        )
        last_seen_raw = data.get("last_seen", "")
        last_seen_display = last_seen_raw
        is_inactive_7d = False
        if "T" in str(last_seen_raw):
            try:
                dt = datetime.fromisoformat(last_seen_raw)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                last_seen_display = format_datetime(dt)
                if (now - dt).total_seconds() > 7 * 86400:
                    is_inactive_7d = True
            except Exception:
                pass
        activity_str = safe(last_seen_display) or texts.CONNECTION_CONFIG_COMMON_NE_BYLO_AKTIVNOSTEY
        if is_inactive_7d:
            activity_str += texts.AWG_DEVICE_INACTIVE_7D_TAG
        items_text.append(
            texts.AWG_SUB_DEVICE_ITEM.format(
                label=safe(label),
                last_seen=activity_str,
            )
        )
        builder.button(
            text=texts.BTN_DISCONNECT_DEVICE_TEMPLATE.format(label=label),
            callback_data=f"awg_disconnect_sub:{hwid_hash[:16]}",
        )

    # 2. Manual .conf profiles
    for profile in manual_profiles:
        server = profile.server
        flag = server.country_flag if server else texts.EMOJI_GLOBE
        server_name = server.name if server else texts.LABEL_UNKNOWN_CAP
        location_label = f"{flag} {server_name}"
        traffic_str = format_traffic(
            (getattr(profile, "traffic_down", 0) or 0) + (getattr(profile, "traffic_up", 0) or 0)
        )
        status_label = (
            texts.DEVICE_STATUS_CREATING
            if profile.provisioning_status == "pending_create"
            else texts.DEVICE_STATUS_ACTIVE_LABEL
        )
        items_text.append(
            texts.AWG_MANUAL_DEVICE_ITEM.format(
                device_name=safe(profile.device_name),
                country=safe(location_label),
                traffic=traffic_str,
                status=status_label,
            )
        )
        builder.button(
            text=texts.BTN_DISCONNECT_DEVICE_TEMPLATE.format(label=profile.device_name),
            callback_data=f"request_delete_device:{profile.id}",
        )

    if not items_text:
        devices_list = texts.CONNECTION_EMPTY
    else:
        devices_list = "\n".join(items_text)

    rendered = texts.AWG_MANAGE_DEVICES_HEADER.format(
        active_count=total_active,
        limit=limit,
        devices_list=devices_list,
    )

    builder.button(text=texts.BTN_RESET_SUB_LINK, callback_data="awg_reset_sub_prompt")
    builder.button(text=texts.BTN_BACK, callback_data="back_to_connections")
    builder.adjust(1)

    await render_hub(
        message.bot,
        message.chat.id,
        rendered,
        builder.as_markup(),
    )


@router.callback_query(F.data == "awg_reset_sub_prompt")
async def awg_reset_sub_prompt(
    callback: CallbackQuery,
    state: FSMContext,
):
    """Show confirmation for resetting subscription token."""
    await callback.answer(show_alert=False)
    await state.clear()
    builder = InlineKeyboardBuilder()
    builder.button(text=texts.BTN_CONFIRM_RESET_SUB, callback_data="awg_reset_sub_execute")
    builder.button(text=texts.BTN_CANCEL_ACTION, callback_data="awg_manage_devices")
    builder.adjust(1)
    await render_hub(
        callback.bot,
        callback.message.chat.id,
        texts.AWG_RESET_SUB_CONFIRM_TEXT,
        builder.as_markup(),
    )


@router.callback_query(F.data == "awg_reset_sub_execute")
async def awg_reset_sub_execute(
    callback: CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
    db_user: User | None = None,
):
    """Execute reset/rotation of subscription token."""
    await state.clear()
    user = db_user or await get_user_by_telegram_id(session, callback.from_user.id)
    if not user:
        await callback.answer(texts.ERROR_USER_NOT_FOUND, show_alert=True)
        return

    await users_repo.rotate_subscription_token(session, user)
    await session.commit()
    await callback.answer(texts.AWG_RESET_SUB_SUCCESS, show_alert=True)
    await session.refresh(user)
    await _render_manage_devices(callback.message, user, session)


@router.callback_query(F.data == "awg_manage_devices")
async def awg_manage_devices(
    callback: CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
    db_user: User | None = None,
):
    """Handle ⚙️ Управление устройствами button."""
    await callback.answer(show_alert=False)
    await state.clear()

    user = db_user or await get_user_by_telegram_id(session, callback.from_user.id)
    if not user:
        await callback.answer(texts.ERROR_USER_NOT_FOUND, show_alert=True)
        return

    await _render_manage_devices(callback.message, user, session)


@router.callback_query(F.data.startswith("awg_disconnect_sub:"))
async def awg_disconnect_sub(
    callback: CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
    db_user: User | None = None,
):
    """Disconnect and revoke all node profiles for a sub device."""
    await state.clear()
    hwid_prefix = callback.data.split(":", 1)[1].strip()

    user = db_user or await get_user_by_telegram_id(session, callback.from_user.id)
    if not user:
        await callback.answer(texts.ERROR_USER_NOT_FOUND, show_alert=True)
        return

    active_sub_devices = dict(getattr(user, "active_sub_devices", None) or {})
    matched_hwid = None
    for h in active_sub_devices:
        if h.startswith(hwid_prefix):
            matched_hwid = h
            break

    if not matched_hwid:
        await callback.answer(texts.ERROR_DEVICE_NOT_FOUND_LABEL, show_alert=True)
        await _render_manage_devices(callback.message, user, session)
        return

    try:
        await DeviceService.delete_sub_device(session, user_id=user.id, hwid_hash=matched_hwid)
        await session.commit()
        await callback.answer(texts.DEVICE_DISCONNECTED_SUCCESS, show_alert=True)
    except Exception:
        logger.exception("Failed to delete sub device %s for user %s", matched_hwid, user.id)
        await callback.answer(texts.ERROR_TECHNICAL_MESSAGE, show_alert=True)

    await session.refresh(user)
    await _render_manage_devices(callback.message, user, session)
