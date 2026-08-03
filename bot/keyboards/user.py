from aiogram.types import InlineKeyboardMarkup, CopyTextButton
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot import texts


def get_profile_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()

    builder.button(
        text=texts.BUTTON_BALANCE,
        callback_data="menu_balance",
    )
    builder.button(
        text=texts.BUTTON_INVITE,
        callback_data="referral",
    )
    builder.button(
        text=texts.BUTTON_TOPUP_HISTORY,
        callback_data="user_history",
    )
    builder.button(
        text=texts.BUTTON_MAIN_MENU,
        callback_data="back_to_main_menu",
    )

    builder.adjust(1, 1, 1, 1)

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
