from bot import texts
from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder


def get_hub_keyboard(
    is_admin: bool = False,
    is_active: bool = False,
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()

    if is_active:
        builder.button(
            text=texts.UI_BOT_KEYBOARDS_COMMON_L13_1,
            callback_data="menu_subscription",
        )
    else:
        builder.button(
            text=texts.UI_BOT_KEYBOARDS_COMMON_L18_1,
            callback_data="menu_buy",
        )

    builder.button(
        text=texts.UI_BOT_KEYBOARDS_COMMON_L23_1,
        callback_data="menu_connections",
    )

    builder.button(
        text=texts.UI_BOT_KEYBOARDS_COMMON_L28_1,
        callback_data="menu_balance",
    )

    builder.button(
        text=texts.UI_BOT_KEYBOARDS_COMMON_L33_1,
        callback_data="menu_profile",
    )

    builder.button(
        text=texts.UI_BOT_KEYBOARDS_COMMON_L38_1,
        callback_data="menu_support",
    )

    if is_admin:
        builder.button(
            text=texts.UI_BOT_KEYBOARDS_COMMON_L44_1,
            callback_data="menu_admin",
        )

    if is_admin:
        builder.adjust(1, 2, 2)
    else:
        builder.adjust(1, 2, 1)

    return builder.as_markup()


def get_back_button(
    callback_data: str = "back_to_main_menu",
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()

    if callback_data == "back_to_main_menu":
        text = texts.RUNTIME_BOT_KEYBOARDS_COMMON_L63_1
    else:
        text = texts.RUNTIME_BOT_KEYBOARDS_COMMON_L65_1

    builder.button(text=text, callback_data=callback_data)

    return builder.as_markup()
