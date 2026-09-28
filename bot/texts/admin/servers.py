"""Domain texts for admin/servers.py."""
from __future__ import annotations

ADMIN_BTN_ADD_SERVER = "➕ Добавить сервер"
ADMIN_SERVER_SELECT_PROTO_PROMPT = """🛠 Админка › ➕ <b>Добавление сервера</b>

Выберите протокол узла:"""
ADMIN_SERVER_BTN_PROTO_AWG = "🛡 AmneziaWG"
ADMIN_SERVER_BTN_PROTO_XRAY = "⚡ Xray (Белый Интернет)"

PROTOCOL_XRAY_ORIGIN = "Xray (Origin)"

ADMIN_BTN_BACK_TO_SERVERS = "← К списку серверов"

ADMIN_SERVERS_COMMON = """🛠 Админка › 🌍 <b>Серверы</b>
(стр. {v0}/{v1}) · Всего: {v2}
"""

ADMIN_SERVERS_EMPTY = """<i>Серверов пока нет</i>
"""

ADMIN_SERVER_API_URL = "• API URL: <code>{api_url}</code>\n"
ADMIN_AUDIT_LOG_DETAILS_DELETE_SERVER = "{server_name}: {count} profiles deleted"
ADMIN_AUDIT_LOG_DETAILS_EDIT_SERVER = "{field} -> {value}"
ADMIN_AUDIT_LOG_DETAILS_EDIT_SERVER_REDACTED = "{field} -> [REDACTED]"

ADMIN_SERVER_ADDED = """🛠 Админка › 🖥 <b>Серверы</b>

✅ <b>Сервер успешно добавлен!</b>

<b>Название:</b> {flag} {name}
<b>Протокол:</b> {protocol}
<b>Лимит клиентов:</b> {max_clients}
<b>API URL:</b> <code>{api_url}</code>"""

ADMIN_SERVER_ADDED_NO_RELAYS_WARNING = (
    "\n\n⚠️ <b>Внимание:</b> на сервере нет подключенных Relay!\n"
    "Шлюз переведен в режим ожидания и <b>не будет выдавать подписки</b>, "
    "пока вы не привяжете хотя бы один Relay через <code>just1knode relay add</code>."
)

ADMIN_SERVER_BTN_DELETE = "🗑 Удалить сервер"
ADMIN_SERVER_BTN_MIGRATE = "📦 Мигрировать подписчиков"
ADMIN_SERVER_BTN_RELAYS = "🌐 Статус Relay-узлов"
ADMIN_SERVER_RELAYS_CHECKING = "⚡ Опрос Relay-узлов..."
ADMIN_SERVER_RELAYS_HEADER = """{header}🌐 <b>Статус Relay-узлов для {flag} {server_name}</b>

• {flag} <b>Origin (Прямой выход):</b> {origin_status_badge}
"""
ADMIN_SERVER_RELAYS_EMPTY = """\n<i>На этом сервере нет подключенных Relay-узлов. Доступен прямой выход в зону RU (зарубежный трафик блокируется).</i>\n"""
ADMIN_SERVER_RELAYS_ROW = "• {flag} <b>{name}</b> (<code>{ip}:{port}</code>)\n  Статус: {status_badge}\n"
ADMIN_SERVER_RELAYS_API_ERROR = "\n🔴 <b>Ошибка проверки узлов:</b> <code>{error}</code>\n"
ADMIN_SERVER_RELAYS_ORIGIN_UNAVAILABLE = " (API недоступен)"
ADMIN_SERVER_RELAYS_ERR_FETCH = "Не удалось получить статус релеев"
ADMIN_SERVER_RELAYS_FALLBACK_NAME = "Релей"
ADMIN_SERVER_RELAYS_STATUS_ONLINE_RTT = "🟢 Онлайн (RTT: {rtt_ms} ms)"
ADMIN_SERVER_RELAYS_STATUS_ONLINE = "🟢 Онлайн"
ADMIN_SERVER_RELAYS_STATUS_OFFLINE_ERR = "🔴 Офлайн ({error})"
ADMIN_SERVER_RELAYS_STATUS_OFFLINE = "🔴 Офлайн"
ADMIN_SERVER_BTN_REFRESH_RELAYS = "🔄 Обновить статус узлов"
ADMIN_SERVER_BTN_BACK_TO_SERVER = "« Назад к серверу"

