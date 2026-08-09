from bot import texts
from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder


def get_hub_keyboard(
    is_admin: bool = False,
    is_active: bool = False,
    mtproto_url: str | None = None,
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

    if mtproto_url:
        builder.button(
            text="🚀 MTProto Proxy",
            url=mtproto_url,
        )

    if is_admin:
        builder.button(
            text=texts.UI_BOT_KEYBOARDS_COMMON_L44_1,
            callback_data="menu_admin",
        )

    sizes = [1, 2, 2]
    if mtproto_url:
        sizes.append(1)
    if is_admin:
        sizes.append(1)

    builder.adjust(*sizes)


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
