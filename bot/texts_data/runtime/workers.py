"""Domain copy catalogue for: runtime/workers.py"""

TEXTS = {
    'NOTIFY_1D': """🟡 <b>Ваш доступ отключится через 1 день.</b>

Рекомендуем продлить подписку заранее.

Нажмите кнопку ниже для быстрого продления.""",
    'NOTIFY_2H': """🔴 <b>Ваш доступ отключится через 2 часа!</b>

Не оставайтесь без just1kbot.

Нажмите кнопку ниже, чтобы продлить подписку.""",
    'NOTIFY_3D': """🟢 <b>Ваш доступ отключится через 3 дня.</b>

Успейте продлить подписку и продолжайте пользоваться сервисом без перебоев.

Нажмите кнопку ниже для оплаты.""",
    'NOTIFY_EXPIRED': """🔴 <b>Подписка истекла</b>
━━━━━━━━━━━━━━━━

Ваши устройства перестали работать.

Устройства будут удалены через: <b>{countdown}</b>

Продлите доступ, чтобы сохранить их.""",
    'NOTIFY_GRACE_12H': """⚠️ <b>Осталось 12 часов до удаления устройств</b>
━━━━━━━━━━━━━━━━

Подписка истекла.

Если вы не продлите доступ, устройства будут удалены.

Продлите доступ, чтобы сохранить их.""",
    'NOTIF_REFERRAL_BONUS_GRANTED': """🎉 <b>Ваш реферал пополнил баланс!</b>

Вам зачислено <b>+{bonus_amount} ₽</b> бонусов на баланс.""",
    'NOTIF_WELCOME_BONUS_GRANTED': '🎁 <b>Вам начислен приветственный бонус +{bonus_amount} ₽ за первое пополнение по приглашению!</b>',
    'PROVISIONING_CREATE_FAILED': 'ошибка создания',
    'PROVISIONING_DELETE_FAILED': 'ошибка удаления',
    'PROVISIONING_UPDATING': 'обновляется',
    'RUNTIME_SERVICES_WORKERS_ACCOUNT_BALANCE_L223_1': '{value_0} дней',
    'RUNTIME_SERVICES_WORKERS_ACCOUNT_BALANCE_L224_1': ' {value_0} ч.',
    'RUNTIME_SERVICES_WORKERS_ACCOUNT_BALANCE_L227_1': 'Смена тарифа с баланса выполнена',
    'RUNTIME_SERVICES_WORKERS_ACCOUNT_BALANCE_L229_1': 'Покупка с баланса выполнена',
    'RUNTIME_SERVICES_WORKERS_ACCOUNT_BALANCE_L340_1': """

Тариф готов к покупке. Подтвердите покупку с баланса.""",
    'RUNTIME_SERVICES_WORKERS_ACCOUNT_BALANCE_L345_1': """✅ <b>Баланс пополнен на +{value_0} ₽!</b>

💰 Баланс: <b>{value_1} ₽</b>
🎁 Бонусный баланс: <b>{value_2} ₽</b>{value_3}""",
    'RUNTIME_SERVICES_WORKERS_ACCOUNT_BALANCE_L361_1': """⚠️ Поздняя оплата превысила лимит баланса
Payment: <code>{value_0}</code>
User: <code>{value_1}</code>
Баланс: <b>{value_2} ₽</b>""",
    'RUNTIME_SERVICES_WORKERS_CLEANUP_L255_1': 'Ошибка получения списка пиров на %s: %s',
    'RUNTIME_SERVICES_WORKERS_CLEANUP_L90_1': 'Критическая ошибка в цикле очистки: %s',
    'RUNTIME_SERVICES_WORKERS_INIT_L119_1': """🚨 <b>{value_0}</b>
🧩 <b>Воркер:</b> <code>{value_1}</code>
🔁 <b>Падений:</b> {value_2}
⚠️ <b>Тип ошибки:</b> <code>{value_3}</code>""",
    'RUNTIME_SERVICES_WORKERS_INIT_L174_1': 'Критическая остановка фоновых задач',
    'RUNTIME_SERVICES_WORKERS_INIT_L233_1': 'Фоновый воркер упал',
    'RUNTIME_SERVICES_WORKERS_NOTIFICATIONS_L122_1': 'Критическая ошибка в цикле уведомлений: %s',
    'RUNTIME_SERVICES_WORKERS_NOTIFICATIONS_L43_1': 'в ближайшее время',
    'RUNTIME_SERVICES_WORKERS_NOTIFICATIONS_L49_1': '{value_0} дн. {value_1} ч.',
    'RUNTIME_SERVICES_WORKERS_NOTIFICATIONS_L53_1': '{value_0} ч. {value_1} мин.',
    'RUNTIME_SERVICES_WORKERS_QUEUE_HEALTH_L230_1': """🚨 <b>Durable queue unhealthy</b>
Queue: <code>{value_0}</code>
Problems: {value_1}{value_2}""",
    'RUNTIME_SERVICES_WORKERS_QUEUE_HEALTH_L237_1': """✅ <b>Durable queue recovered</b>
Queue: <code>{value_0}</code>""",
    'RUNTIME_SERVICES_WORKERS_TRAFFIC_L128_1': 'Ошибка трафика с %s: %s',
    'RUNTIME_SERVICES_WORKERS_TRAFFIC_L304_1': """⚠️ <b>Fair Usage Policy: Превышение квоты трафика!</b>
{value_0}
👤 <b>Пользователь:</b> <code>{value_1}</code>
🌍 <b>Сервер:</b> {value_2}
📊 <b>Использовано:</b> <b>{value_3:.2f} TiB</b>
🆔 <b>Profile ID:</b> <code>{value_4}</code>
{value_5}
<i>Пользователь скачал более 1 TiB трафика.
Рекомендуется связаться с ним или принять меры.
Доступ НЕ отключён автоматически (Fair Usage Policy).</i>""",
    'RUNTIME_SERVICES_WORKERS_TRAFFIC_L67_1': 'Критическая ошибка в цикле трафика (crash #%s, next retry in %ss): %s',
    'STALE_TOPUP_ALERT': """🚨 <b>Обнаружено зависших пополнений: {count}</b>

{details}

Рекомендуется проверить их в админке.""",
    'STALE_TOPUP_ALERT_MORE': """
<i>... и ещё {count} платежей</i>""",
    'STALE_TOPUP_ALERT_ROW': '{icon} <b>Пополнение #{payment_id}</b> от <code>{telegram_id}</code>: <b>{amount} {currency}</b> ({method})',
    'UI_SERVICES_WORKERS_ACCOUNT_BALANCE_L232_1': """✅ <b>{value_0}</b>
Срок: <b>{value_1}</b>
Устройства: <b>до {value_2}</b>""",
    'UI_SERVICES_WORKERS_ACCOUNT_BALANCE_L295_1': """💳 <b>Ссылка на пополнение готова</b>

Сумма: <b>{value_0} ₽</b>

Перейдите на защищённую страницу ЮKassa.""",
    'UI_SERVICES_WORKERS_CLEANUP_L184_1': '⚠️ Ваши устройства были удалены из-за истечения подписки. Продлите доступ, чтобы создать новые.',
    'UI_SERVICES_WORKERS_NOTIFICATIONS_L251_1': '💳 Продлить доступ',
    'UI_SERVICES_WORKERS_NOTIFICATIONS_L256_1': '✅ Прочитано (убрать)',
    'UI_SERVICES_WORKERS_NOTIFICATIONS_L410_1': '🚀 Купить доступ',
    'UI_SERVICES_WORKERS_NOTIFICATIONS_L415_1': '💬 Поддержка',
    'UI_SERVICES_WORKERS_NOTIFICATIONS_L420_1': '✅ Прочитано (убрать)',
    'UI_SERVICES_WORKERS_TRAFFIC_L319_1': '👤 Карточка пользователя',
}
