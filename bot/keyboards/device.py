from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot import texts


def get_device_keyboard(
    profile_id: int,
    *,
    config_ready: bool = True,
    show_delete: bool = True,
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()

    adjustments = []

    if config_ready:
        builder.button(
            text=texts.CONNECTION_DEVICES_DEVICE_ALT_CONNECTION,
            callback_data=f"alt_connection:{profile_id}",
            style="primary",
        )
        adjustments.append(1)

    builder.button(
        text=texts.DEVICE_STATUS_DELETING_LABEL,
        callback_data=f"rename_device:{profile_id}",
    )
    builder.button(
        text=texts.BTN_HELP_INSTRUCTIONS,
        callback_data=f"support_help:device_{profile_id}",
    )
    adjustments.append(2)

    if show_delete:
        builder.button(
            text=texts.CONNECTION_DEVICES_DEVICE,
            callback_data=f"request_delete_device:{profile_id}",
            style="danger",
        )
        adjustments.append(1)

    builder.button(
        text=texts.DEVICE_DEFAULT_FLAG_ICON,
        callback_data="back_to_connections",
    )
    builder.button(
        text=texts.DEVICE_DEFAULT_SERVER_TITLE,
        callback_data="back_to_main_menu",
    )
    adjustments.append(2)

    builder.adjust(*adjustments)
    return builder.as_markup()


def get_alt_connection_keyboard(
    profile_id: int,
    amnezia_bridge_url: str | None = None,
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()

    if amnezia_bridge_url:
        builder.button(
            text=texts.CONNECTION_DEVICES_DEVICE_OPEN_IN_AMNEZIA,
            url=amnezia_bridge_url,
            style="primary",
        )

    builder.button(
        text=texts.CONNECTION_DEVICES_DEVICE_BACK_TO_DEVICE,
        callback_data=f"manage_device:{profile_id}",
    )
    builder.adjust(1)
    return builder.as_markup()


def get_device_delete_confirm_keyboard(
    profile_id: int,
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()

    builder.button(
        text=texts.DEVICE_STATUS_CREATING_LABEL,
        callback_data=f"confirm_delete_device:{profile_id}",
        style="danger",
    )

    builder.button(
        text=texts.BTN_CANCEL,
        callback_data=f"cancel_delete_device:{profile_id}",
    )

    builder.adjust(2)

    return builder.as_markup()
