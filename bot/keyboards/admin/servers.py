from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot import texts


def get_admin_server_card_keyboard(
    server_id: int,
    is_active: bool,
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()

    builder.button(
        text=texts.BTN_ADMIN_SERVER_PING,
        callback_data=f"admin_server_ping:{server_id}",
    )
    builder.button(
        text=texts.ADMIN_SERVER_STATUS_PROBLEM_BADGE,
        callback_data=f"admin_server_edit_name:{server_id}",
    )
    builder.button(
        text=texts.ADMIN_SERVER_STATUS_OFFLINE_BADGE,
        callback_data=f"admin_server_edit_flag:{server_id}",
    )
    builder.button(
        text=texts.ADMIN_SERVER_STATUS_DISABLED_BADGE,
        callback_data=f"admin_server_edit_url:{server_id}",
    )
    builder.button(
        text=texts.ADMIN_SERVER_BTN_PING,
        callback_data=f"admin_server_edit_key:{server_id}",
    )
    builder.button(
        text=texts.ADMIN_SERVER_BTN_EDIT,
        callback_data=f"admin_server_edit_max_clients:{server_id}",
    )

    if is_active:
        status_text = texts.ADMIN_SERVERS
    else:
        status_text = texts.ADMIN_SERVER_STATUS_ONLINE_BADGE

    builder.button(
        text=status_text,
        callback_data=f"admin_server_toggle:{server_id}",
    )
    builder.button(
        text=texts.ADMIN_SERVER_BTN_DELETE,
        callback_data=f"admin_server_delete:{server_id}",
    )
    builder.button(
        text=texts.ADMIN_SERVER_EDIT_IP_PROMPT,
        callback_data="admin_servers",
    )

    builder.adjust(1)
    return builder.as_markup()



def get_server_delete_confirm_keyboard(
    server_id: int,
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()

    builder.button(
        text=texts.ADMIN_SERVER_EDIT_PORT_PROMPT,
        callback_data=f"confirm_server_delete:{server_id}",
    )
    builder.button(
        text=texts.BTN_CANCEL,
        callback_data=f"admin_server_card:{server_id}",
    )
    builder.button(
        text=texts.ADMIN_SERVER_EDIT_LIMIT_PROMPT,
        callback_data="admin_servers",
    )
    builder.button(
        text=texts.ADMIN_SERVER_EDIT_SUCCESS,
        callback_data="back_to_main_menu",
    )

    builder.adjust(1, 1, 1, 1)
    return builder.as_markup()