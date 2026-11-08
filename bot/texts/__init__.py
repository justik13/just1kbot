"""Single Source of Truth for all user-facing and admin texts in Just1k 2.0.

STRICT COMPLIANCE NOTICE:
In compliance with payment provider (YooKassa) requirements and GEMINI.md Rule 5,
the following terms are strictly forbidden in any user-facing text:
'VPN', 'ВПН', 'обход блокировок', 'белые списки', 'глушилки', 'LTE', 'xray', 'vless'.
"""

# URLs and Links
PRIVACY_POLICY_URL = "https://just1k.pro/privacy"
TOS_AGREEMENT_URL = "https://just1k.pro/terms"

# Common UI Buttons
BTN_BACK = "◀️ Назад"
BTN_MAIN_MENU_NAV = "🏠 Главное меню"
BTN_HUB_CONNECT = "⚡️ Подключить"
BTN_HUB_REFERRALS = "👥 Рефералы"
BTN_HUB_BALANCE = "💳 Баланс {real}/{bonus}₽"
BTN_HUB_PROXY = "✈️ Прокси TG"
BTN_HUB_CHANNEL = "📢 ТГ КАНАЛ"
BTN_HUB_INFO = "ℹ️ Информация"
BTN_SUPPORT = "💬 Поддержка"
BTN_ADMIN = "🛠 Управление"
BTN_ADMIN_MENU = "🛠 Меню администратора"
BTN_CONTACT_SUPPORT = "💬 Написать в поддержку"
BTN_HELP_INSTRUCTIONS = "📖 Инструкции"
BTN_TOS = "📄 Пользовательское соглашение"
BTN_PRIVACY_POLICY = "🔒 Политика конфиденциальности"
BTN_DISMISS_ALERT = "✖️ Закрыть"
BTN_DISMISS_NOTIFICATION = "✖️ Понятно"
BTN_PAGINATION_PREV = "◀️"
BTN_PAGINATION_NEXT = "▶️"
BTN_INVITE_FRIEND = "🎁 Поделиться"
BTN_BUY_ACCESS = "💳 Оформить доступ"
BTN_BUY_SUBSCRIPTION = "💳 Оформить подписку"
BTN_RENEW_ACCESS = "🔄 Продлить"
BTN_CONNECTIONS = "📱 Устройства"
BTN_SPLIT_TUNNELING = "⚙️ Настройка приложений"
BTN_INSTRUCTION_INCY = "📱 Инструкция для приложения"
BTN_MY_BALANCE = "💰 Мой баланс"
BTN_USERS_AND_BROADCAST = "👥 Пользователи и рассылка"
BTN_SERVERS_AND_TARIFFS = "🖥 Сервера и тарифы"
BTN_SERVERS_LIST = "🖥 Список серверов"
BTN_ADMIN_PURCHASES_LOGS = "📜 Журнал покупок"
BTN_ADMIN_YOOKASSA_PAYMENTS = "💳 Платежи ЮKassa"
BTN_BACK_TO_FINANCES = "◀️ Назад к финансам"
BTN_OPEN_USER_CARD = "👤 Профиль пользователя"
BTN_STD_SUB = "Стандартный доступ"
BTN_WI_SUB = "Белый Интернет"
BTN_WHITE_INTERNET = "Белый Интернет"
BTN_WL_TOPUP = "➕ Докупить трафик"
BTN_WL_CONNECT_CLIENT = "⚡️ Подключить"

# Hub & Menu Texts
USER_HUB_START = "друг"
PLACEHOLDER_DASH = "—"
ZERO_DAYS_LABEL = "0 дней"
USER_ID_LABEL = "ID"
CURRENCY_RUB_SYMBOL = "₽"
NOUN_USERS = "пользователей"

STATUS_SUBSCRIPTION_ACTIVE = "🟢 Активен"
STATUS_SUBSCRIPTION_INACTIVE = "🔴 Не активен"

HUB_HEADER = (
    "<b>Привет, {name}!</b>\n\n"
    "<b>Статус:</b> {status}\n"
    "<b>Срок действия:</b> {valid_until} ({days_left})\n"
    "<b>Устройств:</b> {devices_count} из {device_limit}\n"
    "{white_internet_line}"
    "<b>Баланс:</b> {real_balance} ₽\n"
    "{bonus_line}"
    "{inviter_line}\n"
    "<b>Ваш ID:</b> <code>{telegram_id}</code>"
)

