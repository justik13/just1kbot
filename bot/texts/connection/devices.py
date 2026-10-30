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

BTN_CONFIRM_DELETE = "🗑 Да, удалить"
DEVICE_STATUS_CREATING_LABEL = BTN_CONFIRM_DELETE

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

BTN_VLESS_SUB_INFO = "⚡️ Подключение по ссылке (все серверы)"

VLESS_SUB_SCREEN_TEXT = """⚡️ <b>Основное подключение по ссылке (INCY / Happ)</b>

Единая ссылка дает доступ сразу ко всем серверам с автообновлением без ручной настройки отдельных ключей.

1️⃣ <b>Установите приложение:</b>
   <b>INCY:</b>
   🍏 <a href="https://apps.apple.com/app/incy/id6756943388">App Store (iPhone / iPad)</a>
   🤖 <a href="https://play.google.com/store/apps/details?id=llc.itdev.incy">Google Play (Android)</a>
   💻 / 📦 <a href="https://github.com/INCY-DEV/incy-platforms/releases">Для ПК / APK (GitHub)</a>

   <b>Happ:</b>
   🍏 <a href="https://apps.apple.com/app/happ-proxy-utility/id6504287215">App Store (iPhone / iPad)</a>
   🤖 <a href="https://play.google.com/store/apps/details?id=com.happproxy">Google Play (Android)</a>
   📦 <a href="https://github.com/Happ-proxy/happ-android/releases">APK для Android (GitHub)</a>
   💻 <a href="https://github.com/Happ-proxy/happ-desktop/releases">Для ПК (GitHub)</a>

2️⃣ <b>Скопируйте вашу ссылку на подписку:</b>
<code>{sub_url}</code>
<i>(нажмите на ссылку выше для копирования)</i>

3️⃣ <b>Добавьте подписку:</b>
   • Откройте приложение (<b>INCY</b> или <b>Happ</b>)
   • Вставьте скопированную ссылку подписки и сохраните
   • Нажмите кнопку подключения!

<i>Каждое подключенное устройство учитывается в общем лимите устройств.</i>"""

BTN_VLESS_RESET_HWID = "🔄 Сбросить привязку устройств"
BTN_VLESS_RESET_CONFIRM = "🔄 Сбросить все устройства"
VLESS_RESET_CONFIRM_TEXT = """⚠️ <b>Сброс привязки устройств</b>

При сбросе все подключенные по ссылке устройства будут отключены. Им потребуется заново обновить подписку в приложении.

Вы уверены, что хотите сбросить привязку устройств?"""
VLESS_RESET_SUCCESS = "✅ Привязка устройств по ссылке сброшена."
VLESS_RESET_EMPTY = "ℹ️ У вас нет привязанных устройств по ссылке."
VLESS_DEFAULT_SERVER_DESCRIPTION = "Прямой доступ"

BTN_AMNEZIA_DEVICES = "🛡 Устройства Amnezia"
BTN_AMNEZIA_DEVICES_COUNT = "🛡 Устройства Amnezia ({count})"
BTN_BACK_TO_CONNECTION = "◀️ Назад к подключению"
BTN_ADD_DEVICE = "➕ Добавить устройство"

CONNECTION_HUB_ACTIVE_TEXT = """🌐 <b>Подключение</b>

<b>Статус:</b> 🟢 Активна
<b>Срок действия:</b> до {expiry}
<b>Тариф:</b> {tariff}

📱 <b>Устройства:</b> {active} из {limit}
<i>(по ссылке: {vless_count} • Amnezia: {amnezia_count})</i>

⚡️ <b>Основное подключение по ссылке (INCY / Happ):</b>
Единая ссылка дает доступ сразу ко всем серверам с автообновлением без ручной настройки отдельных ключей. Рекомендуется для всех основных устройств.

📋 <b>Ваша ссылка на подписку:</b>
<code>{sub_url}</code>
<i>(нажмите на ссылку для копирования)</i>

🛡 <b>Устройства Amnezia:</b>
<i>Для приложений Amnezia или роутеров перейдите в раздел ниже.</i>"""

CONNECTION_HUB_EXPIRED_TEXT = """🌐 <b>Подключение</b>

<b>Статус:</b> 🔴 Не активна
{grace_notice}
<i>Для возобновления доступа продлите подписку.</i>"""

BTN_SUB_CONNECT_INCY = "🚀 Подключить в приложении"
BTN_SUB_COPY_LINK = "📋 Скопировать ссылку"
BTN_INCY_INSTRUCTIONS = "📱 Инструкция (INCY / Happ)"
VLESS_WEB_TOO_MANY_REQUESTS = "Слишком много запросов к подписке. Попробуйте позже."
AMNEZIA_SCREEN_HINT_ADD = "\n\n<i>Нажмите кнопку ниже, чтобы создать первое устройство.</i>"
AMNEZIA_SCREEN_HINT_LIMIT = "\n\n⚠️ <i>Общий лимит устройств исчерпан. Для добавления перейдите на тариф с большим количеством устройств или отключите неиспользуемые.</i>"

