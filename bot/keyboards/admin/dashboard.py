from bot import texts
from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder


def get_admin_menu(
    maintenance_enabled: bool = False,
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()

    builder.button(
        text=texts.UI_BOT_KEYBOARDS_ADMIN_DASHBOARD_L11_1,
        callback_data="admin_users",
    )
    builder.button(
        text=texts.UI_BOT_KEYBOARDS_ADMIN_DASHBOARD_L15_1,
        callback_data="admin_broadcast",
    )
    builder.button(
        text=texts.UI_BOT_KEYBOARDS_ADMIN_DASHBOARD_L19_1,
        callback_data="admin_servers",
    )
    builder.button(
        text=texts.UI_BOT_KEYBOARDS_ADMIN_DASHBOARD_L23_1,
        callback_data="admin_tariffs",
    )
    builder.button(
        text=texts.UI_BOT_KEYBOARDS_ADMIN_DASHBOARD_L27_1,
        callback_data="admin_payments",
    )
    builder.button(
        text=texts.UI_BOT_KEYBOARDS_ADMIN_DASHBOARD_L31_1,
        callback_data="admin_disputes",
    )
    builder.button(
        text=texts.UI_BOT_KEYBOARDS_ADMIN_DASHBOARD_L35_1,
        callback_data="aq:home",
    )
    builder.button(
        text=texts.UI_BOT_KEYBOARDS_ADMIN_DASHBOARD_L39_1,
        callback_data="admin_audit",
    )

    if maintenance_enabled:
        builder.button(
            text=texts.UI_BOT_KEYBOARDS_ADMIN_DASHBOARD_L45_1,
            callback_data="admin_maintenance",
        )
    else:
        builder.button(
            text=texts.UI_BOT_KEYBOARDS_ADMIN_DASHBOARD_L50_1,
            callback_data="admin_maintenance",
        )

    builder.button(
        text=texts.UI_BOT_KEYBOARDS_ADMIN_DASHBOARD_L55_1,
        callback_data="back_to_main_menu",
    )

    builder.adjust(2, 2, 2, 2, 1, 1)
    return builder.as_markup()


def get_audit_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(
        text=texts.UI_BOT_KEYBOARDS_ADMIN_DASHBOARD_L66_1,
        callback_data="admin_audit",
    )
    builder.button(
        text=texts.UI_BOT_KEYBOARDS_ADMIN_DASHBOARD_L70_1,
        callback_data="admin_menu",
    )
    builder.adjust(1, 1)
    return builder.as_markup()


def get_maintenance_confirm_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(
        text=texts.UI_BOT_KEYBOARDS_ADMIN_DASHBOARD_L80_1,
        callback_data="admin_maintenance_toggle_apply",
    )
    builder.button(
        text=texts.UI_BOT_KEYBOARDS_ADMIN_DASHBOARD_L84_1,
        callback_data="admin_menu",
    )
    builder.adjust(2)
    return builder.as_markup()
