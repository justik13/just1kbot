import logging

from aiogram import Router, F
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder
from cachetools import TTLCache
from sqlalchemy.ext.asyncio import AsyncSession

from bot import texts
from bot.keyboards import get_back_button
from bot.states import DeviceCreationStates
from database.models import User
from database.repositories.servers_repo import (
    get_available_servers,
    get_server_by_id,
)
from database.repositories.users_repo import get_user_by_telegram_id
from services.device_service import (
    DeviceCreationError,
    DailyLimitExceeded,
    DeviceLimitExceeded,
    DeviceService,
    DuplicateDeviceName,
    InvalidConfig,
    NoActiveSubscription,
    ServerUnavailable,
)
from services.maintenance_service import MaintenanceService
from services.subscription import SubscriptionService
from services.slots_cache import capture_server_peer_snapshot
from utils.callbacks import parse_callback_id
from utils.telegram import render_hub, safe

from .common import (
    DEVICE_NAME_REGEX,
    _get_effective_device_limit,
    _render_maintenance,
)

router = Router()
logger = logging.getLogger(__name__)

_creating_devices: TTLCache[int, bool] = TTLCache(
    maxsize=5000,
    ttl=300,
)


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

    if not await MaintenanceService.can_user_perform_action(
        session, callback.from_user.id
    ):
        await _render_maintenance(
            callback.message, session, back_to="back_to_connections"
        )
        _creating_devices.pop(callback.from_user.id, None)
        await state.clear()
        return

    user = db_user or await get_user_by_telegram_id(session, callback.from_user.id)

    if not user or not await SubscriptionService.check_access(
        session, user.telegram_id
    ):
        await render_hub(
            callback.bot,
            callback.message.chat.id,
            texts.ERROR_NO_SUBSCRIPTION,
            _get_no_subscription_keyboard(),
        )
        _creating_devices.pop(callback.from_user.id, None)
        await state.clear()
        return

    server_id = parse_callback_id(callback.data, 1)

    if server_id is None:
        await callback.answer(texts.UI_BOT_HANDLERS_CONNECTION_DEVICE_CREATE_ROUTES_L207_1, show_alert=True)
        _creating_devices.pop(callback.from_user.id, None)
        await state.clear()
        return

    server = await get_server_by_id(session, server_id)

    if not server:
        await callback.answer(texts.ERROR_LOCATION_NOT_FOUND, show_alert=True)
        _creating_devices.pop(callback.from_user.id, None)
        await state.clear()
        return

    if not server.is_active:
        await render_hub(
            callback.bot,
            callback.message.chat.id,
            texts.ERROR_SERVER_DISABLED,
            get_back_button("add_device"),
        )
        _creating_devices.pop(callback.from_user.id, None)
        await state.clear()
        return

    await state.update_data(server_id=server_id)
    await state.set_state(DeviceCreationStates.enter_device_name)

    flag = server.country_flag or texts.RUNTIME_BOT_HANDLERS_CONNECTION_DEVICE_CREATE_ROUTES_L234_1

    _creating_devices.pop(callback.from_user.id, None)

    await render_hub(
        callback.bot,
        callback.message.chat.id,
        texts.DEVICE_ADD_NAME_PROMPT.format(flag=flag, server_name=safe(server.name)),
        get_back_button("add_device"),
    )


