# bot/texts_data/overrides.py
#
# Точечные переопределения и согласованные шаблоны текстов интерфейса.
# Применяются поверх базовых текстов из user_texts, admin_texts, ui_texts.

OVERRIDES = {
    # ------------------------------------------------------------
    # Серверы
    # ------------------------------------------------------------
    "ADMIN_SERVER_URL_PROMPT": (
        "🔗 Введите API URL сервера "
        "(например: https://api.example.com:8443):"
    ),
    "ERROR_INVALID_URL": """⚠️ Некорректный формат URL.

URL должен начинаться с <code>https://</code>

Пример: <code>https://api.example.com:8443</code>""",
    "ERROR_SERVER_DUPLICATE_URL": "❌ Сервер с API URL <code>{api_url}</code> уже зарегистрирован в системе.",
    "ADMIN_SERVER_ADDED": """🛠 Админка › 🖥 <b>Серверы</b>

✅ <b>Сервер успешно добавлен!</b>

<b>Название:</b> {flag} {name}
<b>Протокол:</b> {protocol}
<b>Лимит клиентов:</b> {max_clients}
<b>API URL:</b> <code>{api_url}</code>""",
    "ADMIN_SERVER_CHECKING": "🔄 Проверка сервера...",
    "ADMIN_SERVER_DELETE_CONFIRM": """⚠️ <b>Удаление сервера</b>

Вы уверены, что хотите удалить сервер {flag} <b>{name}</b>?
📱 Активных устройств на сервере: <b>{profiles_count}</b>""",
    "ADMIN_SERVER_EDIT_KEY_PROMPT": "Введите новый ключ для сервера:",
    "ADMIN_SERVER_EDIT_MAX_CLIENTS_PROMPT": "Введите максимальное количество клиентов для сервера:",
    "ADMIN_SERVER_EDIT_URL_PROMPT": "Введите новый URL сервера:",
    "ADMIN_SERVER_FLAG_PROMPT": "Выберите флаг для сервера:",
    "ADMIN_SERVER_FLAG_PROMPT_EDIT": "Текущий флаг: {current_flag}\nВыберите новый флаг для сервера:",
    "ADMIN_SERVER_FLAG_TOO_LONG": "❌ Флаг слишком длинный.",
    "ADMIN_SERVER_FLAG_UPDATED": "✅ Флаг сервера обновлен на {flag}.",
    "ADMIN_SERVER_KEY_PROMPT": "Введите ключ доступа:",
    "ADMIN_SERVER_KEY_UPDATED": "✅ Ключ сервера обновлен.",
    "ADMIN_SERVER_MAX_CLIENTS_UPDATED": "✅ Макс. количество клиентов обновлено: <b>{max_clients}</b>.",
    "ADMIN_SERVER_MAX_CLIENTS_WARNING": "⚠️ Внимание: на сервере уже <b>{current}</b> клиентов, что больше нового лимита (<b>{new}</b>).",
    "ADMIN_SERVER_NAME_PROMPT": "Введите название сервера:",
    "ADMIN_SERVER_RENAMED": "✅ Сервер успешно переименован в <b>{name}</b>.",
    "ADMIN_SERVER_RENAME_PROMPT": "Введите новое имя сервера:",
    "ADMIN_SERVER_STATE_DISABLED": "🔴 Отключен",
    "ADMIN_SERVER_STATE_ENABLED": "🟢 Включен",
    "ADMIN_SERVER_TOGGLE_DISABLE_CONFIRM": "Вы уверены, что хотите отключить сервер {flag} <b>{name}</b>?",
    "ADMIN_SERVER_TOGGLE_ENABLE_CONFIRM": "Вы уверены, что хотите включить сервер {flag} <b>{name}</b>?",
    "ADMIN_SERVER_TOGGLE_SUCCESS": "✅ Статус сервера изменен: <b>{status}</b>",
    "ADMIN_SERVER_URL_UPDATED": "✅ API URL сервера обновлен на <code>{api_url}</code>.",

    # ------------------------------------------------------------
    # Пользователи и баны
    # ------------------------------------------------------------
    "ADMIN_BAN_CONFIRM": "Вы уверены, что хотите забанить пользователя <code>{telegram_id}</code>?",
    "ADMIN_UNBAN_CONFIRM": "Вы уверены, что хотите разбанить пользователя <code>{telegram_id}</code>?",
    "ADMIN_BAN_SUCCESS": "✅ Пользователь успешно забанен.\n{message}",
    "ADMIN_BAN_FAILED": "❌ Ошибка при попытке забанить пользователя:\n{message}",
    "ADMIN_DELETE_DEVICE_CONFIRM": "Удалить устройство <b>{device_name}</b> ({flag} {server_name}) у пользователя <code>{telegram_id}</code>?",
    "ADMIN_DELETE_DEVICE_ERROR": "❌ Произошла ошибка при удалении устройства.",
    "ADMIN_DELETE_DEVICE_FAILED": "❌ Не удалось удалить устройство.",
    "ADMIN_DELETE_DEVICE_SUCCESS": "✅ Устройство <b>{device_name}</b> пользователя <code>{telegram_id}</code> успешно удалено.",
    "ADMIN_MANUAL_GRANT_USER_BANNED": "❌ Невозможно выдать подписку: пользователь забанен.",
    "ADMIN_MANUAL_GRANT_USER_DELETED": "❌ Невозможно выдать подписку: пользователь удален.",

    # ------------------------------------------------------------
    # Управление подписками
    # ------------------------------------------------------------
    "ADMIN_SUBSCRIPTION_HEADER": "🛠 Админка › 👥 Пользователь <code>{telegram_id}</code> › 💳 <b>Подписка</b>\n\n{status_block}",
    "ADMIN_SUB_STATUS_ACTIVE": "<b>Статус:</b> 🟢 Активна ({valid_until}, {time_left})\n<b>Тариф:</b> {tariff_name}\n<b>Устройств:</b> {devices_count}/{device_limit}",
    "ADMIN_SUB_STATUS_INACTIVE": "<b>Статус:</b> 🔴 Истекла ({valid_until})\n<b>Тариф:</b> {tariff_name}",
    "ADMIN_SUB_STATUS_NONE": "<b>Статус:</b> ⚪️ Нет подписки\n<b>Устройств:</b> {devices_count}",

    "ADMIN_SUB_EXTEND_HEADER": "⏳ Продление подписки для <code>{telegram_id}</code>\nТекущий срок: <code>{valid_until}</code>",
    "ADMIN_SUB_EXTEND_PROMPT": "На сколько дней продлить подписку для <code>{telegram_id}</code>?",
    "ADMIN_SUB_CONFIRM_EXTEND": "Продлить подписку пользователю <code>{telegram_id}</code> на <b>{days_text}</b> (текущий: <code>{current_end}</code>, новый: <code>{new_end}</code>)?",
    "ADMIN_SUB_EXTEND_SUCCESS": "✅ Подписка пользователя <code>{telegram_id}</code> продлена на <b>{days_text}</b> (до <code>{new_end}</code>).",
    "ADMIN_SUB_EXTEND_FAILED": "❌ Не удалось продлить подписку.",

    "ADMIN_SUB_GRANT_HEADER": "🎁 Выдача подписки для <code>{telegram_id}</code>\nВыберите тариф:",
    "ADMIN_SUB_GRANT_DAYS_HEADER": "🎁 Выдача тарифа <b>{tariff_name}</b> для <code>{telegram_id}</code>\nВыберите срок:",
    "ADMIN_SUB_GRANT_CUSTOM_PROMPT": "Введите количество дней для тарифа <b>{tariff_name}</b> (пользователь <code>{telegram_id}</code>):",
    "ADMIN_SUB_CONFIRM_GRANT": "Выдать тариф <b>{tariff_name}</b> пользователю <code>{telegram_id}</code> на <b>{days_text}</b> (до <code>{new_end}</code>)?",
    "ADMIN_SUB_GRANT_SUCCESS": "✅ Пользователю <code>{telegram_id}</code> выдан тариф <b>{tariff_name}</b> на <b>{days_text}</b> (до <code>{new_end}</code>).",
    "ADMIN_SUB_GRANT_FAILED": "❌ Ошибка при выдаче подписки.",

    "ADMIN_SUB_CHANGE_TARIFF_HEADER": "⚙️ Смена тарифа для <code>{telegram_id}</code>\nТекущий тариф: <b>{current_tariff}</b> ({devices_count} устр.)\nВыберите новый тариф:",
    "ADMIN_SUB_CONFIRM_TARIFF": "Сменить тариф пользователю <code>{telegram_id}</code> с <b>{old_tariff}</b> на <b>{new_tariff}</b> ({devices_count} устр.)?",
    "ADMIN_SUB_TARIFF_CHANGED": "✅ Тариф пользователя <code>{telegram_id}</code> изменен на <b>{tariff_name}</b> (лимит: {device_limit} устр.).",
    "ADMIN_SUB_DOWNGRADE_BLOCKED": "❌ Нельзя понизить тариф для <code>{telegram_id}</code>: подключено {devices_count} устр., а лимит нового тарифа — {new_limit}.",
    "ADMIN_SUB_CHANGE_FAILED": "❌ Ошибка при изменении подписки.",
    "ADMIN_SUB_TARIFF_ALREADY_SELECTED": "❌ Этот тариф уже активен.",
    "ADMIN_SUB_GROUP_NOT_FOUND": "❌ Группа тарифов не найдена.",
    "ADMIN_SUB_NO_SUBSCRIPTION": "У пользователя нет активной подписки.",
    "ADMIN_SUB_PERMANENT_LABEL": "Навсегда",

    "ADMIN_SUB_REDUCE_PROMPT": "✂️ Сокращение подписки для <code>{telegram_id}</code>\nТекущий срок: <code>{valid_until}</code>\nНа сколько дней сократить?",
    "ADMIN_SUB_CONFIRM_REDUCE": "Сократить подписку пользователю <code>{telegram_id}</code> на <b>{days}</b> дн. (текущий: <code>{current_end}</code>, новый: <code>{new_end}</code>)?",
    "ADMIN_SUB_REDUCED": "✅ Подписка пользователя <code>{telegram_id}</code> сокращена до <code>{new_end}</code>.",
    "ADMIN_SUB_REDUCE_FAILED": "❌ Не удалось сократить подписку.",

    # ------------------------------------------------------------
    # Тарифы и Платежи
    # ------------------------------------------------------------
    "ADMIN_TARIFF_EDIT_RUB_PROMPT": "Введите новую цену в рублях:",
    "ADMIN_TARIFF_EDIT_RUB_SUCCESS": "✅ Цена тарифа успешно обновлена: <b>{value} ₽</b>",
    "ADMIN_TARIFF_TOGGLE_BLOCKED_PENDING": "❌ Нельзя отключить тариф, есть незавершенные платежи.",
    "ADMIN_TARIFF_TOGGLE_SUCCESS_DISABLED": "✅ Тариф отключен.",
    "ADMIN_TARIFF_TOGGLE_SUCCESS_ENABLED": "✅ Тариф включен.",

    "PAYMENT_STATUS_NAMES": {
        "completed": "Выполнен",
        "cancelled": "Отменен",
        "failed": "Ошибка",
        "refunded": "Возврат",
        "requires_manual_review": "Ручная проверка",
        "pending": "Ожидание",
        "paid_processing": "Обработка",
    },
    "ADMIN_PAYMENT_USER_WITH_ID": "ID: <code>{user_id}</code> (@{username})",

    "PAYMENT_SUBSCRIPTION_INACTIVE": (
        "⚠️ Смена тарифа с перерасчётом остатка возможна только при действующей подписке.\n\n"
        "Ваша подписка неактивна. Перейдите в раздел «Купить подписку» для оформления нового тарифа."
    ),
    "PAYMENT_CURRENT_TARIFF_UNKNOWN": (
        "⚠️ Смена тарифа возможна только при действующей подписке.\n\n"
        "Перейдите в раздел «Купить подписку» для оформления нового тарифа."
    ),
    "PAYMENT_ACTIVE_CHECKOUT_EXISTS": (
        "⚠️ У вас уже есть не завершённая операция покупки. Завершите её или попробуйте чуть позже."
    ),
    "PAYMENT_ACTIVE_CHANGE_QUOTE_EXISTS": (
        "⚠️ У вас уже создан запрос на смену тарифа. Нажмите назад и выберите его."
    ),
    "PAYMENT_CHANGE_TARIFF_UNAVAILABLE_NO_SUB": (
        "⚠️ <b>Смена тарифа недоступна</b>\n\n"
        "Смена тарифа с перерасчётом доступна только при наличии действующей подписки.\n"
        "У вас сейчас нет активной подписки. Для оформления подписки воспользуйтесь кнопкой ниже."
    ),

    # ------------------------------------------------------------
    # Системные алерты
    # ------------------------------------------------------------
    "ALERT_CRITICAL_BOT_ERROR": "🚨 <b>Критическая ошибка бота</b>\n<b>Тип:</b> <code>{error_type}</code>\n<b>Request ID:</b> <code>{request_id}</code>\n<b>Детали:</b> <code>{error_short}</code>",

    # ------------------------------------------------------------
    # Главное меню (Главный Хаб)
    # ------------------------------------------------------------
    "HUB_HEADER": """🏠 <b>Главное меню</b>

👋 Привет, <b>{name}</b>!
🆔 <b>Telegram ID:</b> <code>{telegram_id}</code>

<b>📊 Статус подписки:</b> {status}
<b>⏳ Действует до:</b> {valid_until} ({days_left})
<b>📱 Подключено устройств:</b> {devices_count}/{device_limit}

<b>💰 Баланс:</b> {real_balance} ₽
<b>🎁 Бонусный баланс:</b> {bonus_balance} ₽{inviter_line}

Выберите нужный раздел:""",

    # ------------------------------------------------------------
    # Устройства и подключение
    # ------------------------------------------------------------
    "DEVICE_MANAGE_HEADER": """📱 <b>{device_name}</b>

🌍 Локация: <b>{country_display}</b>
📡 Протокол: <b>{protocol}</b>
📊 Трафик: <b>{traffic_total}</b>
⏱ Активность: <b>{last_connected}</b>""",

    "DEVICE_SHOW_KEY": """🔑 <b>Ключ подключения для {device_name}:</b>

<code>{raw_config}</code>

<i>💡 Нажмите на моноширинный текст выше, чтобы скопировать ключ в буфер обмена.</i>""",

    "DEVICE_CONFIG_VPN_CAPTION": """📁 <b>Файл для AmneziaVPN</b>

📱 Устройство: <b>{device_name}</b>

<i>Файл конфигурации (.vpn) — дополнительный способ подключения, если ключ не подходит.</i>""",

    "DEVICE_CONFIG_CONF_CAPTION": """📁 <b>Файл для AmneziaWG</b>

📱 Устройство: <b>{device_name}</b>

<i>Файл конфигурации (.conf) — дополнительный способ подключения, если ключ не подходит.</i>""",

    "DOWNLOAD_CONF_FALLBACK": """⚠️ <b>Не удалось сформировать файл</b> для устройства <b>{device_name}</b>.

Скопируйте ключ подключения на карточке устройства для импорта в приложение <b>AmneziaVPN</b> или обратитесь в <b>💬 Поддержку</b>.""",
}
