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


def format_dynamic_tariff_button(t, base_tariff=None) -> str:
    """Dynamically calculates monthly price equivalent and discount percent relative to base duration."""
    days = getattr(t, "duration_days", 0)
    price = getattr(t, "price_rub", 0)

    if (
        not base_tariff
        or getattr(base_tariff, "id", None) == getattr(t, "id", None)
        or getattr(base_tariff, "duration_days", 0) <= 0
        or days <= getattr(base_tariff, "duration_days", 0)
    ):
        return f"⏱ {days} дн. — {price} ₽"

    base_days = base_tariff.duration_days
    base_price = base_tariff.price_rub
    base_daily_rate = base_price / base_days
    undiscounted_price = base_daily_rate * days
    savings_rub = round(undiscounted_price - price)
    discount_pct = round((savings_rub / undiscounted_price) * 100) if undiscounted_price > 0 else 0
    price_per_month = round((price * 30) / days) if days > 0 else price

    if savings_rub <= 0 or discount_pct <= 0:
        return f"⏱ {days} дн. — {price} ₽"

    if days >= 360:
        return f"💎 {days} дн. — {price} ₽ ({price_per_month} ₽/мес • -{discount_pct}%) 🔥"
    elif days >= 180:
        return f"⚡️ {days} дн. — {price} ₽ ({price_per_month} ₽/мес • -{discount_pct}%)"
    elif days >= 90:
        return f"⏱ {days} дн. — {price} ₽ ({price_per_month} ₽/мес • -{discount_pct}%) 🔥"

    return f"⏱ {days} дн. — {price} ₽ (-{discount_pct}%)"


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
    builder.button(text=texts.BUTTON_BACK, callback_data="menu_subscription")
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
            group_name += texts.RUNTIME_BOT_KEYBOARDS_PAYMENT_L94_1
        elif limit == current_limit:
            group_name += texts.RUNTIME_BOT_KEYBOARDS_PAYMENT_L96_1
        elif limit > current_limit:
            group_name += texts.RUNTIME_BOT_KEYBOARDS_PAYMENT_L98_1

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
        text="🧾 История пополнений",
        callback_data="user_history",
    )
    builder.button(
        text="📊 История операций",
        callback_data="balance_history",
    )
    builder.button(text=texts.BUTTON_BACK, callback_data="back_to_main_menu")
    builder.adjust(1, 2, 1)
    return builder.as_markup()


def get_balance_history_keyboard(

    page: int = 1,
    total_pages: int = 1,
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()

    if total_pages > 1:
        if page > 1:
            builder.button(text="◀️ Назад", callback_data=f"balance_history:{page - 1}")
        else:
            builder.button(text=" ", callback_data="ignore")

        builder.button(text=f"📄 {page}/{total_pages}", callback_data="ignore")

        if page < total_pages:
            builder.button(text="Вперед ▶️", callback_data=f"balance_history:{page + 1}")
        else:
            builder.button(text=" ", callback_data="ignore")

        builder.button(text=texts.BUTTON_BACK, callback_data="menu_balance")
        builder.adjust(3, 1)
    else:
        builder.button(text=texts.BUTTON_BACK, callback_data="menu_balance")
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


def get_back_or_cancel_topups_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(
        text="❌ Закрыть незавершённые ссылки",
        callback_data="balance_cancel_all",
    )
    builder.button(text=texts.BUTTON_BACK, callback_data="menu_balance")
    builder.adjust(1)
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
    builder.adjust(1)
    return builder.as_markup()


def get_balance_purchase_start_keyboard(
    quote_public_id: str, _back_callback: str | None = None
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(
        text=texts.UI_BOT_KEYBOARDS_PAYMENT_L206_1,
        callback_data=f"balance_purchase_review:{quote_public_id}",
    )
    builder.button(
        text=texts.BUTTON_BACK,
        callback_data=f"balance_purchase_cancel:{quote_public_id}",
    )
    builder.adjust(1)
    return builder.as_markup()


def get_balance_purchase_confirm_keyboard(
    quote_public_id: str, _back_callback: str | None = None
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(
        text=texts.UI_BOT_KEYBOARDS_PAYMENT_L219_1,
        callback_data=f"balance_purchase_confirm:{quote_public_id}",
    )
    builder.button(
        text=texts.BUTTON_BACK,
        callback_data=f"balance_purchase_cancel:{quote_public_id}",
    )
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
    quote_public_id: str, exact_amount: int, _back_callback: str | None = None
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(
        text=texts.UI_BOT_KEYBOARDS_PAYMENT_L269_1.format(value_0=exact_amount),
        callback_data=f"bal_short_exact:{quote_public_id}",
    )
    builder.button(
        text=texts.UI_BOT_KEYBOARDS_PAYMENT_L273_1,
        callback_data=f"bal_short_custom:{quote_public_id}",
    )
    builder.button(
        text=texts.BUTTON_BACK,
        callback_data=f"balance_purchase_cancel:{quote_public_id}",
    )
    builder.adjust(1)
    return builder.as_markup()


def get_balance_change_shortage_keyboard(
    quote_public_id: str, exact_amount: int, back_callback: str
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(
        text=texts.UI_BOT_KEYBOARDS_PAYMENT_L286_1.format(value_0=exact_amount),
        callback_data=f"bal_chg_short_exact:{quote_public_id}",
    )
    builder.button(
        text=texts.UI_BOT_KEYBOARDS_PAYMENT_L290_1,
        callback_data=f"bal_chg_short_custom:{quote_public_id}",
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
    builder.button(text="✅ Прочитано", callback_data="dismiss_notification")
    builder.adjust(1)
    return builder.as_markup()
