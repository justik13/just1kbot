"""Domain texts for admin/dashboard.py."""
from __future__ import annotations

ADMIN_DASHBOARD_REVENUE_24H_LINE = """• Выручка за 24ч: <b>{fin_stats__rev_24h} ₽</b> ({fin_stats__count_24h} продаж)
"""

ADMIN_DASHBOARD_SECTION_FINANCES_QUEUES = "💰 Финансы и Очереди{fin_badge}"

ADMIN_DASHBOARD_SECTION_USERS_BROADCAST = """{header}👥 <b>Управление пользователями и рассылками:</b>

Выберите нужный раздел:"""

ADMIN_MAINTENANCE_DISABLED_ANSWER = "✅ Технические работы выключены"

ADMIN_MAINTENANCE_ENABLED_ANSWER = "✅ Технические работы включены"

ADMIN_MAINTENANCE_MENU_DISABLED = """🛠 <b>Режим технических работ</b>

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

<i>Администраторы могут обходить этот режим.</i>"""

ADMIN_MAINTENANCE_MENU_ENABLED = """🛠 <b>Режим технических работ</b>

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

Все ограничения для пользователей будут сняты."""

AUDIT_ACTIONS = {
    'ACCOUNT_PURCHASE_SETTLED': '🛒 Покупка тарифа',
    'ACCOUNT_TARIFF_CHANGE_SETTLED': '🔄 Смена тарифа',
    'ADD_SERVER': '➕ Добавил сервер',
    'ADD_TARIFF': '➕ Добавил тариф',
    'ADMIN_BALANCE_DEDUCT': '➖ Списание баланса админом',
    'ADMIN_BALANCE_TOPUP': '➕ Начисление баланса админом',
    'ADMIN_DEVICE_DELETE': '🗑 Удаление устройства админом',
    'ADMIN_DIRECT_MESSAGE': '✉️ Сообщение от админа',
    'ADMIN_DIRECT_MESSAGE_SENT': '✉️ Сообщение от админа',
    'ADMIN_SUB_CHANGE': '⚙️ Изменение тарифа админом',
    'ADMIN_SUB_EXTEND': '⏳ Продление подписки админом',
    'ADMIN_SUB_GRANT': '🎁 Выдача подписки админом',
    'ADMIN_SUB_REDUCE': '✂️ Сокращение подписки админом',
    'BALANCE_REFUND_REQUESTED': '↩️ Запрос возврата средств',
    'BAN': '🚫 Блокировка пользователя',
    'BAN_USER': '🚫 Блокировка пользователя',
    'BROADCAST': '📢 Сделал рассылку',
    'BROADCAST_COMPLETED': '📢 Рассылка завершена',
    'CHANGE_TARIFF': '⚙️ Изменение тарифа',
    'CLEANUP_DEVICE_DELETE': '🧹 Автоудаление устройства',
    'DEDUCT_USER_BALANCE': '➖ Списание баланса админом',
    'DELETE_DEVICE': '🗑 Удалил устройство',
    'DELETE_SERVER': '🗑 Удалил сервер',
    'DELETE_TARIFF': '🗑 Удалил тариф',
    'DEVICE_CREATE': '📱 Создание устройства',
    'DEVICE_CREATED': '📱 Создание устройства',
    'DEVICE_CREATE_BLOCKED': '🚫 Блокировка создания (дневной лимит)',
    'DEVICE_DELETE': '🗑 Удаление устройства',
    'DEVICE_DELETED': '🗑 Удаление устройства',
    'DEVICE_RENAME': '✏️ Переименование устройства',
    'EDIT_SERVER': '✏️ Изменил сервер',
    'EDIT_TARIFF': '✏️ Изменил тариф',
    'EXTEND': '⏳ Продление подписки админом',
    'GRANT': '🎁 Выдача подписки админом',
    'MANUAL_GRANT': '🎫 Ручная выдача подписки',
    'MASS_BONUS_GRANTED': '🎁 Массовый бонус',
    'PAID_AFTER_CANCEL': '⚠️ Оплата после отмены',
    'PAYMENT_CANCELLED': '❌ Платёж отменён',
    'PAYMENT_CANCEL_AFTER_COMPLETED': '🚨 Отмена после завершения',
    'PAYMENT_CHARGEBACK': '↩️ Возврат/Чарджбэк',
    'PAYMENT_DISPUTE_MANUAL_REVIEW': '🧪 Спор на проверке',
    'PAYMENT_DISPUTE_OPENED': '⚠️ Открыт спор по платежу',
    'PAYMENT_DISPUTE_RESOLVED': '⚖️ Спор по платежу разрешён',
    'PAYMENT_FAILED': '❌ Ошибка оплаты',
    'PAYMENT_MANUAL_REVIEW': '🧪 Платёж на ручной проверке',
    'PAYMENT_SUCCESS': '💳 Пополнение баланса',
    'REDUCE': '✂️ Сокращение подписки админом',
    'REFERRAL_ATTACHED': '🤝 Привязка реферала',
    'REFERRAL_BONUS_GRANTED': '🎁 Реферальный бонус',
    'REFUND': '↩️ Возврат средств',
    'SUB_EXPIRED': '⌛ Истечение срока подписки',
    'TARIFF_EDIT_BLOCKED': '🚫 Блокировка изменения тарифа',
    'TOGGLE_MAINTENANCE': '🛠 Переключил техработы',
    'TOGGLE_SERVER': '🔄 Переключил сервер',
    'TOPUP_USER_BALANCE': '💳 Начисление баланса',
    'UNBAN': '✅ Разблокировка пользователя',
    'UNBAN_USER': '✅ Разблокировка пользователя',
    'USER_REGISTER': '👋 Регистрация',
    'USER_RESTORED': '♻️ Восстановление аккаунта',
    'WELCOME_BONUS_GRANTED': '🎁 Приветственный бонус',
    'YOOKASSA_CALLBACK': '📥 Платёжный callback (YooKassa)',
}

