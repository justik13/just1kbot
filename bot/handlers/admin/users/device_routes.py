import logging

from aiogram import Router, F
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
from database.repositories.servers_repo import get_server_by_id
from services.device_service import DeviceService
from utils.admin import is_admin
from utils.callbacks import (
    parse_callback_id,
    parse_callback_int,
    parse_callback_parts,
)
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

    from utils.formatters import format_admin_breadcrumbs, format_traffic, format_datetime
    from utils.datetime_helpers import now_utc

    header = format_admin_breadcrumbs("👥 Пользователи", f"ID {telegram_id}", "📱 Устройства")
    now = now_utc()

    if not profiles:
        text = (
            f"{header}"
            f"📱 <b>Устройства пользователя ID {telegram_id}:</b>\n\n"
            f"<i>У пользователя пока нет созданных устройств.</i>"
        )
    else:
        # Resolve server names in one pass so the admin can immediately see
        # where every device is provisioned without exposing internal IDs only.
        server_ids = {profile.server_id for profile in profiles if profile.server_id is not None}
        servers = {}
        for server_id in server_ids:
            server = await get_server_by_id(session, server_id)
            if server:
                servers[server_id] = server

        lines = [f"{header}📱 <b>Устройства пользователя ID {telegram_id}:</b>\n"]
        for profile in profiles:
            name = (
                getattr(profile, "device_name", None)
                or f"Устройство #{profile.id}"
            )
            server = servers.get(profile.server_id)
            server_name = safe(server.name) if server else "Неизвестный сервер"
            server_flag = safe(server.country_flag) if server and server.country_flag else "🌐"

            # Статус по last_handshake_at
            last_hs = getattr(profile, "last_handshake_at", None) or getattr(profile, "updated_at", None)
            is_online = False
            if last_hs:
                if last_hs.tzinfo is None:
                    last_hs = last_hs.replace(tzinfo=now.tzinfo)
                delta_sec = (now - last_hs).total_seconds()
                if delta_sec <= 180:  # Рукопожатие за последние 3 минуты
                    is_online = True

            status_hs = "🟢 <b>В сети (онлайн)</b>" if is_online else "🔴 <b>Офлайн</b>"
            traffic_total = format_traffic((getattr(profile, "traffic_down", 0) or 0) + (getattr(profile, "traffic_up", 0) or 0))
            last_conn = format_datetime(profile.last_connected) if getattr(profile, "last_connected", None) else "⏱ не было подключения"

            lines.append(
                f"• 📱 <b>{safe(name)}</b>\n"
                f"   🆔 ID устройства: <code>{profile.id}</code>\n"
                f"   🖥 Сервер: {server_flag} <b>{server_name}</b>\n"
                f"   Состояние: {status_hs}\n"
                f"   Трафик: <code>{traffic_total}</code>\n"
                f"   Активность: <i>{last_conn}</i>\n"
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

    server = await get_server_by_id(session, profile.server_id)
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
                    f"admin_delete_device_apply:"
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
