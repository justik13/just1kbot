"""Domain texts for connection/devices.py."""
from __future__ import annotations

BTN_BACK_TO_DEVICES = "← К списку устройств"

BTN_CHANGE_NAME = "✏️ Изменить имя"

BTN_CHANGE_TARIFF = "⚙️ Сменить тариф"

BTN_DELETE_DEVICE = "🗑 Удалить устройство"

CONNECTION_DEVICES_DEVICE_ALT_CONNECTION = "🔄 Другой способ подключения"

CONNECTION_DEVICES_DEVICE_BACK_TO_DEVICE = "← К устройству"

CONNECTION_DEVICE_CREATE_CREATING_SCREEN = """⏳ <b>Настраиваем подключение...</b>

🌍 Сервер: <b>{v0}</b>

<i>Подготавливаем защищенный доступ...</i>"""

CONNECTION_DEVICE_CREATE_DEFAULT_SERVER_NAME = "Сервер подключения"

DEVICE_ACCESS_INACTIVE = "⚠️ Доступ неактивен. Продлите подписку."

DEVICE_ACCESS_INACTIVE_NOTICE = """
⚠️ <b>Доступ неактивен</b>
Ключ и файлы конфигурации недоступны.
Устройство можно удалить.
"""

DEVICE_ACTION_UNAVAILABLE_STATE = "⚠️ Это действие сейчас недоступно для текущего состояния устройства."


DEVICE_CONFIG_CONF_CAPTION = """📁 <b>Файл для AmneziaWG</b>

📱 Устройство: <b>{device_name}</b>

<i>Файл конфигурации (.conf) — дополнительный способ подключения, если ключ не подходит.</i>"""

DEVICE_CONFIG_GENERATING = "⏳ Генерирую файлы..."

DEVICE_CONFIG_UNAVAILABLE = "⚠️ Конфигурация недоступна. Обратитесь в поддержку."

DEVICE_CONFIG_VPN_CAPTION = """📁 <b>Файл для AmneziaVPN</b>

📱 Устройство: <b>{device_name}</b>

<i>Файл конфигурации (.vpn) — дополнительный способ подключения, если ключ не подходит.</i>"""

DEVICE_CREATE_IN_PROGRESS = "⏳ Уже обрабатываем запрос..."

DEVICE_CREATING_SPINNER_NOTICE = "{v0} {value_1}"

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
📊 Трафик: <b>{traffic_total}</b>
⏱ Активность: <b>{last_connected}</b>"""

DEVICE_NAME_DUPLICATE = """⚠️ <b>Устройство с таким именем уже существует</b>

На этом сервере уже есть устройство <b>{device_name}</b>.

Выберите другое имя."""

DEVICE_RENAMED_SUCCESS = "✅ Устройство переименовано в <b>{device_name}</b>"

DEVICE_RENAME_PROMPT = """✏️ <b>Переименование устройства</b>

Введите новое имя (буквы, цифры, пробелы, дефисы, подчёркивания, до 16 символов):"""

DEVICE_SHOW_KEY = """🔑 <b>Ключ подключения для {device_name}:</b>

<code>{raw_config}</code>

<i>💡 Нажмите на моноширинный текст выше, чтобы скопировать ключ в буфер обмена.</i>"""

DEVICE_STATUS_CREATING_LABEL = "🗑 Да, удалить"

DOWNLOAD_CONF_FALLBACK = """⚠️ <b>Не удалось сформировать файл</b> для устройства <b>{device_name}</b>.

Скопируйте ключ подключения на карточке устройства для импорта в приложение <b>AmneziaVPN</b> или обратитесь в <b>💬 Поддержку</b>."""


DEVICE_DEFAULT_NAME_TEMPLATE = 'Устройство #{slot}'
FINALIZER_OPERATION_FAILED_TEMPLATE = 'Operation failed: {code}'
FINALIZER_ERROR_LABELS = {'server_full': 'Сервер переполнен', 'auth_failed': 'Неверный API ключ сервера', 'invalid_raw_config': 'Ошибка шифрования конфига', 'create_ambiguous_reconcile': 'Не удалось проверить создание пира', 'invalid_created_config_cleanup': 'Конфиг не сгенерирован (очистка)', 'duplicate_exact_client_name': 'Такое имя уже существует на сервере', 'network_error': 'Ошибка сети при обращении к серверу', 'timeout': 'Таймаут обращения к серверу'}
CONNECTION_DEVICE_ROW_FORMAT = '\n• 📱 <b>{device_name}</b> ({location})\n   └ 📊 <code>{traffic}</code> | <i>{last_conn}</i>'

BTN_CHANGE_SERVER = "🔄 Сменить сервер"
BTN_CONFIRM_MIGRATE = "✅ Да, сменить сервер"

DEVICE_MIGRATE_SELECT_SERVER = """🔄 <b>Смена сервера</b>

📱 Устройство: <b>{device_name}</b>
🌍 Текущий сервер: <b>{current_server}</b>

Выберите новый сервер для подключения:"""

DEVICE_MIGRATE_CONFIRM = """🔄 <b>Подтверждение смены сервера</b>

📱 Устройство: <b>{device_name}</b>
🌍 Новый сервер: <b>{target_server}</b>

⚠️ <b>Как это работает:</b>
• <b>Старое подключение отключится через 15 минут.</b>
• Ваша связь с интернетом и Telegram <b>не прервётся</b> — старый сервер продолжает работать, пока вы настраиваете новый.
• У вас будет 15 минут, чтобы добавить новый ключ в приложение Amnezia и переключиться.

Подтверждаете перенос?"""

DEVICE_MIGRATE_SUCCESS_NOTICE = """🎉 <b>Сервер успешно изменён на {target_server}!</b>

⏳ <b>Внимание:</b> старое подключение автоматически отключится через <b>15 минут</b>.

📋 <b>Инструкция — как переключиться прямо сейчас:</b>
<b>1.</b> Нажмите на ключ ниже, чтобы скопировать его в буфер.
<b>2.</b> Откройте приложение <b>Amnezia</b>.
<b>3.</b> Нажмите кнопку <b>«+»</b> (или «Добавить») → выберите «Вставить из буфера».
<b>4.</b> Подключитесь к новому серверу.
<b>5.</b> Старое подключение в приложении Amnezia можно смело удалить."""

DEVICE_MIGRATE_FAILED_NOTICE = """⚠️ <b>Не удалось настроить новый сервер</b>

Сервер временно не отвечает.
✅ <b>Ваше текущее подключение сохранено и продолжает работать без изменений.</b>
Вы можете попробовать выбрать другую локацию."""

DEVICE_MIGRATE_COOLDOWN_ALERT = "⏳ Смена сервера для этого устройства доступна не чаще раза в 15 минут. Подождите ещё {minutes} мин."

DEVICE_MIGRATE_NO_OTHER_SERVERS = "⚠️ Нет других доступных серверов для смены."

DEVICE_MIGRATE_IN_PROGRESS = "⏳ Устройство в процессе смены сервера. Пожалуйста, подождите завершения."

DEVICE_MIGRATE_PENDING_NOTICE = """⏳ <b>Настройка нового сервера продолжается в фоновом режиме</b>

Сервер отвечает чуть дольше обычного.
✅ <b>Ваше текущее подключение продолжает работать.</b>
Новое подключение станет доступно в списке устройств в течение 1–2 минут."""
