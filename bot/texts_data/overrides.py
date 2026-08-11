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
OVERRIDES['ADMIN_SERVER_CARD'] = '{flag} <b>Сервер:</b> {name} (ID: {id})\nСтатус: {status}\nПротокол: {protocol}\nURL: {api_url}\nМакс. клиентов: {max_clients}'
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
OVERRIDES['PAYMENT_SUBSCRIPTION_INACTIVE'] = (
    "⚠️ Смена тарифа с перерасчётом остатка возможна только при действующей подписке.\n\n"
    "Ваша подписка неактивна. Перейдите в раздел «Купить подписку» для оформления нового тарифа."
)
OVERRIDES['PAYMENT_CURRENT_TARIFF_UNKNOWN'] = (
    "⚠️ Смена тарифа возможна только при действующей подписке.\n\n"
    "Перейдите в раздел «Купить подписку» для оформления нового тарифа."
)
OVERRIDES['PAYMENT_ACTIVE_CHECKOUT_EXISTS'] = (
    "⚠️ У вас уже есть не завершённая операция покупки. Завершите её или попробуйте чуть позже."
)
OVERRIDES['PAYMENT_ACTIVE_CHANGE_QUOTE_EXISTS'] = (
    "⚠️ У вас уже создан запрос на смену тарифа. Нажмите назад и выберите его."
)
OVERRIDES['PAYMENT_CHANGE_TARIFF_UNAVAILABLE_NO_SUB'] = (
    "⚠️ <b>Смена тарифа недоступна</b>\n\n"
    "Смена тарифа с перерасчётом доступна только при наличии действующей подписки.\n"
    "У вас сейчас нет активной подписки. Для оформления подписки воспользуйтесь кнопкой ниже."
)

# Main Hub: the profile is displayed as account information in the hub,
# while navigation actions remain in the keyboard.
OVERRIDES["HUB_HEADER"] = """🏠 <b>Главное меню</b>

👋 Привет, <b>{name}</b>!
🆔 <b>Telegram ID:</b> <code>{telegram_id}</code>

<b>📊 Статус подписки:</b> {status}
<b>⏳ Действует до:</b> {valid_until} ({days_left})
<b>📱 Подключено устройств:</b> {devices_count}/{device_limit}

<b>💰 Баланс:</b> {real_balance} ₽
<b>🎁 Бонусный баланс:</b> {bonus_balance} ₽{inviter_line}

Выберите нужный раздел:"""

# Device connection methods: key is primary, file is the fallback.
OVERRIDES["DEVICE_MANAGE_HEADER"] = """📱 <b>Управление устройством</b>

<b>{device_name}</b>

📍 Страна / Локация: <b>{country_display}</b> ({server_name})
📡 Протокол: {protocol}
📊 Трафик: ∑ {traffic_total}
⏱ Последняя активность: {last_connected}

💡 <b>Способы подключения:</b>
• 🔑 <b>Ключ</b> — основной и самый простой способ подключения.
• 📁 <b>Скачать файлом</b> — дополнительный резервный способ, если ключ не работает или приложение его не поддерживает."""

OVERRIDES["DEVICE_SHOW_KEY"] = """🔑 <b>Ключ подключения для {device_name}:</b>

<code>{raw_config}</code>

<i>💡 Ключ — основной способ подключения. Нажмите на моноширинный текст выше, чтобы скопировать его.
Если ключ не работает или приложение его не поддерживает, используйте «📥 Скачать файлом».</i>"""

OVERRIDES["DEVICE_ADDED_SUCCESS"] = """✅ <b>Устройство добавлено!</b>

📱 {device_name} ({flag} {server_name})

💡 Основной способ подключения — «🔑 Показать ключ».
📁 «📥 Скачать файлом» — дополнительный способ, если ключ не работает или не поддерживается приложением."""

OVERRIDES["DEVICE_CONFIG_VPN_CAPTION"] = """📁 <b>Файл для AmneziaVPN</b>

📱 Устройство: <b>{device_name}</b>

<i>Файл конфигурации (.vpn) — дополнительный способ подключения, если ключ не подходит.</i>"""

OVERRIDES["DEVICE_CONFIG_CONF_CAPTION"] = """📁 <b>Файл для AmneziaWG</b>

📱 Устройство: <b>{device_name}</b>

<i>Файл конфигурации (.conf) — дополнительный способ подключения, если ключ не подходит.</i>"""

