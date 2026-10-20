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
<p><b>Статус:</b> {status}<br/><b>Баланс:</b> {real_balance} ₽{bonus_line}</p>{white_internet_line}{inviter_line}
<tg-button-row align="center">
  <tg-button type="callback_data" style="success" data="{buy_or_sub_action}">{buy_or_sub_text}</tg-button>
  <tg-button type="callback_data" style="primary" data="menu_balance">➕ Пополнить счёт</tg-button>
</tg-button-row>
<hr/>
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
    "\n⚪️ <b>Белый Интернет:</b> {status_icon} до {expiry} ({days_left})\n"
    "<blockquote expandable>• <i>Трафик: {traffic_rem} из {traffic_total} ГБ · {devices_count} устр.</i></blockquote>"
)
HUB_BONUS_LINE_FORMAT = " (+{bonus_balance} 🎁)"

PAYMENT_STATUS_PENDING_ICON = "⏳"

USER_HUB_START = "Пользователь"
