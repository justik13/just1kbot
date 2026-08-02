from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

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
        text="🏠 В главное меню", callback_data="back_to_main_menu"
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
        text = f"⏱ {t.duration_days} дн. — {t.price_rub}₽"
        if t.duration_days >= 90:
            text += " 🔥"
        elif t.duration_days >= 30:
            text += " 🌟"
        builder.button(
            text=text, callback_data=f"select_tariff:{t.id}:{source}"
        )
    if source == "change":
        builder.button(
            text="← Назад", callback_data="payment_change_tariff"
        )
    elif source == "renew":
        builder.button(
            text="← Назад", callback_data="menu_subscription"
        )
    else:
        builder.button(
            text="← К выбору тарифа", callback_data="payment_showcase"
        )
    builder.adjust(1)
    return builder.as_markup()


def get_renew_keyboard(tariffs: list) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    tariffs_sorted = sorted(tariffs, key=lambda t: t.duration_days)
    for t in tariffs_sorted:
        text = f"⏱ {t.duration_days} дн. — {t.price_rub}₽"
        if t.duration_days >= 90:
            text += " 🔥"
        elif t.duration_days >= 30:
            text += " 🌟"
        builder.button(
            text=text, callback_data=f"select_tariff:{t.id}:renew"
        )
    builder.button(text="← Назад", callback_data="menu_subscription")
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
            group_name += " 🔽"
        elif limit == current_limit:
            group_name += " ✅"
        elif limit > current_limit:
            group_name += " 🔼"
        builder.button(
            text=group_name,
            callback_data=f"select_tariff_type:{limit}:change",
        )
    builder.button(
        text="← Назад", callback_data="back_to_main_menu"
    )
    builder.adjust(1)
    return builder.as_markup()


