from bot import texts
from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder


def get_admin_menu(
    maintenance_enabled: bool = False,
    dead_queues_count: int = 0,
    disputes_count: int = 0,
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()

    builder.button(
        text="👥 Пользователи и Рассылки",
        callback_data="admin_cat_users",
    )
    builder.button(
        text="⚙️ Серверы и Тарифы",
        callback_data="admin_cat_infra",
    )

    fin_badge = ""
    if disputes_count > 0 or dead_queues_count > 0:
        fin_badge = f" ⚠️ ({disputes_count + dead_queues_count})"

    builder.button(
        text=f"💰 Финансы и Очереди{fin_badge}",
        callback_data="admin_cat_finance",
    )

    maint_icon = "🔴" if maintenance_enabled else "🟢"
    builder.button(
        text=f"🛠 Система и Логи {maint_icon}",
        callback_data="admin_cat_system",
    )

    builder.button(
        text="🔙 В главное меню бота",
        callback_data="back_to_main_menu",
    )

    builder.adjust(2, 2, 1)
    return builder.as_markup()


def get_admin_cat_users_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="👥 Список пользователей", callback_data="admin_users")
    builder.button(text="📢 Массовая рассылка", callback_data="admin_broadcast")
    builder.button(text="🎁 Массовый бонус", callback_data="admin_mass_bonus")
    builder.button(text="🔙 В админ-меню", callback_data="admin_menu")
    builder.adjust(1)
    return builder.as_markup()


def get_admin_cat_infra_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="🖥 VPN Серверы", callback_data="admin_servers")
    builder.button(text="💎 Тарифы подписок", callback_data="admin_tariffs")
    builder.button(text="🔙 В админ-меню", callback_data="admin_menu")
    builder.adjust(1)
    return builder.as_markup()


def get_admin_cat_finance_keyboard(
    dead_queues_count: int = 0,
    disputes_count: int = 0,
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="💳 История платежей", callback_data="admin_payments")
    builder.button(text="🛒 Журнал покупок", callback_data="admin_purchases")
    dispute_label = f"⚠️ Диспуты ({disputes_count})" if disputes_count > 0 else "⚖️ Диспуты"
    builder.button(text=dispute_label, callback_data="admin_disputes")
    queue_label = f"🚨 Очереди ({dead_queues_count})" if dead_queues_count > 0 else "🔄 Очереди задач"
    builder.button(text=queue_label, callback_data="aq:home")
    builder.button(text="🔙 В админ-меню", callback_data="admin_menu")
    builder.adjust(1)
    return builder.as_markup()


def get_admin_cat_system_keyboard(maintenance_enabled: bool = False) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="🚀 Настройки MTProto Proxy", callback_data="admin_settings")
    builder.button(text="📜 Системный аудит-лог", callback_data="admin_audit")
    maint_label = "🔴 Техработы: ВКЛЮЧЕНЫ" if maintenance_enabled else "🟢 Техработы: ВЫКЛЮЧЕНЫ"
    builder.button(text=maint_label, callback_data="admin_maintenance")
    builder.button(text="🔙 В админ-меню", callback_data="admin_menu")
    builder.adjust(1)
    return builder.as_markup()


def get_audit_keyboard(page: int = 1, total_pages: int = 1) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    if total_pages > 1:
        if page > 1:
            builder.button(text="◀️ Назад", callback_data=f"admin_audit:{page - 1}")
        else:
            builder.button(text=" ⏹ ", callback_data="ignore")
        builder.button(text=f"Стр {page}/{total_pages}", callback_data="ignore")
        if page < total_pages:
            builder.button(text="Вперед ▶️", callback_data=f"admin_audit:{page + 1}")
        else:
            builder.button(text=" ⏹ ", callback_data="ignore")
        builder.adjust(3, 1)
    else:
        builder.adjust(1)

    builder.button(
        text="🔙 В админ-меню",
        callback_data="admin_menu",
    )
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
