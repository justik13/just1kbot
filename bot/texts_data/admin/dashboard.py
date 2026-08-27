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

UI_DASHBOARD_SERVEROV_POKA_NET_119 = '<i>Серверов пока нет</i>'
UI_DASHBOARD_GLAVNYY_DASHBORD_148 = 'Главный Дашборд'
UI_DASHBOARD_POLZOVATELI_I_PODPISKI_151 = """📊 <b>Пользователи и Подписки:</b>
"""
UI_DASHBOARD_VSEGO_POLZOVATELEY_TOTAL_152 = """• Всего пользователей: <b>{stats__total}</b>
"""
UI_DASHBOARD_AKTIVNYKH_PODPISOK_ACTIVE_153 = """• Активных подписок: <b>{stats__active}</b>
"""
UI_DASHBOARD_NOVYKH_ZA_24_CHASA_NEW_24H_154 = """• Новых за 24 часа: <b>{stats__new_24h}</b>

"""
UI_DASHBOARD_FINANSOVYE_POKAZATELI_155 = """💰 <b>Финансовые показатели:</b>
"""
UI_DASHBOARD_VYRUCHKA_ZA_24CH_PRODAZH_REV_2_156 = """• Выручка за 24ч: <b>{fin_stats__rev_24h} ₽</b> ({fin_stats__count_24h} продаж)
"""
UI_DASHBOARD_VYRUCHKA_ZA_7D_REV_7D_157 = """• Выручка за 7д: <b>{fin_stats__rev_7d} ₽</b>
"""
UI_DASHBOARD_VYRUCHKA_ZA_30D_REV_30D_158 = """• Выручка за 30д: <b>{fin_stats__rev_30d} ₽</b>
"""
UI_DASHBOARD_SREDNIY_CHEK_30D_AVG_CHECK_159 = """• Средний чек (30д): <b>{fin_stats__avg_check} ₽</b>

"""
UI_DASHBOARD_VPN_SERVERY_I_PUL_IP_160 = """🖥 <b>VPN Серверы и Пул IP:</b>
"""
UI_DASHBOARD_SVOBODNYKH_IP_V_PULE_161 = """• Свободных IP в пуле: <b>{free_ips}</b>
"""
UI_DASHBOARD_TREBUET_VNIMANIYA_167 = """⚠️ <b>Требует внимания:</b>
"""
UI_DASHBOARD_ZAVISSHIKH_ZADACH_V_OCHEREDYAK_168 = """• Зависших задач в очередях: <b>{dead_queues_count}</b>
"""
UI_DASHBOARD_OTKRYTYKH_PLATEZHNYKH_SPOROV_169 = """• Открытых платежных споров: <b>{disputes_count}</b>

"""
UI_DASHBOARD_POLZOVATELI_I_RASSYLKI_224 = '👥 Пользователи и Рассылки'
UI_DASHBOARD_UPRAVLENIE_POLZOVATELYAMI_I_RA_225 = """{header}👥 <b>Управление пользователями и рассылками:</b>

Выберите нужный раздел:"""
UI_DASHBOARD_SERVERY_I_TARIFY_239 = '⚙️ Серверы и Тарифы'
UI_DASHBOARD_UPRAVLENIE_VPN_SERVERAMI_I_TAR_240 = """{header}⚙️ <b>Управление VPN-серверами и тарифами:</b>

Выберите нужный раздел:"""
UI_DASHBOARD_FINANSY_I_OCHEREDI_256 = '💰 Финансы и Очереди'
UI_DASHBOARD_FINANSY_OCHEREDI_I_PLATEZHNYE__257 = """{header}💰 <b>Финансы, Очереди и Платежные споры:</b>

Выберите нужный раздел:"""
UI_DASHBOARD_SISTEMA_I_NASTROYKI_272 = '🛠 Система и Настройки'
UI_DASHBOARD_SISTEMNYE_NASTROYKI_I_LOGI_VYB_273 = """{header}🛠 <b>Системные настройки и логи:</b>

Выберите нужный раздел:"""
UI_DASHBOARD_SISTEMA_311 = '🛠 Система'
UI_DASHBOARD_AUDIT_LOG_311 = '📜 Аудит-лог'
UI_DASHBOARD_AUDIT_LOG_DEYSTVIY_ADMINISTRAT_312 = """{header}📜 <b>Аудит-лог действий администраторов</b> (Стр. {page}/{total_pages}, всего: {total_count})

"""
UI_DASHBOARD_NASTROYKI_BOTA_441 = '⚙️ Настройки бота'
UI_DASHBOARD_SISTEMNYE_NASTROYKI_BOTA_445 = """⚙️ <b>Системные настройки бота:</b>

"""
UI_DASHBOARD_NE_ZADANO_SSYLKA_SKRYTA_OT_POL_447 = """<code>{safe_mtproto_url_or}</code>

"""
UI_DASHBOARD_VY_MOZHETE_IZMENIT_SSYLKU_NA_M_448 = 'Вы можете изменить ссылку на MTProto Proxy в 1 клик прямо из бота без перезапуска сервера.'
UI_DASHBOARD_IZMENIT_SSYLKU_MTPROTO_PROXY_453 = '🚀 Изменить ссылку MTProto Proxy'
UI_DASHBOARD_V_ADMIN_MENYU_457 = '🔙 В админ-меню'
UI_DASHBOARD_NASTROYKI_483 = '⚙️ Настройки'
UI_DASHBOARD_VVOD_SSYLKI_MTPROTO_PROXY_486 = """🚀 <b>Ввод ссылки MTProto Proxy:</b>

"""
UI_DASHBOARD_OTPRAVTE_NOVUYU_SSYLKU_NA_MTPR_487 = """Отправьте новую ссылку на MTProto Proxy (например, <code>https://t.me/proxy?server=...</code>)
"""
UI_DASHBOARD_ILI_OTPRAVTE_DEFIS_CHTOBY_UDAL_488 = 'Или отправьте <code>-</code> (дефис), чтобы удалить ссылку и скрыть кнопку у пользователей:'
UI_DASHBOARD_NASTROYKI_533 = '⚙️ Настройки'
UI_DASHBOARD_SSYLKA_MTPROTO_PROXY_USPESHNO__535 = '✅ Ссылка MTProto Proxy успешно удалена.'
UI_DASHBOARD_SSYLKA_MTPROTO_PROXY_OBNOVLENA_535 = """✅ Ссылка MTProto Proxy обновлена на:
<code>{safe_new_val}</code>"""
UI_DASHBOARD_FINANSY_I_OCHEREDI_28 = '💰 Финансы и Очереди{fin_badge}'
UI_DASHBOARD_SISTEMA_I_LOGI_34 = '🛠 Система и Логи {maint_icon}'
UI_DASHBOARD_DISPUTY_73 = '⚠️ Диспуты ({disputes_count})'
UI_DASHBOARD_DISPUTY_73 = '⚖️ Диспуты'
UI_DASHBOARD_OCHEREDI_75 = '🚨 Очереди ({dead_queues_count})'
UI_DASHBOARD_OCHEREDI_ZADACH_75 = '🔄 Очереди задач'
UI_DASHBOARD_TEKHRABOTY_VKLYUCHENY_86 = '🔴 Техработы: ВКЛЮЧЕНЫ'
UI_DASHBOARD_TEKHRABOTY_VYKLYUCHENY_86 = '🟢 Техработы: ВЫКЛЮЧЕНЫ'
UI_DASHBOARD_STR_100 = 'Стр {page}/{total_pages}'