def get_payment_method_keyboard(
    tariff_id: int,
    device_limit: int | None = None,
    source: str = "showcase",
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(
        text="💳 Оплатить",
        callback_data=f"pay_yookassa:{tariff_id}:{source}",
    )
    if source == "renew":
        builder.button(
            text="← Назад", callback_data="payment_quick_renew"
        )
    elif device_limit is not None:
        builder.button(
            text="← Назад",
            callback_data=f"select_tariff_type:{device_limit}:{source}",
        )
    else:
        builder.button(
            text="← В главное меню", callback_data="back_to_main_menu"
        )
    builder.adjust(1)
    return builder.as_markup()


def get_payment_success_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(
        text="🔌 Подключить устройство", callback_data="menu_connections"
    )
    builder.button(
        text="⏳ К подписке", callback_data="menu_subscription"
    )
    builder.button(
        text="🏠 В главное меню", callback_data="back_to_main_menu"
    )
    builder.adjust(1, 1, 1)
    return builder.as_markup()


def get_yookassa_payment_keyboard(
    payment_url: str,
    payment_id: int,
    tariff_id: int,
    source: str = "showcase",
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="💳 Открыть страницу оплаты", url=payment_url)
    builder.button(
        text="✅ Я оплатил (проверить)",
        callback_data=f"check_payment:{payment_id}",
    )
    builder.button(
        text="❌ Отменить",
        callback_data=f"cancel_invoice:{payment_id}:{tariff_id}:{source}",
    )
    builder.adjust(1, 1, 1)
    return builder.as_markup()


def get_balance_keyboard(*, has_visible_topup: bool = False) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    if has_visible_topup:
        builder.button(
            text="💳 Продолжить пополнение",
            callback_data="balance_resume_topup",
        )
    else:
        builder.button(
            text="➕ Пополнить баланс",
            callback_data="balance_topup",
        )
    builder.button(
        text="🧾 История операций",
        callback_data="balance_history",
    )
    builder.button(text="← Назад", callback_data="back_to_main_menu")
    builder.adjust(1)
    return builder.as_markup()


def get_balance_amounts_keyboard(amounts: list[int]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for amount in amounts:
        builder.button(
            text=f"{amount} ₽",
            callback_data=f"balance_create:{amount}",
        )
    builder.button(
        text="Другая сумма",
        callback_data="balance_custom_amount",
    )
    builder.button(text="← Назад", callback_data="menu_balance")
    builder.adjust(2, 2, 2, 1, 1)
    return builder.as_markup()


def get_topup_waiting_keyboard(payment_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(
        text="🔄 Проверить статус",
        callback_data=f"balance_check:{payment_id}",
    )
    builder.button(
        text="❌ Отменить пополнение",
        callback_data=f"balance_cancel:{payment_id}",
    )
    builder.button(
        text="Вернуться позже",
        callback_data=f"balance_later:{payment_id}",
    )
    builder.adjust(1)
    return builder.as_markup()


def get_topup_payment_keyboard(
    payment_url: str, payment_id: int
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="💳 Перейти к оплате", url=payment_url)
    builder.button(
        text="🔄 Проверить статус",
        callback_data=f"balance_check:{payment_id}",
    )
    builder.button(
        text="❌ Отменить пополнение",
        callback_data=f"balance_cancel:{payment_id}",
    )
    builder.button(
        text="Вернуться позже",
        callback_data=f"balance_later:{payment_id}",
    )
    builder.adjust(1)
    return builder.as_markup()


def get_balance_purchase_start_keyboard(
    quote_public_id: str, back_callback: str
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(
        text="💰 Купить с баланса",
        callback_data=f"balance_purchase_review:{quote_public_id}",
    )
    builder.button(text="← Назад", callback_data=back_callback)
    builder.adjust(1)
    return builder.as_markup()


def get_balance_purchase_confirm_keyboard(
    quote_public_id: str, back_callback: str
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(
        text="✅ Подтвердить покупку",
        callback_data=f"balance_purchase_confirm:{quote_public_id}",
    )
    builder.button(text="← Назад", callback_data=back_callback)
    builder.adjust(1)
    return builder.as_markup()


def get_balance_change_start_keyboard(
    quote_public_id: str, back_callback: str
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(
        text="💱 Сменить тариф с баланса",
        callback_data=f"balance_change_review:{quote_public_id}",
    )
    builder.button(text="← Назад", callback_data=back_callback)
    builder.adjust(1)
    return builder.as_markup()


def get_balance_change_confirm_keyboard(
    quote_public_id: str, back_callback: str
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(
        text="✅ Подтвердить смену тарифа",
        callback_data=f"balance_change_confirm:{quote_public_id}",
    )
    builder.button(text="← Назад", callback_data=back_callback)
    builder.adjust(1)
    return builder.as_markup()


def get_same_tariff_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(
        text="🔄 Перейти к продлению",
        callback_data="payment_quick_renew",
    )
    builder.button(text="← Назад", callback_data="payment_change_tariff")
    builder.adjust(1)
    return builder.as_markup()


def get_balance_shortage_keyboard(
    quote_public_id: str, exact_amount: int, back_callback: str
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(
        text=f"Пополнить на {exact_amount} ₽",
        callback_data=f"balance_shortage_exact:{quote_public_id}",
    )
    builder.button(
        text="Указать другую сумму",
        callback_data=f"balance_shortage_custom:{quote_public_id}",
    )
    builder.button(text="← Назад", callback_data=back_callback)
    builder.adjust(1)
    return builder.as_markup()


def get_balance_change_shortage_keyboard(
    quote_public_id: str, exact_amount: int, back_callback: str
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(
        text=f"Пополнить на {exact_amount} ₽",
        callback_data=f"balance_change_shortage_exact:{quote_public_id}",
    )
    builder.button(
        text="Указать другую сумму",
        callback_data=f"balance_change_shortage_custom:{quote_public_id}",
    )
    builder.button(text="← Назад", callback_data=back_callback)
    builder.adjust(1)
    return builder.as_markup()


def get_topup_credit_keyboard(context: dict) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    tariff_id = context.get("tariff_id")
    source = context.get("source")
    if tariff_id and source in {"showcase", "renew", "change"}:
        builder.button(
            text="Вернуться к покупке",
            callback_data=f"balance_resume_purchase:{tariff_id}:{source}",
        )
    builder.button(text="💰 К балансу", callback_data="menu_balance")
    builder.adjust(1)
    return builder.as_markup()