ADMIN_SERVER_BTN_CHANGE_LIMIT = "👥 Изменить лимит"

ADMIN_SERVER_BTN_CHANGE_KEY = "🔑 Изменить ключ"

ADMIN_SERVER_CHECKING = "🔄 Проверка сервера..."

ADMIN_SERVER_CONFIRMATION_EXPIRED = "⚠️ Сессия подтверждения истекла"

ADMIN_SERVER_DELETED_BADGE = "Удалено"

ADMIN_SERVER_DELETE_BLOCKED_PENDING = """⚠️ <b>Удаление сервера отменено:</b>

На сервере присутствуют незавершенные операции создания или фоновые обновления. Дождитесь их завершения."""

ADMIN_SERVER_DELETE_BLOCKED_PENDING_CLIENT = "На сервере есть незавершённое создание клиента. Дождитесь reconciliation и повторите удаление."

ADMIN_SERVER_DELETE_BLOCKED_ACTIVE_WL_ALERT = "Удаление заблокировано: к серверу привязано {count} активных подписок White Internet. Сначала отключите их."

ADMIN_SERVER_DELETE_BLOCKED_ACTIVE_WL_TEXT = """❌ <b>Удаление сервера невозможно</b>

К серверу <b>{name}</b> привязано активных подписок Белый Интернет: <b>{count}</b>.

Перед удалением сервера необходимо отключить или перенести активные подписки."""

ADMIN_SERVER_DELETE_CONFIRM = """⚠️ <b>Удаление сервера</b>

Вы уверены, что хотите удалить сервер {flag} <b>{name}</b>?
📱 Активных устройств на сервере: <b>{profiles_count}</b>"""

ADMIN_SERVER_DELETE_WL_SUBS_NOTE = "\n\n🌐 <i>К серверу также привязано подписок Белый Интернет: {count}</i>"

ADMIN_SERVER_DELETE_SUCCESS_NOTICE = "✅ Сервер {v0} удалён ({v1} устр.)"

ADMIN_SERVER_EDIT_KEY_BLOCKED = """❌ Нельзя изменить ключ API сервера, пока на нём есть устройства или активные операции.

• Связанных устройств: <b>{devices_count}</b>
• Операций в обработке: <b>{operations_count}</b>

Для подключения нового узла добавьте новый сервер в панели управления."""

ADMIN_SERVER_EDIT_KEY_PROMPT = "Введите новый ключ для сервера:"

ADMIN_SERVER_EDIT_MAX_CLIENTS_PROMPT = "Введите максимальное количество клиентов для сервера:"

ADMIN_SERVER_BTN_CONFIRM_DELETE = "✅ Да, удалить полностью"

ADMIN_SERVER_EDIT_URL_BLOCKED = """❌ Нельзя изменить адрес сервера, пока на нём есть устройства или активные операции.

• Связанных устройств: <b>{devices_count}</b>
• Операций в обработке: <b>{operations_count}</b>

Для подключения нового узла добавьте новый сервер в панели управления."""

ADMIN_SERVER_EDIT_URL_PROMPT = "Введите новый URL сервера:"

ADMIN_SERVER_FLAG_PROMPT = "Выберите флаг для сервера:"

ADMIN_SERVER_FLAG_PROMPT_EDIT = """Текущий флаг: {current_flag}
Выберите новый флаг для сервера:"""

ADMIN_SERVER_FLAG_TOO_LONG = "❌ Флаг слишком длинный."

ADMIN_SERVER_FLAG_UPDATED = "✅ Флаг сервера обновлен на {flag}."

ADMIN_SERVER_KEY_PROMPT = "Введите ключ доступа:"

ADMIN_SERVER_KEY_UPDATED = "✅ Ключ сервера обновлен."

ADMIN_SERVER_LIST_ROW_FORMAT = "{v0} {v1} {v2} · {v3}"

ADMIN_SERVER_MAX_CLIENTS_UPDATED = "✅ Макс. количество клиентов обновлено: <b>{max_clients}</b>."

ADMIN_SERVER_MAX_CLIENTS_WARNING = "⚠️ Внимание: на сервере уже <b>{current}</b> клиентов, что больше нового лимита (<b>{new}</b>)."

ADMIN_SERVER_NAME_PROMPT = "Введите название сервера:"