HUB_WHITE_INTERNET_LINE_FORMAT = (
    "<b>Белый Интернет:</b> {status_icon} до {expiry} ({days_left})\n"
    "  └ Слоты: {devices_count}/{device_limit} | Трафик: {traffic_used}/{traffic_total} ГБ\n"
)

HUB_BONUS_LINE_FORMAT = "<b>Бонусный баланс:</b> {bonus_balance} ₽\n"
INVITED_BY_NAMED_LINE = "<b>Вас пригласил:</b> {name}\n"
INVITED_BY_ID_LINE = "<b>Вас пригласил ID:</b> <code>{referrer_id}</code>\n"

# Support & Info
SUPPORT = "Поддержка"
SUPPORT_TEXT = (
    "<b>Центр информации и помощи</b>\n\n"
    "Здесь вы можете получить помощь по работе сервиса, ознакомиться с правилами "
    "и инструкциями по подключению."
)
SUPPORT_GREETING_TEMPLATE = "Здравствуйте! Мой Id: {telegram_id}"
SUPPORT_HELP_ROOT_TEXT = (
    "<b>Инструкции по подключению</b>\n\n"
    "1. Нажмите кнопку <b>«Подключить»</b> в главном меню для перехода в личный кабинет.\n"
    "2. Скопируйте персональную ссылку доступа.\n"
    "3. Вставьте ссылку в поддерживаемое приложение (INCY или Happ).\n"
    "4. Включите соединение."
)
SUPPORT_INCY_INSTRUCTION_TEXT = (
    "<b>Подключение через INCY / Happ</b>\n\n"
    "1. Установите приложение на ваше устройство.\n"
    "2. Скопируйте персональную ссылку подписки из кабинета.\n"
    "3. Откройте приложение, добавьте подписку по ссылке.\n"
    "4. Выберите нужную локацию и нажмите кнопку включения."
)
SUPPORT_SPLIT_TUNNELING_TEXT = (
    "<b>Раздельное подключение</b>\n\n"
    "В приложении вы можете настроить список сайтов и приложений, "
    "которые будут работать напрямую или через защищенное соединение."
)
FAQ_TEXT = (
    "<b>Часто задаваемые вопросы (FAQ)</b>\n\n"
    "<b>В: Как работает подписка?</b>\n"
    "О: Вы получаете единую персональную ссылку, в которой сразу доступны все оплаченные режимы.\n\n"
    "<b>В: Сколько устройств можно подключить?</b>\n"
    "О: В стандартном тарифе доступно 2 устройства. Вы можете докупить слоты в кабинете.\n\n"
    "<b>В: Что делать, если не работает?</b>\n"
    "О: Напишите в нашу службу поддержки, мы оперативно поможем настроить подключение."
)

# Balance & Payments
BALANCE_BALANCE = "Баланс"
HISTORY_HEADER = "<b>История операций:</b>\n\n"
HISTORY_EMPTY = "История операций пуста."
HISTORY_LIMIT_NOTE = "\n<i>Отображены последние 10 операций.</i>"
TOPUP_CREDITED_NOTICE = "✅ Баланс успешно пополнен на {amount} ₽!"
CHECKOUT_DESCRIPTION_DEFAULT = "Пополнение счета в Just1k"
PAYMENT_PURCHASE_SUCCESS_CARD = "✅ Доступ успешно подключен!"
PURCHASE_COMPLETED = "✅ Оплата прошла успешно!"

PAYMENT_OP_TITLE_CHANGE = "Смена тарифа"
PAYMENT_STATUS_PENDING_ICON = "⏳"
PAYMENT_STATUS_NAMES = {
    "pending": "Ожидает оплаты",
    "succeeded": "Оплачен",
    "canceled": "Отменен",
    "refunded": "Возврат",
}
PAYMENT_STATUS_ICONS = {
    "pending": "⏳",
    "succeeded": "✅",
    "canceled": "❌",
    "refunded": "↩️",
}

