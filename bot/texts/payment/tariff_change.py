"""Domain texts for payment/tariff_change.py."""
from __future__ import annotations

ADMIN_SUB_TARIFF_CHANGED = "✅ Тариф пользователя <code>{telegram_id}</code> изменен на <b>{tariff_name}</b> (лимит: {device_limit} устр.)."

PAYMENT_CHANGE_TARIFF_HEADER = """<p>⚙️ <b>Смена тарифа</b></p>
<table>
  <tr><td>Текущий тариф:</td><td><b>{tariff_name}</b></td></tr>
  <tr><td>Активен до:</td><td><i>{valid_until}</i></td></tr>
</table>
<blockquote>💡 <b>Правила смены тарифа:</b>
• Оставшаяся стоимость текущей подписки учитывается при переходе.
• 🔼 При увеличении устройств: новые лимиты становятся доступны сразу.
• 🔽 При уменьшении устройств: количество активных устройств не должно превышать новый лимит.</blockquote>
<p>Выберите новый тариф для перехода:</p>"""

PAYMENT_CHANGE_TARIFF_UNAVAILABLE_NO_SUB = """<p>⚠️ <b>Операция недоступна</b></p>
<blockquote>Смена тарифа с перерасчётом доступна только при наличии действующей подписки. У вас сейчас нет активной подписки.</blockquote>"""

PAYMENT_CREATING_LINK_NOTICE = "Создаём ссылку…"

PAYMENT_DEBT_BLOCKED_NOTICE = "Операция недоступна до погашения задолженности."

PAYMENT_DISPUTE_BLOCKED_NOTICE = "Операция заблокирована из-за финансового спора."

PAYMENT_DOWNGRADE_BLOCKED_PROFILES = """<p>⚠️ <b>Смена тарифа невозможна</b></p>
<table>
  <tr><td>Подключено устройств:</td><td><b>{profiles_count}</b></td></tr>
  <tr><td>Лимит нового тарифа:</td><td><b>{new_limit}</b></td></tr>
</table>
<blockquote>Чтобы перейти на этот тариф, сначала удалите лишние устройства в разделе «Устройства».</blockquote>"""

PAYMENT_INSUFFICIENT_FUNDS_ALERT = "На балансе недостаточно средств."

PAYMENT_TARIFF_UNAVAILABLE_NOTICE = "Выбранный тариф больше недоступен."
