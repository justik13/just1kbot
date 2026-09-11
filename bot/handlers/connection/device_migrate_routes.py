import logging

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery
from aiogram.utils.keyboard import InlineKeyboardBuilder
from cachetools import TTLCache
from sqlalchemy.ext.asyncio import AsyncSession

from bot import texts
from bot.keyboards import get_back_button
from bot.keyboards.device import get_device_migrate_confirm_keyboard
from database.models import User
from database.repositories.profiles_repo import get_profile_by_id
from database.repositories.servers_repo import (
    get_available_servers,
    get_server_by_id,
)
from database.repositories.users_repo import get_user_by_telegram_id
from services.device_service import (
    DailyLimitExceeded,
    DeviceCreationError,
    DeviceLimitExceeded,
    DeviceMigrationInProgress,
    DeviceService,
    DuplicateDeviceName,
    MigrationCooldownActive,
    NoActiveSubscription,
    ServerUnavailable,
)
from services.maintenance_service import MaintenanceService
from services.slots_cache import capture_server_peer_snapshot
from services.subscription import SubscriptionService
from utils.callbacks import parse_callback_id
from utils.datetime_helpers import now_utc
from utils.telegram import EFFECT_FIRE, render_hub, safe

from .common import _render_maintenance
from .device_create_routes import (
    _await_profile_ready,
    _classify_server_error,
    _get_server_error_text,
)
from .device_view_routes import render_device_screen

router = Router()
logger = logging.getLogger(__name__)

_migrating_devices: TTLCache[int, bool] = TTLCache(
    maxsize=5000,
    ttl=300,
)


