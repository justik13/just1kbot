"""Domain texts for admin/finances.py."""
from __future__ import annotations

# --- Navigation buttons --------------------------------------------------
ADMIN_BTN_BACK_TO_PAYMENT = "← Назад к платежу"
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
ADMIN_PAYMENTS_LIST_TITLE = """🛠 Админка › 💳 <b>Платежи</b>
(стр. {page}/{total_pages}) · Всего: {total}
"""
ADMIN_PAYMENTS_LIST_EMPTY = """<i>Платежей пока нет</i>
"""
ADMIN_PAYMENTS_ROW_ENTRY = "{status_icon} #{payment_id} · {user_label} · {amount_rub}₽"
ADMIN_PAYMENT_STATUS_FALLBACK_ICON = "❓"
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

# --- Refund flow -------------------------------------------------------------
ADMIN_REFUND_START_BUTTON = "↩️ Вернуть доступный остаток"
ADMIN_CLIENT_CARD_BUTTON = "👤 Карточка клиента"
ADMIN_REFUND_CONFIRM_BUTTON = "✅ Подтвердить возврат {amount_rub} ₽"
ADMIN_REFUND_CONFIRMATION_BODY = """⚠️ <b>Подтверждение возврата</b>

Платёж: <code>#{payment_id}</code>
YooKassa ID: <code>{provider_payment_id}</code>
Будет возвращено: <b>{amount_rub} RUB</b>

Сумма сначала будет заморожена на внутреннем балансе, затем durable worker отправит идемпотентный запрос в YooKassa."""
ADMIN_REFUND_ACCEPTED_TEMPLATE = """✅ <b>Возврат принят</b>

{status_text}
Сумма: <b>{amount_rub} RUB</b>
Operation: <code>{operation_id}</code>
Статус: <code>{operation_status}</code>

Зарезервированная сумма недоступна для новых покупок до подтверждения или безопасного завершения операции."""
ADMIN_PAYMENTS_BACK_TO_LIST_BUTTON = "💳 К списку платежей"

# --- Payment/refund alerts ----------------------------------------------------
ADMIN_PAYMENT_NOT_FOUND_ALERT = "Платёж не найден"
ADMIN_REFUND_NOT_AVAILABLE_ALERT = "Платёж недоступен для возврата"
ADMIN_REFUND_NO_REMAINDER_ALERT = "Возвращаемого остатка уже нет"
ADMIN_REFUND_ERR_ONLY_TOPUP = "Можно вернуть только пополнение баланса"
ADMIN_REFUND_ERR_NOT_REFUNDABLE = "Платёж ещё не подтверждён или уже возвращён"
ADMIN_REFUND_ERR_NO_PROVIDER_ID = "У платежа нет YooKassa ID"
ADMIN_REFUND_ERR_MANUAL_REVIEW = "Возврат требует ручной проверки"
ADMIN_REFUND_ENQUEUE_FAILED_ALERT = "Не удалось поставить возврат в очередь"
ADMIN_REFUND_ENQUEUED_STATUS = "Возврат поставлен в durable-очередь."
ADMIN_REFUND_ALREADY_QUEUED_STATUS = "Этот возврат уже находится в durable-очереди."


REFUND_ORDER_DESCRIPTION_TEMPLATE = 'Возврат средств по заказу #{order_id}'
