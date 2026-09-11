from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot import texts


def get_hub_keyboard(
    is_admin: bool = False,
    is_active: bool = False,
    is_wi_active: bool = False,
    mtproto_url: str | None = None,
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()

    if is_active:
        builder.button(
            text=texts.BTN_MY_SUBSCRIPTION,
            callback_data="menu_subscription",
            style="success",
        )
    elif is_wi_active:
        builder.button(
            text=texts.BTN_WHITE_INTERNET,
            callback_data="white_internet",
            style="success",
        )
    else:
        builder.button(
            text=texts.BTN_BUY_ACCESS,
            callback_data="menu_buy",
            style="success",
        )

    from utils.telegram import equalize_buttons_braille

    conn_text, bal_text = equalize_buttons_braille(texts.BTN_CONNECTIONS, texts.BTN_BALANCE)
    invite_text, supp_text = equalize_buttons_braille(texts.BTN_INVITE_FRIEND, texts.BTN_SUPPORT)

    builder.button(
        text=conn_text,
        callback_data="menu_connections",
    )

    builder.button(
        text=bal_text,
        callback_data="menu_balance",
    )

    builder.button(
        text=invite_text,
        callback_data="menu_referral",
    )

    builder.button(
        text=supp_text,
        callback_data="menu_support",
    )

    if mtproto_url:
        builder.button(
            text=texts.BTN_MTPROTO_PROXY,
            url=mtproto_url,
        )

    show_wi = not (is_wi_active and not is_active)
    if show_wi:
        builder.button(
            text=texts.BTN_WHITE_INTERNET,
            callback_data="white_internet",
        )

    if is_admin:
        builder.button(
            text=texts.BTN_ADMIN,
            callback_data="menu_admin",
        )

    sizes = [1, 2, 2]
    if mtproto_url:
        sizes.append(1)
    if show_wi:
        sizes.append(1)
    if is_admin:
        sizes.append(1)

    builder.adjust(*sizes)

    return builder.as_markup()





def get_back_button(
    callback_data: str = "back_to_main_menu",
    text: str | None = None,
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()

    if text is not None:
        button_text = text
    elif callback_data == "back_to_main_menu":
        button_text = texts.BTN_MAIN_MENU_NAV
    else:
        button_text = texts.BTN_BACK

    builder.button(text=button_text, callback_data=callback_data)

    return builder.as_markup()
