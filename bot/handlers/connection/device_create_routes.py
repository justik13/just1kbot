import asyncio
import logging
import re
import time

from aiogram import F, Router
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery
from aiogram.utils.keyboard import InlineKeyboardBuilder
from cachetools import TTLCache
from sqlalchemy.ext.asyncio import AsyncSession

from bot import texts
from bot.keyboards import get_back_button
from bot.states import DeviceCreationStates
from database.connection import get_session
from database.models import User, VPNProfile
from database.repositories.profiles_repo import get_user_profiles
from database.repositories.servers_repo import (
    get_available_servers,
    get_server_by_id,
)
from database.repositories.users_repo import get_user_by_telegram_id
from services.device_service import (
    DailyLimitExceeded,
    DeviceCreationError,
    DeviceLimitExceeded,
    DeviceService,
    DuplicateDeviceName,
    InvalidConfig,
    NoActiveSubscription,
    ServerUnavailable,
)
from services.maintenance_service import MaintenanceService
from services.slots_cache import capture_server_peer_snapshot
from services.subscription import SubscriptionService
from utils.callbacks import parse_callback_id
from utils.telegram import render_hub

from .common import (
    _get_effective_device_limit,
    _render_connections,
    _render_maintenance,
)

router = Router()
logger = logging.getLogger(__name__)

_creating_devices: TTLCache[int, bool] = TTLCache(
    maxsize=5000,
    ttl=300,
)


async def _await_profile_ready(
    profile_id: int,
    timeout_seconds: float = 4.0,
    poll_interval: float = 0.25,
) -> VPNProfile | None:
    """Poll for profile to become active or fail within a monotonic UI wait window.

    Uses short-lived independent read sessions to prevent identity-map staleness
    and avoid holding DB connections from the pool during sleeps.
    """
    deadline = time.monotonic() + timeout_seconds
    while True:
        session = await get_session()
        try:
            profile = await session.get(VPNProfile, profile_id)
            if profile and profile.provisioning_status == "active" and bool(profile.raw_config) and bool(profile.peer_id):
                return profile
            if profile and profile.provisioning_status in ("create_failed", "create_cleanup_pending"):
                return profile
        finally:
            await session.close()

        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return None
        await asyncio.sleep(min(poll_interval, remaining))


def _get_no_subscription_keyboard():
    builder = InlineKeyboardBuilder()
    builder.button(text=texts.UI_BOT_HANDLERS_CONNECTION_DEVICE_CREATE_ROUTES_L51_1, callback_data="menu_buy")
    builder.button(text=texts.UI_BOT_HANDLERS_CONNECTION_DEVICE_CREATE_ROUTES_L52_1, callback_data="back_to_main_menu")
    builder.adjust(1)
    return builder.as_markup()


def _get_device_limit_keyboard():
    builder = InlineKeyboardBuilder()
    builder.button(text=texts.UI_BOT_HANDLERS_CONNECTION_DEVICE_CREATE_ROUTES_L59_1, callback_data="payment_change_tariff")
    builder.button(text=texts.UI_BOT_HANDLERS_CONNECTION_DEVICE_CREATE_ROUTES_L60_1, callback_data="back_to_connections")
    builder.adjust(1)
    return builder.as_markup()


def _classify_server_error(error_msg: str) -> str:
    msg_lower = error_msg.lower()

    if "full" in msg_lower:
        return "full"
    if "disabled" in msg_lower:
        return "disabled"
    if "busy" in msg_lower:
        return "busy"
    if "verify" in msg_lower or "slots" in msg_lower:
        return "slots_unknown"
    if "api" in msg_lower or "create_user" in msg_lower:
        return "api_failed"
    if "db error" in msg_lower:
        return "db_error"

    return "unknown"


def _get_server_error_text(error_type: str) -> str:
    mapping = {
        "full": texts.ERROR_SERVER_FULL,
        "disabled": texts.ERROR_SERVER_DISABLED,
        "busy": texts.ERROR_SERVER_BUSY,
        "slots_unknown": texts.ERROR_SERVER_SLOTS_UNKNOWN,
        "api_failed": texts.ERROR_API_CREATE_FAILED,
        "db_error": texts.ERROR_TECHNICAL_MESSAGE,
        "unknown": texts.ERROR_SERVER_UNAVAILABLE,
    }
    return mapping.get(error_type, texts.ERROR_SERVER_UNAVAILABLE)


