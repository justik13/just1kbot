"""Domain texts for payment/status.py."""
from __future__ import annotations

from bot.texts.common import BTN_BUY_ACCESS, BTN_CONNECTIONS, BTN_MY_SUBSCRIPTION

PAYMENT_STATUS_COMMON_DOSTUPNYE_VARIANTY_TARIFOV = """

💡 <b>Доступные варианты тарифов:</b>
"""

PAYMENT_STATUS_COMMON_UST_OT = "• <b>{name}</b> ({limit} уст.) — от <b>{min_price} ₽</b>"

PAYMENT_STATUS_ICONS = {'cancelled': '❌', 'completed': '✅', 'failed': '⚠️', 'paid_processing': '🔄', 'pending': '⏳', 'refunded': '↩️', 'requires_manual_review': '🧪'}

PAYMENT_STATUS_NAMES = {'cancelled': 'Отменен', 'completed': 'Выполнен', 'failed': 'Ошибка', 'paid_processing': 'Обработка', 'pending': 'Ожидание', 'refunded': 'Возврат', 'requires_manual_review': 'Ручная проверка'}

TOPUP_PAID_TARIFF_CHANGE_NOTICE = '🎉 <b>Оплата получена и тариф успешно обновлен!</b>\n\nВаш новый тариф активирован. Настройки подписки и подключений обновлены.'
TOPUP_PAID_SUBSCRIPTION_NOTICE = '🎉 <b>Оплата получена и подписка успешно оформлена!</b>\n\nВаши ключи и настройки подключений доступны в разделе «🔌 Подключения».'
TOPUP_PAID_BALANCE_TEMPLATE = '✅ <b>Баланс пополнен на +{amount} ₽!</b>\n\n💰 Баланс: <b>{real_balance} ₽</b>'
TOPUP_BONUS_BALANCE_LINE = '\n🎁 Бонусный баланс: <b>{bonus_balance} ₽</b>'
TOPUP_WELCOME_BONUS_LINE = '\n\n🎁 <b>Вам начислен приветственный бонус +{welcome_bonus} ₽ за первое пополнение по приглашению!</b>'
NOTIF_OPEN_CONNECTIONS_BUTTON = BTN_CONNECTIONS
NOTIF_OPEN_SUBSCRIPTION_BUTTON = BTN_MY_SUBSCRIPTION
NOTIF_BUY_NEW_SUBSCRIPTION_BUTTON = BTN_BUY_ACCESS
