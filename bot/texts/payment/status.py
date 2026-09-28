"""Domain texts for payment/status.py."""
from __future__ import annotations

from bot.texts.common import BTN_BUY_ACCESS, BTN_CONNECTIONS, BTN_MY_SUBSCRIPTION

PAYMENT_STATUS_ICONS = {'cancelled': '❌', 'completed': '✅', 'failed': '⚠️', 'paid_processing': '🔄', 'pending': '⏳', 'refunded': '↩️', 'requires_manual_review': '🧪'}

PAYMENT_STATUS_NAMES = {'cancelled': 'Отменен', 'completed': 'Выполнен', 'failed': 'Ошибка', 'paid_processing': 'Обработка', 'pending': 'Ожидание', 'refunded': 'Возврат', 'requires_manual_review': 'Ручная проверка'}

NOTIF_OPEN_CONNECTIONS_BUTTON = BTN_CONNECTIONS
NOTIF_OPEN_SUBSCRIPTION_BUTTON = BTN_MY_SUBSCRIPTION
NOTIF_BUY_NEW_SUBSCRIPTION_BUTTON = BTN_BUY_ACCESS
