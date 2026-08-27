import logging

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
from utils.formatters import format_admin_breadcrumbs, format_datetime, format_traffic
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
            texts.UI_BOT_HANDLERS_ADMIN_USERS_DEVICE_ROUTES_L46_1,
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

    profiles = await get_user_profiles(session, user.id)

    header = format_admin_breadcrumbs(texts.UI_DEVICE_ROUTES_POLZOVATELI_64, f"ID {telegram_id}", texts.UI_DEVICE_ROUTES_USTROYSTVA_64)
    now = now_utc()

    if not profiles:
        text = (
            f"{header}"+
            texts.UI_DEVICE_ROUTES_USTROYSTVA_POLZOVATELYA_ID_70.format(telegram_id=telegram_id)+
            texts.UI_DEVICE_ROUTES_U_POLZOVATELYA_POKA_NET_SOZDAN_71.format()
        )
    else:
        lines = [texts.UI_DEVICE_ROUTES_USTROYSTVA_POLZOVATELYA_ID_74.format(header=header, telegram_id=telegram_id)]
        for profile in profiles:
            name = (
                getattr(profile, "device_name", None)
                or texts.UI_DEVICE_ROUTES_USTROYSTVO_78.format(profile_id=profile.id)
            )
            # get_user_profiles() eagerly loads VPNProfile.server, so this does
            # not add a query per device and keeps the device list efficient.
            server = getattr(profile, "server", None)
            server_name = safe(server.name) if server else texts.UI_DEVICE_ROUTES_NEIZVESTNYY_SERVER_83
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

            status_hs = texts.UI_DEVICE_ROUTES_V_SETI_AKTIVNOST_3_MIN_100 if is_online else texts.UI_DEVICE_ROUTES_OFLAYN_100
            traffic_total = format_traffic((getattr(profile, "traffic_down", 0) or 0) + (getattr(profile, "traffic_up", 0) or 0))
            last_conn = format_datetime(profile.last_connected) if getattr(profile, "last_connected", None) else texts.UI_DEVICE_ROUTES_NE_BYLO_PODKLYUCHENIYA_102

            lines.append(
                f"• 📱 <b>{safe(name)}</b>\n"+
                texts.UI_DEVICE_ROUTES_ID_USTROYSTVA_106.format(profile_id=profile.id)+
                texts.UI_DEVICE_ROUTES_SERVER_107.format(server_flag=server_flag, server_name=server_name)+
                texts.UI_DEVICE_ROUTES_SOSTOYANIE_108.format(status_hs=status_hs)+
                texts.UI_DEVICE_ROUTES_TRAFIK_109.format(traffic_total=traffic_total)+
                texts.UI_DEVICE_ROUTES_AKTIVNOST_110.format(last_conn=last_conn)
            )
        text = "\n".join(lines)

    try:
        await callback.message.edit_text(
            text,
            reply_markup=get_admin_user_devices_keyboard(
                telegram_id,
                profiles,
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
            texts.UI_BOT_HANDLERS_ADMIN_USERS_DEVICE_ROUTES_L109_1,
            show_alert=True,
        )
        return

    telegram_id = parse_callback_int(parts, 1)
    profile_id = parse_callback_int(parts, 2)

    if telegram_id is None or profile_id is None:
        await callback.answer(
            texts.UI_BOT_HANDLERS_ADMIN_USERS_DEVICE_ROUTES_L119_1,
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
    flag = server.country_flag if server else texts.RUNTIME_BOT_HANDLERS_ADMIN_USERS_DEVICE_ROUTES_L135_1
    server_name = server.name if server else texts.RUNTIME_BOT_HANDLERS_ADMIN_USERS_DEVICE_ROUTES_L136_1

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
                    f"admin_delete_device_apply:"+
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
            texts.UI_BOT_HANDLERS_ADMIN_USERS_DEVICE_ROUTES_L180_1,
            show_alert=True,
        )
        return

    telegram_id = parse_callback_int(parts, 1)
    profile_id = parse_callback_int(parts, 2)

    if telegram_id is None or profile_id is None:
        await callback.answer(
            texts.UI_BOT_HANDLERS_ADMIN_USERS_DEVICE_ROUTES_L190_1,
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
