"""Domain texts for user/hub.py."""
from __future__ import annotations

BOT_START_DESCRIPTION = "🚀 Запустить бота"
BOT_DESCRIPTION = (
    "Личный сервис безопасного доступа в интернет. "
    "Управление подключениями и устройствами в едином интерфейсе."
)
BOT_SHORT_DESCRIPTION = "Личный сервис безопасного доступа в интернет"

CURRENCY_RUB_SYMBOL = "₽"

HUB_HEADER = """<h2>🏠 Личный кабинет</h2>
<p>👋 Здравствуйте, <b>{name}</b>! (ID: <code>{telegram_id}</code>)</p>
<table>
  <tr><td>Статус:</td><td>{status}</td></tr>
  <tr><td>Действует до:</td><td>{valid_until} <i>({days_left})</i></td></tr>
  <tr><td>Устройства:</td><td><b>{devices_count} / {device_limit}</b></td></tr>
  <tr><td>Баланс:</td><td><b>{real_balance} ₽</b>{bonus_line}</td></tr>
</table>{white_internet_line}{inviter_line}
<tg-button-row align="center">
  <tg-button type="callback_data" style="success" data="{buy_or_sub_action}">{buy_or_sub_text}</tg-button>
  <tg-button type="callback_data" style="primary" data="menu_balance">➕ Пополнить счёт</tg-button>
</tg-button-row>

<p><b>Разделы сервиса:</b></p>
<tg-button-row align="center">
  <tg-button type="callback_data" style="primary" data="menu_connections">📱 Мои устройства</tg-button>
  <tg-button type="callback_data" style="primary" data="white_internet">⚪️ Белый Интернет</tg-button>
</tg-button-row>
<tg-button-row align="center">
  <tg-button type="callback_data" style="primary" data="menu_referral">👥 Партнёрство</tg-button>
  <tg-button type="callback_data" style="primary" data="menu_support">💬 Справка и FAQ</tg-button>
</tg-button-row>{admin_row}{mtproto_row}"""

HUB_WHITE_INTERNET_LINE_FORMAT = (
    "\n⚪️ <b>Белый Интернет:</b> {status_icon} до {expiry} <i>({days_left})</i>\n"
    "   • <i>Трафик: {traffic_used} из {traffic_total} ГБ · {devices_count}/{device_limit} устр.</i>\n"
)
HUB_BONUS_LINE_FORMAT = " (+{bonus_balance} 🎁)"

PAYMENT_STATUS_PENDING_ICON = "⏳"

USER_HUB_START = "Пользователь"
