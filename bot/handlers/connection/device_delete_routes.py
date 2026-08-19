from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery
from cachetools import TTLCache
from sqlalchemy.ext.asyncio import AsyncSession

from bot import texts
from bot.keyboards import get_device_delete_confirm_keyboard
from database.models import User
from database.repositories.profiles_repo import get_profile_by_id
from database.repositories.users_repo import get_user_by_telegram_id
from services.device_service import DeviceService
from utils.callbacks import parse_callback_id
from utils.telegram import render_hub, safe

from .common import _render_connections

router = Router()

_deleting_devices: TTLCache[int, bool] = TTLCache(
    maxsize=5000,
    ttl=300,
)


@router.callback_query(F.data.startswith("request_delete_device:"))
async def request_delete_device(
    callback: CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
    db_user: User | None = None,
):
    await state.clear()

    profile_id = parse_callback_id(callback.data, 1)

    if profile_id is None:
        await callback.answer(texts.UI_BOT_HANDLERS_CONNECTION_DEVICE_DELETE_ROUTES_L42_1, show_alert=True)
        return

    profile = await get_profile_by_id(session, profile_id)

    if not profile or not db_user or profile.user_id != db_user.id:
        await callback.answer(texts.ERROR_ACCESS_DENIED, show_alert=True)
        return

    from .device_view_routes import can_show_delete_action, render_device_screen

    if not can_show_delete_action(profile):
        status = getattr(profile, "provisioning_status", "")
        if status == "deleting":
            msg = "🗑 Устройство уже удаляется с сервера."
        elif status == "create_cleanup_pending":
            msg = "⚠️ Идёт автоматическое восстановление после сбоя. Попробуйте позже."
        elif status == "pending_create":
            msg = texts.DEVICE_CREATE_IN_PROGRESS
        else:
            msg = "⚠️ Это действие сейчас недоступно для текущего состояния устройства."
        await callback.answer(msg, show_alert=True)
        await render_device_screen(callback.bot, callback.message.chat.id, profile, db_user, session)
        return

    await callback.answer(show_alert=False)

    await render_hub(
        callback.bot,
        callback.message.chat.id,
        texts.DEVICE_DELETE_CONFIRM.format(device_name=safe(profile.device_name)),
        get_device_delete_confirm_keyboard(profile_id),
    )


@router.callback_query(F.data.startswith("cancel_delete_device:"))
async def cancel_delete_device(
    callback: CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
    db_user: User | None = None,
):
    await state.clear()

    profile_id = parse_callback_id(callback.data, 1)

    if profile_id is None:
        await callback.answer(texts.UI_BOT_HANDLERS_CONNECTION_DEVICE_DELETE_ROUTES_L71_1, show_alert=True)
        return

    profile = await get_profile_by_id(session, profile_id)

    if not profile or not db_user or profile.user_id != db_user.id:
        await callback.answer(texts.ERROR_ACCESS_DENIED, show_alert=True)
        return

    await callback.answer(texts.DEVICE_DELETE_CANCELLED, show_alert=False)

    from .device_view_routes import render_device_screen
    await render_device_screen(
        callback.bot,
        callback.message.chat.id,
        profile,
        db_user,
        session,
    )


@router.callback_query(F.data.startswith("confirm_delete_device:"))
async def confirm_delete_device(
    callback: CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
    db_user: User | None = None,
):
    profile_id = parse_callback_id(callback.data, 1)

    if profile_id is None:
        await callback.answer(texts.UI_BOT_HANDLERS_CONNECTION_DEVICE_DELETE_ROUTES_L100_1, show_alert=True)
        return

    profile = await get_profile_by_id(session, profile_id)

    if not profile or not db_user or profile.user_id != db_user.id:
        await callback.answer(texts.ERROR_ACCESS_DENIED, show_alert=True)
        return

    if profile_id in _deleting_devices:
        await callback.answer(texts.DEVICE_DELETE_IN_PROGRESS, show_alert=True)
        return

    _deleting_devices[profile_id] = True

    try:
        await state.clear()

        from services.device_service import DeviceStillCreating

        try:
            success = await DeviceService.delete_device(
                session,
                profile,
                actor_id=callback.from_user.id,
            )
        except DeviceStillCreating:
            await callback.answer(texts.DEVICE_CREATE_IN_PROGRESS, show_alert=True)
            return

        if not success:
            await callback.answer(
                texts.ERROR_SERVER_UNAVAILABLE_GENERIC,
                show_alert=True,
            )
            return

        await callback.answer(texts.DEVICE_DELETING_PROGRESS, show_alert=False)

        user = db_user or await get_user_by_telegram_id(
            session,
            callback.from_user.id,
        )

        if user:
            await _render_connections(callback.message, user, session)

    finally:
        _deleting_devices.pop(profile_id, None)
