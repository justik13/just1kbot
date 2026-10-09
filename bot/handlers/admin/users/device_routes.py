import logging
from datetime import datetime

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery
from sqlalchemy.ext.asyncio import AsyncSession

from bot import texts
from bot.keyboards import get_back_button
from bot.keyboards.admin.users import (
    get_admin_confirm_action_keyboard,
    get_admin_user_devices_keyboard,
)
from database.repositories.profiles_repo import get_profile_by_id, get_user_profiles
from services.device_service import DeviceService
from utils.admin import is_admin
from utils.callbacks import (
    parse_callback_id,
    parse_callback_int,
    parse_callback_parts,
)
from utils.datetime_helpers import now_utc
from bot.formatters import format_admin_breadcrumbs
from utils.formatters import format_datetime, format_traffic
from utils.telegram import safe

from .common import _get_user_with_profiles

router = Router()
logger = logging.getLogger(__name__)


@router.callback_query(F.data.startswith("admin_user_devices:"))
async def admin_user_devices(
    callback: CallbackQuery,
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
            texts.ERROR_INVALID_REQUEST,
            show_alert=True,
        )
        return

    await callback.answer(show_alert=False)

    user = await _get_user_with_profiles(session, telegram_id)
    if not user:
        await callback.message.edit_text(
            texts.ERROR_USER_NOT_FOUND,
            reply_markup=get_back_button("admin_users"),
        )
        return

    profiles = await get_user_profiles(session, user.id, include_deleting=True)

    header = format_admin_breadcrumbs(texts.BTN_USERS, f"ID {telegram_id}", texts.ADMIN_USERS_DEVICE_DEVICES)
    now = now_utc()

    if not profiles:
        text = (
            f"{header}"+
            texts.ADMIN_USERS_DEVICE_DEVICES_POLZOVATELYA_ID.format(telegram_id=telegram_id)+
            texts.ADMIN_USERS_DEVICE_U_POLZOVATELYA_POKA_NET_SOZDAN.format()
        )
    else:
        lines = [texts.ADMIN_USER_DEVICES_HEADER.format(header=header, telegram_id=telegram_id)]
        for profile in profiles:
            name = (
                getattr(profile, "device_name", None)
                or texts.ADMIN_USERS_DEVICE_DEVICE.format(profile_id=profile.id)
            )
            # get_user_profiles() eagerly loads VPNProfile.server, so this does
            # not add a query per device and keeps the device list efficient.
            server = getattr(profile, "server", None)
            server_name = safe(server.name) if server else texts.ADMIN_USERS_DEVICE_NEIZVESTNYY_SERVER
            server_flag = safe(server.country_flag) if server and server.country_flag else "🌐"

            # VPNProfile does not have a last_handshake_at column. The traffic
            # worker persists the provider's lastHandshake/lastSeen/updatedAt
            # into last_connected, so use that field as the only available
            # activity timestamp instead of silently treating updated_at as a
            # handshake signal.
            last_activity = getattr(profile, "last_connected", None)
            is_online = False
            if last_activity:
                if last_activity.tzinfo is None:
                    last_activity = last_activity.replace(tzinfo=now.tzinfo)
                delta_sec = (now - last_activity).total_seconds()
                if 0 <= delta_sec <= 180:
                    is_online = True

            status_hs = texts.ADMIN_USERS_DEVICE_V_SETI_AKTIVNOST_3_MIN if is_online else texts.ADMIN_USERS_DEVICE_OFLAYN
            from utils.traffic_helpers import get_archived_traffic_for_device

            archived_bytes = get_archived_traffic_for_device(
                getattr(user, "archived_device_traffic", None), name
            )
            dev_bytes = (getattr(profile, "traffic_down", 0) or 0) + (getattr(profile, "traffic_up", 0) or 0) + archived_bytes
            traffic_total = format_traffic(dev_bytes)
            last_conn = format_datetime(profile.last_connected) if getattr(profile, "last_connected", None) else texts.ADMIN_USERS_DEVICE_NE_BYLO_PODKLYUCHENIYA

            lines.append(
                f"• 📱 <b>{safe(name)}</b>\n"+
                texts.ADMIN_USERS_DEVICE_ID_DEVICES.format(profile_id=profile.id)+
                texts.ADMIN_USERS_DEVICE_SERVER.format(server_flag=server_flag, server_name=server_name)+
                texts.ADMIN_USERS_DEVICE_SOSTOYANIE.format(status_hs=status_hs)+
                texts.ADMIN_USERS_DEVICE_TRAFIK.format(traffic_total=traffic_total)+
                texts.ADMIN_USERS_DEVICE_AKTIVNOST.format(last_conn=last_conn)
            )
        text = "\n".join(lines)

    # VLESS subscription & active HWIDs
    from database.repositories import vless_subscription_repo
    from database.repositories.vless_subscription_repo import VLESS_HWID_TTL_HOURS
    from services.vless_subscription_service import VlessSubscriptionService

    try:
        vless_sub = await vless_subscription_repo.get_subscription_by_user_id(session, user.id)
    except Exception as e:
        logger.debug("Failed to get VLESS subscription for admin devices: %s", e)
        vless_sub = None

    vless_sub_url: str | None = None
    raw_vless_hwids: dict = {}
    if vless_sub:
        vless_sub_url = VlessSubscriptionService.build_subscription_url(vless_sub.token)
        raw_vless_hwids = getattr(vless_sub, "active_hwids", None) or {}

    active_hwids = vless_subscription_repo.prune_stale_hwids(raw_vless_hwids, ttl_hours=VLESS_HWID_TTL_HOURS)
    vless_active_count = len(active_hwids)

    if not raw_vless_hwids:
        vless_devices_list = texts.ADMIN_VLESS_NO_DEVICES
    else:
        vless_lines = []
        for hwid, ts in sorted(
            raw_vless_hwids.items(),
            key=lambda item: item[1] if isinstance(item[1], str) else "",
            reverse=True,
        ):
            if isinstance(ts, str):
                try:
                    dt = datetime.fromisoformat(ts)
                    last_conn = format_datetime(dt)
                except Exception:
                    last_conn = ts
                status = (
                    texts.ADMIN_VLESS_DEVICE_STATUS_ACTIVE
                    if hwid in active_hwids
                    else texts.ADMIN_VLESS_DEVICE_STATUS_INACTIVE
                )
            else:
                last_conn = texts.PLACEHOLDER_DASH
                status = texts.ADMIN_VLESS_DEVICE_STATUS_INACTIVE
            vless_lines.append(
                texts.ADMIN_VLESS_DEVICE_ITEM.format(
                    hwid=safe(hwid),
                    last_conn=last_conn,
                    status=status,
                )
            )
        vless_devices_list = "\n".join(vless_lines)

    vless_block = texts.ADMIN_VLESS_DEVICES_HEADER.format(
        active=vless_active_count,
        devices_list=vless_devices_list,
    )
    text = f"{text}\n\n{vless_block}"

    from database.repositories import white_internet_repo
    try:
        wi_sub = await white_internet_repo.get_subscription_by_user_id(session, user.id)
    except Exception as e:
        logger.debug("Failed to get WI subscription for admin devices: %s", e)
        wi_sub = None
    has_wi_devices = bool(wi_sub and getattr(wi_sub, "active_hwids", None))

    try:
        await callback.message.edit_text(
            text,
            reply_markup=get_admin_user_devices_keyboard(
                telegram_id,
                profiles,
                has_wi_devices=has_wi_devices,
                vless_sub_url=vless_sub_url,
                has_vless_hwids=bool(raw_vless_hwids),
            ),
            parse_mode="HTML",
        )
    except TelegramBadRequest as e:
        logger.debug(f"admin_user_devices edit_text failed: {e}")


