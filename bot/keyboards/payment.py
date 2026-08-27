from decimal import Decimal, ROUND_HALF_UP

from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot import texts
from utils.tariff_names import get_tariff_group_name


def _round_half_up(val: Decimal | float | int) -> int:
    d = Decimal(str(val))
    return int(d.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


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
        text=texts.BTN_MAIN_MENU_NAV, callback_data="back_to_main_menu"
    )
    builder.adjust(1)
    return builder.as_markup()


def format_dynamic_tariff_button(t, base_tariff=None) -> str:
    """Dynamically calculates discount percent relative to base duration."""
    days = getattr(t, "duration_days", 0)
    price = getattr(t, "price_rub", 0)

    curr_price = Decimal(str(price))
    display_price = int(curr_price) if int(curr_price) == curr_price else curr_price

    if (
        not base_tariff
        or getattr(base_tariff, "id", None) == getattr(t, "id", None)
        or getattr(base_tariff, "duration_days", 0) <= 0
        or days <= getattr(base_tariff, "duration_days", 0)
    ):
        return texts.BTN_DURATION_STANDARD.format(days=days, display_price=display_price)

    base_days = Decimal(str(base_tariff.duration_days))
    base_price = Decimal(str(base_tariff.price_rub))
    curr_days = Decimal(str(days))

    base_daily_rate = base_price / base_days
    undiscounted_price = base_daily_rate * curr_days
    if undiscounted_price > 0 and undiscounted_price > curr_price:
        discount_pct = _round_half_up(((undiscounted_price - curr_price) / undiscounted_price) * Decimal("100"))
        savings_rub = _round_half_up(undiscounted_price - curr_price)
    else:
        discount_pct = 0
        savings_rub = 0

    if savings_rub <= 0 or discount_pct <= 0:
        return texts.BTN_DURATION_STANDARD.format(days=days, display_price=display_price)

    if days >= 360:
        return texts.BTN_DURATION_YEAR_DISCOUNT.format(days=days, display_price=display_price, discount_pct=discount_pct)
    elif days >= 180:
        return texts.BTN_DURATION_HALF_YEAR_DISCOUNT.format(days=days, display_price=display_price, discount_pct=discount_pct)
    return texts.BTN_DURATION_MONTH_DISCOUNT.format(days=days, display_price=display_price, discount_pct=discount_pct)


