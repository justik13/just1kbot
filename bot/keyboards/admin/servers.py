from bot import texts
from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder


def get_admin_server_card_keyboard(
    server_id: int,
    is_active: bool,
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()

    builder.button(
        text="⚡ Проверить доступность (Ping)",
        callback_data=f"admin_server_ping:{server_id}",
    )
    builder.button(
        text=texts.UI_BOT_KEYBOARDS_ADMIN_SERVERS_L12_1,
        callback_data=f"admin_server_edit_name:{server_id}",
    )
    builder.button(
        text=texts.UI_BOT_KEYBOARDS_ADMIN_SERVERS_L16_1,
        callback_data=f"admin_server_edit_flag:{server_id}",
    )
    builder.button(
        text=texts.UI_BOT_KEYBOARDS_ADMIN_SERVERS_L20_1,
        callback_data=f"admin_server_edit_url:{server_id}",
    )
    builder.button(
        text=texts.UI_BOT_KEYBOARDS_ADMIN_SERVERS_L24_1,
        callback_data=f"admin_server_edit_key:{server_id}",
    )
    builder.button(
        text=texts.UI_BOT_KEYBOARDS_ADMIN_SERVERS_L28_1,
        callback_data=f"admin_server_edit_max_clients:{server_id}",
    )

    if is_active:
        status_text = texts.RUNTIME_BOT_KEYBOARDS_ADMIN_SERVERS_L34_1
    else:
        status_text = texts.RUNTIME_BOT_KEYBOARDS_ADMIN_SERVERS_L36_1

    builder.button(
        text=status_text,
        callback_data=f"admin_server_toggle:{server_id}",
    )
    builder.button(
        text=texts.UI_BOT_KEYBOARDS_ADMIN_SERVERS_L42_1,
        callback_data=f"admin_server_delete:{server_id}",
    )
    builder.button(
        text=texts.UI_BOT_KEYBOARDS_ADMIN_SERVERS_L46_1,
        callback_data="admin_servers",
    )

    builder.adjust(1)
    return builder.as_markup()



def get_server_delete_confirm_keyboard(
    server_id: int,
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()

    builder.button(
        text=texts.UI_BOT_KEYBOARDS_ADMIN_SERVERS_L60_1,
        callback_data=f"confirm_server_delete:{server_id}",
    )
    builder.button(
        text=texts.UI_BOT_KEYBOARDS_ADMIN_SERVERS_L64_1,
        callback_data=f"admin_server_card:{server_id}",
    )
    builder.button(
        text=texts.UI_BOT_KEYBOARDS_ADMIN_SERVERS_L68_1,
        callback_data="admin_servers",
    )
    builder.button(
        text=texts.UI_BOT_KEYBOARDS_ADMIN_SERVERS_L72_1,
        callback_data="back_to_main_menu",
    )

    builder.adjust(1, 1, 1, 1)
    return builder.as_markup()