@router.callback_query(F.data.startswith("admin_delete_device:"))
async def admin_delete_device_confirm(
    callback: CallbackQuery,
    session: AsyncSession,
):
    if not is_admin(callback.from_user.id):
        await callback.answer(
            texts.ERROR_ACCESS_DENIED,
            show_alert=True,
        )
        return

    parts = parse_callback_parts(callback.data, 3)
    if parts is None:
        await callback.answer(
            texts.ERROR_INVALID_REQUEST,
            show_alert=True,
        )
        return

    telegram_id = parse_callback_int(parts, 1)
    profile_id = parse_callback_int(parts, 2)

    if telegram_id is None or profile_id is None:
        await callback.answer(
            texts.ERROR_INVALID_REQUEST,
            show_alert=True,
        )
        return

    await callback.answer(show_alert=False)

    profile = await get_profile_by_id(session, profile_id)
    if not profile:
        await callback.answer(
            texts.ERROR_PROFILE_NOT_FOUND,
            show_alert=True,
        )
        return

    # The Telegram user id is part of the callback contract. Do not trust it
    # merely for navigation: verify that the selected profile actually belongs
    # to that user before exposing or deleting the device.
    user = await _get_user_with_profiles(session, telegram_id)
    if not user or profile.user_id != user.id:
        await callback.answer(
            texts.ERROR_PROFILE_NOT_FOUND,
            show_alert=True,
        )
        return

    server = getattr(profile, "server", None)
    flag = server.country_flag if server else texts.EMOJI_GLOBE
    server_name = server.name if server else texts.LABEL_UNKNOWN_CAP

    text = texts.ADMIN_DELETE_DEVICE_CONFIRM.format(
        telegram_id=telegram_id,
        device_name=safe(profile.device_name),
        flag=flag,
        server_name=safe(server_name),
    )

    try:
        await callback.message.edit_text(
            text,
            reply_markup=get_admin_confirm_action_keyboard(
                confirm_callback=(
                    "admin_delete_device_apply:"+
                    f"{telegram_id}:{profile_id}"
                ),
                cancel_callback=(
                    f"admin_user_devices:{telegram_id}"
                ),
            ),
            parse_mode="HTML",
        )
    except TelegramBadRequest as e:
        logger.debug(
            f"admin_delete_device_confirm edit_text failed: {e}"
        )


