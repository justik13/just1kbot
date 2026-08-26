"""Domain copy catalogue for: admin/tariffs.py"""

TEXTS = {
    'ADMIN_TARIFF_EDIT_RUB_PROMPT': 'Введите новую цену в рублях:',
    'ADMIN_TARIFF_EDIT_RUB_SUCCESS': '✅ Цена тарифа успешно обновлена: <b>{value} ₽</b>',
    'ADMIN_TARIFF_TOGGLE_BLOCKED_PENDING': '❌ Нельзя отключить тариф, есть незавершенные платежи.',
    'ADMIN_TARIFF_TOGGLE_SUCCESS_DISABLED': '✅ Тариф отключен.',
    'ADMIN_TARIFF_TOGGLE_SUCCESS_ENABLED': '✅ Тариф включен.',
    'RUNTIME_BOT_HANDLERS_ADMIN_TARIFFS_L191_1': '🟢 Активен',
    'RUNTIME_BOT_HANDLERS_ADMIN_TARIFFS_L193_1': '🔴 Отключён',
    'RUNTIME_BOT_HANDLERS_ADMIN_TARIFFS_L197_1': """🛠 Админка › 💰 Тарифы › <b>Тариф</b>
<b>ID:</b> {value_0}
<b>Название:</b> {value_1}
<b>Описание:</b> {value_2}
<b>Дней:</b> {value_3}
<b>Устройств:</b> {value_4}
<b>Цена ₽:</b> {value_5}
<b>Статус:</b> {value_6}""",
    'RUNTIME_BOT_HANDLERS_ADMIN_TARIFFS_L312_1': """⚠️ <b>Подтверждение включения тарифа</b>
Тариф: <b>{value_0} ({value_1} дн. / {value_2} устр.)</b>
Тариф снова будет доступен пользователям
при покупке доступа.
<i>Уже купленные подписки продолжат работать.</i>""",
    'RUNTIME_BOT_HANDLERS_ADMIN_TARIFFS_L323_1': """⚠️ <b>Подтверждение отключения тарифа</b>
Тариф: <b>{value_0} ({value_1} дн. / {value_2} устр.)</b>
Тариф будет скрыт из списка доступных
при покупке доступа.
<i>Уже купленные подписки продолжат работать.</i>""",
    'RUNTIME_BOT_HANDLERS_ADMIN_TARIFFS_L45_1': """🛠 Админка › 💰 <b>Тарифы</b>
(стр. {value_0}/{value_1}) · Всего: {value_2}
""",
    'RUNTIME_BOT_HANDLERS_ADMIN_TARIFFS_L52_1': """<i>Тарифов пока нет</i>
""",
    'RUNTIME_BOT_HANDLERS_ADMIN_TARIFFS_L55_1': '🟢',
    'RUNTIME_BOT_HANDLERS_ADMIN_TARIFFS_L57_1': '{value_0} {value_1} · {value_2} дн. · {value_3}₽',
    'RUNTIME_BOT_KEYBOARDS_ADMIN_TARIFFS_L26_1': '🔴 Выключить',
    'RUNTIME_BOT_KEYBOARDS_ADMIN_TARIFFS_L28_1': '🟢 Включить',
    'UI_BOT_HANDLERS_ADMIN_TARIFFS_L176_1': 'Некорректный запрос',
    'UI_BOT_HANDLERS_ADMIN_TARIFFS_L209_1': '✏️ Изменить цену ₽',
    'UI_BOT_HANDLERS_ADMIN_TARIFFS_L215_1': '🔴 Выключить',
    'UI_BOT_HANDLERS_ADMIN_TARIFFS_L220_1': '🟢 Включить',
    'UI_BOT_HANDLERS_ADMIN_TARIFFS_L225_1': '← К списку тарифов',
    'UI_BOT_HANDLERS_ADMIN_TARIFFS_L258_1': 'Некорректный запрос',
    'UI_BOT_HANDLERS_ADMIN_TARIFFS_L293_1': 'Некорректный запрос',
    'UI_BOT_HANDLERS_ADMIN_TARIFFS_L372_1': 'Некорректный запрос',
    'UI_BOT_HANDLERS_ADMIN_TARIFFS_L450_1': 'Некорректный запрос',
    'UI_BOT_HANDLERS_ADMIN_TARIFFS_L516_1': '⚠️ Цена должна быть от 1 до {value_0} ₽.',
    'UI_BOT_HANDLERS_ADMIN_TARIFFS_L68_1': '⬅️',
    'UI_BOT_HANDLERS_ADMIN_TARIFFS_L73_1': '➡️',
    'UI_BOT_HANDLERS_ADMIN_TARIFFS_L78_1': '← В админку',
    'UI_BOT_KEYBOARDS_ADMIN_TARIFFS_L20_1': '✏️ Изменить цену ₽',
    'UI_BOT_KEYBOARDS_ADMIN_TARIFFS_L34_1': '← К списку тарифов',
}
