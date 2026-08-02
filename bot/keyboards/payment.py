from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot import texts

from utils.tariff_names import get_tariff_group_name


def get_tariff_showcase_keyboard(
    grouped_tariffs: dict,
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for limit in sorted(grouped_tariffs.keys()):
        group_name = get_tariff_group_name(limit)
        builder.button(
            text=group_name,
            callback_data=f"select_tariff_type:{limit}:showcase",
        )
    builder.button(
        text=texts.BUTTON_MAIN_MENU, callback_data="back_to_main_menu"
    )
    builder.adjust(1)
    return builder.as_markup()


def get_tariff_duration_keyboard(
    tariffs: list,
    *,
    source: str = "showcase",
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    tariffs_sorted = sorted(tariffs, key=lambda t: t.duration_days)
    for t in tariffs_sorted:
        text = texts.RUNTIME_BOT_KEYBOARDS_PAYMENT_L34_1.format(value_0=t.duration_days, value_1=t.price_rub)
        if t.duration_days >= 90:
            text += texts.RUNTIME_BOT_KEYBOARDS_PAYMENT_L36_1
        elif t.duration_days >= 30:
            text += texts.RUNTIME_BOT_KEYBOARDS_PAYMENT_L38_1
        builder.button(
            text=text, callback_data=f"select_tariff:{t.id}:{source}"
        )
    if source == "change":
        builder.button(
            text=texts.BUTTON_BACK, callback_data="payment_change_tariff"
        )
    elif source == "renew":
        builder.button(
            text=texts.BUTTON_BACK, callback_data="menu_subscription"
        )
    else:
        builder.button(
            text=texts.UI_BOT_KEYBOARDS_PAYMENT_L52_1, callback_data="payment_showcase"
        )
    builder.adjust(1)
    return builder.as_markup()


def get_renew_keyboard(tariffs: list) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    tariffs_sorted = sorted(tariffs, key=lambda t: t.duration_days)
    for t in tariffs_sorted:
        text = texts.RUNTIME_BOT_KEYBOARDS_PAYMENT_L62_1.format(value_0=t.duration_days, value_1=t.price_rub)
        if t.duration_days >= 90:
            text += texts.RUNTIME_BOT_KEYBOARDS_PAYMENT_L64_1
        elif t.duration_days >= 30:
            text += texts.RUNTIME_BOT_KEYBOARDS_PAYMENT_L66_1
        builder.button(
            text=text, callback_data=f"select_tariff:{t.id}:renew"
        )
    builder.button(text=texts.BUTTON_BACK, callback_data="menu_subscription")
    builder.adjust(1)
    return builder.as_markup()


def get_change_tariff_keyboard(
    tariffs: list,
    current_limit: int,
    *,
    is_subscription_active: bool = False,
    current_tariff_id: int | None = None,
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    grouped: dict[int, list] = {}
    for t in tariffs:
        if t.id == current_tariff_id:
            continue
        limit = getattr(t, "device_limit", 2)
        if limit not in grouped:
            grouped[limit] = []
        grouped[limit].append(t)
    for limit in sorted(grouped.keys()):
        group_name = get_tariff_group_name(limit)
        if is_subscription_active and limit < current_limit:
            group_name += texts.RUNTIME_BOT_KEYBOARDS_PAYMENT_L94_1
        elif limit == current_limit:
            group_name += texts.RUNTIME_BOT_KEYBOARDS_PAYMENT_L96_1
        elif limit > current_limit:
            group_name += texts.RUNTIME_BOT_KEYBOARDS_PAYMENT_L98_1
        builder.button(
            text=group_name,
            callback_data=f"select_tariff_type:{limit}:change",
        )
    builder.button(
        text=texts.BUTTON_BACK, callback_data="back_to_main_menu"
    )
    builder.adjust(1)
    return builder.as_markup()


def get_payment_success_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(
        text=texts.UI_BOT_KEYBOARDS_PAYMENT_L113_1, callback_data="menu_connections"
    )
    builder.button(
        text=texts.UI_BOT_KEYBOARDS_PAYMENT_L116_1, callback_data="menu_subscription"
    )
    builder.button(
        text=texts.BUTTON_MAIN_MENU, callback_data="back_to_main_menu"
    )
    builder.adjust(1, 1, 1)
    return builder.as_markup()


def get_balance_keyboard(*, has_visible_topup: bool = False) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    if has_visible_topup:
        builder.button(
            text=texts.BUTTON_RESUME_TOPUP,
            callback_data="balance_resume_topup",
        )
    else:
        builder.button(
            text=texts.BUTTON_TOPUP,
            callback_data="balance_topup",
        )
    builder.button(
        text=texts.BUTTON_BALANCE_HISTORY,
        callback_data="balance_history",
    )
    builder.button(text=texts.BUTTON_BACK, callback_data="back_to_main_menu")
    builder.adjust(1)
    return builder.as_markup()


def get_balance_amounts_keyboard(amounts: list[int]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for amount in amounts:
        builder.button(
            text=texts.UI_BOT_KEYBOARDS_PAYMENT_L150_1.format(value_0=amount),
            callback_data=f"balance_create:{amount}",
        )
    builder.button(
        text=texts.BUTTON_CUSTOM_AMOUNT,
        callback_data="balance_custom_amount",
    )
    builder.button(text=texts.BUTTON_BACK, callback_data="menu_balance")
    builder.adjust(2, 2, 2, 1, 1)
    return builder.as_markup()


def get_topup_waiting_keyboard(payment_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(
        text=texts.BUTTON_CHECK_TOPUP,
        callback_data=f"balance_check:{payment_id}",
    )
    builder.button(
        text=texts.BUTTON_CLOSE_TOPUP,
        callback_data=f"balance_cancel:{payment_id}",
    )
    builder.button(
        text=texts.BUTTON_RETURN_LATER,
        callback_data=f"balance_later:{payment_id}",
    )
    builder.adjust(1)
    return builder.as_markup()


def get_topup_payment_keyboard(
    payment_url: str, payment_id: int
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text=texts.BUTTON_OPEN_PAYMENT, url=payment_url)
    builder.button(
        text=texts.BUTTON_CHECK_TOPUP,
        callback_data=f"balance_check:{payment_id}",
    )
    builder.button(
        text=texts.BUTTON_CLOSE_TOPUP,
        callback_data=f"balance_cancel:{payment_id}",
    )
    builder.button(
        text=texts.BUTTON_RETURN_LATER,
        callback_data=f"balance_later:{payment_id}",
    )
    builder.adjust(1)
    return builder.as_markup()


def get_balance_purchase_start_keyboard(
    quote_public_id: str, back_callback: str
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(
        text=texts.UI_BOT_KEYBOARDS_PAYMENT_L206_1,
        callback_data=f"balance_purchase_review:{quote_public_id}",
    )
    builder.button(text=texts.BUTTON_BACK, callback_data=back_callback)
    builder.adjust(1)
    return builder.as_markup()


def get_balance_purchase_confirm_keyboard(
    quote_public_id: str, back_callback: str
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(
        text=texts.UI_BOT_KEYBOARDS_PAYMENT_L219_1,
        callback_data=f"balance_purchase_confirm:{quote_public_id}",
    )
    builder.button(text=texts.BUTTON_BACK, callback_data=back_callback)
    builder.adjust(1)
    return builder.as_markup()


def get_balance_change_start_keyboard(
    quote_public_id: str, back_callback: str
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(
        text=texts.UI_BOT_KEYBOARDS_PAYMENT_L232_1,
        callback_data=f"balance_change_review:{quote_public_id}",
    )
    builder.button(text=texts.BUTTON_BACK, callback_data=back_callback)
    builder.adjust(1)
    return builder.as_markup()


def get_balance_change_confirm_keyboard(
    quote_public_id: str, back_callback: str
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(
        text=texts.UI_BOT_KEYBOARDS_PAYMENT_L245_1,
        callback_data=f"balance_change_confirm:{quote_public_id}",
    )
    builder.button(text=texts.BUTTON_BACK, callback_data=back_callback)
    builder.adjust(1)
    return builder.as_markup()


def get_same_tariff_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(
        text=texts.UI_BOT_KEYBOARDS_PAYMENT_L256_1,
        callback_data="payment_quick_renew",
    )
    builder.button(text=texts.BUTTON_BACK, callback_data="payment_change_tariff")
    builder.adjust(1)
    return builder.as_markup()


def get_balance_shortage_keyboard(
    quote_public_id: str, exact_amount: int, back_callback: str
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(
        text=texts.UI_BOT_KEYBOARDS_PAYMENT_L269_1.format(value_0=exact_amount),
        callback_data=f"balance_shortage_exact:{quote_public_id}",
    )
    builder.button(
        text=texts.UI_BOT_KEYBOARDS_PAYMENT_L273_1,
        callback_data=f"balance_shortage_custom:{quote_public_id}",
    )
    builder.button(text=texts.BUTTON_BACK, callback_data=back_callback)
    builder.adjust(1)
    return builder.as_markup()


def get_balance_change_shortage_keyboard(
    quote_public_id: str, exact_amount: int, back_callback: str
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(
        text=texts.UI_BOT_KEYBOARDS_PAYMENT_L286_1.format(value_0=exact_amount),
        callback_data=f"balance_change_shortage_exact:{quote_public_id}",
    )
    builder.button(
        text=texts.UI_BOT_KEYBOARDS_PAYMENT_L290_1,
        callback_data=f"balance_change_shortage_custom:{quote_public_id}",
    )
    builder.button(text=texts.BUTTON_BACK, callback_data=back_callback)
    builder.adjust(1)
    return builder.as_markup()


def get_topup_credit_keyboard(context: dict) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    tariff_id = context.get("tariff_id")
    source = context.get("source")
    if tariff_id and source in {"showcase", "renew", "change"}:
        builder.button(
            text=texts.UI_BOT_KEYBOARDS_PAYMENT_L304_1,
            callback_data=f"balance_resume_purchase:{tariff_id}:{source}",
        )
    builder.button(text=texts.UI_BOT_KEYBOARDS_PAYMENT_L307_1, callback_data="menu_balance")
    builder.adjust(1)
    return builder.as_markup()