def get_tariff_duration_keyboard(
    tariffs: list,
    *,
    source: str = "showcase",
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    tariffs_sorted = sorted(tariffs, key=lambda t: t.duration_days)
    # Prefer standard 30-day tariff as baseline if available, otherwise shortest duration
    base_tariff = next(
        (t for t in tariffs_sorted if getattr(t, "duration_days", 0) == 30),
        tariffs_sorted[0] if tariffs_sorted else None,
    )

    for t in tariffs_sorted:
        text = format_dynamic_tariff_button(t, base_tariff)
        builder.button(
            text=text, callback_data=f"select_tariff:{t.id}:{source}"
        )
    if source == "change":
        builder.button(
            text=texts.BTN_BACK, callback_data="payment_change_tariff"
        )
    elif source == "renew":
        builder.button(
            text=texts.BTN_BACK, callback_data="menu_subscription"
        )
    else:
        builder.button(
            text=texts.BTN_PAYMENT_TO_TARIFF_CHOICE, callback_data="payment_showcase"
        )
    builder.adjust(1)
    return builder.as_markup()


def get_renew_keyboard(tariffs: list) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    tariffs_sorted = sorted(tariffs, key=lambda t: t.duration_days)
    # Prefer standard 30-day tariff as baseline if available, otherwise shortest duration
    base_tariff = next(
        (t for t in tariffs_sorted if getattr(t, "duration_days", 0) == 30),
        tariffs_sorted[0] if tariffs_sorted else None,
    )

    for t in tariffs_sorted:
        text = format_dynamic_tariff_button(t, base_tariff)
        builder.button(
            text=text, callback_data=f"select_tariff:{t.id}:renew"
        )
    builder.button(text=texts.BTN_BACK, callback_data="menu_subscription")
    builder.adjust(1)
    return builder.as_markup()


def get_change_tariff_keyboard(
    tariffs: list,
    current_limit: int,
    *,
    is_subscription_active: bool = False,
    current_tariff_id: int | None = None,
    current_duration_days: int | None = 30,
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
            group_name += texts.PAYMENT
        elif limit == current_limit:
            group_name += texts.PAYMENT_STATUS_ACTIVE_BADGE
        elif limit > current_limit:
            group_name += texts.PAYMENT_STATUS_UPGRADE_BADGE

        group_tariffs = grouped[limit]
        best_tariff = next(
            (t for t in group_tariffs if getattr(t, "duration_days", None) == current_duration_days),
            min(group_tariffs, key=lambda t: getattr(t, "duration_days", 30)),
        )
        builder.button(
            text=group_name,
            callback_data=f"select_tariff:{best_tariff.id}:change",
        )
    builder.button(
        text=texts.BTN_BACK, callback_data="back_to_main_menu"
    )
    builder.adjust(1)
    return builder.as_markup()


def get_payment_success_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(
        text=texts.BTN_PAYMENT_CONNECT_DEVICE, callback_data="menu_connections"
    )
    builder.button(
        text=texts.BTN_PAYMENT_TO_SUBSCRIPTION, callback_data="menu_subscription"
    )
    builder.button(
        text=texts.BTN_MAIN_MENU_NAV, callback_data="back_to_main_menu"
    )
    builder.adjust(1, 1, 1)
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
    builder.button(text=texts.BTN_BACK, callback_data="back_to_main_menu")
    builder.adjust(1, 2, 1)
    return builder.as_markup()


def get_balance_history_keyboard(

    page: int = 1,
    total_pages: int = 1,
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()

    if total_pages > 1:
        if page > 1:
            builder.button(text=texts.BTN_BACK, callback_data=f"balance_history:{page - 1}")
        else:
            builder.button(text=" ", callback_data="ignore")

        builder.button(text=f"📄 {page}/{total_pages}", callback_data="ignore")

        if page < total_pages:
            builder.button(text=texts.BTN_PAGINATION_NEXT, callback_data=f"balance_history:{page + 1}")
        else:
            builder.button(text=" ", callback_data="ignore")

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


def get_back_or_cancel_topups_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(
        text=texts.BTN_ZAKRYT_NEZAVERSHYONNYE_SSYLKI,
        callback_data="balance_cancel_all",
        style="danger",
    )
    builder.button(text=texts.BTN_BACK, callback_data="menu_balance")
    builder.adjust(1)
    return builder.as_markup()


def get_topup_waiting_keyboard(payment_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(
        text=texts.BUTTON_CHECK_TOPUP,
        callback_data=f"balance_check:{payment_id}",
        style="primary",
    )
    builder.button(
        text=texts.BUTTON_CLOSE_TOPUP,
        callback_data=f"balance_cancel:{payment_id}",
        style="danger",
    )
    builder.adjust(1)
    return builder.as_markup()


def get_topup_payment_keyboard(
    payment_url: str, payment_id: int
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text=texts.BUTTON_OPEN_PAYMENT, url=payment_url, style="success")
    builder.button(
        text=texts.BUTTON_CHECK_TOPUP,
        callback_data=f"balance_check:{payment_id}",
        style="primary",
    )
    builder.button(
        text=texts.BUTTON_CLOSE_TOPUP,
        callback_data=f"balance_cancel:{payment_id}",
        style="danger",
    )
    builder.adjust(1)
    return builder.as_markup()


def get_balance_purchase_start_keyboard(
    quote_public_id: str, _back_callback: str | None = None
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(
        text=texts.BTN_PAYMENT_BUY_FROM_BALANCE,
        callback_data=f"balance_purchase_review:{quote_public_id}",
        style="success",
    )
    builder.button(
        text=texts.BTN_BACK,
        callback_data=f"balance_purchase_cancel:{quote_public_id}",
    )
    builder.adjust(1)
    return builder.as_markup()


def get_balance_purchase_confirm_keyboard(
    quote_public_id: str, _back_callback: str | None = None
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(
        text=texts.BTN_PAYMENT_CONFIRM_PURCHASE,
        callback_data=f"balance_purchase_confirm:{quote_public_id}",
        style="success",
    )
    builder.button(
        text=texts.BTN_BACK,
        callback_data=f"balance_purchase_cancel:{quote_public_id}",
    )
    builder.adjust(1)
    return builder.as_markup()


def get_balance_change_start_keyboard(
    quote_public_id: str, back_callback: str
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(
        text=texts.BTN_PAYMENT_CHANGE_TARIFF_FROM_BALANCE,
        callback_data=f"balance_change_review:{quote_public_id}",
        style="success",
    )
    builder.button(text=texts.BTN_BACK, callback_data=back_callback)
    builder.adjust(1)
    return builder.as_markup()


def get_balance_change_confirm_keyboard(
    quote_public_id: str, back_callback: str
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(
        text=texts.BTN_PAYMENT_CONFIRM_TARIFF_CHANGE,
        callback_data=f"balance_change_confirm:{quote_public_id}",
        style="success",
    )
    builder.button(text=texts.BTN_BACK, callback_data=back_callback)
    builder.adjust(1)
    return builder.as_markup()


def get_same_tariff_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(
        text=texts.BTN_PAYMENT_GO_TO_RENEW,
        callback_data="payment_quick_renew",
        style="success",
    )
    builder.button(text=texts.BTN_BACK, callback_data="payment_change_tariff")
    builder.adjust(1)
    return builder.as_markup()


def get_balance_shortage_keyboard(
    quote_public_id: str, exact_amount: int, _back_callback: str | None = None
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(
        text=texts.BTN_PAYMENT_TOPUP_PRESET_AMOUNT.format(amount_rub=exact_amount),
        callback_data=f"bal_short_exact:{quote_public_id}",
        style="success",
    )
    builder.button(
        text=texts.BTN_PAYMENT_SPECIFY_OTHER_AMOUNT,
        callback_data=f"bal_short_custom:{quote_public_id}",
    )
    builder.button(
        text=texts.BTN_BACK,
        callback_data=f"balance_purchase_cancel:{quote_public_id}",
    )
    builder.adjust(1)
    return builder.as_markup()


def get_balance_change_shortage_keyboard(
    quote_public_id: str, exact_amount: int, back_callback: str
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(
        text=texts.BTN_PAYMENT_TOPUP_PRESET_AMOUNT.format(amount_rub=exact_amount),
        callback_data=f"bal_chg_short_exact:{quote_public_id}",
        style="success",
    )
    builder.button(
        text=texts.BTN_PAYMENT_SPECIFY_OTHER_AMOUNT,
        callback_data=f"bal_chg_short_custom:{quote_public_id}",
    )
    builder.button(text=texts.BTN_BACK, callback_data=back_callback)
    builder.adjust(1)
    return builder.as_markup()


def get_topup_credit_keyboard(context: dict) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    tariff_id = context.get("tariff_id")
    source = context.get("source")
    if tariff_id and source in {"showcase", "renew", "change"}:
        builder.button(
            text=texts.BTN_PAYMENT_RETURN_TO_PURCHASE,
            callback_data=f"balance_resume_purchase:{tariff_id}:{source}",
        )
    builder.button(text=texts.BTN_PAYMENT_TO_BALANCE, callback_data="menu_balance")
    builder.button(text=texts.BTN_DISMISS, callback_data="dismiss_notification")
    builder.adjust(1)
    return builder.as_markup()