# Referrals
REFERRAL_TEXT_BALANCE = (
    "<b>Партнерская программа</b>\n\n"
    "Приглашайте друзей и получайте бонусные рубли с каждой их оплаты!\n\n"
    "<b>Ваш статус:</b> {tier_name} ({percent}%)\n"
    "<b>Активных рефералов:</b> {active_count}\n"
    "<b>Всего приглашено:</b> {total_count}\n"
    "<b>Заработано:</b> {total_earned} ₽\n\n"
    "<b>Ваша пригласительная ссылка:</b>\n"
    "<code>{invite_url}</code>\n\n"
    "<i>Реферал учитывается в квалификации при сумме покупок от 67 ₽. "
    "Новым пользователям доступна скидка на первую покупку!</i>"
)
REFERRAL_ONBOARDING_NOTIFICATION = (
    "👋 По вашей ссылке зарегистрировался новый пользователь!"
)
REFERRAL_BONUS_ACCREDITED = (
    "🎉 Реферал совершил оплату! Вам начислено <b>+{amount} ₽</b> на бонусный баланс."
)
REFERRAL_BONUS_ACCREDITED_DETAILED = (
    "🎉 Реферал совершил покупку на {order_amount} ₽!\n"
    "Вам начислено <b>+{amount} ₽</b> ({percent}%) на бонусный баланс."
)
REFERRAL_BONUS_WITH_UPGRADE_NOTIFICATION = (
    "🎉 Вам начислено <b>+{amount} ₽</b>!\n"
    "🚀 Поздравляем! Ваш реферальный уровень повышен до <b>{new_tier}</b> ({percent}%)."
)
REFERRAL_TIER_PROGRESS_NEXT = "До следующего уровня ({next_tier}) осталось: {needed} рефералов."
REFERRAL_TIER_PROGRESS_MAX = "У вас максимальный партнерский уровень!"
REFERRAL_LIST_HEADER = "<b>Ваши рефералы:</b>\n\n"
REFERRAL_LIST_EMPTY = "У вас пока нет приглашенных пользователей."
REFERRAL_LIST_ITEM_FORMAT = "• {name} — {status} ({total_spent} ₽)\n"
REFERRAL_LIST_FOOTER = "\nСтраница {page} из {total_pages}"
REFERRAL_LEADERBOARD_TITLE = "🏆 <b>Топ 5 рефереров</b>\n\n"
REFERRAL_LEADERBOARD_ITEM = "{rank}. {name} — {count} приглашенных\n"
REFERRAL_LEADERBOARD_MEDALS = {1: "🥇", 2: "🥈", 3: "🥉"}
REFERRAL_LEADERBOARD_YOUR_RANK = "\nВаше место в рейтинге: <b>#{rank}</b>"
REFERRAL_LEADERBOARD_NOT_RANKED = "\nВы пока не в рейтинге."
REFERRAL_LEADERBOARD_USER_YOU = "Вы"
REFERRAL_LEADERBOARD_USER_OTHER = "Пользователь"
REFERRAL_LEADERBOARD_USER_OPEN = "{name}"
REFERRAL_LEADERBOARD_USER_OPEN_YOU = "Вы ({name})"
REFERRAL_MAIN_RANK_RANKED = "Ваше место в топе: #{rank}"
REFERRAL_MAIN_RANK_NOT_RANKED = "Вне рейтинга"
LEADERBOARD_ANONYMITY_TOGGLED = "Настройки отображения в топе обновлены."

