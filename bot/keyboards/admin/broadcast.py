"""Keyboards for admin broadcast."""

from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot import texts
from utils.text_limits import truncate_button_text


def get_broadcast_audience_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()

    builder.button(text="👥 Всем", callback_data="broadcast_aud:all")
    builder.button(text="🟢 С активным доступом", callback_data="broadcast_aud:active")
    builder.button(text="🔴 С истекшим доступом", callback_data="broadcast_aud:expired")
    builder.button(text="🧪 Тест мне", callback_data="broadcast_aud:test")
    builder.button(text=texts.BTN_BACK, callback_data="admin_menu")

    builder.adjust(2, 2, 1)
    return builder.as_markup()


def get_broadcast_server_selection_keyboard(servers) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for s in servers:
        flag = s.country_flag or "🌐"
        builder.button(
            text=truncate_button_text(f"{flag} {s.name}"),
            callback_data=f"broadcast_aud:server_{s.id}",
        )
    builder.button(text=texts.BTN_BACK, callback_data="admin_broadcast")
    builder.adjust(1)
    return builder.as_markup()


def get_broadcast_launch_keyboard(total_count: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(
        text=f"🚀 Запустить рассылку ({total_count})",
        callback_data="broadcast_confirm_launch",
    )
    builder.button(
        text="Отмена",
        callback_data="admin_menu",
    )
    builder.adjust(1, 1)
    return builder.as_markup()


def get_broadcast_result_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="Закрыть", callback_data="broadcast_dismiss")
    builder.adjust(1)
    return builder.as_markup()


def get_broadcast_close_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text=texts.BTN_DISMISS_NOTIFICATION, callback_data="dismiss_broadcast")
    builder.adjust(1)
    return builder.as_markup()
