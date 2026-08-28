from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot import texts


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
        text=texts.BTN_BROADCAST_TEST_ADMIN,
        callback_data="broadcast_aud:test",
    )
    builder.button(
        text=texts.BTN_CANCEL,
        callback_data="admin_menu",
    )

    builder.adjust(2, 2, 2, 1)

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