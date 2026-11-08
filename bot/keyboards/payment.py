"""Payment and balance keyboards for Just1k 2.0."""

from __future__ import annotations

from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot import texts


def get_balance_keyboard() -> InlineKeyboardMarkup:
    """Build balance screen keyboard strictly matching just1kbotv2.md:

    - Button 'Пополнить'
    - History of topups (real money) | History of operations (ledger)
    - Back button
    """
    builder = InlineKeyboardBuilder()

    builder.button(
        text="➕ Пополнить баланс",
        callback_data="balance_topup",
    )
    builder.button(
        text="💳 История пополнений",
        callback_data="balance_history_topups",
    )
    builder.button(
        text="📜 История операций",
        callback_data="balance_history_ops",
    )
    builder.button(
        text=texts.BTN_MAIN_MENU_NAV,
        callback_data="back_to_main_menu",
    )

    builder.adjust(1, 2, 1)
    return builder.as_markup()


def get_balance_amounts_keyboard(amounts: list[int] | None = None) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    preset = amounts or [100, 250, 500, 1000]
    for amt in preset:
        builder.button(
            text=f"{amt} ₽",
            callback_data=f"balance_create:{amt}",
        )
    builder.button(
        text="Другая сумма",
        callback_data="balance_custom_amount",
    )
    builder.button(
        text=texts.BTN_BACK,
        callback_data="menu_balance",
    )
    builder.adjust(2, 2, 1, 1)
    return builder.as_markup()


def get_balance_history_keyboard(
    page: int = 0,
    total_pages: int = 1,
    back_target: str = "menu_balance",
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()

    nav = []
    if page > 0:
        nav.append(("◀️", f"balance_history_page:{page - 1}"))
    if page < total_pages - 1:
        nav.append(("▶️", f"balance_history_page:{page + 1}"))

    for btn_text, cb in nav:
        builder.button(text=btn_text, callback_data=cb)

    builder.button(
        text=texts.BTN_BACK,
        callback_data=back_target,
    )

    if nav:
        builder.adjust(len(nav), 1)
    else:
        builder.adjust(1)

    return builder.as_markup()
