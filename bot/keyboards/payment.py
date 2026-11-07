from aiogram.types import DisabledButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot import texts


def get_payment_success_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    try:
        from config.settings import get_settings
        miniapp_url = get_settings().MINIAPP_URL or "https://just1k.pro/app"
    except Exception:
        miniapp_url = "https://just1k.pro/app"
    from aiogram.types import WebAppInfo
    builder.button(
        text=texts.BTN_HUB_CONNECT,
        web_app=WebAppInfo(url=miniapp_url),
    )
    builder.button(
        text=texts.BTN_DISMISS_NOTIFICATION,
        callback_data="dismiss_notification",
    )
    builder.adjust(1)
    return builder.as_markup()


def get_balance_keyboard(*, has_visible_topup: bool = False) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    if has_visible_topup:
        builder.button(
            text=texts.BUTTON_RESUME_TOPUP,
            callback_data="balance_resume_topup",
            style="primary",
        )
    else:
        builder.button(
            text=texts.BUTTON_TOPUP,
            callback_data="balance_topup",
            style="success",
        )
    builder.button(
        text=texts.BTN_ISTORIYA_POPOLNENIJ,
        callback_data="user_history",
    )
    builder.button(
        text=texts.BTN_ISTORIYA_OPERATSIJ,
        callback_data="balance_history",
    )
    builder.button(
        text=texts.BTN_MAIN_MENU_NAV, callback_data="back_to_main_menu"
    )
    builder.adjust(1, 2, 1)
    return builder.as_markup()


def get_balance_history_keyboard(

    page: int = 1,
    total_pages: int = 1,
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()

    if total_pages > 1:
        if page > 1:
            builder.button(text=texts.BTN_PAGINATION_PREV, callback_data=f"balance_history:{page - 1}")
        else:
            builder.button(text=texts.BTN_PAGINATION_EMPTY, disabled=DisabledButton())

        builder.button(
            text=texts.BTN_PAGINATION_PAGE.format(page=page, total_pages=total_pages),
            disabled=DisabledButton(),
        )

        if page < total_pages:
            builder.button(text=texts.BTN_PAGINATION_NEXT, callback_data=f"balance_history:{page + 1}")
        else:
            builder.button(text=texts.BTN_PAGINATION_EMPTY, disabled=DisabledButton())

        builder.button(text=texts.BTN_BACK, callback_data="menu_balance")
        builder.adjust(3, 1)
    else:
        builder.button(text=texts.BTN_BACK, callback_data="menu_balance")
        builder.adjust(1)

    return builder.as_markup()



def get_balance_amounts_keyboard(amounts: list[int]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for amount in amounts:
        builder.button(
            text=texts.PAYMENT_PRICE_RUB_FORMAT.format(amount_rub=amount),
            callback_data=f"balance_create:{amount}",
        )
    builder.button(
        text=texts.BUTTON_CUSTOM_AMOUNT,
        callback_data="balance_custom_amount",
    )
    builder.button(text=texts.BTN_BACK, callback_data="menu_balance")
    builder.adjust(2, 2, 2, 1, 1)
    return builder.as_markup()


def get_topup_credit_keyboard(
    context: dict | None = None,
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    context = context or {}
    source = context.get("source")
    if source == "white_internet":
        builder.button(
            text=texts.BTN_WL_RETURN_TO_SERVICE,
            callback_data="white_internet",
        )
    builder.button(text=texts.BTN_PAYMENT_TO_BALANCE, callback_data="menu_balance")
    builder.button(text=texts.BTN_DISMISS, callback_data="dismiss_notification")
    builder.adjust(1)
    return builder.as_markup()


def get_order_invoice_keyboard(
    payment_url: str,
    order_id: str,
    price: int,
    support_url: str | None = None,
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(
        text=texts.BTN_PAYMENT_PAY_ORDER.format(price=price),
        url=payment_url,
        style="success",
    )
    builder.button(
        text=texts.BTN_PAYMENT_CHECK,
        callback_data=f"order_check:{order_id}",
        style="primary",
    )
    if not support_url:
        from utils.support import get_support_url
        support_url = get_support_url()
    builder.button(
        text=texts.BTN_SUPPORT,
        url=support_url,
    )
    builder.button(
        text=texts.BTN_CANCEL_ACTION,
        callback_data=f"order_cancel:{order_id}",
        style="danger",
    )
    builder.adjust(1)
    return builder.as_markup()


def get_dispute_blocked_keyboard(
    back_callback: str = "back_to_main_menu",
    support_url: str | None = None,
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    if support_url:
        builder.button(
            text=texts.BTN_SUPPORT,
            url=support_url,
        )
    builder.button(
        text=texts.BTN_BACK if back_callback != "back_to_main_menu" else texts.BTN_MAIN_MENU_NAV,
        callback_data=back_callback,
    )
    builder.adjust(1)
    return builder.as_markup()