@router.callback_query(F.data == "add_device")
async def start_add_device(
    callback: CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
    db_user: User | None = None,
):
    user_id = callback.from_user.id

    if not await MaintenanceService.can_user_perform_action(session, user_id):
        await callback.answer(show_alert=False)
        await _render_maintenance(
            callback.message, session, back_to="back_to_connections"
        )
        return

    if user_id in _creating_devices:
        await callback.answer(texts.DEVICE_CREATE_IN_PROGRESS, show_alert=True)
        return

    user = db_user or await get_user_by_telegram_id(session, user_id)

    if not user or not await SubscriptionService.check_access(
        session, user.telegram_id
    ):
        await render_hub(
            callback.bot,
            callback.message.chat.id,
            texts.ERROR_NO_SUBSCRIPTION,
            _get_no_subscription_keyboard(),
        )
        await callback.answer(show_alert=False)
        return

    await callback.answer(show_alert=False)
    await state.clear()

    servers = await get_available_servers(session)

    if not servers:
        await render_hub(
            callback.bot,
            callback.message.chat.id,
            texts.ERROR_NO_FREE_SLOTS,
            get_back_button("back_to_connections"),
        )
        return

    if len(servers) == 1:
        await state.set_state(DeviceCreationStates.choose_server)
        return await _process_server_selection(callback, state, session, servers[0].id, user)

    builder = InlineKeyboardBuilder()

    for server in servers:
        flag = server.country_flag or texts.RUNTIME_BOT_HANDLERS_CONNECTION_DEVICE_CREATE_ROUTES_L148_1
        builder.button(
            text=texts.UI_BOT_HANDLERS_CONNECTION_DEVICE_CREATE_ROUTES_L150_1.format(value_0=flag, value_1=server.name),
            callback_data=f"select_server:{server.id}",
        )

    builder.button(text=texts.UI_BOT_HANDLERS_CONNECTION_DEVICE_CREATE_ROUTES_L154_1, callback_data="back_to_connections")
    builder.adjust(1)

    await render_hub(
        callback.bot,
        callback.message.chat.id,
        texts.CONNECTION_SELECT_SERVER,
        builder.as_markup(),
    )

    await state.set_state(DeviceCreationStates.choose_server)


@router.callback_query(
    StateFilter(DeviceCreationStates.choose_server),
    F.data.startswith("select_server:"),
)
async def select_server(
    callback: CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
    db_user: User | None = None,
):
    await callback.answer(show_alert=False)
    server_id = parse_callback_id(callback.data, 1)

    if server_id is None:
        await callback.answer(texts.UI_BOT_HANDLERS_CONNECTION_DEVICE_CREATE_ROUTES_L207_1, show_alert=True)
        _creating_devices.pop(callback.from_user.id, None)
        await state.clear()
        return

    user = db_user or await get_user_by_telegram_id(session, callback.from_user.id)
    return await _process_server_selection(callback, state, session, server_id, user)