@router.message(DeviceCreationStates.enter_device_name)
async def enter_device_name(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
    db_user: User | None = None,
):
    telegram_user_id = message.from_user.id

    if not await MaintenanceService.can_user_perform_action(session, telegram_user_id):
        await _render_maintenance(message, session, back_to="back_to_connections")
        _creating_devices.pop(telegram_user_id, None)
        await state.clear()
        return

    user = db_user or await get_user_by_telegram_id(session, telegram_user_id)

    if not user or not await SubscriptionService.check_access(
        session, user.telegram_id
    ):
        await render_hub(
            message.bot,
            message.chat.id,
            texts.ERROR_NO_SUBSCRIPTION,
            _get_no_subscription_keyboard(),
        )
        _creating_devices.pop(telegram_user_id, None)
        await state.clear()
        return

    if telegram_user_id in _creating_devices:
        await render_hub(
            message.bot,
            message.chat.id,
            texts.DEVICE_CREATE_IN_PROGRESS,
            get_back_button("add_device"),
        )
        return

    _creating_devices[telegram_user_id] = True

    try:
        if not message.text or message.text.startswith("/"):
            await render_hub(
                message.bot,
                message.chat.id,
                texts.ERROR_TEXT_REQUIRED,
                get_back_button("add_device"),
            )
            return

        device_name = message.text.strip()

        if (
            not device_name
            or len(device_name) > 16
            or not DEVICE_NAME_REGEX.match(device_name)
        ):
            await render_hub(
                message.bot,
                message.chat.id,
                texts.ERROR_INVALID_DEVICE_NAME,
                get_back_button("add_device"),
            )
            return

        data = await state.get_data()
        server_id = data.get("server_id")

        if not server_id:
            await render_hub(
                message.bot,
                message.chat.id,
                texts.ERROR_SERVER_UNAVAILABLE,
                get_back_button("back_to_connections"),
            )
            await state.clear()
            return

        await render_hub(
            message.bot,
            message.chat.id,
            texts.DEVICE_CREATING,
            get_back_button("add_device"),
            parse_mode="HTML",
        )

        try:
            db_user_id = user.id
            await session.commit()
            snapshot = await capture_server_peer_snapshot(server_id)
            await DeviceService.create_device(
                session,
                user_id=db_user_id,
                server_id=server_id,
                device_name=device_name,
                snapshot=snapshot,
            )
        except NoActiveSubscription:
            await render_hub(
                message.bot,
                message.chat.id,
                texts.ERROR_NO_SUBSCRIPTION,
                _get_no_subscription_keyboard(),
            )
            await state.clear()
            return
        except DailyLimitExceeded:
            await render_hub(
                message.bot,
                message.chat.id,
                texts.ERROR_DEVICE_DAILY_LIMIT,
                get_back_button("back_to_connections"),
                parse_mode="HTML",
            )
            await state.clear()
            return
        except DeviceLimitExceeded:
            device_limit = await _get_effective_device_limit(user, session)

            await render_hub(
                message.bot,
                message.chat.id,
                texts.ERROR_DEVICE_LIMIT_UPGRADE.format(limit=device_limit),
                _get_device_limit_keyboard(),
            )
            await state.clear()
            return
        except DuplicateDeviceName:
            await render_hub(
                message.bot,
                message.chat.id,
                texts.DEVICE_NAME_DUPLICATE,
                get_back_button("add_device"),
            )
            await state.clear()
            return
        except InvalidConfig:
            await render_hub(
                message.bot,
                message.chat.id,
                texts.ERROR_API_CREATE_FAILED,
                get_back_button("back_to_connections"),
            )
            await state.clear()
            return
        except DeviceCreationError as e:
            logger.error(
                "Device creation failed for user=%s server=%s: %s",
                telegram_user_id,
                server_id,
                e,
                exc_info=True,
            )
            await render_hub(
                message.bot,
                message.chat.id,
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
                "ServerUnavailable in enter_device_name: type=%s, msg=%s",
                error_type,
                error_msg,
            )

            await render_hub(
                message.bot,
                message.chat.id,
                error_text,
                get_back_button("back_to_connections"),
            )
            await state.clear()
            return
        except Exception as e:
            logger.error(f"Unexpected error in enter_device_name: {e}", exc_info=True)

            await render_hub(
                message.bot,
                message.chat.id,
                texts.ERROR_TECHNICAL_MESSAGE,
                get_back_button("back_to_connections"),
                parse_mode="HTML",
            )
            await state.clear()
            return

        await render_hub(
            message.bot,
            message.chat.id,
            texts.UI_BOT_HANDLERS_CONNECTION_DEVICE_CREATE_ROUTES_L418_1,
            get_back_button("back_to_connections"),
        )

        await state.clear()

    finally:
        _creating_devices.pop(telegram_user_id, None)