# Fallbacks and Errors
ERROR_TECHNICAL_MESSAGE = "Произошла временная техническая ошибка. Пожалуйста, попробуйте позже."
ERROR_TECHNICAL_ALERT = "Техническая ошибка. Попробуйте снова."
ERROR_ACCESS_DENIED = "Доступ запрещен."
ERROR_BANNED_MESSAGE = "Ваш аккаунт заблокирован администратором."
ERROR_BANNED_ALERT = "Действие недоступно: аккаунт заблокирован."
ERROR_USER_NOT_FOUND = "Пользователь не найден."
ERROR_ACTION_IN_PROGRESS = "Предыдущее действие еще выполняется. Подождите пару секунд."
ERROR_TOO_FREQUENT = "Слишком много запросов. Подождите немного."
ERROR_OPERATION_CANCELLED = "Операция отменена."
ERROR_INVALID_REQUEST = "Некорректный запрос."
ERROR_CONFIRMATION_EXPIRED = "Время действия подтверждения истекло."
ERROR_TEXT_REQUIRED = "Пожалуйста, отправьте текст сообщения."
ERROR_TEXT_EMPTY = "Текст сообщения не может быть пустым."
ERROR_TEXT_OR_MEDIA = "Пожалуйста, отправьте текст или медиафайл."
ERROR_VALIDATION = "Ошибка валидации данных."
FALLBACK_FLOW_IN_PROGRESS = "Действие отменено, возврат в меню."
FALLBACK_STALE_CALLBACK = "Кнопка устарела, возвращаемся в меню."
FALLBACK_UNKNOWN_TEXT = "Команда не распознана. Используйте меню ниже."
FALLBACK_UNSUPPORTED_TYPE = "Данный тип сообщений не поддерживается."
MAINTENANCE_DEFAULT_MESSAGE = "🛠 Сервис временно на техническом обслуживании. Мы скоро вернемся!"
ALERT_CRITICAL_BOT_ERROR = "Произошла системная ошибка."
ALERT_COPY_TEXT_TOO_LONG = "Текст слишком длинный для копирования."

# Feed Notices (Zero Endpoint Notice Nodes for INCY / Happ)
UNIFIED_FEED_NOTICE_EXPIRED = "Подписка истекла. Пожалуйста, продлите доступ в боте."
UNIFIED_FEED_NOTICE_TOTAL_LIMIT = "Превышен лимит устройств. Управляйте устройствами в боте."
UNIFIED_FEED_NOTICE_WI_EXHAUSTED = "Трафик Белого Интернета закончился. Докупите в боте."
UNIFIED_FEED_NOTICE_WI_LIMIT = "Лимит устройств Белого Интернета исчерпан."
VLESS_WEB_TOO_MANY_REQUESTS = "Слишком много запросов к подписке. Пожалуйста, повторите позже."

# Bot Description
BOT_START_DESCRIPTION = "Быстрый и надежный доступ к интернет-ресурсам."
BOT_SHORT_DESCRIPTION = "Сервис надежного доступа Just1k."
BOT_DESCRIPTION = (
    "Just1k — сервис для быстрого и надежного доступа к нужным сайтам и ресурсам.\n"
    "Поддерживает подключение телефонов, планшетов и компьютеров."
)
WELCOME_TEXT = "Добро пожаловать в Just1k!"

# Admin Panel Texts
ADMIN_BREADCRUMB_HOME = "Админка"
ADMIN_BREADCRUMB_SEPARATOR = " » "
ADMIN_BREADCRUMB_TEMPLATE = "<b>{path}</b>\n\n"
ADMIN_BTN_BACK_TO_ADMIN = "◀️ В админку"
ADMIN_BTN_BACK_TO_PAYMENTS = "◀️ К платежам"
ADMIN_BTN_BACK_TO_USER_PAYMENTS = "◀️ К платежам пользователя"
ADMIN_BTN_PAGINATION_PREV = "◀️ Назад"
ADMIN_BTN_PAGINATION_NEXT = "Вперед ▶️"
ADMIN_CLIENT_CARD_BUTTON = "👤 Карточка клиента"
ADMIN_MAINTENANCE_ENABLED_ANSWER = "Режим обслуживания включен."
ADMIN_MAINTENANCE_DISABLED_ANSWER = "Режим обслуживания выключен."
ADMIN_MAINTENANCE_MENU_ENABLED = "🔴 Режим обслуживания: ВКЛ"
ADMIN_MAINTENANCE_MENU_DISABLED = "🟢 Режим обслуживания: ВЫКЛ"
ADMIN_DASHBOARD_MAIN_DASHBOARD = "<b>Панель управления Just1k</b>\n\n"
ADMIN_DASHBOARD_REVENUE_24H_LINE = "<b>Доход за 24ч:</b> {revenue_24h} ₽\n"
ADMIN_DASHBOARD_SECTION_ORIGIN = "🌐 <b>Российские ХАБЫ:</b>\n"
ADMIN_DASHBOARD_SECTION_STD_SUB = "⚡️ <b>Стандартный доступ:</b>\n"
ADMIN_DASHBOARD_SECTION_VLESS = "⚡️ <b>VLESS узлы:</b>\n"
ADMIN_DASHBOARD_SECTION_WI_SUB = "🛡 <b>Белый Интернет:</b>\n"
ADMIN_DASHBOARD_SECTION_USERS_BROADCAST = "👥 Пользователи: {total_users}\n"
ADMIN_DASHBOARD_SERVER_ROW_FORMAT = "• {name}: {status} ({active_clients}/{max_clients})\n"
ADMIN_DASHBOARD_SERVERS_SECTION = "🖥 <b>Сервера:</b>\n"
ADMIN_DASHBOARD_VLESS_ROW_FORMAT = "• {name}: {status} ({active_clients} подкл.)\n"

