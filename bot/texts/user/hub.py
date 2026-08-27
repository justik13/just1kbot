"""Domain texts for user/hub.py."""
from __future__ import annotations

BOT_START_DESCRIPTION = "🚀 Запустить бота"

CURRENCY_RUB_SYMBOL = "₽"

HUB_HEADER = """🏠 <b>Главное меню</b>

👋 Привет, <b>{name}</b>!
🆔 <b>Telegram ID:</b> <code>{telegram_id}</code>

<b>📊 Статус подписки:</b> {status}
<b>⏳ Действует до:</b> {valid_until} ({days_left})
<b>📱 Подключено устройств:</b> {devices_count}/{device_limit}

<b>💰 Баланс:</b> {real_balance} ₽
<b>🎁 Бонусный баланс:</b> {bonus_balance} ₽{inviter_line}

Выберите нужный раздел:"""

PAYMENT_STATUS_PENDING_ICON = "⏳"

USER_HUB_START = "Пользователь"
