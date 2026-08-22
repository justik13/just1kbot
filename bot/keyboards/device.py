from aiogram.types import CopyTextButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot import texts


def get_device_keyboard(
    profile_id: int,
    raw_config: str | None = None,
    *,
    config_ready: bool = True,
    show_delete: bool = True,
    amnezia_bridge_url: str | None = None,
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()

    adjustments = []

    if config_ready:
        # Telegram Bot API enforces CopyTextButton text length of 1-256 characters
        if raw_config and 1 <= len(raw_config) <= 256:
            builder.button(
                text="📋 Скопировать ключ",
                copy_text=CopyTextButton(text=raw_config),
            )
            adjustments.append(1)

        builder.button(
            text="🔄 Другой способ подключения",
            callback_data=f"alt_connection:{profile_id}",
        )
        adjustments.append(1)

    builder.button(
        text=texts.UI_BOT_KEYBOARDS_DEVICE_L9_1,
        callback_data=f"rename_device:{profile_id}",
    )
    builder.button(
        text="📖 Инструкция и помощь",
        callback_data=f"support_help:device_{profile_id}",
    )
    adjustments.append(2)

    if show_delete:
        builder.button(
            text=texts.UI_BOT_KEYBOARDS_DEVICE_L18_1,
            callback_data=f"request_delete_device:{profile_id}",
        )
        adjustments.append(1)

    builder.button(
        text=texts.UI_BOT_KEYBOARDS_DEVICE_L23_1,
        callback_data="back_to_connections",
    )
    builder.button(
        text=texts.UI_BOT_KEYBOARDS_DEVICE_L28_1,
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
            text="🚀 Открыть в Amnezia",
            url=amnezia_bridge_url,
        )

    builder.button(
        text="« К устройству",
        callback_data=f"manage_device:{profile_id}",
    )
    builder.adjust(1)
    return builder.as_markup()


def get_device_delete_confirm_keyboard(
    profile_id: int,
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()

    builder.button(
        text=texts.UI_BOT_KEYBOARDS_DEVICE_L43_1,
        callback_data=f"confirm_delete_device:{profile_id}",
    )

    builder.button(
        text=texts.UI_BOT_KEYBOARDS_DEVICE_L48_1,
        callback_data=f"cancel_delete_device:{profile_id}",
    )

    builder.adjust(2)

    return builder.as_markup()