ADMIN_SERVER_PING_CHECKING = "⚡ Проверка связи..."

ADMIN_SERVER_PING_ERROR = "🔴 <b>API недоступен / ошибка соединения</b> ({error})"

ADMIN_SERVER_PING_NO_HEALTHZ = "🔴 <b>API сервер НЕ отвечает на /healthz!</b>"

ADMIN_SERVER_PING_ONLINE = "🟢 <b>API сервер доступен</b> (Latency: {latency_ms} ms)"

ADMIN_SERVER_RENAMED = "✅ Сервер успешно переименован в <b>{name}</b>."

ADMIN_SERVER_RENAME_PROMPT = "Введите новое имя сервера:"

ADMIN_SERVER_SLOTS_BREAKDOWN_NOTE = " <i>(на узле: {cached_used}, в БД: {db_used})</i>"

ADMIN_SERVER_SLOTS_VALUE = "<b>{used_clients} / {max_clients}</b>"

ADMIN_SERVER_BTN_PEERS = "👥 Пиры на узле ({used}/{total})"
ADMIN_SERVER_BTN_PEERS_NO_COUNT = "👥 Пиры на узле"
ADMIN_SERVER_BTN_SERVER_USERS = "👥 Все пользователи этого сервера"
ADMIN_SERVER_BTN_BROADCAST = "📢 Рассылка пользователям"
ADMIN_SERVER_PEERS_AWG_ONLY = "Управление пирами доступно только для серверов AmneziaWG."
ADMIN_SERVER_PEERS_HEADER = """{header}👥 <b>Пиры сервера {flag} {server_name}</b>{status_banner}
• На узле: <b>{live_peers}</b> (Бот: {bot_peers}, Внешние: {external_peers}){deleting_note}{missing_note}{pending_note}
Стр. {page}/{total_pages}:

"""
ADMIN_SERVER_PEERS_HEADER_DEGRADED = """{header}👥 <b>Пиры сервера {flag} {server_name}</b>{status_banner}
• На узле: <b>?</b> (API недоступен)
• Профилей бота в БД: <b>{db_profiles_count}</b>
Стр. {page}/{total_pages}:

"""
ADMIN_SERVER_PEERS_API_ERROR_BANNER = "\n⚠️ <i>API узла недоступен (показаны данные из базы бота)</i>"
ADMIN_SERVER_PEERS_DELETING_NOTE = " | ⏳ Удаляются: <b>{deleting_count}</b>"
ADMIN_SERVER_PEERS_MISSING_NOTE = " | ⚠️ Не на узле: <b>{missing_count}</b>"
ADMIN_SERVER_PEERS_PENDING_NOTE = " | ⏳ В процессе: <b>{pending_count}</b>"
ADMIN_SERVER_PEERS_EMPTY = "<i>На этом сервере пока нет зарегистрированных пиров.</i>\n"
ADMIN_SERVER_PEERS_BREADCRUMB = "Пиры"
ADMIN_SERVER_PEERS_FALLBACK_DEVICE = "Внешнее устройство"
ADMIN_SERVER_PEER_BOT_ROW = "• 🟢 <b>{username}</b> ({first_name}) — 📱 \"{device_name}\"\n  IP: <code>{ip}</code> | {status_online}\n"
ADMIN_SERVER_PEER_UNKNOWN_ROW = "• ⚪ <b>{username}</b> ({first_name}) — 📱 \"{device_name}\"\n  IP: <code>{ip}</code> | ⚠️ Состояние на узле неизвестно\n"
ADMIN_SERVER_PEER_EXTERNAL_ROW = "• 👤 <b>[Внешний пир]</b> \"{device_name}\"\n  IP: <code>{ip}</code> | AmneziaWG Key: <code>{key}</code>\n"
ADMIN_SERVER_PEER_MISSING_ROW = "• ⚠️ <b>[Не на узле]</b> {username} — 📱 \"{device_name}\"\n  IP: <code>{ip}</code>\n"
ADMIN_SERVER_PEER_PENDING_ROW = "• {badge} {username} — 📱 \"{device_name}\"\n  Статус: <code>{status}</code> | IP: <code>{ip}</code>\n"
ADMIN_SERVER_PEER_BADGE_PENDING = "⏳ <b>[Создаётся]</b>"
ADMIN_SERVER_PEER_BADGE_CLEANUP = "🧹 <b>[Очистка сбоя]</b>"
ADMIN_SERVER_PEER_BADGE_FAILED = "❌ <b>[Сбой создания]</b>"
ADMIN_SERVER_PEER_BADGE_DEFAULT = "⏳ <b>[{status}]</b>"
ADMIN_SERVER_PEER_ICON_PENDING = "⏳"
ADMIN_SERVER_PEER_ICON_CLEANUP = "🧹"
ADMIN_SERVER_PEER_ICON_FAILED = "❌"
PEER_ONLINE_THRESHOLD_SECONDS = 180
ADMIN_SERVER_PEER_BTN_BOT = "🟢 {username} • {device_name}"
ADMIN_SERVER_PEER_BTN_PENDING = "{icon} {username} • {device_name}"
ADMIN_SERVER_PEER_BTN_EXTERNAL = "👤 Внешний: {device_name}"
ADMIN_SERVER_PEER_STATUS_ONLINE = "🟢 В сети"
ADMIN_SERVER_PEER_STATUS_OFFLINE = "⚪ Офлайн"
ADMIN_SERVER_PEER_STATUS_DELETING = "⏳ Удаляется"
ADMIN_SERVER_PEER_INFO_ALERT = "👤 Внешний пир:\nНаходится на узле AmneziaVPN, но не привязан к профилям Telegram-бота (не отслеживается ботом)."
ADMIN_SERVER_BTN_BACK_TO_CARD = "« Назад в карточку сервера"
ADMIN_SERVER_BTN_RESET_FILTER = "❌ Сбросить фильтр"

