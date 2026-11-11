"""Domain texts for connection/devices.py."""
from __future__ import annotations

BTN_CHANGE_NAME = "✏️ Изменить имя"

BTN_CHANGE_TARIFF = "⚙️ Сменить тариф"

DEVICE_ACCESS_INACTIVE = "⚠️ Доступ неактивен. Продлите подписку."

VLESS_SUB_SCREEN_TEXT = """⚡️ <b>Подключение по ссылке (Happ / INCY)</b>

Одна ссылка дает доступ сразу ко всем серверам на всех ваших устройствах (смартфон, планшет, ПК) в рамках тарифа.

1️⃣ <b>Установите приложение:</b>
   <b>Happ (Рекомендуется для ПК):</b>
   💻 <a href="https://github.com/Happ-proxy/happ-desktop/releases">Для ПК / Windows (GitHub)</a> — <i>рекомендуется для компьютеров</i>
   🍏 <a href="https://testflight.apple.com/join/XMls6Ckd">TestFlight (iOS РФ)</a> | <a href="https://apps.apple.com/app/happ-proxy-utility/id6504287215">App Store</a>
   📦 <a href="https://github.com/Happ-proxy/happ-android/releases">APK для Android (GitHub)</a>

   <b>INCY:</b>
   🍏 <a href="https://testflight.apple.com/join/dFw66h3a">TestFlight (iOS РФ)</a> | <a href="https://apps.apple.com/app/incy/id6756943388">App Store</a>
   🤖 <a href="https://play.google.com/store/apps/details?id=llc.itdev.incy">Google Play (Android)</a>
   💻 / 📦 <a href="https://github.com/INCY-DEV/incy-platforms/releases">Для ПК / APK (GitHub)</a>

2️⃣ <b>Скопируйте вашу ссылку на подписку:</b>
<code>{sub_url}</code>
<i>(нажмите на ссылку для копирования)</i>

3️⃣ <b>Добавьте подписку:</b>
   • Откройте приложение (<b>Happ</b> или <b>INCY</b>)
   • Вставьте скопированную ссылку подписки через «+» и включите связь
   <i>(при первом запуске подтвердите системный запрос на подключение)</i>

<i>Каждое подключенное устройство учитывается в общем лимите устройств.</i>"""

BTN_VLESS_RESET_HWID = "🔄 Сбросить привязку устройств"
BTN_VLESS_RESET_CONFIRM = "🔄 Сбросить все устройства"
VLESS_RESET_CONFIRM_TEXT = """⚠️ <b>Сброс привязки устройств</b>

При сбросе все подключенные по ссылке устройства будут отключены. Им потребуется заново обновить подписку в приложении.

Вы уверены, что хотите сбросить привязку устройств?"""
VLESS_RESET_SUCCESS = "✅ Привязка устройств по ссылке сброшена."
VLESS_RESET_EMPTY = "ℹ️ У вас нет привязанных устройств по ссылке."

BTN_BACK_TO_CONNECTION = "← К подключению"

CONNECTION_HUB_ACTIVE_TEXT = """🌐 <b>Подключение</b>

<b>Статус:</b> 🟢 Активна
<b>Срок действия:</b> до {expiry}
<b>Тариф:</b> {tariff}

📱 <b>Устройства:</b> {active} из {limit}

⚡️ <b>Подключение по ссылке (Happ / INCY):</b>
Одна ссылка дает доступ сразу ко всем серверам на всех ваших устройствах (смартфон, планшет, ПК) в рамках тарифа.

📋 <b>Ваша ссылка на подписку:</b>
<code>{sub_url}</code>
<i>(нажмите на ссылку для копирования)</i>"""

CONNECTION_HUB_EXPIRED_TEXT = """🌐 <b>Подключение</b>

<b>Статус:</b> 🔴 Не активна
{grace_notice}
<i>Для возобновления доступа продлите подписку.</i>"""

BTN_SUB_CONNECT_INCY = "Подключить"
BTN_INCY_INSTRUCTIONS = "📱 Инструкция (INCY / Happ)"
VLESS_WEB_TOO_MANY_REQUESTS = "Слишком много запросов к подписке. Попробуйте позже."
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





