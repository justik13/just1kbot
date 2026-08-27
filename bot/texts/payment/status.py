"""Domain texts for payment/status.py."""
from __future__ import annotations

PAYMENT_STATUS_COMMON_DOSTUPNYE_VARIANTY_TARIFOV = """

💡 <b>Доступные варианты тарифов:</b>
"""

PAYMENT_STATUS_COMMON_UST_OT = "• <b>{name}</b> ({limit} уст.) — от <b>{min_price} ₽</b>"

PAYMENT_STATUS_ICONS = {'cancelled': '❌', 'completed': '✅', 'failed': '⚠️', 'paid_processing': '🔄', 'pending': '⏳', 'refunded': '↩️', 'requires_manual_review': '🧪'}

PAYMENT_STATUS_NAMES = {'cancelled': 'Отменен', 'completed': 'Выполнен', 'failed': 'Ошибка', 'paid_processing': 'Обработка', 'pending': 'Ожидание', 'refunded': 'Возврат', 'requires_manual_review': 'Ручная проверка'}
