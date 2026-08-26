"""Domain copy catalogue for: admin/subscriptions.py"""

TEXTS = {
    'ADMIN_SUBSCRIPTION_HEADER': """🛠 Админка › 👥 Пользователь <code>{telegram_id}</code> › 💳 <b>Подписка</b>

{status_block}""",
    'ADMIN_SUB_CHANGE_FAILED': '❌ Ошибка при изменении подписки.',
    'ADMIN_SUB_CHANGE_TARIFF_HEADER': """⚙️ Смена тарифа для <code>{telegram_id}</code>
Текущий тариф: <b>{current_tariff}</b> ({devices_count} устр.)
Выберите новый тариф:""",
    'ADMIN_SUB_CONFIRM_EXTEND': 'Продлить подписку пользователю <code>{telegram_id}</code> на <b>{days_text}</b> (текущий: <code>{current_end}</code>, новый: <code>{new_end}</code>)?',
    'ADMIN_SUB_CONFIRM_GRANT': 'Выдать тариф <b>{tariff_name}</b> пользователю <code>{telegram_id}</code> на <b>{days_text}</b> (до <code>{new_end}</code>)?',
    'ADMIN_SUB_CONFIRM_REDUCE': 'Сократить подписку пользователю <code>{telegram_id}</code> на <b>{days}</b> дн. (текущий: <code>{current_end}</code>, новый: <code>{new_end}</code>)?',
    'ADMIN_SUB_CONFIRM_TARIFF': 'Сменить тариф пользователю <code>{telegram_id}</code> с <b>{old_tariff}</b> на <b>{new_tariff}</b> ({devices_count} устр.)?',
    'ADMIN_SUB_DOWNGRADE_BLOCKED': '❌ Нельзя понизить тариф для <code>{telegram_id}</code>: подключено {devices_count} устр., а лимит нового тарифа — {new_limit}.',
    'ADMIN_SUB_EXTEND_FAILED': '❌ Не удалось продлить подписку.',
    'ADMIN_SUB_EXTEND_HEADER': """⏳ Продление подписки для <code>{telegram_id}</code>
Текущий срок: <code>{valid_until}</code>""",
    'ADMIN_SUB_EXTEND_PROMPT': 'На сколько дней продлить подписку для <code>{telegram_id}</code>?',
    'ADMIN_SUB_EXTEND_SUCCESS': '✅ Подписка пользователя <code>{telegram_id}</code> продлена на <b>{days_text}</b> (до <code>{new_end}</code>).',
    'ADMIN_SUB_GRANT_CUSTOM_PROMPT': 'Введите количество дней для тарифа <b>{tariff_name}</b> (пользователь <code>{telegram_id}</code>):',
    'ADMIN_SUB_GRANT_DAYS_HEADER': """🎁 Выдача тарифа <b>{tariff_name}</b> для <code>{telegram_id}</code>
Выберите срок:""",
    'ADMIN_SUB_GRANT_FAILED': '❌ Ошибка при выдаче подписки.',
    'ADMIN_SUB_GRANT_HEADER': """🎁 Выдача подписки для <code>{telegram_id}</code>
Выберите тариф:""",
    'ADMIN_SUB_GRANT_SUCCESS': '✅ Пользователю <code>{telegram_id}</code> выдан тариф <b>{tariff_name}</b> на <b>{days_text}</b> (до <code>{new_end}</code>).',
    'ADMIN_SUB_GROUP_NOT_FOUND': '❌ Группа тарифов не найдена.',
    'ADMIN_SUB_NO_SUBSCRIPTION': 'У пользователя нет активной подписки.',
    'ADMIN_SUB_PERMANENT_LABEL': 'Навсегда',
    'ADMIN_SUB_REDUCED': '✅ Подписка пользователя <code>{telegram_id}</code> сокращена до <code>{new_end}</code>.',
    'ADMIN_SUB_REDUCE_FAILED': '❌ Не удалось сократить подписку.',
    'ADMIN_SUB_REDUCE_PROMPT': """✂️ Сокращение подписки для <code>{telegram_id}</code>
Текущий срок: <code>{valid_until}</code>
На сколько дней сократить?""",
    'ADMIN_SUB_STATUS_ACTIVE': """<b>Статус:</b> 🟢 Активна ({valid_until}, {time_left})
<b>Тариф:</b> {tariff_name}
<b>Устройств:</b> {devices_count}/{device_limit}""",
    'ADMIN_SUB_STATUS_INACTIVE': """<b>Статус:</b> 🔴 Истекла ({valid_until})
<b>Тариф:</b> {tariff_name}""",
    'ADMIN_SUB_STATUS_NONE': """<b>Статус:</b> ⚪️ Нет подписки
<b>Устройств:</b> {devices_count}""",
    'ADMIN_SUB_TARIFF_ALREADY_SELECTED': '❌ Этот тариф уже активен.',
    'ADMIN_SUB_TARIFF_CHANGED': '✅ Тариф пользователя <code>{telegram_id}</code> изменен на <b>{tariff_name}</b> (лимит: {device_limit} устр.).',
    'RUNTIME_BOT_HANDLERS_ADMIN_USERS_SUBSCRIPTION_CHANGE_ROUTES_L202_1': '—',
    'RUNTIME_BOT_HANDLERS_ADMIN_USERS_SUBSCRIPTION_CHANGE_ROUTES_L76_1': '—',
    'RUNTIME_BOT_HANDLERS_ADMIN_USERS_SUBSCRIPTION_EXTEND_ROUTES_L158_1': '{value_0} дн.',
    'RUNTIME_BOT_HANDLERS_ADMIN_USERS_SUBSCRIPTION_EXTEND_ROUTES_L256_1': '{value_0} дн.',
    'RUNTIME_BOT_HANDLERS_ADMIN_USERS_SUBSCRIPTION_EXTEND_ROUTES_L271_1': '—',
    'RUNTIME_BOT_HANDLERS_ADMIN_USERS_SUBSCRIPTION_EXTEND_ROUTES_L415_1': '{value_0} дн.',
    'RUNTIME_BOT_HANDLERS_ADMIN_USERS_SUBSCRIPTION_GRANT_ROUTES_L240_1': '{value_0} дн.',
    'RUNTIME_BOT_HANDLERS_ADMIN_USERS_SUBSCRIPTION_GRANT_ROUTES_L403_1': '{value_0} дн.',
    'RUNTIME_BOT_HANDLERS_ADMIN_USERS_SUBSCRIPTION_GRANT_ROUTES_L521_1': '{value_0} дн.',
    'RUNTIME_BOT_HANDLERS_ADMIN_USERS_SUBSCRIPTION_GRANT_ROUTES_L543_1': '—',
    'RUNTIME_BOT_HANDLERS_ADMIN_USERS_SUBSCRIPTION_MENU_ROUTES_L65_1': '—',
    'RUNTIME_BOT_HANDLERS_ADMIN_USERS_SUBSCRIPTION_MENU_ROUTES_L78_1': '{value_0} ({value_1} устр.)',
    'UI_BOT_HANDLERS_ADMIN_USERS_SUBSCRIPTION_CHANGE_ROUTES_L127_1': 'Некорректный запрос',
    'UI_BOT_HANDLERS_ADMIN_USERS_SUBSCRIPTION_CHANGE_ROUTES_L137_1': 'Некорректный запрос',
    'UI_BOT_HANDLERS_ADMIN_USERS_SUBSCRIPTION_CHANGE_ROUTES_L261_1': 'Некорректный запрос',
    'UI_BOT_HANDLERS_ADMIN_USERS_SUBSCRIPTION_CHANGE_ROUTES_L271_1': 'Некорректный запрос',
    'UI_BOT_HANDLERS_ADMIN_USERS_SUBSCRIPTION_CHANGE_ROUTES_L53_1': 'Некорректный запрос',
    'UI_BOT_HANDLERS_ADMIN_USERS_SUBSCRIPTION_EXTEND_ROUTES_L108_1': 'Некорректный запрос',
    'UI_BOT_HANDLERS_ADMIN_USERS_SUBSCRIPTION_EXTEND_ROUTES_L123_1': 'Некорректное количество дней',
    'UI_BOT_HANDLERS_ADMIN_USERS_SUBSCRIPTION_EXTEND_ROUTES_L204_1': 'Некорректный запрос',
    'UI_BOT_HANDLERS_ADMIN_USERS_SUBSCRIPTION_EXTEND_ROUTES_L219_1': 'Некорректное количество дней',
    'UI_BOT_HANDLERS_ADMIN_USERS_SUBSCRIPTION_EXTEND_ROUTES_L323_1': 'Некорректный запрос',
    'UI_BOT_HANDLERS_ADMIN_USERS_SUBSCRIPTION_EXTEND_ROUTES_L57_1': 'Некорректный запрос',
    'UI_BOT_HANDLERS_ADMIN_USERS_SUBSCRIPTION_GRANT_ROUTES_L129_1': 'Некорректный запрос',
    'UI_BOT_HANDLERS_ADMIN_USERS_SUBSCRIPTION_GRANT_ROUTES_L139_1': 'Некорректный запрос',
    'UI_BOT_HANDLERS_ADMIN_USERS_SUBSCRIPTION_GRANT_ROUTES_L196_1': 'Некорректный запрос',
    'UI_BOT_HANDLERS_ADMIN_USERS_SUBSCRIPTION_GRANT_ROUTES_L213_1': 'Некорректное количество дней',
    'UI_BOT_HANDLERS_ADMIN_USERS_SUBSCRIPTION_GRANT_ROUTES_L290_1': 'Некорректный запрос',
    'UI_BOT_HANDLERS_ADMIN_USERS_SUBSCRIPTION_GRANT_ROUTES_L300_1': 'Некорректный запрос',
    'UI_BOT_HANDLERS_ADMIN_USERS_SUBSCRIPTION_GRANT_ROUTES_L448_1': 'Некорректный запрос',
    'UI_BOT_HANDLERS_ADMIN_USERS_SUBSCRIPTION_GRANT_ROUTES_L465_1': 'Некорректное количество дней',
    'UI_BOT_HANDLERS_ADMIN_USERS_SUBSCRIPTION_GRANT_ROUTES_L64_1': 'Некорректный запрос',
    'UI_BOT_HANDLERS_ADMIN_USERS_SUBSCRIPTION_MENU_ROUTES_L43_1': 'Некорректный запрос',
    'UI_BOT_HANDLERS_ADMIN_USERS_SUBSCRIPTION_REDUCE_ROUTES_L175_1': 'Некорректный запрос',
    'UI_BOT_HANDLERS_ADMIN_USERS_SUBSCRIPTION_REDUCE_ROUTES_L190_1': 'Некорректное количество дней',
    'UI_BOT_HANDLERS_ADMIN_USERS_SUBSCRIPTION_REDUCE_ROUTES_L53_1': 'Некорректный запрос',
}
