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
    "ADMIN_BAN_CONFIRM": "Вы уверены, что хотите забанить пользователя?",
    "ADMIN_BAN_FAILED": "❌ Ошибка при попытке забанить пользователя.",
    "ADMIN_BAN_SUCCESS": "✅ Пользователь успешно забанен.",
    "ADMIN_DELETE_DEVICE_CONFIRM": "Вы уверены, что хотите удалить устройство?",
    "ADMIN_DELETE_DEVICE_ERROR": "❌ Произошла ошибка при удалении устройства.",
    "ADMIN_DELETE_DEVICE_FAILED": "❌ Не удалось удалить устройство.",
    "ADMIN_DELETE_DEVICE_SUCCESS": "✅ Устройство успешно удалено.",
    "ADMIN_MANUAL_GRANT_USER_BANNED": "❌ Невозможно выдать подписку: пользователь забанен.",
    "ADMIN_MANUAL_GRANT_USER_DELETED": "❌ Невозможно выдать подписку: пользователь удален.",
    "ADMIN_SERVER_ADDED": "✅ Сервер успешно добавлен.",
    "ADMIN_SERVER_CARD": "{flag} <b>Сервер:</b> {name} (ID: {id})\nСтатус: {status}\nПротокол: {protocol}\nURL: {api_url}\nМакс. клиентов: {max_clients}",
    "ADMIN_SERVER_CHECKING": "🔄 Проверка сервера...",
    "ADMIN_SERVER_DELETE_CONFIRM": "Вы уверены, что хотите удалить этот сервер?",
    "ADMIN_SERVER_EDIT_KEY_PROMPT": "Введите новый ключ для сервера:",
    "ADMIN_SERVER_EDIT_MAX_CLIENTS_PROMPT": "Введите максимальное количество клиентов для сервера:",
    "ADMIN_SERVER_EDIT_URL_PROMPT": "Введите новый URL сервера:",
    "ADMIN_SERVER_FLAG_PROMPT": "Выберите флаг для сервера:",
    "ADMIN_SERVER_FLAG_PROMPT_EDIT": "Выберите новый флаг для сервера:",
    "ADMIN_SERVER_FLAG_TOO_LONG": "❌ Флаг слишком длинный.",
    "ADMIN_SERVER_FLAG_UPDATED": "✅ Флаг сервера обновлен.",
    "ADMIN_SERVER_KEY_PROMPT": "Введите ключ доступа:",
    "ADMIN_SERVER_KEY_UPDATED": "✅ Ключ сервера обновлен.",
    "ADMIN_SERVER_MAX_CLIENTS_UPDATED": "✅ Макс. количество клиентов обновлено.",
    "ADMIN_SERVER_MAX_CLIENTS_WARNING": "⚠️ Внимание: на сервере уже больше клиентов, чем новый лимит.",
    "ADMIN_SERVER_NAME_PROMPT": "Введите название сервера:",
    "ADMIN_SERVER_RENAMED": "✅ Сервер успешно переименован.",
    "ADMIN_SERVER_RENAME_PROMPT": "Введите новое имя сервера:",
    "ADMIN_SERVER_STATE_DISABLED": "🔴 Отключен",
    "ADMIN_SERVER_STATE_ENABLED": "🟢 Включен",
    "ADMIN_SERVER_TOGGLE_DISABLE_CONFIRM": "Вы уверены, что хотите отключить сервер?",
    "ADMIN_SERVER_TOGGLE_ENABLE_CONFIRM": "Вы уверены, что хотите включить сервер?",
    "ADMIN_SERVER_TOGGLE_SUCCESS": "✅ Статус сервера изменен.",
    "ADMIN_SERVER_URL_UPDATED": "✅ URL сервера обновлен.",
    "ADMIN_SUBSCRIPTION_HEADER": "💳 Управление подпиской пользователя",
    "ADMIN_SUB_CHANGE_FAILED": "❌ Ошибка при изменении подписки.",
    "ADMIN_SUB_CHANGE_TARIFF_HEADER": "Выберите новый тариф:",
    "ADMIN_SUB_CONFIRM_EXTEND": "Вы уверены, что хотите продлить подписку?",
    "ADMIN_SUB_CONFIRM_GRANT": "Вы уверены, что хотите выдать подписку?",
    "ADMIN_SUB_CONFIRM_REDUCE": "Вы уверены, что хотите сократить подписку?",
    "ADMIN_SUB_CONFIRM_TARIFF": "Вы уверены, что хотите сменить тариф?",
    "ADMIN_SUB_DOWNGRADE_BLOCKED": "❌ Нельзя понизить тариф (превышен лимит устройств).",
    "ADMIN_SUB_EXTEND_FAILED": "❌ Не удалось продлить подписку.",
    "ADMIN_SUB_EXTEND_HEADER": "Продление подписки",
    "ADMIN_SUB_EXTEND_PROMPT": "На сколько дней продлить подписку?",
    "ADMIN_SUB_EXTEND_SUCCESS": "✅ Подписка успешно продлена.",
    "ADMIN_SUB_GRANT_CUSTOM_PROMPT": "Введите количество дней:",
    "ADMIN_SUB_GRANT_DAYS_HEADER": "На какой срок выдать подписку?",
    "ADMIN_SUB_GRANT_FAILED": "❌ Ошибка при выдаче подписки.",
    "ADMIN_SUB_GRANT_HEADER": "Выдача новой подписки",
    "ADMIN_SUB_GRANT_SUCCESS": "✅ Подписка успешно выдана.",
    "ADMIN_SUB_GROUP_NOT_FOUND": "❌ Группа тарифов не найдена.",
    "ADMIN_SUB_NO_SUBSCRIPTION": "У пользователя нет активной подписки.",
    "ADMIN_SUB_PERMANENT_LABEL": "Навсегда",
    "ADMIN_SUB_REDUCED": "✅ Подписка успешно сокращена.",
    "ADMIN_SUB_REDUCE_FAILED": "❌ Не удалось сократить подписку.",
    "ADMIN_SUB_REDUCE_PROMPT": "На сколько дней сократить подписку?",
    "ADMIN_SUB_STATUS_ACTIVE": "Активна",
    "ADMIN_SUB_STATUS_INACTIVE": "Неактивна",
    "ADMIN_SUB_STATUS_NONE": "Отсутствует",
    "ADMIN_SUB_TARIFF_ALREADY_SELECTED": "❌ Этот тариф уже активен.",
    "ADMIN_SUB_TARIFF_CHANGED": "✅ Тариф успешно изменен.",
    "ADMIN_TARIFF_EDIT_RUB_PROMPT": "Введите новую цену в рублях:",
    "ADMIN_TARIFF_EDIT_RUB_SUCCESS": "✅ Цена тарифа успешно обновлена.",
    "ADMIN_TARIFF_TOGGLE_BLOCKED_PENDING": "❌ Нельзя отключить тариф, есть незавершенные платежи.",
    "ADMIN_TARIFF_TOGGLE_SUCCESS_DISABLED": "✅ Тариф отключен.",
    "ADMIN_TARIFF_TOGGLE_SUCCESS_ENABLED": "✅ Тариф включен.",
    "ADMIN_UNBAN_CONFIRM": "Вы уверены, что хотите разбанить пользователя?",
    "ADMIN_USER_DEVICES_EMPTY": "Нет подключенных устройств.",
    "ADMIN_USER_DEVICES_HEADER": "📱 Устройства пользователя",
    "ALERT_CRITICAL_BOT_ERROR": "❌ Произошла критическая ошибка бота.",
    "ERROR_SERVER_DUPLICATE_URL": "❌ Сервер с таким URL уже существует.",
    "PAYMENT_STATUS_NAMES": {"completed": "Выполнен", "cancelled": "Отменен", "failed": "Ошибка", "refunded": "Возврат", "requires_manual_review": "Ручная проверка", "pending": "Ожидание", "paid_processing": "Обработка"},
    "ADMIN_PAYMENT_USER_WITH_USERNAME": "@{username}",
    "ADMIN_PAYMENT_USER_WITH_ID": "ID: {user_id}",
    "PAYMENT_SUBSCRIPTION_INACTIVE": "⚠️ Смена тарифа с перерасчётом остатка возможна только при действующей подписке.\n\nВаша подписка неактивна. Перейдите в раздел «Купить подписку» для оформления нового тарифа.",
    "PAYMENT_CURRENT_TARIFF_UNKNOWN": "⚠️ Смена тарифа возможна только при действующей подписке.\n\nПерейдите в раздел «Купить подписку» для оформления нового тарифа.",
    "PAYMENT_ACTIVE_CHECKOUT_EXISTS": "⚠️ У вас уже есть не завершённая операция покупки. Завершите её или попробуйте чуть позже.",
    "PAYMENT_ACTIVE_CHANGE_QUOTE_EXISTS": "⚠️ У вас уже создан запрос на смену тарифа. Нажмите назад и выберите его.",
    "PAYMENT_CHANGE_TARIFF_UNAVAILABLE_NO_SUB": "⚠️ <b>Смена тарифа недоступна</b>\n\nСмена тарифа с перерасчётом доступна только при наличии действующей подписки.\nУ вас сейчас нет активной подписки. Для оформления подписки воспользуйтесь кнопкой ниже.",
}

