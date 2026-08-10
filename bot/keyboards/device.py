from bot import texts
from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder


def get_device_keyboard(profile_id: int, *, config_ready: bool = True) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()

    builder.button(
        text=texts.UI_BOT_KEYBOARDS_DEVICE_L9_1,
        callback_data=f"rename_device:{profile_id}",
    )

    if config_ready:
        builder.button(text=texts.UI_BOT_KEYBOARDS_DEVICE_L14_1, callback_data=f"show_config:{profile_id}")
        builder.button(text=texts.UI_BOT_KEYBOARDS_DEVICE_L15_1, callback_data=f"download_conf:{profile_id}")

    builder.button(
        text="📖 Инструкция и помощь",
        callback_data="support_help",
    )

    builder.button(
        text=texts.UI_BOT_KEYBOARDS_DEVICE_L18_1,
        callback_data=f"request_delete_device:{profile_id}",
    )

    builder.button(
        text=texts.UI_BOT_KEYBOARDS_DEVICE_L23_1,
        callback_data="back_to_connections",
    )

    builder.button(
        text=texts.UI_BOT_KEYBOARDS_DEVICE_L28_1,
        callback_data="back_to_main_menu",
    )

    builder.adjust(1, 1, 1, 1, 1, 2)

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
