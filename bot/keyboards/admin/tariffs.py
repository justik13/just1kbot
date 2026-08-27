from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot import texts


def get_admin_tariff_card_keyboard(
    tariff_id: int,
    is_active: bool,
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()

    # ──────────────────────────────────────────────────
    # ИСПРАВЛЕНО: убраны кнопки «Изменить дни»,
    # «Изменить лимит устр.», «Удалить тариф».
    #
    # Тарифы захардкожены. В админке доступно только:
    #   • изменение цены;
    #   • вкл / выкл (видимость на витрине).
    # ──────────────────────────────────────────────────
    builder.button(
        text=texts.ADMIN_TARIFF_CREATE_SUCCESS,
        callback_data=f"admin_tariff_edit_rub:{tariff_id}",
    )

    if is_active:
        status_text = texts.ADMIN_TARIFF_EDIT_PRICE_PROMPT
    else:
        status_text = texts.ADMIN_TARIFF_EDIT_DEVICES_PROMPT
    builder.button(
        text=status_text,
        callback_data=f"admin_tariff_toggle:{tariff_id}",
    )

    builder.button(
        text=texts.ADMIN_BTN_BACK_TO_TARIFFS,
        callback_data="admin_tariffs",
    )
    builder.adjust(1)
    return builder.as_markup()