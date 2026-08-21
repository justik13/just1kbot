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
        text=texts.UI_BOT_KEYBOARDS_ADMIN_TARIFFS_L20_1,
        callback_data=f"admin_tariff_edit_rub:{tariff_id}",
    )

    if is_active:
        status_text = texts.RUNTIME_BOT_KEYBOARDS_ADMIN_TARIFFS_L26_1
    else:
        status_text = texts.RUNTIME_BOT_KEYBOARDS_ADMIN_TARIFFS_L28_1
    builder.button(
        text=status_text,
        callback_data=f"admin_tariff_toggle:{tariff_id}",
    )

    builder.button(
        text=texts.UI_BOT_KEYBOARDS_ADMIN_TARIFFS_L34_1,
        callback_data="admin_tariffs",
    )
    builder.adjust(1)
    return builder.as_markup()