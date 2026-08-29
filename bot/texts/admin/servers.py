"""Domain texts for admin/servers.py."""
from __future__ import annotations

ADMIN_BTN_ADD_SERVER = "➕ Добавить сервер"

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

ADMIN_SERVER_BTN_DELETE = "🗑 Удалить сервер"

ADMIN_SERVER_BTN_CHANGE_LIMIT = "👥 Изменить лимит"

ADMIN_SERVER_BTN_CHANGE_KEY = "🔑 Изменить ключ"

ADMIN_SERVER_CHECKING = "🔄 Проверка сервера..."

ADMIN_SERVER_CONFIRMATION_EXPIRED = "⚠️ Сессия подтверждения истекла"

ADMIN_SERVER_DELETED_BADGE = "Удалено"

ADMIN_SERVER_DELETE_BLOCKED_PENDING = """⚠️ <b>Удаление сервера отменено:</b>

На сервере присутствуют незавершенные операции создания или фоновые обновления. Дождитесь их завершения."""

ADMIN_SERVER_DELETE_BLOCKED_PENDING_CLIENT = "На сервере есть незавершённое создание клиента. Дождитесь reconciliation и повторите удаление."

ADMIN_SERVER_DELETE_CONFIRM = """⚠️ <b>Удаление сервера</b>

Вы уверены, что хотите удалить сервер {flag} <b>{name}</b>?
📱 Активных устройств на сервере: <b>{profiles_count}</b>"""

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

ADMIN_SERVER_SLOTS_DB_NOTE = " <i>(в БД: {db_used})</i>"
ADMIN_SERVER_SLOTS_EXTRA_NOTE = " <i>(в боте: {db_used}, внешних: +{extra})</i>"
ADMIN_SERVER_SLOTS_MISSING_NOTE = " <i>(в боте: {db_used}, ⚠️ не на узле: {missing})</i>"

ADMIN_SERVER_SLOTS_VALUE = "<b>{used_clients} / {max_clients}</b>"

ADMIN_SERVER_BTN_PEERS = "👥 Подключенные пиры ({used}/{total})"
ADMIN_SERVER_BTN_PEERS_NO_COUNT = "👥 Подключенные пиры"
ADMIN_SERVER_BTN_SERVER_USERS = "👥 Все пользователи этого сервера"
ADMIN_SERVER_PEERS_HEADER = """{header}👥 <b>Пиры сервера {flag} {server_name}</b>{status_banner}
• На узле: <b>{live_peers}</b> (Бот: {bot_peers}, Внешние: {external_peers}){missing_note}
Стр. {page}/{total_pages}:

"""
ADMIN_SERVER_PEERS_API_ERROR_BANNER = "\n⚠️ <i>API узла недоступен (показаны данные из базы бота)</i>"
ADMIN_SERVER_PEERS_MISSING_NOTE = " | ⚠️ Не на узле: <b>{missing_count}</b>"
ADMIN_SERVER_PEERS_EMPTY = "<i>На этом сервере пока нет зарегистрированных пиров.</i>\n"
ADMIN_SERVER_PEERS_BREADCRUMB = "Пиры"
ADMIN_SERVER_PEERS_FALLBACK_DEVICE = "Внешнее устройство"
ADMIN_SERVER_PEER_BOT_ROW = "• 🟢 <b>{username}</b> ({first_name}) — 📱 \"{device_name}\"\n  IP: <code>{ip}</code> | {status_online}\n"
ADMIN_SERVER_PEER_EXTERNAL_ROW = "• 👤 <b>[Внешний пир]</b> \"{device_name}\"\n  IP: <code>{ip}</code> | AmneziaWG Key: <code>{key}</code>\n"
ADMIN_SERVER_PEER_MISSING_ROW = "• ⚠️ <b>[Не на узле]</b> {username} — 📱 \"{device_name}\"\n  IP: <code>{ip}</code>\n"
ADMIN_SERVER_PEER_BTN_BOT = "🟢 {username} • {device_name}"
ADMIN_SERVER_PEER_BTN_EXTERNAL = "👤 Внешний: {device_name}"
ADMIN_SERVER_PEER_STATUS_ONLINE = "🟢 В сети"
ADMIN_SERVER_PEER_STATUS_OFFLINE = "⚪ Офлайн"
ADMIN_SERVER_PEER_INFO_ALERT = "👤 Внешний пир:\nНаходится на узле AmneziaVPN, но не привязан к пользователям Telegram-бота (например, создан вручную)."
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