OVERRIDES["DEVICE_CONFIG_INSTRUCTION"] = """✅ <b>Файлы конфигурации отправлены!</b>

📥 <b>Резервный способ подключения:</b>

1️⃣ <b>.vpn</b> — импортируйте в приложение <b>AmneziaVPN</b>.
2️⃣ <b>.conf</b> — импортируйте в приложение <b>AmneziaWG</b> или <b>DefaultVPN</b>.

💡 Основной способ — «🔑 Показать ключ». Используйте файлы, если ключ не работает или не поддерживается приложением.

<i>Нажмите на прикреплённый файл выше, чтобы открыть его в приложении.</i>"""

OVERRIDES["DOWNLOAD_CONF_FALLBACK"] = """⚠️ <b>Не удалось сформировать файл</b> для устройства <b>{device_name}</b>.

Используйте основной способ — кнопку «🔑 Показать ключ» для импорта в приложение <b>AmneziaVPN / AmneziaWG</b> или обратитесь в <b>💬 Поддержку</b>."""

OVERRIDES["FAQ_TEXT"] = """❓ <b>Частые вопросы (FAQ)</b>

📌 <b>1. Как настроить подключение?</b>
• Откройте раздел «🔌 Подключения» ➔ «➕ Добавить устройство».
• Выберите подходящую локацию и создайте устройство.
• Откройте готовое устройство и выберите один из способов:
  — «🔑 Показать ключ» — основной способ подключения, скопировать ключ vpn://.
  — «📥 Скачать файлом» — дополнительный способ, получить файлы конфигурации (.vpn и .conf), если ключ не работает или не поддерживается приложением.

📌 <b>2. Какие приложения использовать для подключения?</b>
• <b>AmneziaVPN</b> — основное приложение. Ключ vpn:// — основной способ; файл .vpn — резервный вариант.
• <b>AmneziaWG</b> — лёгкий клиент. Используйте файл .conf, если ключ не подходит.
• <b>DefaultVPN</b> — альтернатива для iOS. Файл .conf или ключ vpn://.

📌 <b>3. Что делать, если подключение не работает или работает медленно?</b>
• Попробуйте сменить сервер подключения в разделе «🔌 Подключения».
• Проверьте подключение к сети (переключитесь с Wi-Fi на 4G/LTE или наоборот).
• Если проблема сохраняется — напишите в «💬 Поддержка».

📌 <b>4. Как продлить подписку или сменить тариф?</b>
• В главном меню откройте «⏳ Подписка» или «🛒 Купить подписку».
• Выберите удобный срок и тариф. При смене тарифа оставшиеся дни автоматически пересчитываются.

📌 <b>5. На скольких устройствах одновременно работает подписка?</b>
• Количество доступных устройств зависит от выбранного тарифа.
• Актуальный лимит отображается в разделе подписки и в главном меню.

📌 <b>6. Как получить бонусные дни за приглашенных друзей?</b>
• Откройте «🤝 Пригласить друга» в главном меню.
• Скопируйте вашу персональную ссылку и отправьте друзьям. За каждого приглашенного друга вы получаете бонусы при пополнении.

📌 <b>7. Безопасность и конфиденциальность</b>
• Для работы сервиса сохраняются только минимально необходимые технические данные, связанные с доступом и подключёнными устройствами.
• Мы не заявляем о хранении истории посещённых сайтов или содержимого трафика.
• Весь трафик шифруется используемым протоколом подключения."""

# These are the only legal URLs used by the bot.
OVERRIDES["TOS_AGREEMENT_URL"] = "https://telegra.ph/Polzovatelskoe-soglashenie-07-23-48"
OVERRIDES["PRIVACY_POLICY_URL"] = "https://telegra.ph/Politika-konfidencialnosti-07-23-84"

# Neutral user-facing copy for active texts that still existed in the legacy base catalogue.
OVERRIDES["WELCOME_TEXT"] = """👋 <b>Добро пожаловать!</b>

🔐 Здесь вы можете подключить свои устройства к <b>just1kbot</b> и управлять подключениями в одном месте.

ℹ️ <i>Используя сервис, вы автоматически соглашаетесь с <a href=\"https://telegra.ph/Polzovatelskoe-soglashenie-07-23-48\">Условиями использования</a> и <a href=\"https://telegra.ph/Politika-konfidencialnosti-07-23-84\">Политикой конфиденциальности</a>.</i>"""

OVERRIDES["SUPPORT_TEXT"] = """💬 <b>Поддержка</b>

Если у вас возникли вопросы, напишите нашему оператору:

👤 {support_username}

Мы постараемся помочь как можно скорее."""

OVERRIDES["PAYMENT_SHOWCASE_HEADER"] = """🛡 <b>Выберите формат подписки</b>

Выберите тариф, который подходит под ваши задачи."""
