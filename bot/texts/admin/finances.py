"""Domain texts for admin/finances.py."""
from __future__ import annotations

# --- Navigation buttons --------------------------------------------------
ADMIN_BTN_BACK_TO_PAYMENTS = "← К списку платежей"
ADMIN_BTN_PAGINATION_NEXT = "➡️"
ADMIN_BTN_PAGINATION_PREV = "⬅️"

# --- Purchases catalogue / purchase log -----------------------------------
ADMIN_PURCHASES_TAB_TITLE = "🛒 Покупки"
ADMIN_PURCHASES_LOGS_BUTTON = "🛒 К логам покупок"
ADMIN_PURCHASES_LOGS_TITLE = """🛒 <b>Логи покупок пользователей</b> (Стр. {page}/{total_pages}, всего: {total})

"""
ADMIN_PURCHASES_LIST_BUTTON = "🛒 К списку покупок"
ADMIN_PURCHASES_PAYMENTS_BUTTON = "💳 К платежам"
ADMIN_PURCHASES_PAGE_INDICATOR = "Стр. {page}/{total_pages}"
ADMIN_PURCHASES_NOT_FOUND_ALERT = "Покупка не найдена"
ADMIN_PURCHASES_RECORD_NOT_FOUND_ALERT = "Запись покупки не найдена"
ADMIN_PURCHASES_EMPTY_NOTICE = "<i>Покупки не найдены.</i>"
ADMIN_PURCHASES_USER_CARD_LINK = "👤 Карточка пользователя"
ADMIN_PURCHASES_DETAILS_LINK = "Детали #{entry_numeric_id}"
ADMIN_PURCHASES_ENTRY_TITLE = """🛒 <b>Детали покупки / транзакции #{entry_numeric_id}</b>

"""
ADMIN_PURCHASES_ROW_FORMAT = "<b>{idx}. {user_label}</b> | {operation_title}\n{tariff_info}   🕒 {dt_str}\n\n"
ADMIN_PURCHASES_AMOUNT_ZERO_BONUS = "0 ₽ (Бонус)"
ADMIN_PURCHASES_AMOUNT_ZERO_BONUS_GRANT = "0 ₽ (Бонус/Выдача)"
ADMIN_PURCHASES_ENTRY_SUMMARY_LINE = """💳 <b>Сумма:</b> <b>{amount_str}</b>
"""
ADMIN_PURCHASES_ENTRY_FUNDS_LINE = """💰 <b>Оплата:</b> реал {real_str} + бонус {bonus_str}
"""
ADMIN_PURCHASES_ENTRY_DATETIME_LINE = """🕒 <b>Дата и время:</b> {dt_str}
"""
ADMIN_PURCHASES_ENTRY_DURATION_LINE = """⏳ <b>Длительность:</b> {entry_duration_days} дней
"""
ADMIN_PURCHASES_ENTRY_TARIFF_LINE = """💎 <b>Тариф:</b> {safe_entry_tariff_name}
"""
ADMIN_PURCHASES_TARIFF_ROW = """   💎 {safe_entry_tariff_name} ({entry_duration_days} дн., {entry_device_limit} устр.) — <b>{amount_str}</b>
"""
ADMIN_PURCHASES_ENTRY_DEVICE_LIMIT_LINE = """📱 <b>Лимит устройств:</b> {entry_device_limit} шт.
"""
ADMIN_PURCHASES_ENTRY_OPERATION_TYPE_LINE = """⚙️ <b>Тип операции:</b> {safe_entry_operation_title}
"""
ADMIN_PURCHASES_ENTRY_USER_LINE = """👤 <b>Пользователь:</b> {safe_entry_user_label} (Telegram ID: <code>{entry_telegram_id}</code>)
"""

# --- Payments list ---------------------------------------------------------
ADMIN_PAYMENTS_LIST_TITLE = """🛠 Админка › 💳 <b>Платежи через ЮKassa</b>
(стр. {page}/{total_pages}) · Всего: {total}
"""
ADMIN_PAYMENTS_LIST_EMPTY = """<i>Платежей пока нет</i>
"""
ADMIN_PAYMENTS_USER_TITLE = """🛠 Админка › 💳 <b>Платежи пользователя</b> @{user_label}
Страница {page} из {total_pages} (всего: {total_count})"""
ADMIN_PAYMENTS_USER_EMPTY = "<i>Платежи пользователя не найдены.</i>"
ADMIN_PAYMENTS_ROW_ENTRY = "{status_icon} #{payment_id} · {user_label} · {amount_rub}₽"
ADMIN_PAYMENT_STATUS_FALLBACK_ICON = "❓"
ADMIN_PAYMENTS_GATEWAY_YOOKASSA = "ЮKassa"
ADMIN_PURCHASES_ROW_BUTTON_TEMPLATE = "🛒 #{numeric_id} | {user_label} | {amount}"