ADMIN_SERVER_STATE_DISABLED = "🔴 Отключен"

ADMIN_SERVER_STATE_ENABLED = "🟢 Включен"

ADMIN_SERVER_BTN_CHANGE_URL = "🔗 Изменить URL"

ADMIN_SERVER_BTN_CHANGE_FLAG = "🏳 Изменить флаг"

ADMIN_SERVER_TOGGLE_DISABLE_CONFIRM = "Вы уверены, что хотите отключить сервер {flag} <b>{name}</b>?"

ADMIN_SERVER_TOGGLE_ENABLE_CONFIRM = "Вы уверены, что хотите включить сервер {flag} <b>{name}</b>?"

ADMIN_SERVER_TOGGLE_SUCCESS = "✅ Статус сервера изменен: <b>{status}</b>"

ADMIN_SERVER_URL_FORBIDDEN = """⚠️ <b>URL запрещён правилами безопасности</b>
Использование приватных IP-адресов, loopback и metadata endpoints запрещено."""

ADMIN_SERVER_URL_PROMPT = "🔗 Введите API URL сервера (например: https://api.example.com:8443):"

ADMIN_SERVER_URL_UPDATED = "✅ API URL сервера обновлен на <code>{api_url}</code>."

STATUS_ACTIVE_ICON = "🟢"

BTN_DISABLE_SERVER = "🔴 Выключить"

BTN_ENABLE_SERVER_CARD = "🟢 Включить"

LABEL_UNKNOWN_LOWER = "неизвестно"

ADMIN_SERVER_BTN_FORCE_PURGE = "💥 Аварийное списание (Force Purge)"
ADMIN_SERVER_MIGRATE_CONFIRM_PROMPT = """⚠️ <b>Подтверждение миграции</b>

Перенести <b>{source_count}</b> подписчиков:
С сервера: {source_flag} <b>{source_name}</b>
На сервер: {target_flag} <b>{target_name}</b> (свободно мест: {free_slots})

Исходный сервер будет переведён в статус вывода из эксплуатации (DECOMMISSIONING)."""
ADMIN_SERVER_MIGRATE_SUCCESS = "Миграция успешно запущена: {count} подписчиков перенесены."
ADMIN_SERVER_MIGRATE_FAILED = "Ошибка миграции: {error}"
ADMIN_SERVER_FORCE_PURGE_CONFIRM = """⚠️ <b>ВНИМАНИЕ: Аварийное списание узла</b>

Сервер: <b>{name}</b>
Подписок на узле: <b>{count}</b>

Все подписки узла будут немедленно переведены в статус архивных (DISABLED / SYNCED_INACTIVE), а задачи очистки отменены.

<b>Используйте ТОЛЬКО если узел безвозвратно уничтожен!</b>"""
ADMIN_SERVER_FORCE_PURGE_SUCCESS = "Сервер {name} списан ({count} подписок)."
ADMIN_SERVER_MIGRATE_NO_SUBS = "На сервере нет активных подписчиков для миграции."
ADMIN_SERVER_MIGRATE_NO_TARGETS = "Нет доступных серверов Xray Origin для миграции."
ADMIN_SERVER_MIGRATE_SELECT_TARGET_PROMPT = """📦 <b>Миграция подписчиков</b>

Исходный сервер: {source_flag} <b>{source_name}</b>
Подписчиков для переноса: <b>{count}</b>

Выберите целевой сервер Origin:"""

