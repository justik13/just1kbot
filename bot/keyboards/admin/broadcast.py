from bot import texts
from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder


def get_broadcast_audience_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()

    builder.button(
        text="📢 Всем пользователям",
        callback_data="broadcast_aud:all",
    )
    builder.button(
        text="🟢 Активным подпискам",
        callback_data="broadcast_aud:active",
    )
    builder.button(
        text="⏳ Истекают < 3 дней",
        callback_data="broadcast_aud:expiring_3d",
    )
    builder.button(
        text="🔴 Истекшим подпискам",
        callback_data="broadcast_aud:expired",
    )
    builder.button(
        text="🆕 Без подписок",
        callback_data="broadcast_aud:never",
    )
    builder.button(
        text="🧪 Тест админу",
        callback_data="broadcast_aud:test",
    )
    builder.button(
        text=texts.UI_BOT_KEYBOARDS_ADMIN_BROADCAST_L19_1,
        callback_data="admin_menu",
    )

    builder.adjust(2, 2, 2, 1)

    return builder.as_markup()


def get_broadcast_launch_keyboard(total_count: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()

    builder.button(
        text=f"🚀 Запустить рассылку ({total_count})",
        callback_data="broadcast_confirm_launch",
    )
    builder.button(
        text="❌ Отмена",
        callback_data="admin_menu",
    )

    builder.adjust(1, 1)

    return builder.as_markup()


def get_broadcast_confirm_keyboard() -> InlineKeyboardMarkup:
    return get_broadcast_audience_keyboard()


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