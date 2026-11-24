"""Canonical texts used by backend services, HTTP endpoints, and workers."""
from __future__ import annotations

# Notification buttons
BTN_RENEW_ACCESS = "Продлить доступ"
BTN_RESUME_ACCESS = "Возобновить доступ"
BTN_TOPUP_BALANCE = "Пополнить баланс"
BTN_OPEN_CABINET = "Открыть кабинет"
BTN_OPEN_PROFILE = "Открыть профиль"
BTN_OPEN_APP = "Открыть приложение"

NOTIFY_AUTORENEW_INSUFFICIENT_FUNDS = """⚠️ <b>Автопродление не удалось: недостаточно средств</b>

Для продления подписки «{service}» требуется <b>{due_rub} ₽</b>.
Доступный баланс: <b>{available_rub} ₽</b>.

Пополните баланс, чтобы доступ не отключился."""

# Errors & statuses
ALERT_COPY_TEXT_TOO_LONG = (
    "Текст слишком длинный для кнопки копирования. "
    "Нажмите на текст в сообщении выше — он скопируется в один тап."
)
ERROR_ACCESS_DENIED = "⛔️ Нет доступа"
ERROR_TECHNICAL_ALERT = "⚠️ Ведутся технические работы. Попробуйте через минуту."
TIME_HOURS_LEFT = "{hours} ч."

# Billing, orders & tariffs
BALANCE_BALANCE = "Баланс: <b>{int_snapshot_real_available} ₽</b>"
TOPUP_CREDITED_NOTICE = "✅ Баланс пополнен."
TOPUP_ERROR_BLOCKED = "Новые пополнения временно заблокированы. Обратитесь в поддержку."
CHECKOUT_DESCRIPTION_DEFAULT = "Предоставление доступа к информационному сервису Just1k"
PAYMENT_DISPUTE_BLOCKED_NOTICE = "Операция заблокирована из-за финансового спора."
PAYMENT_PURCHASE_SUCCESS_CARD = """<b>{operation_title}</b>

Тариф: <b>{tariff_name}</b>
Срок: {duration_text}
Списано: <b>{charged} ₽</b>
Баланс: <b>{real_balance} ₽</b>
Бонусный баланс: <b>{bonus_balance} ₽</b>"""
PURCHASE_COMPLETED = "Тариф куплен"
PAYMENT_OP_TITLE_CHANGE = "Смена тарифа"
PAYMENT_OP_TITLE_RENEWAL = "Тариф продлён"
PAYMENT_OP_TITLE_WI_BOUGHT = "Белый интернет подключён"
PAYMENT_OP_TITLE_WI_RENEWED = "Белый интернет продлён"
PAYMENT_OP_TITLE_WI_ADD_SLOTS = "Лимит устройств увеличен"
PAYMENT_OP_TITLE_WI_TRAFFIC_TOPUP = "Пакет трафика подключён"
TARIFF_DISPLAY_BASIC = "Базовый"
TARIFF_DISPLAY_FAMILY = "Семейный"
TARIFF_DISPLAY_PRO = "Pro"

# Admin alerts
ADMIN_ALERT_ORDER_REQUIRES_MANUAL_REVIEW = (
    "⚠️ <b>Внимание: требуется ручная проверка заказа</b>\n\n"
    "Заказ: <code>{order_id}</code>\n"
    "Сумма заказа: {amount_rub} ₽\n"
    "Причина: частичный возврат или несовпадение суммы ({mismatch})"
)
ADMIN_WI_SUB_NOT_FOUND = "❌ Подписка Белый интернет не найдена"
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

# Subscription feeds & web landing pages
VLESS_WEB_TOO_MANY_REQUESTS = "Слишком много запросов к подписке. Попробуйте позже."
VLESS_FEED_EXPIRED_TITLE = "⚠️ Подписка закончилась"
VLESS_FEED_EXPIRED_NOTICE = "⚠️ Подписка закончилась. Продлите доступ: @{bot_username}"
VLESS_FEED_DEVICE_LIMIT_TITLE = "⚠️ Лимит устройств"
VLESS_FEED_DEVICE_LIMIT_NOTICE = "⚠️ Превышен лимит устройств ({active}/{limit}). Отключите старые в @{bot_username}"
VLESS_FEED_CHURN_LIMIT_NOTICE = "⚠️ Превышен суточный лимит смены устройств ({limit} в сутки). Новое подключение будет доступно позже."

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
LANDING_WHITE_INTERNET_PAGE_TITLE = "Белый интернет"
LANDING_SPECIAL_ACCESS_BADGE = "СПЕЦИАЛЬНЫЙ ДОСТУП"
LANDING_DEVICE_FORMS_LABELS = ("УСТРОЙСТВО", "УСТРОЙСТВА", "УСТРОЙСТВ")

