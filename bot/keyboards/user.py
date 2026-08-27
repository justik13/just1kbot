from urllib.parse import quote

from aiogram.types import CopyTextButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot import texts


def get_history_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()

    builder.button(
        text=texts.BUTTON_MAIN_MENU,
        callback_data="back_to_main_menu",
    )

    builder.adjust(1)

    return builder.as_markup()


def get_referral_keyboard(
    referral_link: str,
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()

    if referral_link and 1 <= len(referral_link) <= 256:
        builder.button(
            text=texts.BUTTON_COPY_REFERRAL,
            copy_text=CopyTextButton(text=referral_link),
        )
    share_text = getattr(texts, "REFERRAL_SHARE_TEXT", texts.UI_U_PRIGLASHAYU_V_JUST1KBOT_PRI_PE_32)
    share_url = f"https://t.me/share/url?url={quote(referral_link, safe='')}&text={quote(share_text, safe='')}"
    builder.button(
        text=texts.BTN_SHARE_REFERRAL,
        url=share_url,
    )
    builder.button(
        text=texts.BUTTON_REFERRAL_LIST,
        callback_data="referrals_list",
    )
    builder.button(
        text=texts.BUTTON_MAIN_MENU,
        callback_data="back_to_main_menu",
    )

    builder.adjust(1, 1, 1, 1)

    return builder.as_markup()


def get_referrals_list_keyboard(
    page: int = 1,
    total_pages: int = 1,
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()

    if total_pages > 1:
        if page > 1:
            builder.button(text=texts.BTN_PAGINATION_PREV, callback_data=f"referrals_list:{page - 1}")
        else:
            builder.button(text=" ", callback_data="ignore")

        builder.button(text=f"📄 {page}/{total_pages}", callback_data="ignore")

        if page < total_pages:
            builder.button(text=texts.BTN_PAGINATION_NEXT, callback_data=f"referrals_list:{page + 1}")
        else:
            builder.button(text=" ", callback_data="ignore")

        builder.button(text=texts.BUTTON_BACK, callback_data="referral")
        builder.adjust(3, 1)
    else:
        builder.button(text=texts.BUTTON_BACK, callback_data="referral")
        builder.adjust(1)

    return builder.as_markup()
