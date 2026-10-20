"""Domain texts for user/hub.py."""
from __future__ import annotations

BOT_START_DESCRIPTION = "🚀 Запустить бота"
BOT_DESCRIPTION = (
    "Личный сервис безопасного доступа в интернет. "
    "Управление подключениями и устройствами в едином интерфейсе."
)
BOT_SHORT_DESCRIPTION = "Личный сервис безопасного доступа в интернет"

CURRENCY_RUB_SYMBOL = "₽"

HUB_HEADER = """<p>🏠 <b>Главное меню</b></p>
<p>👋 Привет, <b>{name}</b>! <code>(ID: {telegram_id})</code></p>
<table>
  <tr><td>Статус:</td><td>{status}</td></tr>
  <tr><td>Действует до:</td><td>{valid_until} <i>({days_left})</i></td></tr>
  <tr><td>Устройства:</td><td><b>{devices_count} / {device_limit}</b></td></tr>
  <tr><td>Баланс:</td><td><b>{real_balance} ₽</b>{bonus_line}</td></tr>
</table>{white_internet_line}{inviter_line}

Выберите нужный раздел:"""

HUB_WHITE_INTERNET_LINE_FORMAT = (
    "\n⚪️ <b>Белый Интернет:</b> {status_icon} до {expiry} ({days_left})\n"
    "<blockquote expandable>• <i>Трафик: {traffic_rem} из {traffic_total} ГБ · {devices_count} устр.</i></blockquote>"
)
HUB_BONUS_LINE_FORMAT = " (+{bonus_balance} 🎁)"

PAYMENT_STATUS_PENDING_ICON = "⏳"

USER_HUB_START = "Пользователь"
