# Точечные переопределения текстов.
# Применяются после user_texts и admin_texts.

OVERRIDES = {
    "ADMIN_SERVER_URL_PROMPT": (
        "🔗 Введите API URL сервера "
        "(например: https://api.example.com:8443):"
    ),

    "ERROR_INVALID_URL": """⚠️ Некорректный формат URL.

URL должен начинаться с <code>https://</code>

Пример: <code>https://api.example.com:8443</code>""",
}

OVERRIDES['ADMIN_BAN_CONFIRM'] = 'Вы уверены, что хотите забанить пользователя?'

OVERRIDES['ADMIN_BAN_FAILED'] = '❌ Ошибка при попытке забанить пользователя.'

OVERRIDES['ADMIN_BAN_SUCCESS'] = '✅ Пользователь успешно забанен.'

OVERRIDES['ADMIN_DELETE_DEVICE_CONFIRM'] = 'Вы уверены, что хотите удалить устройство?'

OVERRIDES['ADMIN_DELETE_DEVICE_ERROR'] = '❌ Произошла ошибка при удалении устройства.'

OVERRIDES['ADMIN_DELETE_DEVICE_FAILED'] = '❌ Не удалось удалить устройство.'

OVERRIDES['ADMIN_DELETE_DEVICE_SUCCESS'] = '✅ Устройство успешно удалено.'

OVERRIDES['ADMIN_MANUAL_GRANT_USER_BANNED'] = '❌ Невозможно выдать подписку: пользователь забанен.'

OVERRIDES['ADMIN_MANUAL_GRANT_USER_DELETED'] = '❌ Невозможно выдать подписку: пользователь удален.'

OVERRIDES['ADMIN_SERVER_ADDED'] = '✅ Сервер успешно добавлен.'

OVERRIDES['ADMIN_SERVER_CARD'] = '🖥 <b>Сервер:</b> {server_name}\nURL: {server_url}\nМакс. клиентов: {max_clients}'

OVERRIDES['ADMIN_SERVER_CHECKING'] = '🔄 Проверка сервера...'

OVERRIDES['ADMIN_SERVER_DELETE_CONFIRM'] = 'Вы уверены, что хотите удалить этот сервер?'

OVERRIDES['ADMIN_SERVER_EDIT_KEY_PROMPT'] = 'Введите новый ключ для сервера:'

OVERRIDES['ADMIN_SERVER_EDIT_MAX_CLIENTS_PROMPT'] = 'Введите максимальное количество клиентов для сервера:'

OVERRIDES['ADMIN_SERVER_EDIT_URL_PROMPT'] = 'Введите новый URL сервера:'

OVERRIDES['ADMIN_SERVER_FLAG_PROMPT'] = 'Выберите флаг для сервера:'

OVERRIDES['ADMIN_SERVER_FLAG_PROMPT_EDIT'] = 'Выберите новый флаг для сервера:'

OVERRIDES['ADMIN_SERVER_FLAG_TOO_LONG'] = '❌ Флаг слишком длинный.'

OVERRIDES['ADMIN_SERVER_FLAG_UPDATED'] = '✅ Флаг сервера обновлен.'

OVERRIDES['ADMIN_SERVER_KEY_PROMPT'] = 'Введите ключ доступа:'

OVERRIDES['ADMIN_SERVER_KEY_UPDATED'] = '✅ Ключ сервера обновлен.'

OVERRIDES['ADMIN_SERVER_MAX_CLIENTS_UPDATED'] = '✅ Макс. количество клиентов обновлено.'

OVERRIDES['ADMIN_SERVER_MAX_CLIENTS_WARNING'] = '⚠️ Внимание: на сервере уже больше клиентов, чем новый лимит.'

OVERRIDES['ADMIN_SERVER_NAME_PROMPT'] = 'Введите название сервера:'

OVERRIDES['ADMIN_SERVER_RENAMED'] = '✅ Сервер успешно переименован.'

OVERRIDES['ADMIN_SERVER_RENAME_PROMPT'] = 'Введите новое имя сервера:'

OVERRIDES['ADMIN_SERVER_STATE_DISABLED'] = '🔴 Отключен'

OVERRIDES['ADMIN_SERVER_STATE_ENABLED'] = '🟢 Включен'

OVERRIDES['ADMIN_SERVER_TOGGLE_DISABLE_CONFIRM'] = 'Вы уверены, что хотите отключить сервер?'

OVERRIDES['ADMIN_SERVER_TOGGLE_ENABLE_CONFIRM'] = 'Вы уверены, что хотите включить сервер?'

OVERRIDES['ADMIN_SERVER_TOGGLE_SUCCESS'] = '✅ Статус сервера изменен.'

OVERRIDES['ADMIN_SERVER_URL_UPDATED'] = '✅ URL сервера обновлен.'

OVERRIDES['ADMIN_SUBSCRIPTION_HEADER'] = '💳 Управление подпиской пользователя'

OVERRIDES['ADMIN_SUB_CHANGE_FAILED'] = '❌ Ошибка при изменении подписки.'

