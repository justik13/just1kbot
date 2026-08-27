"""Domain texts for connection/devices.py."""
from __future__ import annotations

BTN_BACK_TO_DEVICES = "← К списку устройств"

BTN_CHANGE_NAME = "✏️ Изменить имя"

BTN_CHANGE_TARIFF = "⚙️ Сменить тариф"

BTN_DELETE_DEVICE = "🗑 Удалить устройство"

CONNECTION_DEVICES_DEVICE_ALT_CONNECTION = "🔄 Другой способ подключения"

CONNECTION_DEVICES_DEVICE_BACK_TO_DEVICE = "← К устройству"

CONNECTION_DEVICES_DEVICE_OPEN_IN_AMNEZIA = "🚀 Открыть в Amnezia"

CONNECTION_DEVICE_CREATE_CREATING_SCREEN = """⏳ <b>Настраиваем подключение...</b>

🌍 Сервер: <b>{value_0}</b>

<i>Подготавливаем защищенный доступ...</i>"""

CONNECTION_DEVICE_CREATE_DEFAULT_SERVER_NAME = "Сервер подключения"

DEVICE_ACCESS_INACTIVE = "⚠️ Доступ неактивен. Продлите подписку."

DEVICE_ACCESS_INACTIVE_NOTICE = """
⚠️ <b>Доступ неактивен</b>
Ключ и файлы конфигурации недоступны.
Устройство можно удалить.
"""

DEVICE_ACTION_UNAVAILABLE_STATE = "⚠️ Это действие сейчас недоступно для текущего состояния устройства."

DEVICE_CARD = """📱 <b>{device_name}</b> ({flag} {server_name})
{last_connected_text}

↓ {traffic_down}  ↑ {traffic_up}  Σ {traffic_total}
"""

DEVICE_CONFIG_CONF_CAPTION = """📁 <b>Файл для AmneziaWG</b>

📱 Устройство: <b>{device_name}</b>

<i>Файл конфигурации (.conf) — дополнительный способ подключения, если ключ не подходит.</i>"""

DEVICE_CONFIG_GENERATING = "⏳ Генерирую файлы..."

DEVICE_CONFIG_UNAVAILABLE = "⚠️ Конфигурация недоступна. Обратитесь в поддержку."

DEVICE_CONFIG_VPN_CAPTION = """📁 <b>Файл для AmneziaVPN</b>

📱 Устройство: <b>{device_name}</b>

<i>Файл конфигурации (.vpn) — дополнительный способ подключения, если ключ не подходит.</i>"""

DEVICE_CREATE_IN_PROGRESS = "⏳ Уже обрабатываем запрос..."

DEVICE_CREATING_SPINNER_NOTICE = "{value_0} {value_1}"

DEVICE_DATA_NONE = "Нет данных"

DEVICE_DELETE_CANCELLED = "❌ Удаление отменено"

DEVICE_DELETE_CONFIRM = """⚠️ <b>Подтверждение удаления</b>

Устройство: <b>{device_name}</b>

Ключ перестанет работать. Для повторного подключения нужно создать устройство заново.

<i>Это действие необратимо.</i>"""

DEVICE_DELETE_IN_PROGRESS = "⏳ Уже удаляем устройство..."

DEVICE_DELETING_PROGRESS = "⏳ Удаляю устройство..."

DEVICE_KEY_TOO_LONG_CAPTION = """🔑 <b>Ключ подключения для {device_name}:</b>

<i>Ключ слишком длинный для текстового сообщения, поэтому отправлен файлом.</i>"""

DEVICE_MANAGE_HEADER = """📱 <b>{device_name}</b>

🌍 Локация: <b>{country_display}</b>
📡 Протокол: <b>{protocol}</b>
📊 Трафик: <b>{traffic_total}</b>
⏱ Активность: <b>{last_connected}</b>"""

DEVICE_NAME_DUPLICATE = """⚠️ <b>Устройство с таким именем уже существует</b>

На этом сервере уже есть устройство <b>{device_name}</b>.

Выберите другое имя."""

DEVICE_RENAMED_SUCCESS = "✅ Устройство переименовано в <b>{device_name}</b>"

DEVICE_RENAME_PROMPT = """✏️ <b>Переименование устройства</b>

Введите новое имя (латиница, цифры, пробелы, дефисы, подчёркивания, до 16 символов):"""

DEVICE_SHOW_KEY = """🔑 <b>Ключ подключения для {device_name}:</b>

<code>{raw_config}</code>

<i>💡 Нажмите на моноширинный текст выше, чтобы скопировать ключ в буфер обмена.</i>"""

DEVICE_STATUS_CREATING_LABEL = "✅ Да, удалить"

DOWNLOAD_CONF_FALLBACK = """⚠️ <b>Не удалось сформировать файл</b> для устройства <b>{device_name}</b>.

Скопируйте ключ подключения на карточке устройства для импорта в приложение <b>AmneziaVPN</b> или обратитесь в <b>💬 Поддержку</b>."""
