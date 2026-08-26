"""Domain copy catalogue for: admin/dashboard.py"""

TEXTS = {
    'ADMIN_MAINTENANCE_DISABLED_ANSWER': '✅ Технические работы выключены',
    'ADMIN_MAINTENANCE_ENABLED_ANSWER': '✅ Технические работы включены',
    'ADMIN_MAINTENANCE_MENU_DISABLED': """🛠 <b>Режим технических работ</b>

━━━━━━━━━━━━━━━━━━━━

Текущий статус: 🟢 <b>ВЫКЛЮЧЕН</b>

⚠️ <b>Включить режим технических работ?</b>

Что будет ограничено:
• создание новых устройств;
• создание новых платежей;
• выбор тарифа.

Что продолжит работать:
• существующие подключения;
• админ-панель;
• поддержка;
• обработка уже оплаченных платежей.

<i>Администраторы могут обходить этот режим.</i>""",
    'ADMIN_MAINTENANCE_MENU_ENABLED': """🛠 <b>Режим технических работ</b>

━━━━━━━━━━━━━━━━━━━━

Текущий статус: 🔴 <b>ВКЛЮЧЕН</b>

Что сейчас ограничено:
• создание новых устройств;
• создание новых платежей;
• выбор тарифа.

Что продолжает работать:
• существующие подключения;
• админ-панель;
• поддержка;
• обработка уже оплаченных платежей.

⚠️ <b>Выключить режим технических работ?</b>

Все ограничения для пользователей будут сняты.""",
    'AUDIT_ACTIONS': {   'ACCOUNT_PURCHASE_SETTLED': '🛒 Приобрел тариф',
    'ACCOUNT_TARIFF_CHANGE_SETTLED': '🔄 Сменил тариф',
    'ADD_SERVER': '➕ Добавил сервер',
    'ADD_TARIFF': '➕ Добавил тариф',
    'ADMIN_BALANCE_DEDUCT': '➖ Списал баланс',
    'ADMIN_BALANCE_TOPUP': '➕ Начислил баланс',
    'ADMIN_DEVICE_DELETE': '🗑 Удалил устройство пользователя',
    'ADMIN_DIRECT_MESSAGE': '✉️ Отправил сообщение',
    'ADMIN_DIRECT_MESSAGE_SENT': '✉️ Отправил сообщение',
    'ADMIN_SUB_CHANGE': '⚙️ Изменил тариф подписки',
    'ADMIN_SUB_EXTEND': '⏳ Продлил подписку',
    'ADMIN_SUB_GRANT': '🎁 Выдал подписку',
    'ADMIN_SUB_REDUCE': '✂️ Сократил подписку',
    'BALANCE_REFUND_REQUESTED': '↩️ Запрошен возврат баланса',
    'BAN': '🚫 Заблокировал пользователя',
    'BAN_USER': '🚫 Заблокировал пользователя',
    'BROADCAST': '📢 Сделал рассылку',
    'CHANGE_TARIFF': '💎 Сменил тариф',
    'CLEANUP_DEVICE_DELETE': '🧹 Автоматическое удаление устройства',
    'DEDUCT_USER_BALANCE': '➖ Списал баланс',
    'DELETE_DEVICE': '🗑 Удалил устройство',
    'DELETE_SERVER': '🗑 Удалил сервер',
    'DELETE_TARIFF': '🗑 Удалил тариф',
    'DEVICE_CREATE': '📱 Создал устройство',
    'DEVICE_CREATED': '📱 Создал устройство',
    'DEVICE_CREATE_BLOCKED': '🚫 Блокировка создания (дневной лимит)',
    'DEVICE_DELETE': '🗑 Удалил устройство',
    'DEVICE_DELETED': '🗑 Удалил устройство',
    'DEVICE_RENAME': '✏️ Переименовал устройство',
    'EDIT_SERVER': '✏️ Изменил сервер',
    'EDIT_TARIFF': '✏️ Изменил тариф',
    'EXTEND': '⏰ Продлил доступ',
    'GRANT': '🎫 Выдал доступ',
    'MANUAL_GRANT': '🎫 Ручная выдача подписки',
    'MASS_BONUS_GRANTED': '🎁 Выдал массовый бонус',
    'PAID_AFTER_CANCEL': '⚠️ Оплата после отмены',
    'PAYMENT_CANCELLED': '❌ Платёж отменён',
    'PAYMENT_CANCEL_AFTER_COMPLETED': '🚨 Отмена после завершения',
    'PAYMENT_CHARGEBACK': '↩️ Возврат/Чарджбэк',
    'PAYMENT_DISPUTE_MANUAL_REVIEW': '🧪 Спор отправлен на ручную проверку',
    'PAYMENT_DISPUTE_OPENED': '⚠️ Открыт спор по платежу',
    'PAYMENT_DISPUTE_RESOLVED': '⚖️ Спор по платежу разрешён',
    'PAYMENT_FAILED': '❌ Ошибка оплаты',
    'PAYMENT_MANUAL_REVIEW': '🧪 Платёж на ручной проверке',
    'PAYMENT_SUCCESS': '✅ Успешная оплата',
    'REDUCE': '➖ Сократил подписку',
    'REFERRAL_ATTACHED': '🤝 Привязка к рефереру',
    'REFERRAL_BONUS_GRANTED': '🎁 Начислил реферальный бонус',
    'REFUND': '↩️ Возврат средств',
    'SUB_EXPIRED': '⌛ Истечение срока подписки',
    'TARIFF_EDIT_BLOCKED': '🚫 Блокировка изменения тарифа',
    'TOGGLE_MAINTENANCE': '🛠 Переключил техработы',
    'TOGGLE_SERVER': '🔄 Переключил сервер',
    'TOPUP_USER_BALANCE': '💳 Пополнил баланс',
    'UNBAN': '✅ Разблокировал пользователя',
    'UNBAN_USER': '✅ Разблокировал пользователя',
    'USER_REGISTER': '👋 Регистрация нового пользователя',
    'USER_RESTORED': '♻️ Восстановление удалённого аккаунта',
    'WELCOME_BONUS_GRANTED': '🎁 Начислил приветственный бонус',
    'YOOKASSA_CALLBACK': '📥 Платёжный callback (YooKassa)'},
    'AUDIT_ENTRY': """[{date}]
Admin <code>{admin_id}</code>
➡️ {action}{target}{details}
""",
    'AUDIT_LOG_EMPTY': '<i>Лог действий пуст.</i>',
    'DASHBOARD_MAINTENANCE_OFF': """
🛠 <b>Технические работы:</b> 🟢 выключены
""",
    'DASHBOARD_MAINTENANCE_ON': """
🛠 <b>Технические работы:</b> 🔴 ВКЛЮЧЕНЫ
<i>Новые подключения и оплата временно ограничены.</i>
""",
    'ERROR_ADMIN_BAN_FORBIDDEN': '⛔️ Нельзя банить администраторов',
}