OVERRIDES['ADMIN_SUB_CHANGE_TARIFF_HEADER'] = 'Выберите новый тариф:'

OVERRIDES['ADMIN_SUB_CONFIRM_EXTEND'] = 'Вы уверены, что хотите продлить подписку?'

OVERRIDES['ADMIN_SUB_CONFIRM_GRANT'] = 'Вы уверены, что хотите выдать подписку?'

OVERRIDES['ADMIN_SUB_CONFIRM_REDUCE'] = 'Вы уверены, что хотите сократить подписку?'

OVERRIDES['ADMIN_SUB_CONFIRM_TARIFF'] = 'Вы уверены, что хотите сменить тариф?'

OVERRIDES['ADMIN_SUB_DOWNGRADE_BLOCKED'] = '❌ Нельзя понизить тариф (превышен лимит устройств).'

OVERRIDES['ADMIN_SUB_EXTEND_FAILED'] = '❌ Не удалось продлить подписку.'

OVERRIDES['ADMIN_SUB_EXTEND_HEADER'] = 'Продление подписки'

OVERRIDES['ADMIN_SUB_EXTEND_PROMPT'] = 'На сколько дней продлить подписку?'

OVERRIDES['ADMIN_SUB_EXTEND_SUCCESS'] = '✅ Подписка успешно продлена.'

OVERRIDES['ADMIN_SUB_GRANT_CUSTOM_PROMPT'] = 'Введите количество дней:'

OVERRIDES['ADMIN_SUB_GRANT_DAYS_HEADER'] = 'На какой срок выдать подписку?'

OVERRIDES['ADMIN_SUB_GRANT_FAILED'] = '❌ Ошибка при выдаче подписки.'

OVERRIDES['ADMIN_SUB_GRANT_HEADER'] = 'Выдача новой подписки'

OVERRIDES['ADMIN_SUB_GRANT_SUCCESS'] = '✅ Подписка успешно выдана.'

OVERRIDES['ADMIN_SUB_GROUP_NOT_FOUND'] = '❌ Группа тарифов не найдена.'

OVERRIDES['ADMIN_SUB_NO_SUBSCRIPTION'] = 'У пользователя нет активной подписки.'

OVERRIDES['ADMIN_SUB_PERMANENT_LABEL'] = 'Навсегда'

OVERRIDES['ADMIN_SUB_REDUCED'] = '✅ Подписка успешно сокращена.'

OVERRIDES['ADMIN_SUB_REDUCE_FAILED'] = '❌ Не удалось сократить подписку.'

OVERRIDES['ADMIN_SUB_REDUCE_PROMPT'] = 'На сколько дней сократить подписку?'

OVERRIDES['ADMIN_SUB_STATUS_ACTIVE'] = 'Активна'

OVERRIDES['ADMIN_SUB_STATUS_INACTIVE'] = 'Неактивна'

OVERRIDES['ADMIN_SUB_STATUS_NONE'] = 'Отсутствует'

OVERRIDES['ADMIN_SUB_TARIFF_ALREADY_SELECTED'] = '❌ Этот тариф уже активен.'

OVERRIDES['ADMIN_SUB_TARIFF_CHANGED'] = '✅ Тариф успешно изменен.'

OVERRIDES['ADMIN_TARIFF_EDIT_RUB_PROMPT'] = 'Введите новую цену в рублях:'

OVERRIDES['ADMIN_TARIFF_EDIT_RUB_SUCCESS'] = '✅ Цена тарифа успешно обновлена.'

OVERRIDES['ADMIN_TARIFF_TOGGLE_BLOCKED_PENDING'] = '❌ Нельзя отключить тариф, есть незавершенные платежи.'

OVERRIDES['ADMIN_TARIFF_TOGGLE_SUCCESS_DISABLED'] = '✅ Тариф отключен.'

OVERRIDES['ADMIN_TARIFF_TOGGLE_SUCCESS_ENABLED'] = '✅ Тариф включен.'

OVERRIDES['ADMIN_UNBAN_CONFIRM'] = 'Вы уверены, что хотите разбанить пользователя?'

OVERRIDES['ADMIN_USER_DEVICES_EMPTY'] = 'Нет подключенных устройств.'

OVERRIDES['ADMIN_USER_DEVICES_HEADER'] = '📱 Устройства пользователя'

OVERRIDES['ALERT_CRITICAL_BOT_ERROR'] = '❌ Произошла критическая ошибка бота.'

OVERRIDES['ERROR_SERVER_DUPLICATE_URL'] = '❌ Сервер с таким URL уже существует.'

OVERRIDES['PAYMENT_STATUS_NAMES'] = {'completed': 'Выполнен', 'cancelled': 'Отменен', 'failed': 'Ошибка', 'refunded': 'Возврат', 'requires_manual_review': 'Ручная проверка', 'pending': 'Ожидание', 'paid_processing': 'Обработка'}

OVERRIDES['ADMIN_PAYMENT_USER_WITH_USERNAME'] = '@{username}'

OVERRIDES['ADMIN_PAYMENT_USER_WITH_ID'] = 'ID: {user_id}'