ADMIN_SERVER_BTN_INCY = "🎨 Оформление INCY"
ADMIN_SERVER_INCY_CARD = """🛠 Админка › 🖥 <b>{flag} {name}</b> › 🎨 <b>Оформление INCY</b>

Настройки внешнего вида подписки для приложения INCY:

• <b>Имя профиля:</b> <code>{title}</code>
• <b>Подзаголовок:</b> <code>{description}</code> <i>(не отображается в проверенных мобильных версиях INCY)</i>
• <b>Баннер (объявление):</b> <code>{announce}</code>
• <b>Ссылка баннера:</b> <code>{announce_url}</code>
• <b>Шлюз РФ в клиенте:</b> <code>{origin_name}</code>
• <b>Бейдж шлюза РФ:</b> <code>{origin_badge}</code>
• <b>Статус шлюза РФ:</b> <code>{origin_status}</code>

<i>Для изменения выберите нужный пункт ниже:</i>"""

ADMIN_SERVER_INCY_PROMPT_TITLE = """🎨 <b>Изменение имени профиля в INCY</b>

Текущее значение: <code>{current}</code>

Отправьте новое название профиля (до 25 символов) или дефис «-» для сброса:"""

ADMIN_SERVER_INCY_PROMPT_DESC = """🎨 <b>Изменение подзаголовка профиля в INCY</b>

Текущее значение: <code>{current}</code>

💡 <i>Поддерживаются плейсхолдеры:</i>
• <code>{{devices}}</code> или <code>{{limit}}</code> — лимит устройств подписки
• <code>{{active}}</code> — число активных устройств

⚠️ <i>Примечание: в некоторых мобильных клиентах подзаголовок отображается в карточке подписки, а в INCY — «Объявление».</i>

Отправьте подзаголовок (до 100 символов) или дефис «-» для отключения:"""

ADMIN_SERVER_INCY_PROMPT_ANNOUNCE = """🎨 <b>Изменение объявления (баннера) в INCY</b>

Текущее значение: <code>{current}</code>

💡 <i>Поддерживаются плейсхолдеры:</i>
• <code>{{devices}}</code> или <code>{{limit}}</code> — лимит устройств подписки
• <code>{{active}}</code> — число активных устройств

Отправьте текст объявления (до 200 символов, отображается плашкой в шапке клиента) или дефис «-» для отключения:"""

ADMIN_SERVER_INCY_PROMPT_ANNOUNCE_URL = """🎨 <b>Изменение ссылки баннера в INCY</b>

Текущее значение: <code>{current}</code>

Отправьте URL для клика по баннеру (например <code>https://t.me/your_channel</code>) или дефис «-» для удаления ссылки:"""

ADMIN_SERVER_INCY_PROMPT_ORIGIN_NAME = """🎨 <b>Изменение имени шлюза РФ в клиенте</b>

Текущее имя: <code>{current}</code>

Отправьте отображаемое имя узла (до 30 символов, например <code>🇷🇺 Россия</code>) или дефис «-» для сброса:"""

ADMIN_SERVER_INCY_PROMPT_ORIGIN_BADGE = """🎨 <b>Изменение бейджа шлюза РФ (Origin)</b>

Текущий бейдж: <code>{current}</code>

Отправьте текст бейджа (до 30 символов, например <code>Прямой шлюз</code>), <code>none</code> для отключения или дефис «-» для сброса:"""