OVERRIDES["HUB_HEADER"] = """🏠 <b>Главное меню</b>

👋 Привет, <b>{name}</b>!
🆔 <b>Telegram ID:</b> <code>{telegram_id}</code>

<b>📊 Статус подписки:</b> {status}
<b>⏳ Действует до:</b> {valid_until} ({days_left})
<b>📱 Подключено устройств:</b> {devices_count}/{device_limit}

<b>💰 Баланс:</b> {real_balance} ₽
<b>🎁 Бонусный баланс:</b> {bonus_balance} ₽{inviter_line}

Выберите нужный раздел:"""

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

📌 <b>3. Что делать, если VPN не подключается или медленно работает?</b>
• Попробуйте сменить сервер подключения в разделе «🔌 Подключения».
• Проверьте подключение к сети (переключитесь с Wi-Fi на 4G/LTE или наоборот).
• Если проблема сохраняется — напишите в «💬 Поддержка», и мы поможем оперативно восстановить доступ.

📌 <b>4. Как продлить подписку или сменить тариф?</b>
• В главном меню откройте «⏳ Подписка» или «🛒 Купить подписку».
• Выберите удобный срок и тариф. При смене тарифа оставшиеся дни автоматически пересчитываются.

📌 <b>5. На скольких устройствах одновременно работает подписка?</b>
• <b>Базовый:</b> до 2 устройств
• <b>Семейный:</b> до 5 устройств
• <b>Pro:</b> до 10 устройств

📌 <b>6. Как получить бонусные дни за приглашенных друзей?</b>
• Откройте «🤝 Пригласить друга» в главном меню.
• Скопируйте вашу персональную ссылку и отправьте друзьям. За каждого приглашенного друга вы получаете бонусы при пополнении.

📌 <b>7. Безопасность и конфиденциальность</b>
• Мы придерживаемся строгой политики No-Logs (не фиксируем и не храним истории подключений).
• Весь трафик надежно шифруется современными устойчивыми протоколами."""

OVERRIDES["PRIVACY_POLICY_URL"] = "https://telegra.ph/Politika-konfidencialnosti-07-23-84"