@router.callback_query(F.data.startswith("admin_delete_device_apply:"))
async def admin_delete_device_apply(
    callback: CallbackQuery,
    session: AsyncSession,
):
    if not is_admin(callback.from_user.id):
        await callback.answer(
            texts.ERROR_ACCESS_DENIED,
            show_alert=True,
        )
        return

    parts = parse_callback_parts(callback.data, 3)
    if parts is None:
        await callback.answer(
            texts.ERROR_INVALID_REQUEST,
            show_alert=True,
        )
        return

    telegram_id = parse_callback_int(parts, 1)
    profile_id = parse_callback_int(parts, 2)

    if telegram_id is None or profile_id is None:
        await callback.answer(
            texts.ERROR_INVALID_REQUEST,
            show_alert=True,
        )
        return

    await callback.answer(show_alert=False)

    try:
        profile = await get_profile_by_id(session, profile_id)
        if not profile:
            await callback.answer(
                texts.ERROR_PROFILE_NOT_FOUND,
                show_alert=True,
            )
            return

        # Re-check ownership at the destructive boundary. A Telegram callback
        # can be forged or stale, so the confirmation step is not sufficient.
        user = await _get_user_with_profiles(session, telegram_id)
        if not user or profile.user_id != user.id:
            await callback.answer(
                texts.ERROR_PROFILE_NOT_FOUND,
                show_alert=True,
            )
            return

        device_name = profile.device_name

        success = await DeviceService.delete_device(
            session,
            profile,
            actor_id=callback.from_user.id,
            force=True,
        )

        if not success:
            await callback.answer(
                texts.ADMIN_DELETE_DEVICE_FAILED,
                show_alert=True,
            )
            return

        text = texts.ADMIN_DELETE_DEVICE_SUCCESS.format(
            telegram_id=telegram_id,
            device_name=safe(device_name),
        )

        try:
            await callback.message.edit_text(
                text,
                reply_markup=get_back_button(
                    f"admin_user_devices:{telegram_id}"
                ),
                parse_mode="HTML",
            )
        except TelegramBadRequest as e:
            logger.debug(
                f"admin_delete_device_apply edit_text failed: {e}"
            )

    except Exception as e:
        logger.error(
            f"admin_delete_device_apply error: {e}",
            exc_info=True,
        )
        await session.rollback()
        await callback.answer(
            texts.ADMIN_DELETE_DEVICE_ERROR,
            show_alert=True,
        )