LANDING_REPORT_TOO_MANY_REQUESTS = "Слишком много запросов. Пожалуйста, подождите."
LANDING_REPORT_INVALID_LINK = "Неверная ссылка."
LANDING_REPORT_INVALID_REQUEST = "Некорректный запрос."
LANDING_REPORT_INVALID_FORMAT = "Некорректный формат."
LANDING_REPORT_CONTACT_LENGTH_ERROR = "Укажите контакт для связи (от 3 до 120 символов)."
LANDING_REPORT_MESSAGE_LENGTH_ERROR = "Опишите проблему подробнее (от 5 до 1000 символов)."
LANDING_REPORT_DELIVERY_FAILED = "Не удалось доставить сообщение. Попробуйте позже."

# White Internet backend, feeds & traffic notifications
WL_RENEWAL_HORIZON_EXCEEDED = "Подписка уже продлена на максимальный срок (до 2 месяцев вперед)."
WL_ADMIN_ONLY_ALERT = "🔒 Данная операция доступна только администраторам бота."
WL_DEVICE_LIMIT_MAX_REACHED = "Достигнут максимум устройств (3 устройства)."
WL_ADD_DEVICE_SUCCESS = "Устройство добавлено. Доступно слотов: {limit}."
WL_INSUFFICIENT_BALANCE_BUY = (
    "❌ <b>Недостаточно средств на балансе</b>\n\n"
    "Для оформления доступа требуется пополнение баланса. Перейдите в раздел «Баланс» для оплаты."
)
WL_INSUFFICIENT_BALANCE_RENEW = (
    "❌ <b>Недостаточно средств для продления</b>\n\n"
    "Пополните баланс в личном кабинете."
)
WL_INSUFFICIENT_BALANCE_TOPUP = (
    "❌ <b>Недостаточно средств</b>\n\n"
    "Пополните баланс для покупки дополнительного пакета трафика."
)
WL_INSUFFICIENT_BALANCE_ADD_DEVICE = (
    "❌ <b>Недостаточно средств для добавления устройства</b>\n\n"
    "• <b>Слот (+1 устройство и +{gb} ГБ):</b> {price} ₽\n"
    "• <b>Ваш баланс:</b> {balance:.2f} ₽\n"
    "• <b>Не хватает:</b> {shortage:.2f} ₽\n\n"
    "Покупка слота расширяет лимит до конца текущего периода и <b>не продлевает</b> срок действия."
)
WL_USER_NOT_FOUND = "Пользователь не найден."
WL_SUB_NOT_FOUND = "Подписка «Белый интернет» не найдена."
WL_ALREADY_ACTIVE = "У вас уже есть активная подписка «Белый интернет». Вы можете продлить ее или докупить трафик."
WL_NO_SUB = "У вас нет подписки «Белый интернет». Сначала оформите подписку."
WL_SUB_EXPIRED = "Срок действия вашей подписки истек. Сначала продлите подписку, чтобы докупить трафик."
WL_SUB_DISABLED = "Подписка отключена администратором. Обратитесь в поддержку."
WL_SUB_NOT_READY = "Подписка еще настраивается. Повторите попытку через несколько секунд."
WL_BUY_SUCCESS = "Подписка оформлена."
WL_RENEW_SUCCESS = "Подписка продлена на 30 дней."
WL_TOPUP_SUCCESS = "Пакет +{gb} ГБ добавлен к вашей подписке."
WL_CAP_EXCEEDED = "Докупка {gb} ГБ превысит максимальный лимит накопления (150 ГБ). У вас доступно {available} ГБ."
WL_INVALID_TOPUP_PACK = "Недопустимый пакет трафика: {gb} ГБ."
WL_DEBIT_FAILED = "Ошибка списания средств."
WL_NO_SERVERS_AVAILABLE = "К сожалению, сейчас нет доступных свободных шлюзов для подключения. Пожалуйста, обратитесь в поддержку или повторите попытку позже."
WL_DEACTIVATION_PENDING = "Ожидается завершение деактивации предыдущей подписки на узле."
WL_AUTO_PUSH_READY = (
    "<b>Белый интернет готов к работе</b>\n\n"
    "Шлюз настроен, соединение активно."
)