ADMIN_ORDER_CARD_TEMPLATE = "<b>Заказ #{order_id}</b>\nСумма: {amount} ₽\nСтатус: {status}"
ADMIN_ORDER_DESCRIPTION_LINE = "Описание: {description}\n"
ADMIN_ORDER_DIAGNOSTICS_AMBIGUOUS = "Статус требует уточнения"
ADMIN_ORDER_DIAGNOSTICS_PENDING = "Ожидание оплаты"
ADMIN_ORDER_EXTERNAL_ID_LINE = "Внешний ID: {external_id}\n"
ADMIN_ORDER_HELD_LINE = "Удержание: да\n"
ADMIN_ORDER_PAID_AT_LINE = "Оплачен: {paid_at}\n"
ADMIN_ORDER_PAYMENT_URL_LINE = "Ссылка на оплату: {url}\n"
ADMIN_ORDER_REFUNDED_AT_LINE = "Возврат: {refunded_at}\n"
ADMIN_ORDER_SERVICE_TOPUP = "Пополнение счета"
ADMIN_PAYMENT_CARD_TEMPLATE = "<b>Платеж #{payment_id}</b>\nСумма: {amount} ₽\nСтатус: {status}"
ADMIN_PAYMENT_MANUAL_REVIEW_LINE = "⚠️ Требует ручной проверки\n"
ADMIN_PAYMENT_METHOD_BTN_ADMIN = "Администратор"
ADMIN_PAYMENT_NOT_FOUND_ALERT = "Платеж не найден."
ADMIN_PAYMENT_SERVICE_CHANGE_FMT = "Смена тарифа: {service}"
ADMIN_PAYMENT_SERVICE_RENEW_FMT = "Продление: {service}"
ADMIN_PAYMENT_SERVICE_STANDARD = "Стандартный доступ"
ADMIN_PAYMENT_SERVICE_WI_DEFAULT = "Белый Интернет"
ADMIN_PAYMENT_SERVICE_WI_QUOTA = "Докупка трафика БИ"
ADMIN_PAYMENT_SERVICE_WI_RENEW = "Продление БИ"
ADMIN_PAYMENT_SERVICE_WI_SLOT = "Доп. устройство БИ"
ADMIN_PAYMENT_SERVICE_WI_TRIAL = "Тестовый доступ БИ"
ADMIN_PAYMENT_STATUS_FALLBACK_ICON = "❓"
ADMIN_PAYMENT_STATUS_NOTE = "Статус платежа"
ADMIN_PAYMENT_USER_ID = "ID пользователя: {user_id}"
ADMIN_PAYMENT_USER_ID_COMPACT = "ID: {user_id}"
ADMIN_PAYMENT_USER_WITH_ID = "Пользователь: {name} (ID: {user_id})"
ADMIN_PAYMENTS_GATEWAY_YOOKASSA = "ЮKassa"
ADMIN_PAYMENTS_LIST_EMPTY = "Список платежей пуст."
ADMIN_PAYMENTS_LIST_TITLE = "💳 <b>Платежи</b>\n\n"
ADMIN_PAYMENTS_ROW_ENTRY = "#{id} — {amount} ₽ — {status}\n"
ADMIN_PAYMENTS_USER_EMPTY = "У пользователя нет платежей."
ADMIN_PAYMENTS_USER_REGISTRY_ROW = "#{id} | {date} | {amount} ₽ | {status}\n"
ADMIN_PAYMENTS_USER_REGISTRY_TITLE = "Платежи пользователя {user_id}"
ADMIN_PAYMENTS_USER_TITLE = "Платежи пользователя"
ADMIN_PURCHASES_AMOUNT_ZERO_BONUS = "Списание 0 ₽ (бонусы)"
ADMIN_PURCHASES_AMOUNT_ZERO_BONUS_GRANT = "Начисление бонусов"
ADMIN_PURCHASES_EMPTY_NOTICE = "Покупок не найдено."
ADMIN_PURCHASES_ENTRY_DATETIME_LINE = "Дата: {date}\n"
ADMIN_PURCHASES_ENTRY_DEVICE_LIMIT_LINE = "Лимит устройств: {limit}\n"
ADMIN_PURCHASES_ENTRY_DURATION_LINE = "Срок: {days} дн.\n"
ADMIN_PURCHASES_ENTRY_FUNDS_LINE = "Сумма: {amount} ₽\n"
ADMIN_PURCHASES_ENTRY_OPERATION_TYPE_LINE = "Тип операции: {type}\n"
ADMIN_PURCHASES_ENTRY_PAYMENT_METHOD_LINE = "Способ оплаты: {method}\n"
ADMIN_PURCHASES_ENTRY_SUMMARY_LINE = "Итог: {summary}\n"
ADMIN_PURCHASES_ENTRY_TARIFF_LINE = "Тариф: {tariff}\n"
ADMIN_PURCHASES_ENTRY_TITLE = "Покупка #{purchase_id}\n"
ADMIN_PURCHASES_ENTRY_USER_LINE = "Пользователь: {user}\n"
ADMIN_PURCHASES_LIST_BUTTON = "📜 Список покупок"
ADMIN_PURCHASES_LOGS_TITLE = "📜 <b>Журнал покупок</b>\n\n"
ADMIN_PURCHASES_METHOD_ADMIN = "Администратор"
ADMIN_PURCHASES_METHOD_OTHER = "Другой"
ADMIN_PURCHASES_METHOD_SBP = "СБП"
ADMIN_PURCHASES_METHOD_WALLET = "Баланс"
ADMIN_PURCHASES_METHOD_YOOKASSA = "ЮKassa"
ADMIN_PURCHASES_NOT_FOUND_ALERT = "Покупка не найдена."
ADMIN_PURCHASES_RECORD_NOT_FOUND_ALERT = "Запись о покупке не найдена."
ADMIN_PURCHASES_ROW_BUTTON_TEMPLATE = "#{id} — {amount} ₽"
ADMIN_PURCHASES_ROW_FORMAT = "#{id} | {date} | {amount} ₽ | {tariff}\n"
ADMIN_PURCHASES_TAB_TITLE = "Покупки"
ADMIN_PURCHASES_TARIFF_ROW = "Тариф: {name}\n"
ADMIN_PURCHASES_USER_CARD_LINK = "👤 Карточка пользователя"
ADMIN_SERVER_CAP_VLESS_SUBS_TPL = "VLESS подписки"
ADMIN_SERVER_PROTO_ORIGIN_BADGE = "RU Hub"
ADMIN_SERVER_PROTO_VLESS_BADGE = "VLESS"
ADMIN_SERVER_SLOTS_BREAKDOWN_NOTE = "Слоты: {used}/{total}"
ADMIN_USERS_BALANCE_BALANCE = "Баланс: {balance} ₽"
ADMIN_ALERT_ORDER_REQUIRES_MANUAL_REVIEW = "⚠️ Заказ #{order_id} требует внимания администратора!"
ADMIN_AUDIT_LOG_DETAILS_BROADCAST = "Рассылка #{broadcast_id}"