@router.callback_query(F.data.startswith("admin_vless_hwid_reset:"))
async def admin_vless_hwid_reset(
    callback: CallbackQuery,
    session: AsyncSession,
):
    if not is_admin(callback.from_user.id):
        await callback.answer(texts.ERROR_ACCESS_DENIED, show_alert=True)
        return

    telegram_id = parse_callback_id(callback.data, 1)
    if telegram_id is None:
        await callback.answer(texts.ERROR_INVALID_REQUEST, show_alert=True)
        return

    user = await _get_user_with_profiles(session, telegram_id)
    if not user:
        await callback.answer(texts.ERROR_USER_NOT_FOUND, show_alert=True)
        return

    from config.enums import AdminAuditAction
    from database.repositories import vless_subscription_repo
    from services.audit_service import AuditService
    from services.subscription import SubscriptionService
    from services.vless_subscription_service import VlessSubscriptionService

    sub = await vless_subscription_repo.get_subscription_by_user_id(session, user.id)
    if sub:
        target_active = (
            SubscriptionService.check_vpn_access(user)
            and not getattr(user, "financial_hold", False)
            and not getattr(user, "is_banned", False)
        )
        old_uuid, _ = await vless_subscription_repo.reset_hwids(session, sub.id)
        sub.is_active = target_active
        session.add(sub)
        if old_uuid:
            VlessSubscriptionService.deprovision_background(old_uuid, session=session)
            VlessSubscriptionService.ensure_synced_background(user.id, is_active=target_active, session=session)
        await AuditService.log_action(
            session,
            admin_id=callback.from_user.id,
            action=AdminAuditAction.VLESS_HWID_RESET,
            target_type="user",
            target_id=user.id,
            details={"telegram_id": telegram_id, "subscription_id": sub.id},
        )
        await session.commit()

    await callback.answer(texts.ADMIN_ALERT_VLESS_HWID_RESET_SUCCESS, show_alert=True)
    try:
        updated_cb = callback.model_copy(update={"data": f"admin_user_devices:{telegram_id}"})
    except Exception:
        callback.data = f"admin_user_devices:{telegram_id}"
        updated_cb = callback
    await admin_user_devices(updated_cb, session)


@router.callback_query(F.data.startswith("admin_vless_token_rotate:"))
async def admin_vless_token_rotate(
    callback: CallbackQuery,
    session: AsyncSession,
):
    if not is_admin(callback.from_user.id):
        await callback.answer(texts.ERROR_ACCESS_DENIED, show_alert=True)
        return

    telegram_id = parse_callback_id(callback.data, 1)
    if telegram_id is None:
        await callback.answer(texts.ERROR_INVALID_REQUEST, show_alert=True)
        return

    user = await _get_user_with_profiles(session, telegram_id)
    if not user:
        await callback.answer(texts.ERROR_USER_NOT_FOUND, show_alert=True)
        return

    from config.enums import AdminAuditAction
    from database.repositories import vless_subscription_repo
    from services.audit_service import AuditService
    from services.subscription import SubscriptionService
    from services.vless_subscription_service import VlessSubscriptionService

    sub = await vless_subscription_repo.get_or_create_subscription(session, user.id)
    if sub:
        target_active = (
            SubscriptionService.check_vpn_access(user)
            and not getattr(user, "financial_hold", False)
            and not getattr(user, "is_banned", False)
        )
        _, old_uuid, _ = await vless_subscription_repo.rotate_token(session, sub.id)
        sub.is_active = target_active
        session.add(sub)
        if old_uuid:
            VlessSubscriptionService.deprovision_background(old_uuid, session=session)
            VlessSubscriptionService.ensure_synced_background(user.id, is_active=target_active, session=session)
        await AuditService.log_action(
            session,
            admin_id=callback.from_user.id,
            action=AdminAuditAction.VLESS_TOKEN_RESET,
            target_type="user",
            target_id=user.id,
            details={"telegram_id": telegram_id, "subscription_id": sub.id, "token_rotated": True},
        )
        await session.commit()

    await callback.answer(texts.ADMIN_ALERT_VLESS_TOKEN_ROTATE_SUCCESS, show_alert=True)
    try:
        updated_cb = callback.model_copy(update={"data": f"admin_user_devices:{telegram_id}"})
    except Exception:
        callback.data = f"admin_user_devices:{telegram_id}"
        updated_cb = callback
    await admin_user_devices(updated_cb, session)

