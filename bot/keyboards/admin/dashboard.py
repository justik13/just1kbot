"""Keyboards for admin dashboard in Just1k 2.0."""

from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot import texts


def get_admin_menu(maintenance_enabled: bool = False) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()

    builder.button(text="📢 Рассылка", callback_data="admin_broadcast")
    builder.button(text="💳 Платежи", callback_data="admin_payments")
    builder.button(text="📜 Покупки", callback_data="admin_purchases")

    maint_icon = "🔴" if maintenance_enabled else "🟢"
    builder.button(
        text=f"{maint_icon} Обслуживание",
        callback_data="admin_maintenance",
    )
    builder.button(
        text=texts.BTN_MAIN_MENU_NAV,
        callback_data="back_to_main_menu",
    )

    builder.adjust(2, 2, 1)
    return builder.as_markup()


def get_admin_cat_finance_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="💳 Платежи ЮKassa", callback_data="admin_payments")
    builder.button(text="📜 Журнал покупок", callback_data="admin_purchases")
    builder.button(text="◀️ Меню админки", callback_data="admin_menu")
    builder.adjust(1)
    return builder.as_markup()


def get_admin_cat_users_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="📢 Рассылка", callback_data="admin_broadcast")
    builder.button(text="◀️ Меню админки", callback_data="admin_menu")
    builder.adjust(1)
    return builder.as_markup()


def get_admin_cat_infra_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="◀️ Меню админки", callback_data="admin_menu")
    builder.adjust(1)
    return builder.as_markup()


def get_admin_cat_std_sub_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="◀️ Меню админки", callback_data="admin_menu")
    builder.adjust(1)
    return builder.as_markup()


def get_admin_cat_wi_sub_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="◀️ Меню админки", callback_data="admin_menu")
    builder.adjust(1)
    return builder.as_markup()


def get_admin_cat_system_keyboard(maintenance_enabled: bool = False) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    maint_label = "🔴 Выключить техобслуживание" if maintenance_enabled else "🟢 Включить техобслуживание"
    builder.button(text=maint_label, callback_data="admin_maintenance")
    builder.button(text="◀️ Меню админки", callback_data="admin_menu")
    builder.adjust(1)
    return builder.as_markup()


def get_audit_keyboard(page: int = 1, total_pages: int = 1) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="◀️ Меню админки", callback_data="admin_menu")
    builder.adjust(1)
    return builder.as_markup()


def get_maintenance_confirm_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="✅ Подтвердить", callback_data="admin_maintenance_toggle_apply")
    builder.button(text="❌ Отмена", callback_data="admin_menu")
    builder.adjust(2)
    return builder.as_markup()
