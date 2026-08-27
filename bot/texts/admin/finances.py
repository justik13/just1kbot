"""Domain texts for admin/finances.py."""
from __future__ import annotations

ADMIN_BTN_BACK_TO_PAYMENT = "← Назад к платежу"

ADMIN_BTN_BACK_TO_PAYMENTS = "← К списку платежей"

ADMIN_BTN_PAGINATION_NEXT = "➡️"

ADMIN_BTN_PAGINATION_PREV = "⬅️"

ADMIN_FINANCES_PAYMENTS_K_LOGAM_POKUPOK = "🛒 К логам покупок"

ADMIN_FINANCES_PURCHASES_0_BONUS = "0 ₽ (Бонус)"

ADMIN_FINANCES_PURCHASES_0_BONUS_VYDACHA = "0 ₽ (Бонус/Выдача)"

ADMIN_FINANCES_PURCHASES_AMOUNT = """💳 <b>Сумма:</b> <b>{amount_str}</b>
"""

ADMIN_FINANCES_PURCHASES_DATA_I_TIME = """🕒 <b>Дата и время:</b> {dt_str}
"""

ADMIN_FINANCES_PURCHASES_DETALI = "Детали #{entry_numeric_id}"

ADMIN_FINANCES_PURCHASES_DETALI_PURCHASE_TRANZAKTSII = """🛒 <b>Детали покупки / транзакции #{entry_numeric_id}</b>

"""

ADMIN_FINANCES_PURCHASES_DLITELNOST_DAYS = """⏳ <b>Длительность:</b> {entry_duration_days} дней
"""

ADMIN_FINANCES_PURCHASES_DN_USTR = """   💎 {safe_entry_tariff_name} ({entry_duration_days} дн., {entry_device_limit} устр.) — <b>{amount_str}</b>
"""

ADMIN_FINANCES_PURCHASES_KARTOCHKA_POLZOVATELYA = "👤 Карточка пользователя"

ADMIN_FINANCES_PURCHASES_K_PLATEZHAM = "💳 К платежам"

ADMIN_FINANCES_PURCHASES_K_SPISKU_POKUPOK = "🛒 К списку покупок"

ADMIN_FINANCES_PURCHASES_LIMIT_DEVICES_SHT = """📱 <b>Лимит устройств:</b> {entry_device_limit} шт.
"""

ADMIN_FINANCES_PURCHASES_LOGI_POKUPOK_USERS_STR = """🛒 <b>Логи покупок пользователей</b> (Стр. {page}/{total_pages}, всего: {total})

"""

ADMIN_FINANCES_PURCHASES_PURCHASE = "🛒 Покупки"

ADMIN_FINANCES_PURCHASES_PURCHASE_NE_NAYDENA = "Покупка не найдена"

ADMIN_FINANCES_PURCHASES_PURCHASE_NE_NAYDENY = "<i>Покупки не найдены.</i>"

ADMIN_FINANCES_PURCHASES_STR = "Стр. {page}/{total_pages}"

ADMIN_FINANCES_PURCHASES_TARIFF = """💎 <b>Тариф:</b> {safe_entry_tariff_name}
"""

ADMIN_FINANCES_PURCHASES_TYPE_OPERATIONS = """⚙️ <b>Тип операции:</b> {safe_entry_operation_title}
"""

ADMIN_FINANCES_PURCHASES_USER_TELEGRAM_ID = """👤 <b>Пользователь:</b> {safe_entry_user_label} (Telegram ID: <code>{entry_telegram_id}</code>)
"""

ADMIN_FINANCES_PURCHASES_ZAPIS_PURCHASE_NE_NAYDENA = "Запись покупки не найдена"

ADMIN_PAYMENTS_BTN_BACK = "Этот возврат уже находится в durable-очереди."

ADMIN_PAYMENTS_BTN_FILTER_ALL = "Можно вернуть только пополнение баланса"

