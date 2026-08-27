from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot import texts


def get_admin_menu(
    maintenance_enabled: bool = False,
    dead_queues_count: int = 0,
    disputes_count: int = 0,
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()

    builder.button(
        text=texts.BTN_USERS_AND_BROADCAST,
        callback_data="admin_cat_users",
    )
    builder.button(
        text=texts.BTN_SERVERS_AND_TARIFFS,
        callback_data="admin_cat_infra",
    )

    fin_badge = ""
    if disputes_count > 0 or dead_queues_count > 0:
        fin_badge = texts.ADMIN_DASHBOARD_FINANCE_BADGE.format(count=disputes_count + dead_queues_count)

    builder.button(
        text=texts.ADMIN_DASHBOARD_SECTION_FINANCES_QUEUES.format(fin_badge=fin_badge),
        callback_data="admin_cat_finance",
    )

    maint_icon = "🔴" if maintenance_enabled else "🟢"
    builder.button(
        text=texts.DASHBOARD_SISTEMA_I_LOGI.format(maint_icon=maint_icon),
        callback_data="admin_cat_system",
    )

    builder.button(
        text=texts.BTN_V_GLAVNOE_MENYU_BOTA,
        callback_data="back_to_main_menu",
    )

    builder.adjust(2, 2, 1)
    return builder.as_markup()


def get_admin_cat_users_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text=texts.BTN_SPISOK_POLZOVATELEJ, callback_data="admin_users")
    builder.button(text=texts.BTN_MASSOVAYA_RASSYLKA, callback_data="admin_broadcast")
    builder.button(text=texts.BTN_MASS_BONUS, callback_data="admin_mass_bonus")
    builder.button(text=texts.BTN_ADMIN_MENU, callback_data="admin_menu")
    builder.adjust(1)
    return builder.as_markup()


def get_admin_cat_infra_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text=texts.BTN_SERVERS, callback_data="admin_servers")
    builder.button(text=texts.BTN_TARIFY_PODPISOK, callback_data="admin_tariffs")
    builder.button(text=texts.BTN_ADMIN_MENU, callback_data="admin_menu")
    builder.adjust(1)
    return builder.as_markup()


def get_admin_cat_finance_keyboard(
    dead_queues_count: int = 0,
    disputes_count: int = 0,
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text=texts.BTN_ISTORIYA_PLATEZHEJ, callback_data="admin_payments")
    builder.button(text=texts.BTN_ZHURNAL_POKUPOK, callback_data="admin_purchases")
    dispute_label = texts.DASHBOARD_DISPUTY_COUNT.format(disputes_count=disputes_count) if disputes_count > 0 else texts.DASHBOARD_DISPUTY
    builder.button(text=dispute_label, callback_data="admin_disputes")
    queue_label = texts.DASHBOARD_OCHEREDI.format(dead_queues_count=dead_queues_count) if dead_queues_count > 0 else texts.DASHBOARD_OCHEREDI_TASKS
    builder.button(text=queue_label, callback_data="aq:home")
    builder.button(text=texts.BTN_ADMIN_MENU, callback_data="admin_menu")
    builder.adjust(1)
    return builder.as_markup()


def get_admin_cat_system_keyboard(maintenance_enabled: bool = False) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text=texts.BTN_NASTROJKI_MTPROTO_PROXY, callback_data="admin_settings")
    builder.button(text=texts.BTN_SISTEMNYJ_AUDIT_LOG, callback_data="admin_audit")
    maint_label = texts.DASHBOARD_TEKHRABOTY_VKLYUCHENY if maintenance_enabled else texts.DASHBOARD_TEKHRABOTY_VYKLYUCHENY
    builder.button(text=maint_label, callback_data="admin_maintenance")
    builder.button(text=texts.BTN_ADMIN_MENU, callback_data="admin_menu")
    builder.adjust(1)
    return builder.as_markup()


def get_audit_keyboard(page: int = 1, total_pages: int = 1) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    if total_pages > 1:
        if page > 1:
            builder.button(text=texts.BTN_BACK, callback_data=f"admin_audit:{page - 1}")
        else:
            builder.button(text=" ⏹ ", callback_data="ignore")
        builder.button(text=texts.PAGE_INDEX_FORMAT.format(page=page, total_pages=total_pages), callback_data="ignore")
        if page < total_pages:
            builder.button(text=texts.BTN_PAGINATION_NEXT, callback_data=f"admin_audit:{page + 1}")
        else:
            builder.button(text=" ⏹ ", callback_data="ignore")
        builder.adjust(3, 1)
    else:
        builder.adjust(1)

    builder.button(
        text=texts.BTN_ADMIN_MENU,
        callback_data="admin_menu",
    )
    return builder.as_markup()


def get_maintenance_confirm_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(
        text=texts.BTN_CONFIRM,
        callback_data="admin_maintenance_toggle_apply",
    )
    builder.button(
        text=texts.BTN_CANCEL,
        callback_data="admin_menu",
    )
    builder.adjust(2)
    return builder.as_markup()
