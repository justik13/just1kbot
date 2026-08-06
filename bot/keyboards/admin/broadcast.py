from bot import texts
from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder


def get_broadcast_confirm_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()

    builder.button(
        text="📢 Всем пользователям",
        callback_data="broadcast_send_all",
    )
    builder.button(
        text="🟢 Активным подпискам",
        callback_data="broadcast_send_active",
    )
    builder.button(
        text="🔴 Истекшим подпискам",
        callback_data="broadcast_send_expired",
    )
    builder.button(
        text="🆕 Без подписок",
        callback_data="broadcast_send_never",
    )
    builder.button(
        text="🧪 Тест мне (Админу)",
        callback_data="broadcast_send_test",
    )
    builder.button(
        text=texts.UI_BOT_KEYBOARDS_ADMIN_BROADCAST_L19_1,
        callback_data="admin_menu",
    )

    builder.adjust(2, 2, 1, 1)

    return builder.as_markup()



def get_broadcast_result_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()

    builder.button(
        text=texts.UI_BOT_KEYBOARDS_ADMIN_BROADCAST_L32_1,
        callback_data="broadcast_dismiss",
    )

    builder.adjust(1)

    return builder.as_markup()


def get_broadcast_close_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()

    builder.button(
        text=texts.UI_BOT_KEYBOARDS_ADMIN_BROADCAST_L45_1,
        callback_data="dismiss_broadcast",
    )

    builder.adjust(1)

    return builder.as_markup()