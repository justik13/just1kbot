from aiogram.types import InlineKeyboardMarkup, CopyTextButton
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot import texts


def get_profile_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()

    builder.button(
        text="💳 Пополнить баланс",
        callback_data="menu_balance",
    )
    builder.button(
        text="🎁 Пригласить друга (+10%)",
        callback_data="referral",
    )
    builder.button(
        text=texts.BUTTON_MAIN_MENU,
        callback_data="back_to_main_menu",
    )

    builder.adjust(1)

    return builder.as_markup()


def get_history_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()

    builder.button(
        text=texts.BUTTON_PROFILE,
        callback_data="back_to_profile",
    )
    builder.button(
        text=texts.BUTTON_MAIN_MENU,
        callback_data="back_to_main_menu",
    )

    builder.adjust(2)

    return builder.as_markup()


def get_referral_keyboard(
    referral_link: str,
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()

    builder.button(
        text=texts.BUTTON_COPY_REFERRAL,
        copy_text=CopyTextButton(text=referral_link),
    )
    builder.button(
        text=texts.BUTTON_REFERRAL_LIST,
        callback_data="referrals_list",
    )
    builder.button(
        text=texts.BUTTON_PROFILE,
        callback_data="back_to_profile",
    )
    builder.button(
        text=texts.BUTTON_MAIN_MENU,
        callback_data="back_to_main_menu",
    )

    builder.adjust(1, 1, 2)

    return builder.as_markup()


def get_referrals_list_keyboard(
    page: int = 1,
    total_pages: int = 1,
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()

    if total_pages > 1:
        if page > 1:
            builder.button(text="◀️ Назад", callback_data=f"referrals_list:{page - 1}")
        else:
            builder.button(text=" ", callback_data="ignore")

        builder.button(text=f"📄 {page}/{total_pages}", callback_data="ignore")

        if page < total_pages:
            builder.button(text="Вперед ▶️", callback_data=f"referrals_list:{page + 1}")
        else:
            builder.button(text=" ", callback_data="ignore")

        builder.button(text=texts.BUTTON_BACK, callback_data="referral")
        builder.adjust(3, 1)
    else:
        builder.button(text=texts.BUTTON_BACK, callback_data="referral")
        builder.adjust(1)

    return builder.as_markup()