WL_TRAFFIC_EXHAUSTED_ALERT = (
    "<b>Белый интернет: Лимит трафика исчерпан</b>\n\n"
    "Включенный пакет трафика закончился. Для продолжения работы вы можете "
    "докупить дополнительный пакет трафика (+10, +25 или +50 ГБ) в главном меню бота."
)
WL_TRAFFIC_90P_ALERT = (
    "<b>Белый интернет: Осталось 10% трафика</b>\n\n"
    "Вы израсходовали 90% пакета трафика. "
    "Рекомендуем докупить пакет заранее в боте, чтобы доступ не прерывался."
)

WL_WEB_PENDING = "Шлюз настраивается, повторите через несколько секунд."

WL_FEED_EXHAUSTED_TITLE = "⚠️ Трафик исчерпан"
WL_FEED_EXHAUSTED_NOTICE = "⚠️ Трафик исчерпан. Докупите пакет в @{bot_username}"
WL_FEED_DISABLED_TITLE = "🚫 Доступ отключен"
WL_FEED_DISABLED_NOTICE = "🚫 Доступ отключен администратором. Обратитесь в поддержку: @{bot_username}"

FEED_STUB_STD_DEVICE_LIMIT = "⚠️ Стандарт: лимит ({limit}/{limit})"
FEED_STUB_STD_EXPIRED = "⛔ Стандарт: истёк"
FEED_STUB_WI_PENDING = "⏳ Белый интернет: настройка"
FEED_STUB_WI_DEVICE_LIMIT = "⚠️ БИ: лимит устройств ({limit}/{limit})"
FEED_STUB_WI_EXHAUSTED = "⚠️ Белый интернет: 0 ГБ"
FEED_STUB_WI_DISABLED = "⛔ Белый интернет: отключён"
FEED_STUB_WI_EXPIRED = "⛔ Белый интернет: истёк"
FEED_STUB_NO_LOCATIONS = "⚠️ Нет доступных локаций"
FEED_STUB_PENDING_SETUP = "⏳ Настройка подключения"

WL_VLESS_TAG = "⚪️ Белый интернет"
WL_DEFAULT_TARIFF_NAME = "Белый интернет 50 ГБ"


# Onboarding & Mini App entry
TOS_AGREEMENT_URL = "https://telegra.ph/Polzovatelskoe-soglashenie-07-23-48"
PRIVACY_POLICY_URL = "https://telegra.ph/Politika-konfidencialnosti-07-23-84"
USER_HUB_START = "друг"
BTN_MINIAPP_CONNECT = "Подключить"
START_REFERRAL_DISCOUNT_LINE = (
    "\n<b>Приветственная скидка {discount_pct}%</b> по приглашению активна для вашего первого заказа.\n"
)
START_HUB_MESSAGE = (
    "Привет, <b>{name}</b>.\n"
    "{discount_line}\n"
    "{profile_block}\n\n"
    "<b>Партнёрская программа:</b> ваша текущая ставка <b>{ref_rate_pct}%</b> ({ref_tier_name}). "
    "Друг получает скидку <b>{welcome_discount_pct}%</b> на первый заказ, "
    "а вы получаете бонусы с каждой его оплаты.{ref_next_line}\n\n"
    "Нажмите <b>«Подключить»</b>, чтобы открыть кабинет, инструкции и тарифы.\n\n"
    "<i>Используя сервис, вы соглашаетесь с "
    f'<a href="{TOS_AGREEMENT_URL}">Условиями использования</a> и '
    f'<a href="{PRIVACY_POLICY_URL}">Политикой конфиденциальности</a>.</i>'
)
START_TECHNICAL_ERROR = "⚠️ Произошла техническая ошибка. Попробуйте позже."
START_FALLBACK_MESSAGE = (
    "Для управления доступом, тарифами и настройками откройте панель управления ниже:"
)


def get_tariff_display_name(device_limit: int) -> str:
    """Map device limit to localized tariff tier display label."""
    if device_limit <= 2:
        return TARIFF_DISPLAY_BASIC
    if device_limit <= 5:
        return TARIFF_DISPLAY_FAMILY
    return TARIFF_DISPLAY_PRO
