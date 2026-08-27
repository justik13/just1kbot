"""Domain copy catalogue for: user/devices.py"""

TEXTS = {
    'DEVICE_ACCESS_INACTIVE': '⚠️ Доступ неактивен. Продлите подписку.',
    'DEVICE_ACTION_UNAVAILABLE_STATE': '⚠️ Это действие сейчас недоступно для текущего состояния устройства.',
    'DEVICE_CARD': """📱 <b>{device_name}</b> ({flag} {server_name})
{last_connected_text}

↓ {traffic_down}  ↑ {traffic_up}  Σ {traffic_total}
""",
    'DEVICE_CONFIG_CONF_CAPTION': """📁 <b>Файл для AmneziaWG</b>

📱 Устройство: <b>{device_name}</b>

<i>Файл конфигурации (.conf) — дополнительный способ подключения, если ключ не подходит.</i>""",
    'DEVICE_CONFIG_GENERATING': '⏳ Генерирую файлы...',
    'DEVICE_CONFIG_UNAVAILABLE': '⚠️ Конфигурация недоступна. Обратитесь в поддержку.',
    'DEVICE_CONFIG_Устройство_CAPTION': """📁 <b>Файл для AmneziaУстройство</b>

📱 Устройство: <b>{device_name}</b>

<i>Файл конфигурации (.устройство) — дополнительный способ подключения, если ключ не подходит.</i>""",
    'DEVICE_CREATE_IN_PROGRESS': '⏳ Уже обрабатываем запрос...',
    'DEVICE_DELETE_ALREADY_IN_PROGRESS': '🗑 Устройство уже удаляется с сервера.',
    'DEVICE_DELETE_CANCELLED': '❌ Удаление отменено',
    'DEVICE_DELETE_CONFIRM': """⚠️ <b>Подтверждение удаления</b>

Устройство: <b>{device_name}</b>

Ключ перестанет работать. Для повторного подключения нужно создать устройство заново.

<i>Это действие необратимо.</i>""",
    'DEVICE_DELETE_IN_PROGRESS': '⏳ Уже удаляем устройство...',
    'DEVICE_DELETING_PROGRESS': '⏳ Удаляю устройство...',
    'DEVICE_KEY_TOO_LONG_CAPTION': """🔑 <b>Ключ подключения для {device_name}:</b>

<i>Ключ слишком длинный для текстового сообщения, поэтому отправлен файлом.</i>""",
    'DEVICE_MANAGE_HEADER': """📱 <b>{device_name}</b>

🌍 Локация: <b>{country_display}</b>
📡 Протокол: <b>{protocol}</b>
📊 Трафик: <b>{traffic_total}</b>
⏱ Активность: <b>{last_connected}</b>""",
    'DEVICE_NAME_DUPLICATE': """⚠️ <b>Устройство с таким именем уже существует</b>

На этом сервере уже есть устройство <b>{device_name}</b>.

Выберите другое имя.""",
    'DEVICE_RENAMED_SUCCESS': '✅ Устройство переименовано в <b>{device_name}</b>',
    'DEVICE_RENAME_PROMPT': """✏️ <b>Переименование устройства</b>

Введите новое имя (латиница, цифры, пробелы, дефисы, подчёркивания, до 16 символов):""",
    'DEVICE_SELF_HEALING_IN_PROGRESS': '⚠️ Идёт автоматическое восстановление после сбоя. Попробуйте позже.',
    'DEVICE_SHOW_KEY': """🔑 <b>Ключ подключения для {device_name}:</b>

<code>{raw_config}</code>

<i>💡 Нажмите на моноширинный текст выше, чтобы скопировать ключ в буфер обмена.</i>""",
    'DOWNLOAD_CONF_FALLBACK': """⚠️ <b>Не удалось сформировать файл</b> для устройства <b>{device_name}</b>.

Скопируйте ключ подключения на карточке устройства для импорта в приложение <b>AmneziaУстройство</b> или обратитесь в <b>💬 Поддержку</b>.""",
    'RUNTIME_BOT_HANDLERS_CONNECTION_DEVICE_CREATE_ROUTES_CREATING_SCREEN': """⏳ <b>Настраиваем подключение...</b>

🌍 Сервер: <b>{value_0}</b>

<i>Подготавливаем защищенный доступ...</i>""",
    'RUNTIME_BOT_HANDLERS_CONNECTION_DEVICE_CREATE_ROUTES_DEFAULT_SERVER_NAME': 'Сервер подключения',
    'RUNTIME_BOT_HANDLERS_CONNECTION_DEVICE_CREATE_ROUTES_L148_1': '🌍',
    'RUNTIME_BOT_HANDLERS_CONNECTION_DEVICE_VIEW_ROUTES_L60_1': '🌍',
    'RUNTIME_BOT_HANDLERS_CONNECTION_DEVICE_VIEW_ROUTES_L61_1': 'Неизвестно',
    'RUNTIME_BOT_HANDLERS_CONNECTION_DEVICE_VIEW_ROUTES_L73_1': 'Нет данных',
    'RUNTIME_BOT_HANDLERS_CONNECTION_DEVICE_VIEW_ROUTES_L87_1': """
⚠️ <b>Доступ неактивен</b>
Ключ и файлы конфигурации недоступны.
Устройство можно удалить.
""",
    'UI_BOT_HANDLERS_CONNECTION_DEVICE_CREATE_ROUTES_L150_1': '{value_0} {value_1}',
    'UI_BOT_HANDLERS_CONNECTION_DEVICE_CREATE_ROUTES_L154_1': '← Назад',
    'UI_BOT_HANDLERS_CONNECTION_DEVICE_CREATE_ROUTES_L51_1': '🚀 Купить доступ',
    'UI_BOT_HANDLERS_CONNECTION_DEVICE_CREATE_ROUTES_L52_1': '🏠 В главное меню',
    'UI_BOT_HANDLERS_CONNECTION_DEVICE_CREATE_ROUTES_L59_1': '⚙️ Сменить тариф',
    'UI_BOT_HANDLERS_CONNECTION_DEVICE_CREATE_ROUTES_L60_1': '← К списку устройств',
    'UI_BOT_HANDLERS_CONNECTION_DEVICE_DELETE_ROUTES_L100_1': 'Некорректный запрос',
    'UI_BOT_HANDLERS_CONNECTION_DEVICE_DELETE_ROUTES_L42_1': 'Некорректный запрос',
    'UI_BOT_HANDLERS_CONNECTION_DEVICE_DELETE_ROUTES_L71_1': 'Некорректный запрос',
    'UI_BOT_HANDLERS_CONNECTION_DEVICE_RENAME_ROUTES_L36_1': 'Некорректный запрос',
    'UI_BOT_HANDLERS_CONNECTION_DEVICE_VIEW_ROUTES_L114_1': 'Некорректный запрос',
    'UI_BOT_HANDLERS_CONNECTION_DEVICE_VIEW_ROUTES_L176_1': 'Некорректный запрос',
    'UI_BOT_HANDLERS_CONNECTION_DEVICE_VIEW_ROUTES_L51_1': 'Некорректный запрос',
    'UI_BOT_HANDLERS_CONNECTION_DEVICE_VIEW_ROUTES_L93_1': '🗑 Удалить устройство',
    'UI_BOT_HANDLERS_CONNECTION_DEVICE_VIEW_ROUTES_L94_1': '← К списку устройств',
    'UI_BOT_HANDLERS_CONNECTION_DEVICE_VIEW_ROUTES_L95_1': '🏠 В главное меню',
    'UI_BOT_KEYBOARDS_DEVICE_ALT_CONNECTION': '🔄 Другой способ подключения',
    'UI_BOT_KEYBOARDS_DEVICE_BACK_TO_DEVICE': '← К устройству',
    'UI_BOT_KEYBOARDS_DEVICE_L18_1': '🗑 Удалить устройство',
    'UI_BOT_KEYBOARDS_DEVICE_L23_1': '← К списку устройств',
    'UI_BOT_KEYBOARDS_DEVICE_L28_1': '🏠 В главное меню',
    'UI_BOT_KEYBOARDS_DEVICE_L43_1': '✅ Да, удалить',
    'UI_BOT_KEYBOARDS_DEVICE_L48_1': '❌ Отмена',
    'UI_BOT_KEYBOARDS_DEVICE_L9_1': '✏️ Изменить имя',
    'UI_BOT_KEYBOARDS_DEVICE_OPEN_IN_AMNEZIA': '🚀 Открыть в Amnezia',
    'BTN_INSTRUKTSIYA_I_POMOSCH': '📖 Инструкция и помощь',
    'BTN_SKACHAT_KLIENT_AMNEZIA': '📥 Скачать клиент Amnezia',
    'BTN_INSTRUKTSIYA_IOS_DLYA_RF': '🍏 Инструкция iOS (для РФ)',
    'BTN_INSTRUKTSII_WINDOWS': '💻 Инструкции Windows',
    'BTN_RAZDELNOE_TUNNELIROVANIE': '🔀 Раздельное Туннелирование',
    'BTN_DOKUMENTATSIYA_AMNEZIA': '📚 Документация Amnezia',
}
