from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder


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
        text="✏️ Изменить цену ₽",
        callback_data=f"admin_tariff_edit_rub:{tariff_id}",
    )

    if is_active:
        status_text = "🔴 Выключить"
    else:
        status_text = "🟢 Включить"
    builder.button(
        text=status_text,
        callback_data=f"admin_tariff_toggle:{tariff_id}",
    )

    builder.button(
        text="← К списку тарифов",
        callback_data="admin_tariffs",
    )
    builder.adjust(1)
    return builder.as_markup()