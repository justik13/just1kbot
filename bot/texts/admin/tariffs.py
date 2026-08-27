"""Domain texts for admin/tariffs.py."""
from __future__ import annotations

ADMIN_BTN_BACK_TO_TARIFFS = "← К списку тарифов"

ADMIN_TARIFFS = "🟢 Активен"

ADMIN_TARIFF_BTN_BACK = "➡️"

ADMIN_TARIFF_BTN_EDIT_DEVICES = "⚠️ Цена должна быть от 1 до {value_0} ₽."

ADMIN_TARIFF_BTN_EDIT_PRICE = "← К списку тарифов"

ADMIN_TARIFF_BTN_TOGGLE_ACTIVE = "⬅️"

ADMIN_TARIFF_CARD_BODY = "🔴 Отключён"

ADMIN_TARIFF_CARD_HEADER = "🟢"

ADMIN_TARIFF_CREATE_PROMPT_NAME = "← В админку"

ADMIN_TARIFF_CREATE_SUCCESS = "✏️ Изменить цену ₽"

ADMIN_TARIFF_EDIT_DEVICES_PROMPT = "🟢 Включить"

ADMIN_TARIFF_EDIT_INVALID = "🔴 Выключить"

ADMIN_TARIFF_EDIT_NAME_PROMPT = "{value_0} {value_1} · {value_2} дн. · {value_3}₽"

ADMIN_TARIFF_EDIT_PRICE_PROMPT = "🔴 Выключить"

ADMIN_TARIFF_EDIT_RUB_PROMPT = "Введите новую цену в рублях:"

ADMIN_TARIFF_EDIT_RUB_SUCCESS = "✅ Цена тарифа успешно обновлена: <b>{value} ₽</b>"

ADMIN_TARIFF_EDIT_SUCCESS = "✏️ Изменить цену ₽"

ADMIN_TARIFF_LIST_EMPTY = """🛠 Админка › 💰 <b>Тарифы</b>
(стр. {value_0}/{value_1}) · Всего: {value_2}
"""

ADMIN_TARIFF_LIST_HEADER = """<i>Тарифов пока нет</i>
"""

ADMIN_TARIFF_ROW_ITEM = """⚠️ <b>Подтверждение отключения тарифа</b>
Тариф: <b>{value_0} ({value_1} дн. / {value_2} устр.)</b>
Тариф будет скрыт из списка доступных
при покупке доступа.
<i>Уже купленные подписки продолжат работать.</i>"""

ADMIN_TARIFF_STATUS_ACTIVE_LABEL = """🛠 Админка › 💰 Тарифы › <b>Тариф</b>
<b>ID:</b> {value_0}
<b>Название:</b> {value_1}
<b>Описание:</b> {value_2}
<b>Дней:</b> {value_3}
<b>Устройств:</b> {value_4}
<b>Цена ₽:</b> {value_5}
<b>Статус:</b> {value_6}"""

ADMIN_TARIFF_STATUS_INACTIVE_LABEL = """⚠️ <b>Подтверждение включения тарифа</b>
Тариф: <b>{value_0} ({value_1} дн. / {value_2} устр.)</b>
Тариф снова будет доступен пользователям
при покупке доступа.
<i>Уже купленные подписки продолжат работать.</i>"""

ADMIN_TARIFF_TOGGLE_ACTIVE_SUCCESS = "🟢 Включить"

ADMIN_TARIFF_TOGGLE_BLOCKED_PENDING = "❌ Нельзя отключить тариф, есть незавершенные платежи."

ADMIN_TARIFF_TOGGLE_SUCCESS_DISABLED = "✅ Тариф отключен."

ADMIN_TARIFF_TOGGLE_SUCCESS_ENABLED = "✅ Тариф включен."