# Broadcast
BROADCAST_ALREADY_RUNNING = "Рассылка уже выполняется."
BROADCAST_AUDIENCE = "Аудитория: {audience}"
BROADCAST_AUDIENCE_SERVER_ID_LABEL = "Сервер #{server_id}"
BROADCAST_AUDIENCE_SERVER_LABEL = "Сервер: {server}"
BROADCAST_BROADCAST = "Рассылка"
BROADCAST_BTN_TEST_ME = "🧪 Отправить мне тест"
BROADCAST_ERR_CAPTION_TOO_LONG = "Подпись к медиа слишком длинная."
BROADCAST_ERR_TEXT_TOO_LONG = "Текст рассылки превышает лимит символов."
BROADCAST_NO_RECIPIENTS = "Получателей для рассылки не найдено."
BROADCAST_PREVIEW_CONFIRM_HINT = "Проверьте текст сообщения перед отправкой."
BROADCAST_PROGRESS_SERVER_LABEL = "Сервер"
BROADCAST_PROMPT = "Введите текст сообщения для рассылки:"
BROADCAST_RECIPIENTS_COUNT_LINE = "Получателей: {count}\n"
BROADCAST_RESULT = "Рассылка завершена! Отправлено: {sent}, ошибок: {errors}."
BROADCAST_SELECT_AUDIENCE_PROMPT = "Выберите аудиторию рассылки:"
BROADCAST_SELECT_SERVER_PROMPT = "Выберите сервер:"
BROADCAST_SERVERS_NOT_FOUND = "Серверов не найдено."
BROADCAST_STARTED = "Рассылка запущена!"
BROADCAST_STOPPED_ERROR_ALERT = "Рассылка остановлена из-за ошибки."
BROADCAST_TEST_SENT_NOTICE = "Тестовое сообщение отправлено вам в чат."
ADMIN_BROADCAST_AUDIENCE_LABELS = {
    "all": "Все пользователи",
    "active": "С активной подпиской",
    "expired": "С истекшей подпиской",
}
ADMIN_BROADCAST_PROGRESS_AUDIENCE_LABELS = ADMIN_BROADCAST_AUDIENCE_LABELS