AMNEZIA_SCREEN_TEXT = """🛡 <b>Устройства Amnezia</b>

Подключение и файлы конфигураций для приложений Amnezia.

📱 <b>Устройства:</b> {active} из {limit} <i>(по ссылке: {vless_count} • Amnezia: {amnezia_count})</i>
📊 <b>Трафик за месяц:</b> {monthly_traffic}
{profiles_block}
<i>Нажмите на устройство ниже для управления:</i>"""

AMNEZIA_SCREEN_EMPTY_TEXT = """🛡 <b>Устройства Amnezia</b>

Подключение и файлы конфигураций для приложений Amnezia.

📱 <b>Устройства:</b> {active} из {limit} <i>(по ссылке: {vless_count} • Amnezia: 0)</i>
📊 <b>Трафик за месяц:</b> {monthly_traffic}

<i>У вас пока нет добавленных устройств Amnezia.</i>{action_hint}"""

VLESS_FEED_EXPIRED_TITLE = "⚠️ Подписка закончилась"
VLESS_FEED_EXPIRED_NOTICE = "⚠️ Подписка закончилась. Продлите доступ: @{bot_username}"
VLESS_FEED_DEVICE_LIMIT_TITLE = "⚠️ Лимит устройств"
VLESS_FEED_DEVICE_LIMIT_NOTICE = "⚠️ Превышен лимит устройств ({active}/{limit}). Отключите старые в @{bot_username}"

LANDING_INACTIVE_BADGE_PAUSED = "Доступ приостановлен"
LANDING_INACTIVE_TITLE_DEFAULT = "Подписка не активна"
LANDING_INACTIVE_DESC_DEFAULT = "Срок действия доступа истек или подписка приостановлена. Для возобновления перейдите в бота."
LANDING_INACTIVE_BADGE_DISABLED = "Отключено"
LANDING_INACTIVE_TITLE_DISABLED = "Доступ отключен"
LANDING_INACTIVE_DESC_DISABLED = "Доступ отключен администратором. Для уточнения обратитесь в поддержку в боте."
LANDING_INACTIVE_BADGE_EXPIRED = "Срок истек"
LANDING_INACTIVE_TITLE_EXPIRED = "Срок действия истек"
LANDING_INACTIVE_DESC_EXPIRED = "Срок действия доступа подошел к концу. Для продления перейдите в бота."
LANDING_INACTIVE_BADGE_EXHAUSTED = "Лимит исчерпан"
LANDING_INACTIVE_TITLE_EXHAUSTED = "Трафик исчерпан"
LANDING_INACTIVE_DESC_EXHAUSTED = "Включенный пакет трафика закончился. Для продолжения работы перейдите в бота."

LANDING_VLESS_PAGE_TITLE = "Стандартная подписка"
LANDING_WHITE_INTERNET_PAGE_TITLE = "Белый Интернет"
LANDING_SPECIAL_ACCESS_BADGE = "СПЕЦИАЛЬНЫЙ ДОСТУП"
LANDING_DEVICE_FORMS_LABELS = ("УСТРОЙСТВО", "УСТРОЙСТВА", "УСТРОЙСТВ")

LANDING_REPORT_TOO_MANY_REQUESTS = "Слишком много запросов. Пожалуйста, подождите."
LANDING_REPORT_INVALID_LINK = "Неверная ссылка."
LANDING_REPORT_INVALID_REQUEST = "Некорректный запрос."
LANDING_REPORT_INVALID_FORMAT = "Некорректный формат."
LANDING_REPORT_CONTACT_LENGTH_ERROR = "Укажите контакт для связи (от 3 до 120 символов)."
LANDING_REPORT_MESSAGE_LENGTH_ERROR = "Опишите проблему подробнее (от 5 до 1000 символов)."
LANDING_REPORT_STATUS_NOT_FOUND = "Не найдена"
LANDING_REPORT_USER_UNKNOWN = "Не определен"
LANDING_REPORT_DELIVERY_FAILED = "Не удалось доставить сообщение. Попробуйте позже."

ADMIN_ALERT_EMERGENCY_SUPPORT = (
    "🆘 <b>Экстренное обращение с веб-страницы</b>\n\n"
    "👤 <b>Пользователь:</b> {user_info}\n"
    "📋 <b>Категория:</b> {category}\n"
    "📊 <b>Статус доступа:</b> <code>{sub_status}</code>\n"
    "📱 <b>Устройства:</b> {devices_info}\n"
    "🌐 <b>IP:</b> <code>{client_ip}</code>\n\n"
    "📞 <b>Контакт для связи:</b>\n<code>{contact}</code>\n\n"
    "💬 <b>Сообщение:</b>\n{message}"
)