# --- Payment card ----------------------------------------------------------
ADMIN_PAYMENT_USER_ID = "ID: <code>{user_id}</code>"
ADMIN_PAYMENT_USER_ID_COMPACT = "ID:{user_id}"
ADMIN_PAYMENT_USER_WITH_ID = "ID: <code>{user_id}</code> (@{username})"
ADMIN_PAYMENT_CARD_TEMPLATE = """🛠 Админка › 💳 Платежи › <b>Платёж #{payment_id}</b>
<b>ID:</b> {payment_id}
<b>Пользователь:</b> {user_label}
<b>Сумма:</b> {amount_rub} {currency}
<b>Статус:</b> {status_icon} {status_name}
<b>Provider:</b> {provider_status}
<b>Исполнение:</b> {fulfillment_status}
<b>Создан:</b> {created_at}
<b>Оплачен:</b> {paid_at}
<b>External ID:</b> <code>{external_id}</code>{refundable_line}{reason_line}"""
ADMIN_PAYMENT_MANUAL_REVIEW_LINE = """
<b>Причина:</b> {reason}"""
ADMIN_PAYMENT_REFUNDABLE_LINE = """
<b>Можно вернуть:</b> {amount_rub} RUB"""

ADMIN_CLIENT_CARD_BUTTON = "👤 Карточка клиента"
ADMIN_PAYMENT_NOT_FOUND_ALERT = "Платёж не найден"

ADMIN_ORDER_CARD_TEMPLATE = """🛠 Админка › 🧾 <b>Заказ #{short_id}</b>

👤 <b>Пользователь:</b> {user_label}
💰 <b>Сумма:</b> <b>{amount_rub} ₽</b>
📦 <b>Услуга:</b> {tariff_label} ({service_type})
📊 <b>Статус:</b> {status_icon} {status_name} (<code>{status}</code>)
💳 <b>Шлюз:</b> <code>{payment_method}</code>
🕒 <b>Создан:</b> {created_at}{paid_at_line}{refunded_at_line}{external_id_line}{payment_url_line}{description_line}{held_line}{diagnostics_line}"""
ADMIN_ORDER_PAID_AT_LINE = "\n✅ <b>Оплачен:</b> {paid_at}"
ADMIN_ORDER_HELD_LINE = "\n⛔ <b>Удержан:</b> {reason} — зачисление и выдача удержаны, требуется ручное решение"
ADMIN_ORDER_REFUNDED_AT_LINE = "\n↩️ <b>Возврат:</b> {refunded_at}"
ADMIN_ORDER_EXTERNAL_ID_LINE = "\n🔗 <b>Внешний ID:</b> <code>{external_id}</code>"
ADMIN_ORDER_PAYMENT_URL_LINE = "\n🌐 <b>Ссылка на оплату:</b> <a href=\"{payment_url}\">Перейти к форме</a>"
ADMIN_ORDER_DIAGNOSTICS_PENDING = "\nℹ️ <i>Ожидает завершения оплаты клиентом или ответа банка.</i>"
ADMIN_ORDER_DIAGNOSTICS_AMBIGUOUS = "\n⚠️ <i>Тайм-аут шлюза при создании. Платёж мог зафиксироваться в ЮKassa.</i>"
ADMIN_ORDER_DESCRIPTION_LINE = "\n📝 <b>Описание:</b> {description}"
ADMIN_ORDER_SERVICE_TOPUP = "Пополнение баланса"

# --- Purchases additional texts --------------------------------------------
ADMIN_PURCHASES_ENTRY_PAYMENT_METHOD_LINE = """💳 <b>Способ оплаты:</b> {payment_method_label}
"""
ADMIN_PURCHASES_METHOD_YOOKASSA = "Банковская карта (ЮKassa)"
ADMIN_PURCHASES_METHOD_WALLET = "Внутренний баланс"
ADMIN_PURCHASES_METHOD_ADMIN = "Выдача администратором"
ADMIN_PURCHASES_METHOD_OTHER = "Другой способ"