AUDIT_ENTRY = """[{date}]
Admin <code>{admin_id}</code>
➡️ {action}{target}{details}
"""

AUDIT_LOG_EMPTY = "<i>Лог действий пуст.</i>"

DASHBOARD_ACTIVE_PODPISOK_ACTIVE = """• Активных подписок: <b>{stats__active}</b>
"""

DASHBOARD_ATTENTION_ATTENTION = """⚠️ <b>Требует внимания:</b>
"""

DASHBOARD_AUDIT_LOG = "📜 Аудит-лог"

DASHBOARD_AUDIT_LOG_DEYSTVIY_ADMINISTRAT = """{header}📜 <b>Аудит-лог действий администраторов</b> (Стр. {page}/{total_pages}, всего: {total_count})

"""

DASHBOARD_AVG_CHECK_30D_AVG_CHECK = """• Средний чек (30д): <b>{fin_stats__avg_check} ₽</b>

"""

DASHBOARD_DISPUTY = "⚖️ Диспуты"

DASHBOARD_DISPUTY_COUNT = "⚠️ Диспуты ({disputes_count})"

DASHBOARD_FINANCIAL_METRICS = """💰 <b>Финансовые показатели:</b>
"""

DASHBOARD_FINANSY_I_OCHEREDI = "💰 Финансы и Очереди"

DASHBOARD_FINANSY_OCHEREDI_I_PLATEZHNYE = """{header}💰 <b>Финансы, Очереди и Платежные споры:</b>

Выберите нужный раздел:"""

DASHBOARD_FREE_IP_V_POOL = """• Свободных IP в пуле: <b>{free_ips}</b>
"""

DASHBOARD_ILI_OTPRAVTE_DEFIS_CHTOBY_UDAL = "Или отправьте <code>-</code> (дефис), чтобы удалить ссылку и скрыть кнопку у пользователей:"

DASHBOARD_IZMENIT_SSYLKU_MTPROTO_PROXY = "🚀 Изменить ссылку MTProto Proxy"

DASHBOARD_LINK_MTPROTO_PROXY_OBNOVLENA = """✅ Ссылка MTProto Proxy обновлена на:
<code>{safe_new_val}</code>"""

DASHBOARD_LINK_MTPROTO_PROXY_SUCCESS = "✅ Ссылка MTProto Proxy успешно удалена."

DASHBOARD_MAINTENANCE_OFF = """
🛠 <b>Технические работы:</b> 🟢 выключены
"""

DASHBOARD_MAINTENANCE_ON = """
🛠 <b>Технические работы:</b> 🔴 ВКЛЮЧЕНЫ
<i>Новые подключения и оплата временно ограничены.</i>
"""

DASHBOARD_MAIN_DASHBOARD = "Главный Дашборд"

DASHBOARD_MANAGE_VPN_SERVERAMI_I_TAR = """{header}⚙️ <b>Управление серверами и тарифами:</b>

Выберите нужный раздел:"""

DASHBOARD_MTPROTO_PROXY_URL_HEADER = """🚀 <b>MTProto Proxy URL:</b>
"""

DASHBOARD_NEW_ZA_24_HOURS_NEW_24H = """• Новых за 24 часа: <b>{stats__new_24h}</b>

"""

DASHBOARD_NE_ZADANO_LINK_HIDDEN_OT_POL = """<code>{safe_mtproto_url_or}</code>

"""

DASHBOARD_OCHEREDI = "🚨 Очереди ({dead_queues_count})"

DASHBOARD_OCHEREDI_TASKS = "🔄 Очереди задач"

DASHBOARD_OPEN_PLATEZHNYKH_DISPUTES = """• Открытых платежных споров: <b>{disputes_count}</b>

"""

DASHBOARD_OTPRAVTE_NOVUYU_SSYLKU_NA_MTPR = """Отправьте новую ссылку на MTProto Proxy (например, <code>https://t.me/proxy?server=...</code>)
"""