ADMIN_SERVER_INCY_PROMPT_RELAY_NAME = """🎨 <b>Изменение имени узла {relay_code}</b>

Текущее имя: <code>{current}</code>

Отправьте отображаемое имя узла в клиенте (до 30 символов, например <code>🇩🇪 Германия (10 Гбит/с)</code>) или дефис «-» для сброса:"""

ADMIN_SERVER_INCY_PROMPT_RELAY_SPECIFIC = """🎨 <b>Настройка бейджа для узла {relay_name} ({relay_code})</b>

Текущий бейдж: <code>{current}</code>

Отправьте персональный бейдж (до 30 символов, например <code>Скоростной канал</code>), <code>none</code> для отключения или дефис «-» для сброса:"""

ADMIN_SERVER_INCY_SAVED = "✅ Настройка сохранена!"
ADMIN_SERVER_INCY_ERR_INVALID_URL = "❌ Некорректный URL. Ссылка должна начинаться с https://, http:// или tg://"
ADMIN_SERVER_INCY_RELAYS_TITLE = "🎨 <b>Управление узлами (Релеями) в INCY:</b>\n\nВыберите узел для настройки его названия и синего бейджа:"
ADMIN_SERVER_INCY_RELAY_CARD = """🎨 <b>Настройка узла {relay_name} ({relay_code})</b>

• <b>Имя в клиенте:</b> <code>{custom_name}</code>
• <b>Бейдж в клиенте:</b> <code>{custom_badge}</code>
• <b>Статус в подписке:</b> <code>{status}</code>

<i>Выберите действие:</i>"""

ADMIN_SERVER_INCY_RESET_CONFIRM_TITLE = """⚠️ <b>Подтверждение сброса оформления</b>

Вы действительно хотите сбросить оформление INCY для сервера <b>{name}</b> к исходным значениям?

Все кастомные названия узлов, бейджи, текст объявления и настройки видимости шлюза/релеев будут удалены."""

ADMIN_SERVER_INCY_BTN_CONFIRM_RESET = "🗑 Да, сбросить к дефолтам"

ADMIN_SERVER_INCY_RESET_SUCCESS = "✅ Настройки оформления сброшены к исходным значениям!"

ADMIN_SERVER_INCY_BTN_TITLE = "✏️ Имя профиля"
ADMIN_SERVER_INCY_BTN_DESC = "✏️ Подзаголовок"
ADMIN_SERVER_INCY_BTN_ANNOUNCE = "📢 Объявление"
ADMIN_SERVER_INCY_BTN_ANNOUNCE_URL = "🔗 Ссылка баннера"
ADMIN_SERVER_INCY_BTN_ORIGIN_NAME = "🇷🇺 Имя шлюза РФ"
ADMIN_SERVER_INCY_BTN_ORIGIN_BADGE = "🏷️ Бейдж шлюза РФ"
ADMIN_SERVER_INCY_BTN_ORIGIN_TOGGLE = "👁️ Шлюз РФ: {status}"
ADMIN_SERVER_INCY_BTN_RELAYS = "🌐 Узлы (Релеи)"
ADMIN_SERVER_INCY_BTN_RESET = "🔄 Сбросить к дефолтам"
ADMIN_SERVER_INCY_BTN_BACK = "« Назад к оформлению"

ADMIN_SERVER_INCY_BTN_RELAY_EDIT_NAME = "✏️ Изменить название"
ADMIN_SERVER_INCY_BTN_RELAY_EDIT_BADGE = "🏷️ Изменить бейдж"
ADMIN_SERVER_INCY_BTN_RELAY_TOGGLE = "👁️ {action} в подписке"
ADMIN_SERVER_INCY_BTN_BACK_TO_RELAYS = "« Назад к списку узлов"

ADMIN_SERVER_INCY_STATUS_ACTIVE = "Активен"
ADMIN_SERVER_INCY_STATUS_HIDDEN = "Скрыт"
ADMIN_SERVER_INCY_ACTION_HIDE = "Скрыть"
ADMIN_SERVER_INCY_ACTION_SHOW = "Показать"

ADMIN_SERVER_INCY_VALUE_NONE = "— (не задан)"
ADMIN_SERVER_INCY_VALUE_DISABLED = "Отключен"
ADMIN_SERVER_INCY_NO_RELAYS = "На сервере нет подключенных Relay-узлов."
ADMIN_SERVER_INCY_RELAY_BTN = "🌐 {name}{badge_part}{status_part}"
