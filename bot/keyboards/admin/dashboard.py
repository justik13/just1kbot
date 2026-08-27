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
        text=texts.BTN_POLZOVATELI_I_RASSYLKI,
        callback_data="admin_cat_users",
    )
    builder.button(
        text=texts.BTN_SERVERY_I_TARIFY,
        callback_data="admin_cat_infra",
    )

    fin_badge = ""
    if disputes_count > 0 or dead_queues_count > 0:
        fin_badge = f" ⚠️ ({disputes_count + dead_queues_count})"

    builder.button(
        text=texts.UI_DASHBOARD_FINANSY_I_OCHEREDI_28.format(fin_badge=fin_badge),
        callback_data="admin_cat_finance",
    )

    maint_icon = "🔴" if maintenance_enabled else "🟢"
    builder.button(
        text=texts.UI_DASHBOARD_SISTEMA_I_LOGI_34.format(maint_icon=maint_icon),
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
    builder.button(text=texts.BTN_MASSOVYJ_BONUS, callback_data="admin_mass_bonus")
    builder.button(text=texts.BTN_V_ADMIN_MENYU, callback_data="admin_menu")
    builder.adjust(1)
    return builder.as_markup()


def get_admin_cat_infra_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text=texts.BTN_VPN_SERVERY, callback_data="admin_servers")
    builder.button(text=texts.BTN_TARIFY_PODPISOK, callback_data="admin_tariffs")
    builder.button(text=texts.BTN_V_ADMIN_MENYU, callback_data="admin_menu")
    builder.adjust(1)
    return builder.as_markup()


def get_admin_cat_finance_keyboard(
    dead_queues_count: int = 0,
    disputes_count: int = 0,
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text=texts.BTN_ISTORIYA_PLATEZHEJ, callback_data="admin_payments")
    builder.button(text=texts.BTN_ZHURNAL_POKUPOK, callback_data="admin_purchases")
    dispute_label = texts.UI_DASHBOARD_DISPUTY_73.format(disputes_count=disputes_count) if disputes_count > 0 else texts.UI_DASHBOARD_DISPUTY_73
    builder.button(text=dispute_label, callback_data="admin_disputes")
    queue_label = texts.UI_DASHBOARD_OCHEREDI_75.format(dead_queues_count=dead_queues_count) if dead_queues_count > 0 else texts.UI_DASHBOARD_OCHEREDI_ZADACH_75
    builder.button(text=queue_label, callback_data="aq:home")
    builder.button(text=texts.BTN_V_ADMIN_MENYU, callback_data="admin_menu")
    builder.adjust(1)
    return builder.as_markup()


def get_admin_cat_system_keyboard(maintenance_enabled: bool = False) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text=texts.BTN_NASTROJKI_MTPROTO_PROXY, callback_data="admin_settings")
    builder.button(text=texts.BTN_SISTEMNYJ_AUDIT_LOG, callback_data="admin_audit")
    maint_label = texts.UI_DASHBOARD_TEKHRABOTY_VKLYUCHENY_86 if maintenance_enabled else texts.UI_DASHBOARD_TEKHRABOTY_VYKLYUCHENY_86
    builder.button(text=maint_label, callback_data="admin_maintenance")
    builder.button(text=texts.BTN_V_ADMIN_MENYU, callback_data="admin_menu")
    builder.adjust(1)
    return builder.as_markup()


def get_audit_keyboard(page: int = 1, total_pages: int = 1) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    if total_pages > 1:
        if page > 1:
            builder.button(text=texts.BTN_NAZAD, callback_data=f"admin_audit:{page - 1}")
        else:
            builder.button(text=" ⏹ ", callback_data="ignore")
        builder.button(text=texts.UI_DASHBOARD_STR_100.format(page=page, total_pages=total_pages), callback_data="ignore")
        if page < total_pages:
            builder.button(text=texts.BTN_VPERED, callback_data=f"admin_audit:{page + 1}")
        else:
            builder.button(text=" ⏹ ", callback_data="ignore")
        builder.adjust(3, 1)
    else:
        builder.adjust(1)

    builder.button(
        text=texts.BTN_V_ADMIN_MENYU,
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