DASHBOARD_REVENUE_ZA_30D_REV_30D = """• Выручка за 30д: <b>{fin_stats__rev_30d} ₽</b>
"""

DASHBOARD_REVENUE_ZA_7D_REV_7D = """• Выручка за 7д: <b>{fin_stats__rev_7d} ₽</b>
"""

DASHBOARD_SERVEROV_POKA_NET = "<i>Серверов пока нет</i>"

DASHBOARD_SETTINGS = "⚙️ Настройки"

DASHBOARD_SETTINGS_BOTA = "⚙️ Настройки бота"

DASHBOARD_SISTEMA = "🛠 Система"

DASHBOARD_SISTEMA_I_LOGI = "🛠 Система и Логи {maint_icon}"

DASHBOARD_SISTEMA_I_SETTINGS = "🛠 Система и Настройки"

DASHBOARD_SISTEMNYE_SETTINGS_BOTA = """⚙️ <b>Системные настройки бота:</b>

"""

DASHBOARD_SISTEMNYE_SETTINGS_I_LOGI_VYB = """{header}🛠 <b>Системные настройки и логи:</b>

Выберите нужный раздел:"""

DASHBOARD_STALE_TASKS_V_OCHEREDYAK = """• Зависших задач в очередях: <b>{dead_queues_count}</b>
"""

DASHBOARD_TEKHRABOTY_VKLYUCHENY = "🔴 Техработы: ВКЛЮЧЕНЫ"

DASHBOARD_TEKHRABOTY_VYKLYUCHENY = "🟢 Техработы: ВЫКЛЮЧЕНЫ"

DASHBOARD_TOTAL_USERS_TOTAL = """• Всего пользователей: <b>{stats__total}</b>
"""

DASHBOARD_USERS_I_SUBSCRIPTION = """📊 <b>Пользователи и Подписки:</b>
"""

DASHBOARD_VPN_SERVERS_I_POOL_IP = """🖥 <b>Серверы и Пул IP:</b>
"""

DASHBOARD_VVOD_SSYLKI_MTPROTO_PROXY = """🚀 <b>Ввод ссылки MTProto Proxy:</b>

"""

DASHBOARD_VY_MOZHETE_IZMENIT_SSYLKU_NA_M = "Вы можете изменить ссылку на MTProto Proxy в 1 клик прямо из бота без перезапуска сервера."

PAGE_INDEX_FORMAT = "Стр {page}/{total_pages}"

ADMIN_DASHBOARD_PROXY_TAB_LABEL = "MTProto Proxy"
ADMIN_DASHBOARD_SERVER_ROW_FORMAT = "{status_icon} {flag} <b>{name}</b>: {used}/{total} ({pct}%){extra_info}"
DASHBOARD_INACTIVE_SERVERS_HEADER = "⏸️ <b>Выключенные серверы:</b>"
DASHBOARD_INACTIVE_SERVER_ROW = "⚪ {flag} <b>{name}</b>: выключен{reason_text} (в БД: {db_used})"


AUDIT_DETAIL_CURRENCY_SUFFIX = " ₽"
AUDIT_DETAIL_DAYS_SUFFIX = " дн."
AUDIT_DETAIL_YES = "Да"
AUDIT_DETAIL_NO = "Нет"
AUDIT_DETAIL_LABELS = {
    'amount': 'Сумма',
    'days': 'Срок',
    'reason': 'Причина',
    'tariff_name': 'Тариф',
    'tariff_id': 'ID тарифа',
    'device_limit': 'Лимит устройств',
    'server_name': 'Сервер',
    'server_id': 'ID сервера',
    'device_name': 'Устройство',
    'device_id': 'ID устройства',
    'profile_id': 'ID устройства',
    'old_name': 'Старое имя',
    'new_name': 'Новое имя',
    'provider': 'Провайдер',
    'payment_id': 'ID платежа',
    'referrer_id': 'ID пригласившего',
    'referrer_telegram_id': 'Telegram ID пригласившего',
    'referred_by': 'Пригласил',
    'from_user_id': 'От пользователя',
    'telegram_id': 'Telegram ID',
    'username': 'Username',
    'debit': 'Списано',
    'credit': 'Зачислено',
    'conversion': 'Перерасчет',
    'force': 'Принудительно',
    'audit_reason': 'Причина',
    'success_count': 'Успешно',
    'fail_count': 'Ошибок',
    'target_audience': 'Аудитория',
    'batch_id': 'Пакет',
    'text': 'Текст',
    'target_telegram_id': 'Telegram ID',
    'outcome': 'Результат',
    'note': 'Заметка',
    'case': 'Кейс',
    'operation': 'Операция',
    'profiles_deleted': 'Удалено устройств',
    'payments_closed': 'Закрыто платежей',
    'devices_restored': 'Устройства восстановлены',
    'new_end': 'Новый срок',
}


ADMIN_DASHBOARD_FINANCE_BADGE = " ⚠️ ({count})"
