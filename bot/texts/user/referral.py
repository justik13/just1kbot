"""Domain texts for user/referral.py."""
from __future__ import annotations

REFERRAL_LIST_EMPTY = """<i>Список рефералов пока пуст.</i>

Пригласите друзей по вашей ссылке, чтобы они появились здесь."""

REFERRAL_LIST_FOOTER = """
Всего приглашено: {count}"""

REFERRAL_LIST_HEADER = """👥 <b>Ваши рефералы</b>
"""

REFERRAL_LIST_ITEM_FORMAT = "\n{idx}. <b>{user}</b> ({date})"

REFERRAL_SHARE_TEXT = "🎁 Приглашаю в just1kbot! При первом пополнении получишь +10% бонуса на баланс:"

REFERRAL_TEXT_BALANCE = """🤝 <b>Реферальная программа</b>

💰 Бонусный баланс: <b>{bonus_balance} ₽</b>
👥 Приглашено друзей: <b>{invited_count}</b>

🎁 <b>Условия программы:</b>
• Вы получаете <b>10% от каждого пополнения</b> приглашённого друга на бонусный баланс.
• Ваш друг получает <b>+10% бонуса</b> к сумме своего <b>первого пополнения</b>.

<i>💡 Бонусы автоматически используются для оплаты и продления подписки.</i>

🔗 <b>Ваша ссылка для приглашения:</b>
<code>{referral_link}</code>{inviter_line}"""


REFERRAL_TOPUP_NOTIFY_TEMPLATE = '🎉 <b>Ваш реферал пополнил баланс!</b>\n\nВам зачислено <b>+{amount} ₽</b> бонусов на баланс.'
