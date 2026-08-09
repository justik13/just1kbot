REFERRAL_TEXTS = {
    "PROFILE_TEXT_ACTIVE_REFERRAL_BALANCE": """👤 <b>Личный кабинет</b>

{name}{username_line}
🆔 Ваш ID: <code>{telegram_id}</code>

💎 <b>Тариф:</b> {tariff_name}
⏳ <b>Действует до:</b> {valid_until} ({days_left})
🔌 <b>Устройства:</b> {devices_count} / {device_limit}
📊 <b>Трафик:</b> {total_traffic}

💳 <b>Баланс:</b> {balance} ₽
🎁 <b>Бонусный баланс:</b> {referral_bonus_balance} ₽

👥 <b>Приглашено друзей:</b> {referrals_count}""",
    "PROFILE_TEXT_INACTIVE_REFERRAL_BALANCE": """👤 <b>Личный кабинет</b>

{name}{username_line}
🆔 Ваш ID: <code>{telegram_id}</code>

🔴 <b>Статус:</b> Нет активной подписки

💳 <b>Баланс:</b> {balance} ₽
🎁 <b>Бонусный баланс:</b> {referral_bonus_balance} ₽

👥 <b>Приглашено друзей:</b> {referrals_count}

<i>Чтобы подключать устройства и пользоваться сервисом, выберите тариф.</i>""",

    # Public aliases kept in the new text module so existing UI contracts keep
    # resolving without restoring the removed legacy user_texts implementation.
    "PROFILE_TEXT_ACTIVE": """👤 <b>Личный кабинет</b>

{name}{username_line}
🆔 Ваш ID: <code>{telegram_id}</code>

💎 <b>Тариф:</b> {tariff_name}
⏳ <b>Действует до:</b> {valid_until} ({days_left})
🔌 <b>Устройства:</b> {devices_count} / {device_limit}
📊 <b>Трафик:</b> {total_traffic}

💳 <b>Баланс:</b> {balance} ₽
🎁 <b>Бонусный баланс:</b> {referral_bonus_balance} ₽

👥 <b>Приглашено друзей:</b> {referrals_count}""",
    "PROFILE_TEXT_INACTIVE": """👤 <b>Личный кабинет</b>

{name}{username_line}
🆔 Ваш ID: <code>{telegram_id}</code>

🔴 <b>Статус:</b> Нет активной подписки

💳 <b>Баланс:</b> {balance} ₽
🎁 <b>Бонусный баланс:</b> {referral_bonus_balance} ₽

👥 <b>Приглашено друзей:</b> {referrals_count}""",
    "REFERRAL_TEXT_BALANCE": """🎁 <b>Пригласи друга — получай 10% за каждое пополнение!</b>

Делитесь ссылкой с друзьями. Каждый раз, когда ваш друг пополняет баланс в боте, вы мгновенно получаете <b>10% от суммы каждого его пополнения</b> на бонусный баланс!

🔗 <b>Ваша реферальная ссылка:</b>
<code>{referral_link}</code>

💡 <i>Нажмите на ссылку, чтобы скопировать.</i>

━━━━━━━━━━━━━━━━━━

📊 <b>Как это работает:</b>
1. Отправьте ссылку другу.
2. Друг пополняет баланс (при каждом пополнении).
3. Вы получаете <b>10% от суммы каждого пополнения</b> на свой бонусный баланс.
4. Бонусный баланс автоматически расходуется на покупку и продление подписки.

👥 Приглашено друзей: <b>{invited_count}</b>
🎁 Доступный бонус: <b>{bonus_balance} ₽</b>""",
    "NOTIFY_TOPUP_SUCCESS": "✅ Баланс успешно пополнен на <b>{amount} ₽</b>.",
    "NOTIFY_CHARGEBACK_DEBT": "⚠️ По вашему аккаунту зафиксирован чарджбэк. Доступ к сервису временно заблокирован до завершения проверки.",
    "NOTIFY_ADMIN_SUB_REDUCE": "ℹ️ Администратор изменил срок вашей подписки.",
    "NOTIFY_GENERIC": "🔔 Новое уведомление.",
}
