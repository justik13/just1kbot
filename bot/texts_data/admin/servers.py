"""Domain copy catalogue for: admin/servers.py"""

TEXTS = {
    'ADMIN_SERVER_ADDED': """🛠 Админка › 🖥 <b>Серверы</b>

✅ <b>Сервер успешно добавлен!</b>

<b>Название:</b> {flag} {name}
<b>Протокол:</b> {protocol}
<b>Лимит клиентов:</b> {max_clients}
<b>API URL:</b> <code>{api_url}</code>""",
    'ADMIN_SERVER_CHECKING': '🔄 Проверка сервера...',
    'ADMIN_SERVER_DELETED_BADGE': 'Удалено',
    'ADMIN_SERVER_DELETE_BLOCKED_PENDING': """⚠️ <b>Удаление сервера отменено:</b>

На сервере присутствуют незавершенные операции создания или фоновые обновления. Дождитесь их завершения.""",
    'ADMIN_SERVER_DELETE_CONFIRM': """⚠️ <b>Удаление сервера</b>

Вы уверены, что хотите удалить сервер {flag} <b>{name}</b>?
📱 Активных устройств на сервере: <b>{profiles_count}</b>""",
    'ADMIN_SERVER_EDIT_KEY_BLOCKED': """❌ Нельзя изменить ключ API сервера, пока на нём есть устройства или активные операции.

• Связанных устройств: <b>{devices_count}</b>
• Операций в обработке: <b>{operations_count}</b>

Для подключения нового узла добавьте новый сервер в панели управления.""",
    'ADMIN_SERVER_EDIT_KEY_PROMPT': 'Введите новый ключ для сервера:',
    'ADMIN_SERVER_EDIT_MAX_CLIENTS_PROMPT': 'Введите максимальное количество клиентов для сервера:',
    'ADMIN_SERVER_EDIT_URL_BLOCKED': """❌ Нельзя изменить адрес сервера, пока на нём есть устройства или активные операции.

• Связанных устройств: <b>{devices_count}</b>
• Операций в обработке: <b>{operations_count}</b>

Для подключения нового узла добавьте новый сервер в панели управления.""",
    'ADMIN_SERVER_EDIT_URL_PROMPT': 'Введите новый URL сервера:',
    'ADMIN_SERVER_FLAG_PROMPT': 'Выберите флаг для сервера:',
    'ADMIN_SERVER_FLAG_PROMPT_EDIT': """Текущий флаг: {current_flag}
Выберите новый флаг для сервера:""",
    'ADMIN_SERVER_FLAG_TOO_LONG': '❌ Флаг слишком длинный.',
    'ADMIN_SERVER_FLAG_UPDATED': '✅ Флаг сервера обновлен на {flag}.',
    'ADMIN_SERVER_KEY_PROMPT': 'Введите ключ доступа:',
    'ADMIN_SERVER_KEY_UPDATED': '✅ Ключ сервера обновлен.',
    'ADMIN_SERVER_MAX_CLIENTS_UPDATED': '✅ Макс. количество клиентов обновлено: <b>{max_clients}</b>.',
    'ADMIN_SERVER_MAX_CLIENTS_WARNING': '⚠️ Внимание: на сервере уже <b>{current}</b> клиентов, что больше нового лимита (<b>{new}</b>).',
    'ADMIN_SERVER_NAME_PROMPT': 'Введите название сервера:',
    'ADMIN_SERVER_PING_CHECKING': '⚡ Проверка связи...',
    'ADMIN_SERVER_PING_ERROR': '🔴 <b>API недоступен / ошибка соединения</b> ({error})',
    'ADMIN_SERVER_PING_NO_HEALTHZ': '🔴 <b>API сервер НЕ отвечает на /healthz!</b>',
    'ADMIN_SERVER_PING_ONLINE': '🟢 <b>API сервер доступен</b> (Latency: {latency_ms} ms)',
    'ADMIN_SERVER_RENAMED': '✅ Сервер успешно переименован в <b>{name}</b>.',
    'ADMIN_SERVER_RENAME_PROMPT': 'Введите новое имя сервера:',
    'ADMIN_SERVER_STATE_DISABLED': '🔴 Отключен',
    'ADMIN_SERVER_STATE_ENABLED': '🟢 Включен',
    'ADMIN_SERVER_TOGGLE_DISABLE_CONFIRM': 'Вы уверены, что хотите отключить сервер {flag} <b>{name}</b>?',
    'ADMIN_SERVER_TOGGLE_ENABLE_CONFIRM': 'Вы уверены, что хотите включить сервер {flag} <b>{name}</b>?',
    'ADMIN_SERVER_TOGGLE_SUCCESS': '✅ Статус сервера изменен: <b>{status}</b>',
    'ADMIN_SERVER_URL_PROMPT': '🔗 Введите API URL сервера (например: https://api.example.com:8443):',
    'ADMIN_SERVER_URL_UPDATED': '✅ API URL сервера обновлен на <code>{api_url}</code>.',
    'BTN_ADMIN_SERVER_PING': '⚡ Проверить доступность (Ping)',
    'ERROR_SERVER_API_INFO_FAILED': """❌ <b>Ошибка подключения к API!</b>

Сервер отвечает на healthcheck, но не удалось получить информацию.

Возможно, неверный API ключ.""",
    'ERROR_SERVER_BUSY': """⏳ <b>Сервер сейчас обрабатывает другой запрос.</b>
━━━━━━━━━━━━━━━━

Подождите несколько секунд и попробуйте снова.""",
    'ERROR_SERVER_DISABLED': """⚠️ <b>Этот сервер временно отключён.</b>
━━━━━━━━━━━━━━━━

Администратор приостановил работу сервера.

Выберите другую локацию или попробуйте позже.""",
    'ERROR_SERVER_DUPLICATE_URL': '❌ Сервер с API URL <code>{api_url}</code> уже зарегистрирован в системе.',
    'ERROR_SERVER_FULL': """⚠️ <b>На этом сервере закончились свободные слоты.</b>
━━━━━━━━━━━━━━━━

Попробуйте выбрать другую локацию.

Если все серверы заполнены, напишите в поддержку.""",
    'ERROR_SERVER_ID_REQUIRED': 'Ошибка: ID сервера не указан',
    'ERROR_SERVER_NOT_FOUND': '❌ Сервер не найден',
    'ERROR_SERVER_SLOTS_UNKNOWN': """⚠️ <b>Не удалось проверить свободные слоты на сервере.</b>
━━━━━━━━━━━━━━━━

Попробуйте другую локацию или повторите через минуту.""",
    'ERROR_SERVER_UNAVAILABLE': """⚠️ <b>Выбранный сервер временно недоступен.</b>
━━━━━━━━━━━━━━━━

Попробуйте другую локацию или обратитесь в поддержку.""",
    'ERROR_SERVER_UNAVAILABLE_GENERIC': '⚠️ Сервер недоступен. Попробуйте позже.',
    'ERROR_SERVER_UNREACHABLE': """❌ <b>Сервер недоступен!</b>

Не удалось подключиться к API по указанному адресу.

Возможные причины:
• Неверный URL или API ключ
• Сервер выключен или недоступен
• Файрвол блокирует соединение
• API-сервис не запущен

Проверьте данные и попробуйте снова.""",
    'RUNTIME_BOT_HANDLERS_ADMIN_SERVERS_ADD_ROUTES_L254_1': 'неизвестно',
    'RUNTIME_BOT_HANDLERS_ADMIN_SERVERS_CARD_ROUTES_L99_1': '🌍',
    'RUNTIME_BOT_HANDLERS_ADMIN_SERVERS_COMMON_L48_1': """🛠 Админка › 🌍 <b>Серверы</b>
(стр. {value_0}/{value_1}) · Всего: {value_2}
""",
    'RUNTIME_BOT_HANDLERS_ADMIN_SERVERS_COMMON_L53_1': """<i>Серверов пока нет</i>
""",
    'RUNTIME_BOT_HANDLERS_ADMIN_SERVERS_COMMON_L56_1': '🌍',
    'RUNTIME_BOT_HANDLERS_ADMIN_SERVERS_COMMON_L57_1': '🟢',
    'RUNTIME_BOT_HANDLERS_ADMIN_SERVERS_COMMON_L59_1': '{value_0} {value_1} {value_2} · {value_3}',
    'RUNTIME_BOT_HANDLERS_ADMIN_SERVERS_DELETE_ROUTES_L92_1': '🌍',
    'RUNTIME_BOT_HANDLERS_ADMIN_SERVERS_EDIT_ROUTES_L199_1': '🌍',
    'RUNTIME_BOT_HANDLERS_ADMIN_SERVERS_EDIT_ROUTES_L476_1': 'неизвестно',
    'RUNTIME_BOT_KEYBOARDS_ADMIN_SERVERS_L34_1': '🔴 Выключить',
    'RUNTIME_BOT_KEYBOARDS_ADMIN_SERVERS_L36_1': '🟢 Включить',
    'UI_BOT_HANDLERS_ADMIN_SERVERS_ADD_ROUTES_L114_1': '⚠️ Флаг слишком длинный (макс. 10 символов).',
    'UI_BOT_HANDLERS_ADMIN_SERVERS_ADD_ROUTES_L157_1': """⚠️ <b>URL запрещён правилами безопасности</b>
Использование приватных IP-адресов, loopback и metadata endpoints запрещено.""",
    'UI_BOT_HANDLERS_ADMIN_SERVERS_CARD_ROUTES_L142_1': 'Некорректный запрос',
    'UI_BOT_HANDLERS_ADMIN_SERVERS_CARD_ROUTES_L43_1': 'Некорректный запрос',
    'UI_BOT_HANDLERS_ADMIN_SERVERS_CARD_ROUTES_L80_1': 'Некорректный запрос',
    'UI_BOT_HANDLERS_ADMIN_SERVERS_COMMON_L67_1': '⬅️',
    'UI_BOT_HANDLERS_ADMIN_SERVERS_COMMON_L72_1': '➡️',
    'UI_BOT_HANDLERS_ADMIN_SERVERS_COMMON_L75_1': '➕ Добавить сервер',
    'UI_BOT_HANDLERS_ADMIN_SERVERS_COMMON_L76_1': '← В админку',
    'UI_BOT_HANDLERS_ADMIN_SERVERS_DELETE_ROUTES_L125_1': '⚠️ Сессия подтверждения истекла',
    'UI_BOT_HANDLERS_ADMIN_SERVERS_DELETE_ROUTES_L134_1': 'Некорректный запрос',
    'UI_BOT_HANDLERS_ADMIN_SERVERS_DELETE_ROUTES_L177_1': 'На сервере есть незавершённое создание клиента. Дождитесь reconciliation и повторите удаление.',
    'UI_BOT_HANDLERS_ADMIN_SERVERS_DELETE_ROUTES_L216_1': '✅ Сервер {value_0} удалён ({value_1} устр.)',
    'UI_BOT_HANDLERS_ADMIN_SERVERS_DELETE_ROUTES_L68_1': 'Некорректный запрос',
    'UI_BOT_HANDLERS_ADMIN_SERVERS_EDIT_ROUTES_L182_1': 'Некорректный запрос',
    'UI_BOT_HANDLERS_ADMIN_SERVERS_EDIT_ROUTES_L324_1': 'Некорректный запрос',
    'UI_BOT_HANDLERS_ADMIN_SERVERS_EDIT_ROUTES_L50_1': 'Некорректный запрос',
    'UI_BOT_HANDLERS_ADMIN_SERVERS_EDIT_ROUTES_L537_1': 'Некорректный запрос',
    'UI_BOT_HANDLERS_ADMIN_SERVERS_EDIT_ROUTES_L707_1': 'Некорректный запрос',
    'UI_BOT_HANDLERS_ADMIN_SERVERS_LIST_ROUTES_L51_1': 'Некорректный запрос',
    'UI_BOT_KEYBOARDS_ADMIN_SERVERS_L12_1': '✏️ Изменить имя',
    'UI_BOT_KEYBOARDS_ADMIN_SERVERS_L16_1': '🏳 Изменить флаг',
    'UI_BOT_KEYBOARDS_ADMIN_SERVERS_L20_1': '🔗 Изменить URL',
    'UI_BOT_KEYBOARDS_ADMIN_SERVERS_L24_1': '🔑 Изменить ключ',
    'UI_BOT_KEYBOARDS_ADMIN_SERVERS_L28_1': '👥 Изменить лимит',
    'UI_BOT_KEYBOARDS_ADMIN_SERVERS_L42_1': '🗑 Удалить сервер',
    'UI_BOT_KEYBOARDS_ADMIN_SERVERS_L46_1': '← К списку серверов',
    'UI_BOT_KEYBOARDS_ADMIN_SERVERS_L60_1': '✅ Да, удалить полностью',
    'UI_BOT_KEYBOARDS_ADMIN_SERVERS_L64_1': '❌ Отмена',
    'UI_BOT_KEYBOARDS_ADMIN_SERVERS_L68_1': '← К списку серверов',
    'UI_BOT_KEYBOARDS_ADMIN_SERVERS_L72_1': '🏠 В главное меню',
}
