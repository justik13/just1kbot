"""Domain texts for user/hub.py."""
from __future__ import annotations

BOT_START_DESCRIPTION = "🚀 Запустить бота"
BOT_DESCRIPTION = (
    "Личный сервис безопасного доступа в интернет. "
    "Управление подключениями и устройствами в едином интерфейсе."
)
BOT_SHORT_DESCRIPTION = "Личный сервис безопасного доступа в интернет"

CURRENCY_RUB_SYMBOL = "₽"

# Standard Telegram HTML (Fallback)
HUB_HEADER = """👋 Привет, <b>{name}</b>!

<blockquote expandable>📊 <b>Статус доступа:</b> {status}
⏳ <b>Срок действия:</b> {valid_until} ({days_left})
📱 <b>Подключено устройств:</b> {devices_count} из {device_limit}{white_internet_line}</blockquote>

💳 <b>Баланс:</b> {real_balance} ₽{bonus_line}
🆔 <b>Ваш ID:</b> <code>{telegram_id}</code>{inviter_line}"""

# Telegram Bot API 10.1+ (2026) Native Rich Message Markdown
HUB_HEADER_MARKDOWN = """# 👋 Привет, {name}!

| Параметр | Значение |
| :--- | :--- |
| **Подключение** | {status} |
| **Срок действия** | {valid_until} ({days_left}) |
| **Устройства** | {devices_count} из {device_limit} |{white_internet_line}

💳 **Баланс:** {real_balance} ₽{bonus_line}
🆔 **Ваш ID:** `{telegram_id}`{inviter_line}"""

HUB_WHITE_INTERNET_LINE_FORMAT = (
    "\n🛡 <b>Белый Интернет:</b> {status_icon} до {expiry} ({days_left}) · {traffic_used}/{traffic_total} ГБ"
)
HUB_WHITE_INTERNET_MD_ROW = (
    "\n| **Белый Интернет** | {status_icon} до {expiry} ({days_left}) · {traffic_used}/{traffic_total} ГБ |"
)
HUB_BONUS_LINE_FORMAT = "  •  🎁 <b>Бонусы:</b> {bonus_balance} ₽"
HUB_BONUS_MD_LINE = "  •  🎁 **Бонусы:** {bonus_balance} ₽"

PAYMENT_STATUS_PENDING_ICON = "⏳"

USER_HUB_START = "Пользователь"

BTN_HUB_CONNECT = "🚀 Подключить"
BTN_HUB_REFERRALS = "👥 Рефералы"
BTN_HUB_BALANCE = "💳 Баланс {real}/{bonus} ₽"
BTN_HUB_PROXY = "🛡 Прокси TG"
BTN_HUB_CHANNEL = "📢 TG КАНАЛ"
BTN_HUB_INFO = "ℹ️ Информация"