ADMIN_PAYMENTS_BTN_FILTER_REFUNDED = "У платежа нет YooKassa ID"

ADMIN_PAYMENTS_BTN_FILTER_SUCCEEDED = "Платёж ещё не подтверждён или уже возвращён"

ADMIN_PAYMENTS_BTN_NEXT = "Возврат поставлен в durable-очередь."

ADMIN_PAYMENTS_BTN_PREV = "Не удалось поставить возврат в очередь"

ADMIN_PAYMENTS_BTN_REFUND = "Возврат требует ручной проверки"

ADMIN_PAYMENTS_CARD = "{amount_rub} #{payment_id} · {status} · {details}₽"

ADMIN_PAYMENTS_LIST_HEADER = """🛠 Админка › 💳 <b>Платежи</b>
(стр. {amount_rub}/{payment_id}) · Всего: {status}
"""

ADMIN_PAYMENTS_NO_REFUNDABLE_REMAINDER = "Возвращаемого остатка уже нет"

ADMIN_PAYMENTS_ROW_ITEM = """<i>Платежей пока нет</i>
"""

ADMIN_PAYMENTS_SEARCH_PROMPT = "Платёж недоступен для возврата"

ADMIN_PAYMENT_NOT_FOUND_ALERT = "Платёж не найден"

ADMIN_PAYMENT_STATUS_CANCELED_LABEL = """
<b>Можно вернуть:</b> {amount_rub} RUB"""

ADMIN_PAYMENT_STATUS_PENDING_LABEL = "❓"

ADMIN_PAYMENT_STATUS_REFUNDED_LABEL = """🛠 Админка › 💳 Платежи › <b>Платёж #{amount_rub}</b>
<b>ID:</b> {payment_id}
<b>Пользователь:</b> {status}
<b>Сумма:</b> {details} {value_4}
<b>Статус:</b> {value_5} {value_6}
<b>Provider:</b> {value_7}
<b>Исполнение:</b> {value_8}
<b>Создан:</b> {value_9}
<b>Оплачен:</b> {value_10}
<b>External ID:</b> <code>{value_11}</code>{value_12}{value_13}"""

ADMIN_PAYMENT_STATUS_SUCCEEDED_LABEL = """
<b>Причина:</b> {amount_rub}"""

ADMIN_PAYMENT_USER_ID = "ID: <code>{user_id}</code>"

ADMIN_PAYMENT_USER_ID_COMPACT = "ID:{user_id}"

ADMIN_PAYMENT_USER_WITH_ID = "ID: <code>{user_id}</code> (@{username})"

ADMIN_PURCHASES_BTN_BACK = "↩️ Вернуть доступный остаток"

ADMIN_PURCHASES_BTN_REFUND = """✅ <b>Возврат принят</b>

{amount_rub}
Сумма: <b>{payment_id} RUB</b>
Operation: <code>{status}</code>
Статус: <code>{details}</code>

Зарезервированная сумма недоступна для новых покупок до подтверждения или безопасного завершения операции."""

ADMIN_PURCHASES_BTN_USER = "💳 К списку платежей"

ADMIN_PURCHASES_LIST_EMPTY = """⚠️ <b>Подтверждение возврата</b>

Платёж: <code>#{amount_rub}</code>
YooKassa ID: <code>{payment_id}</code>
Будет возвращено: <b>{status} RUB</b>

Сумма сначала будет заморожена на внутреннем балансе, затем durable worker отправит идемпотентный запрос в YooKassa."""

ADMIN_PURCHASES_LIST_HEADER = "✅ Подтвердить возврат {amount_rub} ₽"

ADMIN_PURCHASES_SEARCH_PROMPT = "👤 Карточка клиента"

ADMIN_PURCHASES_ROW_FORMAT = "<b>{idx}. {user_label}</b> | {operation_title}\n{tariff_info}   🕒 {dt_str}\n\n"