@router.callback_query(F.data.startswith("migrate_device:"))
async def start_migrate_device(
    callback: CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
    db_user: User | None = None,
):
    await state.clear()
    telegram_user_id = callback.from_user.id

    if not await MaintenanceService.can_user_perform_action(session, telegram_user_id):
        await callback.answer(show_alert=False)
        await _render_maintenance(callback.message, session, back_to="back_to_connections")
        return

    profile_id = parse_callback_id(callback.data, 1)
    if profile_id is None:
        await callback.answer(texts.ERROR_INVALID_REQUEST, show_alert=True)
        return

    profile = await get_profile_by_id(session, profile_id)
    user = db_user or await get_user_by_telegram_id(session, telegram_user_id)

    if not profile or not user or profile.user_id != user.id:
        await callback.answer(texts.ERROR_ACCESS_DENIED, show_alert=True)
        return

    if not await SubscriptionService.check_access(session, user.telegram_id):
        await callback.answer(texts.ERROR_NO_SUBSCRIPTION, show_alert=True)
        return

    if profile.provisioning_status != "active":
        await callback.answer(texts.DEVICE_ACTION_UNAVAILABLE_STATE, show_alert=True)
        return

    # Check 15-minute cooldown
    if profile.created_at:
        elapsed = (now_utc() - profile.created_at).total_seconds()
        if elapsed < 900:
            remaining_min = max(1, int((900 - elapsed + 59) // 60))
            await callback.answer(
                texts.DEVICE_MIGRATE_COOLDOWN_ALERT.format(minutes=remaining_min),
                show_alert=True,
            )
            return

    servers = await get_available_servers(session)
    target_servers = [s for s in servers if s.id != profile.server_id]

    if not target_servers:
        await callback.answer(texts.DEVICE_MIGRATE_NO_OTHER_SERVERS, show_alert=True)
        return

    await callback.answer(show_alert=False)

    current_server = await get_server_by_id(session, profile.server_id)
    current_flag = current_server.country_flag if current_server else texts.EMOJI_GLOBE
    current_name = current_server.name if current_server else texts.LABEL_UNKNOWN_CAP
    current_server_display = f"{current_flag} {current_name}".strip()

    builder = InlineKeyboardBuilder()
    for server in target_servers:
        flag = server.country_flag or texts.EMOJI_GLOBE
        builder.button(
            text=f"{flag} {server.name}",
            callback_data=f"select_migrate_server:{profile.id}:{server.id}",
        )

    builder.button(
        text=texts.BTN_BACK_TO_DEVICE,
        callback_data=f"manage_device:{profile.id}",
    )
    builder.adjust(1)

    await render_hub(
        callback.bot,
        callback.message.chat.id,
        texts.DEVICE_MIGRATE_SELECT_SERVER.format(
            device_name=safe(profile.device_name),
            current_server=safe(current_server_display),
        ),
        builder.as_markup(),
        parse_mode="HTML",
    )


@router.callback_query(F.data.startswith("select_migrate_server:"))
async def select_migrate_target_server(
    callback: CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
    db_user: User | None = None,
):
    await state.clear()
    telegram_user_id = callback.from_user.id

    if not await MaintenanceService.can_user_perform_action(session, telegram_user_id):
        await callback.answer(show_alert=False)
        await _render_maintenance(callback.message, session, back_to="back_to_connections")
        return

    profile_id = parse_callback_id(callback.data, 1)
    target_server_id = parse_callback_id(callback.data, 2)

    if profile_id is None or target_server_id is None:
        await callback.answer(texts.ERROR_INVALID_REQUEST, show_alert=True)
        return

    profile = await get_profile_by_id(session, profile_id)
    user = db_user or await get_user_by_telegram_id(session, telegram_user_id)

    if not profile or not user or profile.user_id != user.id:
        await callback.answer(texts.ERROR_ACCESS_DENIED, show_alert=True)
        return

    target_server = await get_server_by_id(session, target_server_id)
    if not target_server or not target_server.is_active:
        await callback.answer(texts.ERROR_SERVER_UNAVAILABLE, show_alert=True)
        return

    await callback.answer(show_alert=False)

    target_flag = target_server.country_flag or texts.EMOJI_GLOBE
    target_display = f"{target_flag} {target_server.name}".strip()

    await render_hub(
        callback.bot,
        callback.message.chat.id,
        texts.DEVICE_MIGRATE_CONFIRM.format(
            device_name=safe(profile.device_name),
            target_server=safe(target_display),
        ),
        get_device_migrate_confirm_keyboard(profile.id, target_server.id),
        parse_mode="HTML",
    )


@router.callback_query(F.data.startswith("confirm_migrate_device:"))
async def confirm_migrate_device(
    callback: CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
    db_user: User | None = None,
):
    await state.clear()
    telegram_user_id = callback.from_user.id

    if not await MaintenanceService.can_user_perform_action(session, telegram_user_id):
        await callback.answer(show_alert=False)
        await _render_maintenance(callback.message, session, back_to="back_to_connections")
        return

    profile_id = parse_callback_id(callback.data, 1)
    target_server_id = parse_callback_id(callback.data, 2)

    if profile_id is None or target_server_id is None:
        await callback.answer(texts.ERROR_INVALID_REQUEST, show_alert=True)
        return

    if telegram_user_id in _migrating_devices:
        await callback.answer(texts.DEVICE_CREATE_IN_PROGRESS, show_alert=True)
        return

    _migrating_devices[telegram_user_id] = True

    try:
        user = db_user or await get_user_by_telegram_id(session, telegram_user_id)
        profile = await get_profile_by_id(session, profile_id)

        if not profile or not user or profile.user_id != user.id:
            await callback.answer(texts.ERROR_ACCESS_DENIED, show_alert=True)
            return

        target_server = await get_server_by_id(session, target_server_id)
        if not target_server or not target_server.is_active:
            await callback.answer(texts.ERROR_SERVER_UNAVAILABLE, show_alert=True)
            return

        target_name = target_server.name or texts.CONNECTION_DEVICE_CREATE_DEFAULT_SERVER_NAME
        target_display = f"{target_server.country_flag or texts.EMOJI_GLOBE} {target_name}".strip()

        await render_hub(
            callback.bot,
            callback.message.chat.id,
            texts.CONNECTION_DEVICE_CREATE_CREATING_SCREEN.format(v0=safe(target_name)),
            get_back_button(f"manage_device:{profile.id}"),
            parse_mode="HTML",
        )

        new_profile = None
        try:
            await session.commit()
            snapshot = await capture_server_peer_snapshot(target_server_id)
            new_profile = await DeviceService.migrate_device(
                session,
                user_id=user.id,
                profile_id=profile.id,
                target_server_id=target_server_id,
                snapshot=snapshot,
            )
            await session.commit()
        except DeviceMigrationInProgress:
            try:
                await session.rollback()
            except Exception:
                pass
            await callback.answer(texts.DEVICE_MIGRATE_IN_PROGRESS, show_alert=True)
            await render_device_screen(callback.bot, callback.message.chat.id, profile, user, session)
            return
        except MigrationCooldownActive as e:
            try:
                await session.rollback()
            except Exception:
                pass
            remaining_min = max(1, int((e.remaining_seconds + 59) // 60))
            await callback.answer(
                texts.DEVICE_MIGRATE_COOLDOWN_ALERT.format(minutes=remaining_min),
                show_alert=True,
            )
            await render_device_screen(callback.bot, callback.message.chat.id, profile, user, session)
            return
        except ServerUnavailable as e:
            try:
                await session.rollback()
            except Exception:
                pass
            error_type = _classify_server_error(str(e))
            error_text = _get_server_error_text(error_type)
            await callback.answer(error_text, show_alert=True)
            await render_device_screen(callback.bot, callback.message.chat.id, profile, user, session)
            return
        except (NoActiveSubscription, DailyLimitExceeded, DeviceLimitExceeded, DuplicateDeviceName) as e:
            try:
                await session.rollback()
            except Exception:
                pass
            await callback.answer(str(e), show_alert=True)
            await render_device_screen(callback.bot, callback.message.chat.id, profile, user, session)
            return
        except DeviceCreationError as e:
            try:
                await session.rollback()
            except Exception:
                pass
            logger.warning("DeviceCreationError during migration: %s", e)
            await callback.answer(texts.ERROR_TECHNICAL_MESSAGE, show_alert=True)
            await render_device_screen(callback.bot, callback.message.chat.id, profile, user, session)
            return
        except Exception:
            try:
                await session.rollback()
            except Exception:
                pass
            logger.exception("Unexpected error during DeviceService.migrate_device")
            await callback.answer(texts.ERROR_TECHNICAL_MESSAGE, show_alert=True)
            await render_device_screen(callback.bot, callback.message.chat.id, profile, user, session)
            return

        if new_profile:
            try:
                ready_profile = await _await_profile_ready(new_profile.id, timeout_seconds=7.0)
            except Exception:
                logger.exception("Error awaiting profile ready for migrated profile_id=%s", new_profile.id)
                ready_profile = None

            if ready_profile and ready_profile.provisioning_status == "active":
                await session.refresh(user)
                notice = texts.DEVICE_MIGRATE_SUCCESS_NOTICE.format(target_server=safe(target_display))
                await render_device_screen(
                    callback.bot,
                    callback.message.chat.id,
                    ready_profile,
                    user,
                    session,
                    message_effect_id=EFFECT_FIRE,
                    notice=notice,
                )
            elif ready_profile and ready_profile.provisioning_status in ("create_failed", "create_cleanup_pending"):
                # Creation genuinely failed on node: original profile was untouched and remains active
                await session.refresh(user)
                await render_device_screen(
                    callback.bot,
                    callback.message.chat.id,
                    profile,
                    user,
                    session,
                    notice=texts.DEVICE_MIGRATE_FAILED_NOTICE,
                )
            else:
                # Timeout reached while still pending_create: background worker continues provisioning.
                # Never show false failure notice. Render the new profile with pending notice.
                await session.refresh(user)
                pending_profile = ready_profile or await get_profile_by_id(session, new_profile.id) or new_profile
                await render_device_screen(
                    callback.bot,
                    callback.message.chat.id,
                    pending_profile,
                    user,
                    session,
                    notice=texts.DEVICE_MIGRATE_PENDING_NOTICE,
                )
    finally:
        _migrating_devices.pop(telegram_user_id, None)
