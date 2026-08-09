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


def get_broadcast_confirm_keyboard(
    has_button: bool = False,
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()

    builder.button(
        text="🧪 Тест мне (Админу)",
        callback_data="broadcast_send_test",
    )
    builder.button(
        text="✏️ Изменить текст",
        callback_data="broadcast_edit_text",
    )

    btn_url_label = "🔗 Изменить URL-кнопку" if has_button else "🔗 Добавить URL-кнопку"
    builder.button(
        text=btn_url_label,
        callback_data="broadcast_edit_button",
    )

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
        text="❌ Отмена",
        callback_data="admin_menu",
    )

    builder.adjust(2, 1, 2, 2, 1)

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