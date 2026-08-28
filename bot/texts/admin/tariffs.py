"""Domain texts for admin/tariffs.py."""
from __future__ import annotations

ADMIN_TARIFF_BTN_BACK_TO_LIST = "← К списку тарифов"
ADMIN_TARIFF_BTN_DISABLE = "🔴 Выключить"
ADMIN_TARIFF_BTN_EDIT_PRICE = "✏️ Изменить цену ₽"
ADMIN_TARIFF_BTN_ENABLE = "🟢 Включить"

ADMIN_TARIFF_CARD_TEMPLATE = """🛠 Админка › 💰 Тарифы › <b>Тариф</b>
<b>ID:</b> {tariff_id}
<b>Название:</b> {name}
<b>Описание:</b> {description}
<b>Дней:</b> {duration_days}
<b>Устройств:</b> {device_limit}
<b>Цена ₽:</b> {price_rub}
<b>Статус:</b> {status}"""

ADMIN_TARIFF_DISABLE_CONFIRM = """⚠️ <b>Подтверждение отключения тарифа</b>
Тариф: <b>{name} ({duration_days} дн. / {device_limit} устр.)</b>
Тариф будет скрыт из списка доступных
при покупке доступа.
<i>Уже купленные подписки продолжат работать.</i>"""

ADMIN_TARIFF_ENABLE_CONFIRM = """⚠️ <b>Подтверждение включения тарифа</b>
Тариф: <b>{name} ({duration_days} дн. / {device_limit} устр.)</b>
Тариф снова будет доступен пользователям
при покупке доступа.
<i>Уже купленные подписки продолжат работать.</i>"""

ADMIN_TARIFF_EDIT_PRICE_PROMPT = "Введите новую цену в рублях:"

ADMIN_TARIFF_EDIT_PRICE_SUCCESS = "✅ Цена тарифа успешно обновлена: <b>{price_rub} ₽</b>"

ADMIN_TARIFF_ERR_PRICE_RANGE = "⚠️ Цена должна быть от 1 до {max_price} ₽."

ADMIN_TARIFF_LIST_EMPTY = """<i>Тарифов пока нет</i>
"""

ADMIN_TARIFF_LIST_TITLE = """🛠 Админка › 💰 <b>Тарифы</b>
(стр. {page}/{total_pages}) · Всего: {total}
"""

ADMIN_TARIFF_ROW_FORMAT = "{status} {name} · {duration_days} дн. · {price_rub}₽"

ADMIN_TARIFF_STATUS_DISABLED_BADGE = "🔴 Отключён"

ADMIN_TARIFF_TOGGLE_BLOCKED_PENDING = "❌ Нельзя отключить тариф, есть незавершенные платежи."

ADMIN_TARIFF_TOGGLE_SUCCESS_DISABLED = "✅ Тариф отключен."

ADMIN_TARIFF_TOGGLE_SUCCESS_ENABLED = "✅ Тариф включен."

ADMIN_AUDIT_LOG_DETAILS_TARIFF_TOGGLED = "toggled to {status}"
ADMIN_AUDIT_LOG_DETAILS_TARIFF_EDIT_RUB = "RUB: {old_value} -> {new_value}"
