REFERRAL_TEXTS = {
    "PROFILE_TEXT_ACTIVE_REFERRAL_BALANCE": """👤 <b>Профиль</b>

{name}{username_line}
ID: <code>{telegram_id}</code>

💎 <b>Тариф:</b> {tariff_name}
🔌 <b>Устройства:</b> {devices_count}
📊 <b>Всего трафика:</b> {total_traffic}

💰 <b>Баланс:</b> {balance} ₽
🎁 <b>Бонусный баланс:</b> {referral_bonus_balance} ₽

👥 <b>Рефералов:</b> {referrals_count}""",
    "PROFILE_TEXT_INACTIVE_REFERRAL_BALANCE": """👤 <b>Профиль</b>

{name}{username_line}
ID: <code>{telegram_id}</code>

🔴 <b>Статус:</b> Нет активной подписки

💰 <b>Баланс:</b> {balance} ₽
🎁 <b>Бонусный баланс:</b> {referral_bonus_balance} ₽

Чтобы подключать устройства и пользоваться сервисом, оформите подписку.

👥 <b>Рефералов:</b> {referrals_count}""",

    # Public aliases kept in the new text module so existing UI contracts keep
    # resolving without restoring the removed legacy user_texts implementation.
    "PROFILE_TEXT_ACTIVE": """👤 <b>Профиль</b>

{name}{username_line}
ID: <code>{telegram_id}</code>

💎 <b>Тариф:</b> {tariff_name}
🔌 <b>Устройства:</b> {devices_count}
📊 <b>Всего трафика:</b> {total_traffic}

💰 <b>Баланс:</b> {balance} ₽
🎁 <b>Реферальный бонус:</b> {referral_bonus_balance} ₽

👥 <b>Рефералов:</b> {referrals_count}""",
    "PROFILE_TEXT_INACTIVE": """👤 <b>Профиль</b>

{name}{username_line}
ID: <code>{telegram_id}</code>

🔴 <b>Статус:</b> Нет активной подписки

💰 <b>Баланс:</b> {balance} ₽
🎁 <b>Реферальный бонус:</b> {referral_bonus_balance} ₽

Чтобы подключать устройства и пользоваться сервисом, оформите подписку.

👥 <b>Рефералов:</b> {referrals_count}""",
    "REFERRAL_TEXT_BALANCE": """🎁 <b>Реферальная программа</b>

Приглашайте друзей и получайте <b>10% от каждой их покупки внутри бота</b> на реферальный бонусный баланс.

🔗 <b>Ваша ссылка:</b>
<code>{referral_link}</code>

💡 <i>Нажмите на ссылку выше, чтобы скопировать.</i>

━━━━━━━━━━━━━━━━━━

📊 <b>Условия:</b>

• Ваш приглашённый пользователь совершает покупку или продление внутри бота.
• Вы получаете <b>10%</b> от суммы этой покупки на реферальный бонусный баланс.
• Бонусный баланс можно использовать для следующих покупок внутри бота.
• Начисление происходит после успешного завершения покупки.

👥 Приглашено: <b>{invited_count}</b>
🎁 Доступный реферальный бонус: <b>{bonus_balance} ₽</b>""",
    "NOTIFY_TOPUP_SUCCESS": "✅ Баланс успешно пополнен на <b>{amount} ₽</b>.",
    "NOTIFY_CHARGEBACK_DEBT": "⚠️ По вашему аккаунту зафиксирован чарджбэк. Доступ к сервису временно заблокирован до завершения проверки.",
    "NOTIFY_ADMIN_SUB_REDUCE": "ℹ️ Администратор изменил срок вашей подписки.",
    "NOTIFY_GENERIC": "🔔 Новое уведомление.",
}