# Formats & Dates
DATE_DAY_MONTH_FORMAT = "%d.%m"
DATE_DAY_MONTH_YEAR_FORMAT = "%d.%m.%Y"
TIME_DAYS_FORMAT = "{days} дн."
TIME_HOURS_LEFT = "{hours} ч."
TIME_FOREVER = "бессрочно"
MONTH_NAMES_LABELS = {
    1: "января", 2: "февраля", 3: "марта", 4: "апреля",
    5: "мая", 6: "июня", 7: "июля", 8: "августа",
    9: "сентября", 10: "октября", 11: "ноября", 12: "декабря",
}

TARIFF_DISPLAY_BASIC = "Стандартный"
TARIFF_DISPLAY_BASIC_GROUP = "Стандарт"
TARIFF_DISPLAY_FAMILY = "Семейный"
TARIFF_DISPLAY_FAMILY_GROUP = "Семейный"
TARIFF_DISPLAY_PRO = "Pro"
TARIFF_DISPLAY_PRO_GROUP = "Pro"

WL_AUTO_PUSH_READY = "Белый Интернет готов к использованию!"
WL_ORIGIN_VLESS_TAG = "RU Hub"
WL_TRAFFIC_90P_ALERT = "Использовано 90% трафика Белого Интернета."
WL_TRAFFIC_90P_TRIAL_ALERT = "Использовано 90% тестового трафика."
WL_TRAFFIC_EXHAUSTED_ALERT = "Трафик Белого Интернета закончился."
WL_TRAFFIC_EXHAUSTED_TRIAL_ALERT = "Тестовый трафик исчерпан."

AUDIT_ACTIONS = {}
AUDIT_DETAIL_CURRENCY_SUFFIX = " ₽"
AUDIT_DETAIL_DAYS_SUFFIX = " дн."
AUDIT_DETAIL_LABELS = {}
AUDIT_DETAIL_YES = "Да"
AUDIT_ENTRY = "#{id} | {action} | {date}\n"
AUDIT_LOG_EMPTY = "Журнал аудита пуст."

LEGACY_REPLY_BUTTON_TRIGGER_TEXTS = set()

# Helper access function
def get_text(key: str, default: str | None = None, **kwargs) -> str | None:
    val = globals().get(key, default)
    if val is not None and kwargs and isinstance(val, str):
        try:
            return val.format(**kwargs)
        except Exception:
            return val
    return val
