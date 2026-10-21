"""Domain texts for user/hub.py."""
from __future__ import annotations

BOT_START_DESCRIPTION = "🚀 Запустить бота"
BOT_DESCRIPTION = (
    "Личный сервис безопасного доступа в интернет. "
    "Управление подключениями и устройствами в едином интерфейсе."
)
BOT_SHORT_DESCRIPTION = "Личный сервис безопасного доступа в интернет"

CURRENCY_RUB_SYMBOL = "₽"

HUB_HEADER = """🏠 <b>Главное меню</b>

👋 Привет, <b>{name}</b>!
🆔 <b>Telegram ID:</b> <code>{telegram_id}</code>

<b>📊 Статус подписки:</b> {status}
<b>⏳ Действует до:</b> {valid_until} ({days_left})
<b>📱 Подключено устройств:</b> {devices_count}/{device_limit}{white_internet_line}

<b>💰 Баланс:</b> {real_balance} ₽{bonus_line}{inviter_line}

Выберите нужный раздел:"""

HUB_WHITE_INTERNET_LINE_FORMAT = (
    "\n⚪️ <b>Белый Интернет:</b> {status_icon} до {expiry} ({days_left})\n"
    "   • <i>Трафик: {traffic_rem} из {traffic_total} ГБ · {devices_count} устр.</i>"
)
HUB_BONUS_LINE_FORMAT = "\n<b>🎁 Бонусный баланс:</b> {bonus_balance} ₽"

PAYMENT_STATUS_PENDING_ICON = "⏳"

USER_HUB_START = "Пользователь"
