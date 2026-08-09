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
        text="👥 Пользователи",
        callback_data="admin_users",
    )
    builder.button(
        text="📢 Рассылка",
        callback_data="admin_broadcast",
    )
    builder.button(
        text="🖥 Серверы",
        callback_data="admin_servers",
    )
    builder.button(
        text="⚙️ Тарифы",
        callback_data="admin_tariffs",
    )
    builder.button(
        text="💳 Платежи",
        callback_data="admin_payments",
    )

    dispute_label = f"⚠️ Диспуты ({disputes_count})" if disputes_count > 0 else "⚖️ Диспуты"
    builder.button(
        text=dispute_label,
        callback_data="admin_disputes",
    )

    queue_label = f"🚨 Очереди ({dead_queues_count})" if dead_queues_count > 0 else "🔄 Очереди"
    builder.button(
        text=queue_label,
        callback_data="aq:home",
    )

    builder.button(
        text="🎁 Массовый бонус",
        callback_data="admin_mass_bonus",
    )
    builder.button(
        text="📜 Логи аудита",
        callback_data="admin_audit",
    )
    builder.button(
        text="⚙️ Настройки (MTProto)",
        callback_data="admin_settings",
    )

    if maintenance_enabled:
        builder.button(
            text="🔴 Техработы: Вкл",
            callback_data="admin_maintenance",
        )
    else:
        builder.button(
            text="🟢 Техработы: Выкл",
            callback_data="admin_maintenance",
        )

    builder.button(
        text="🔙 В главное меню бота",
        callback_data="back_to_main_menu",
    )

    builder.adjust(2, 2, 2, 2, 2, 1, 1)
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
