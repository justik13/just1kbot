from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot import texts
from config.constants import XRAY_PROTOCOL
from config.enums import ServerLifecycleStatus


def get_broadcast_audience_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()

    builder.button(
        text=texts.BTN_BROADCAST_ALL,
        callback_data="broadcast_aud:all",
    )
    builder.button(
        text=texts.BTN_BROADCAST_ACTIVE,
        callback_data="broadcast_aud:active",
    )
    builder.button(
        text=texts.BTN_BROADCAST_EXPIRING,
        callback_data="broadcast_aud:expiring_3d",
    )
    builder.button(
        text=texts.BTN_BROADCAST_EXPIRED,
        callback_data="broadcast_aud:expired",
    )
    builder.button(
        text=texts.BTN_BROADCAST_NO_SUB,
        callback_data="broadcast_aud:never",
    )
    builder.button(
        text=texts.BROADCAST_AUDIENCE_BTN_SERVER,
        callback_data="broadcast_aud_select_server",
    )
    builder.button(
        text=texts.BTN_BROADCAST_TEST_ADMIN,
        callback_data="broadcast_aud:test",
    )
    builder.button(
        text=texts.BTN_CANCEL,
        callback_data="admin_menu",
    )

    builder.adjust(2, 2, 2, 2)

    return builder.as_markup()


def get_broadcast_server_selection_keyboard(servers) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()

    for s in servers:
        flag = s.country_flag or "🌐"
        is_server_active = (
            getattr(s, "is_active", True)
            and getattr(s, "lifecycle_status", ServerLifecycleStatus.ACTIVE) == ServerLifecycleStatus.ACTIVE
        )
        is_xray = (
            getattr(s, "protocol", None) == XRAY_PROTOCOL
            or "xray_origin" in (getattr(s, "capabilities", None) or [])
        )
        proto = "[Xray]" if is_xray else "[AWG]"
        status = "" if is_server_active else texts.BROADCAST_SERVER_STATUS_DISABLED
        builder.button(
            text=f"{flag} {proto} {s.name}{status}",
            callback_data=f"broadcast_aud:server_{s.id}",
        )

    builder.button(
        text=texts.BTN_BACK,
        callback_data="admin_broadcast",
    )

    builder.adjust(1)

    return builder.as_markup()




def get_broadcast_launch_keyboard(total_count: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()

    builder.button(
        text=texts.BTN_BROADCAST_START.format(count=total_count),
        callback_data="broadcast_confirm_launch",
    )
    builder.button(
        text=texts.BTN_CANCEL,
        callback_data="admin_menu",
    )

    builder.adjust(1, 1)

    return builder.as_markup()


def get_broadcast_result_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()

    builder.button(
        text=texts.BROADCAST_BTN_DISMISS,
        callback_data="broadcast_dismiss",
    )

    builder.adjust(1)

    return builder.as_markup()


def get_broadcast_close_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()

    builder.button(
        text=texts.BTN_DISMISS_NOTIFICATION,
        callback_data="dismiss_broadcast",
    )

    builder.adjust(1)

    return builder.as_markup()