async def _process_server_selection(
    callback: CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
    server_id: int,
    user: User | None = None,
):
    telegram_user_id = callback.from_user.id

    if not await MaintenanceService.can_user_perform_action(
        session, telegram_user_id
    ):
        await _render_maintenance(
            callback.message, session, back_to="back_to_connections"
        )
        _creating_devices.pop(telegram_user_id, None)
        await state.clear()
        return

    user = user or await get_user_by_telegram_id(session, telegram_user_id)

    if not user or not await SubscriptionService.check_access(
        session, user.telegram_id
    ):
        await render_hub(
            callback.bot,
            callback.message.chat.id,
            texts.ERROR_NO_SUBSCRIPTION,
            _get_no_subscription_keyboard(),
        )
        _creating_devices.pop(telegram_user_id, None)
        await state.clear()
        return

    server = await get_server_by_id(session, server_id)

    if not server:
        await callback.answer(texts.ERROR_LOCATION_NOT_FOUND, show_alert=True)
        _creating_devices.pop(telegram_user_id, None)
        await state.clear()
        return

    if not server.is_active:
        await render_hub(
            callback.bot,
            callback.message.chat.id,
            texts.ERROR_SERVER_DISABLED,
            get_back_button("add_device"),
        )
        _creating_devices.pop(telegram_user_id, None)
        await state.clear()
        return
        
    if telegram_user_id in _creating_devices:
        await render_hub(
            callback.bot,
            callback.message.chat.id,
            texts.DEVICE_CREATE_IN_PROGRESS,
            get_back_button("add_device"),
        )
        return

    _creating_devices[telegram_user_id] = True

    try:
        profiles = await get_user_profiles(session, user.id)
        limit = await _get_effective_device_limit(user, session)
        
        used = set()
        for p in profiles:
            m = re.search(r'#(\d+)$', p.device_name)
            if m:
                used.add(int(m.group(1)))
        
        slot_index = 1
        for i in range(1, limit + 1):
            if i not in used:
                slot_index = i
                break
        else:
            slot_index = max(used) + 1 if used else 1
            
        device_name = f"Устройство #{slot_index}"

        await render_hub(
            callback.bot,
            callback.message.chat.id,
            texts.DEVICE_CREATING,
            get_back_button("add_device"),
            parse_mode="HTML",
        )

        new_profile = None
        try:
            db_user_id = user.id
            await session.commit()
            snapshot = await capture_server_peer_snapshot(server_id)
            new_profile = await DeviceService.create_device(
                session,
                user_id=db_user_id,
                server_id=server_id,
                device_name=device_name,
                snapshot=snapshot,
            )
            # Commit the creation transaction immediately so that background workers
            # claiming api_operations can see the durable create_peer task in PostgreSQL.
            await session.commit()
        except NoActiveSubscription:
            await render_hub(
                callback.bot,
                callback.message.chat.id,
                texts.ERROR_NO_SUBSCRIPTION,
                _get_no_subscription_keyboard(),
            )
            await state.clear()
            return
        except DailyLimitExceeded:
            await render_hub(
                callback.bot,
                callback.message.chat.id,
                texts.ERROR_DEVICE_DAILY_LIMIT,
                get_back_button("back_to_connections"),
                parse_mode="HTML",
            )
            await state.clear()
            return
        except DeviceLimitExceeded:
            await render_hub(
                callback.bot,
                callback.message.chat.id,
                texts.ERROR_DEVICE_LIMIT_UPGRADE.format(limit=limit),
                _get_device_limit_keyboard(),
            )
            await state.clear()
            return
        except DuplicateDeviceName:
            await render_hub(
                callback.bot,
                callback.message.chat.id,
                texts.DEVICE_NAME_DUPLICATE,
                get_back_button("add_device"),
            )
            await state.clear()
            return
        except InvalidConfig:
            await render_hub(
                callback.bot,
                callback.message.chat.id,
                texts.ERROR_API_CREATE_FAILED,
                get_back_button("back_to_connections"),
            )
            await state.clear()
            return
        except DeviceCreationError:
            logger.exception(
                "Device creation failed for user=%s server=%s",
                telegram_user_id,
                server_id,
            )
            await render_hub(
                callback.bot,
                callback.message.chat.id,
                texts.ERROR_TECHNICAL_MESSAGE,
                get_back_button("back_to_connections"),
                parse_mode="HTML",
            )
            await state.clear()
            return
        except ServerUnavailable as e:
            error_msg = str(e)
            error_type = _classify_server_error(error_msg)
            error_text = _get_server_error_text(error_type)

            logger.warning(
                "ServerUnavailable in _process_server_selection: type=%s, msg=%s",
                error_type,
                error_msg,
            )

            await render_hub(
                callback.bot,
                callback.message.chat.id,
                error_text,
                get_back_button("back_to_connections"),
            )
            await state.clear()
            return
        except Exception:
            logger.exception("Unexpected error in _process_server_selection")

            await render_hub(
                callback.bot,
                callback.message.chat.id,
                texts.ERROR_TECHNICAL_MESSAGE,
                get_back_button("back_to_connections"),
                parse_mode="HTML",
            )
            await state.clear()
            return

        await state.clear()
        if new_profile:
            ready_profile = await _await_profile_ready(new_profile.id, timeout_seconds=4.0)
            if ready_profile and ready_profile.provisioning_status == "active":
                from .device_view_routes import render_device_screen
                await render_device_screen(callback.bot, callback.message.chat.id, ready_profile, user, session)
            elif ready_profile and ready_profile.provisioning_status == "create_failed":
                await render_hub(
                    callback.bot,
                    callback.message.chat.id,
                    texts.ERROR_API_CREATE_FAILED,
                    get_back_button("back_to_connections"),
                )
            else:
                # Timeout reached or cleanup pending -> render connections list showing current status
                await _render_connections(callback.message, user, session)
        else:
            await _render_connections(callback.message, user, session)

    finally:
        _creating_devices.pop(telegram_user_id, None)
