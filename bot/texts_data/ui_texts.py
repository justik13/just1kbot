"""Generated catalogue of directly rendered Telegram UI copy.

Each entry includes its source location so wording can be found and edited
without touching handlers or keyboards.
"""

TEXTS = {
    # bot/handlers/admin/broadcast.py:116
    'UI_BOT_HANDLERS_ADMIN_BROADCAST_L116_1': '⚠️ Текст рассылки слишком длинный. Максимум {value_0} символов.',
    # bot/handlers/admin/broadcast.py:125
    'UI_BOT_HANDLERS_ADMIN_BROADCAST_L125_1': '⚠️ Подпись к медиа слишком длинная. Максимум {value_0} символов.',
    # bot/handlers/admin/broadcast.py:423
    'UI_BOT_HANDLERS_ADMIN_BROADCAST_L423_1': '🚨 <b>Рассылка остановлена из-за ошибки</b>\n<code>{value_0}</code>',
    # bot/handlers/admin/broadcast.py:709
    'UI_BOT_HANDLERS_ADMIN_BROADCAST_L709_1': 'Рассылка не запущена',
    # bot/handlers/admin/disputes.py:67
    'UI_BOT_HANDLERS_ADMIN_DISPUTES_L67_1': '➕ Зарегистрировать спор',
    # bot/handlers/admin/disputes.py:68
    'UI_BOT_HANDLERS_ADMIN_DISPUTES_L68_1': '🔄 Обновить',
    # bot/handlers/admin/disputes.py:69
    'UI_BOT_HANDLERS_ADMIN_DISPUTES_L69_1': '← В админку',
    # bot/handlers/admin/disputes.py:78
    'UI_BOT_HANDLERS_ADMIN_DISPUTES_L78_1': '✅ Продавец выиграл',
    # bot/handlers/admin/disputes.py:82
    'UI_BOT_HANDLERS_ADMIN_DISPUTES_L82_1': '❌ Продавец проиграл',
    # bot/handlers/admin/disputes.py:87
    'UI_BOT_HANDLERS_ADMIN_DISPUTES_L87_1': '🛑 Ручная проверка',
    # bot/handlers/admin/disputes.py:90
    'UI_BOT_HANDLERS_ADMIN_DISPUTES_L90_1': '← К спорам',
    # bot/handlers/admin/disputes.py:152
    'UI_BOT_HANDLERS_ADMIN_DISPUTES_L152_1': '#{value_0} · {value_1}',
    # bot/handlers/admin/disputes.py:175
    'UI_BOT_HANDLERS_ADMIN_DISPUTES_L175_1': 'Отправьте одной строкой:\n<code>YooKassa_payment_ID | case_ID | сумма | YYYY-MM-DD | open/manual_review/won_by_merchant/lost_by_merchant | заметка</code>\n\nПример:\n<code>2f... | bank-case-17 | 499 | 2026-08-02 | open | ожидаем документы</code>',
    # bot/handlers/admin/disputes.py:198
    'UI_BOT_HANDLERS_ADMIN_DISPUTES_L198_1': 'Нужно ровно 6 полей, разделённых символом |',
    # bot/handlers/admin/disputes.py:202
    'UI_BOT_HANDLERS_ADMIN_DISPUTES_L202_1': 'Некорректный статус спора',
    # bot/handlers/admin/disputes.py:209
    'UI_BOT_HANDLERS_ADMIN_DISPUTES_L209_1': 'Дата должна быть в формате YYYY-MM-DD',
    # bot/handlers/admin/disputes.py:258
    'UI_BOT_HANDLERS_ADMIN_DISPUTES_L258_1': 'Некорректный ID',
    # bot/handlers/admin/disputes.py:262
    'UI_BOT_HANDLERS_ADMIN_DISPUTES_L262_1': 'Спор не найден',
    # bot/handlers/admin/disputes.py:286
    'UI_BOT_HANDLERS_ADMIN_DISPUTES_L286_1': 'Некорректный ID',
    # bot/handlers/admin/disputes.py:297
    'UI_BOT_HANDLERS_ADMIN_DISPUTES_L297_1': 'Переведено на ручную проверку',
    # bot/handlers/admin/disputes.py:313
    'UI_BOT_HANDLERS_ADMIN_DISPUTES_L313_1': 'Некорректный запрос',
    # bot/handlers/admin/disputes.py:318
    'UI_BOT_HANDLERS_ADMIN_DISPUTES_L318_1': 'Некорректный ID',
    # bot/handlers/admin/disputes.py:322
    'UI_BOT_HANDLERS_ADMIN_DISPUTES_L322_1': 'Состояние спора уже изменилось',
    # bot/handlers/admin/disputes.py:326
    'UI_BOT_HANDLERS_ADMIN_DISPUTES_L326_1': '✅ Подтвердить',
    # bot/handlers/admin/disputes.py:330
    'UI_BOT_HANDLERS_ADMIN_DISPUTES_L330_1': 'Отмена',
    # bot/handlers/admin/disputes.py:340
    'UI_BOT_HANDLERS_ADMIN_DISPUTES_L340_1': '⚠️ <b>Подтвердите исход спора</b>\n\nСпор: <code>#{value_0}</code>\nИсход: <b>{value_1}</b>\nЭффект: {value_2}.',
    # bot/handlers/admin/disputes.py:360
    'UI_BOT_HANDLERS_ADMIN_DISPUTES_L360_1': 'Некорректный запрос',
    # bot/handlers/admin/disputes.py:370
    'UI_BOT_HANDLERS_ADMIN_DISPUTES_L370_1': 'Некорректный ID',
    # bot/handlers/admin/disputes.py:381
    'UI_BOT_HANDLERS_ADMIN_DISPUTES_L381_1': 'Исход спора зафиксирован',
    # bot/handlers/admin/payment_queues.py:71
    'UI_BOT_HANDLERS_ADMIN_PAYMENT_QUEUES_L71_1': 'Открыть {value_0}',
    # bot/handlers/admin/payment_queues.py:74
    'UI_BOT_HANDLERS_ADMIN_PAYMENT_QUEUES_L74_1': '🔄 Обновить',
    # bot/handlers/admin/payment_queues.py:75
    'UI_BOT_HANDLERS_ADMIN_PAYMENT_QUEUES_L75_1': '← В админку',
    # bot/handlers/admin/payment_queues.py:144
    'UI_BOT_HANDLERS_ADMIN_PAYMENT_QUEUES_L144_1': '⬅️',
    # bot/handlers/admin/payment_queues.py:146
    'UI_BOT_HANDLERS_ADMIN_PAYMENT_QUEUES_L146_1': '➡️',
    # bot/handlers/admin/payment_queues.py:147
    'UI_BOT_HANDLERS_ADMIN_PAYMENT_QUEUES_L147_1': '← Диагностика',
    # bot/handlers/admin/payment_queues.py:181
    'UI_BOT_HANDLERS_ADMIN_PAYMENT_QUEUES_L181_1': 'Подготовить retry',
    # bot/handlers/admin/payment_queues.py:184
    'UI_BOT_HANDLERS_ADMIN_PAYMENT_QUEUES_L184_1': '← К очереди',
    # bot/handlers/admin/payment_queues.py:205
    'UI_BOT_HANDLERS_ADMIN_PAYMENT_QUEUES_L205_1': 'Некорректный запрос',
    # bot/handlers/admin/payment_queues.py:209
    'UI_BOT_HANDLERS_ADMIN_PAYMENT_QUEUES_L209_1': 'Некорректная страница',
    # bot/handlers/admin/payment_queues.py:213
    'UI_BOT_HANDLERS_ADMIN_PAYMENT_QUEUES_L213_1': 'Некорректная страница',
    # bot/handlers/admin/payment_queues.py:228
    'UI_BOT_HANDLERS_ADMIN_PAYMENT_QUEUES_L228_1': 'Некорректный ID',
    # bot/handlers/admin/payment_queues.py:246
    'UI_BOT_HANDLERS_ADMIN_PAYMENT_QUEUES_L246_1': 'Некорректный ID',
    # bot/handlers/admin/payment_queues.py:250
    'UI_BOT_HANDLERS_ADMIN_PAYMENT_QUEUES_L250_1': 'Состояние уже изменилось',
    # bot/handlers/admin/payment_queues.py:260
    'UI_BOT_HANDLERS_ADMIN_PAYMENT_QUEUES_L260_1': 'Укажите причину ручного retry (3–200 символов).',
    # bot/handlers/admin/payment_queues.py:280
    'UI_BOT_HANDLERS_ADMIN_PAYMENT_QUEUES_L280_1': 'Подтверждение устарело.',
    # bot/handlers/admin/payment_queues.py:283
    'UI_BOT_HANDLERS_ADMIN_PAYMENT_QUEUES_L283_1': 'Причина обязательна и должна содержать 3–200 символов.',
    # bot/handlers/admin/payment_queues.py:288
    'UI_BOT_HANDLERS_ADMIN_PAYMENT_QUEUES_L288_1': 'Состояние уже изменилось',
    # bot/handlers/admin/payment_queues.py:295
    'UI_BOT_HANDLERS_ADMIN_PAYMENT_QUEUES_L295_1': 'Подтвердить retry',
    # bot/handlers/admin/payment_queues.py:298
    'UI_BOT_HANDLERS_ADMIN_PAYMENT_QUEUES_L298_1': 'Отмена',
    # bot/handlers/admin/payment_queues.py:334
    'UI_BOT_HANDLERS_ADMIN_PAYMENT_QUEUES_L334_1': 'Подтверждение устарело',
    # bot/handlers/admin/payments.py:50
    'UI_BOT_HANDLERS_ADMIN_PAYMENTS_L50_1': '↩️ Вернуть доступный остаток',
    # bot/handlers/admin/payments.py:56
    'UI_BOT_HANDLERS_ADMIN_PAYMENTS_L56_1': '👤 Карточка клиента',
    # bot/handlers/admin/payments.py:61
    'UI_BOT_HANDLERS_ADMIN_PAYMENTS_L61_1': '← К списку платежей',
    # bot/handlers/admin/payments.py:105
    'UI_BOT_HANDLERS_ADMIN_PAYMENTS_L105_1': '⬅️',
    # bot/handlers/admin/payments.py:110
    'UI_BOT_HANDLERS_ADMIN_PAYMENTS_L110_1': '➡️',
    # bot/handlers/admin/payments.py:113
    'UI_BOT_HANDLERS_ADMIN_PAYMENTS_L113_1': '← В админку',
    # bot/handlers/admin/payments.py:192
    'UI_BOT_HANDLERS_ADMIN_PAYMENTS_L192_1': 'Некорректный запрос',
    # bot/handlers/admin/payments.py:216
    'UI_BOT_HANDLERS_ADMIN_PAYMENTS_L216_1': 'Некорректный запрос',
    # bot/handlers/admin/payments.py:224
    'UI_BOT_HANDLERS_ADMIN_PAYMENTS_L224_1': 'Платёж не найден',
    # bot/handlers/admin/payments.py:319
    'UI_BOT_HANDLERS_ADMIN_PAYMENTS_L319_1': 'Платёж недоступен для возврата',
    # bot/handlers/admin/payments.py:326
    'UI_BOT_HANDLERS_ADMIN_PAYMENTS_L326_1': 'Возвращаемого остатка уже нет',
    # bot/handlers/admin/payments.py:331
    'UI_BOT_HANDLERS_ADMIN_PAYMENTS_L331_1': '✅ Подтвердить возврат {value_0} ₽',
    # bot/handlers/admin/payments.py:335
    'UI_BOT_HANDLERS_ADMIN_PAYMENTS_L335_1': '← Назад к платежу',
    # bot/handlers/admin/payments.py:340
    'UI_BOT_HANDLERS_ADMIN_PAYMENTS_L340_1': '⚠️ <b>Подтверждение возврата</b>\n\nПлатёж: <code>#{value_0}</code>\nYooKassa ID: <code>{value_1}</code>\nБудет возвращено: <b>{value_2} RUB</b>\n\nСумма сначала будет заморожена на внутреннем балансе, затем durable worker отправит идемпотентный запрос в YooKassa.',
    # bot/handlers/admin/payments.py:363
    'UI_BOT_HANDLERS_ADMIN_PAYMENTS_L363_1': 'Некорректный запрос',
    # bot/handlers/admin/payments.py:389
    'UI_BOT_HANDLERS_ADMIN_PAYMENTS_L389_1': '← Назад к платежу',
    # bot/handlers/admin/payments.py:392
    'UI_BOT_HANDLERS_ADMIN_PAYMENTS_L392_1': '💳 К списку платежей',
    # bot/handlers/admin/payments.py:400
    'UI_BOT_HANDLERS_ADMIN_PAYMENTS_L400_1': '✅ <b>Возврат принят</b>\n\n{value_0}\nСумма: <b>{value_1} RUB</b>\nOperation: <code>{value_2}</code>\nСтатус: <code>{value_3}</code>\n\nЗарезервированная сумма недоступна для новых покупок до подтверждения или безопасного завершения операции.',
    # bot/handlers/admin/servers/add_routes.py:114
    'UI_BOT_HANDLERS_ADMIN_SERVERS_ADD_ROUTES_L114_1': '⚠️ Флаг слишком длинный (макс. 10 символов).',
    # bot/handlers/admin/servers/add_routes.py:157
    'UI_BOT_HANDLERS_ADMIN_SERVERS_ADD_ROUTES_L157_1': '⚠️ <b>URL запрещён правилами безопасности</b>\nИспользование приватных IP-адресов, loopback и metadata endpoints запрещено.',
    # bot/handlers/admin/servers/card_routes.py:43
    'UI_BOT_HANDLERS_ADMIN_SERVERS_CARD_ROUTES_L43_1': 'Некорректный запрос',
    # bot/handlers/admin/servers/card_routes.py:80
    'UI_BOT_HANDLERS_ADMIN_SERVERS_CARD_ROUTES_L80_1': 'Некорректный запрос',
    # bot/handlers/admin/servers/card_routes.py:142
    'UI_BOT_HANDLERS_ADMIN_SERVERS_CARD_ROUTES_L142_1': 'Некорректный запрос',
    # bot/handlers/admin/servers/common.py:67
    'UI_BOT_HANDLERS_ADMIN_SERVERS_COMMON_L67_1': '⬅️',
    # bot/handlers/admin/servers/common.py:72
    'UI_BOT_HANDLERS_ADMIN_SERVERS_COMMON_L72_1': '➡️',
    # bot/handlers/admin/servers/common.py:75
    'UI_BOT_HANDLERS_ADMIN_SERVERS_COMMON_L75_1': '➕ Добавить сервер',
    # bot/handlers/admin/servers/common.py:76
    'UI_BOT_HANDLERS_ADMIN_SERVERS_COMMON_L76_1': '← В админку',
    # bot/handlers/admin/servers/delete_routes.py:68
    'UI_BOT_HANDLERS_ADMIN_SERVERS_DELETE_ROUTES_L68_1': 'Некорректный запрос',
    # bot/handlers/admin/servers/delete_routes.py:125
    'UI_BOT_HANDLERS_ADMIN_SERVERS_DELETE_ROUTES_L125_1': '⚠️ Сессия подтверждения истекла',
    # bot/handlers/admin/servers/delete_routes.py:134
    'UI_BOT_HANDLERS_ADMIN_SERVERS_DELETE_ROUTES_L134_1': 'Некорректный запрос',
    # bot/handlers/admin/servers/delete_routes.py:177
    'UI_BOT_HANDLERS_ADMIN_SERVERS_DELETE_ROUTES_L177_1': 'На сервере есть незавершённое создание клиента. Дождитесь reconciliation и повторите удаление.',
    # bot/handlers/admin/servers/delete_routes.py:216
    'UI_BOT_HANDLERS_ADMIN_SERVERS_DELETE_ROUTES_L216_1': '✅ Сервер {value_0} удалён ({value_1} устр.)',
    # bot/handlers/admin/servers/edit_routes.py:50
    'UI_BOT_HANDLERS_ADMIN_SERVERS_EDIT_ROUTES_L50_1': 'Некорректный запрос',
    # bot/handlers/admin/servers/edit_routes.py:182
    'UI_BOT_HANDLERS_ADMIN_SERVERS_EDIT_ROUTES_L182_1': 'Некорректный запрос',
    # bot/handlers/admin/servers/edit_routes.py:324
    'UI_BOT_HANDLERS_ADMIN_SERVERS_EDIT_ROUTES_L324_1': 'Некорректный запрос',
    # bot/handlers/admin/servers/edit_routes.py:537
    'UI_BOT_HANDLERS_ADMIN_SERVERS_EDIT_ROUTES_L537_1': 'Некорректный запрос',
    # bot/handlers/admin/servers/edit_routes.py:707
    'UI_BOT_HANDLERS_ADMIN_SERVERS_EDIT_ROUTES_L707_1': 'Некорректный запрос',
    # bot/handlers/admin/servers/list_routes.py:51
    'UI_BOT_HANDLERS_ADMIN_SERVERS_LIST_ROUTES_L51_1': 'Некорректный запрос',
    # bot/handlers/admin/tariffs.py:68
    'UI_BOT_HANDLERS_ADMIN_TARIFFS_L68_1': '⬅️',
    # bot/handlers/admin/tariffs.py:73
    'UI_BOT_HANDLERS_ADMIN_TARIFFS_L73_1': '➡️',
    # bot/handlers/admin/tariffs.py:78
    'UI_BOT_HANDLERS_ADMIN_TARIFFS_L78_1': '← В админку',
    # bot/handlers/admin/tariffs.py:176
    'UI_BOT_HANDLERS_ADMIN_TARIFFS_L176_1': 'Некорректный запрос',
    # bot/handlers/admin/tariffs.py:209
    'UI_BOT_HANDLERS_ADMIN_TARIFFS_L209_1': '✏️ Изменить цену ₽',
    # bot/handlers/admin/tariffs.py:215
    'UI_BOT_HANDLERS_ADMIN_TARIFFS_L215_1': '🔴 Выключить',
    # bot/handlers/admin/tariffs.py:220
    'UI_BOT_HANDLERS_ADMIN_TARIFFS_L220_1': '🟢 Включить',
    # bot/handlers/admin/tariffs.py:225
    'UI_BOT_HANDLERS_ADMIN_TARIFFS_L225_1': '← К списку тарифов',
    # bot/handlers/admin/tariffs.py:258
    'UI_BOT_HANDLERS_ADMIN_TARIFFS_L258_1': 'Некорректный запрос',
    # bot/handlers/admin/tariffs.py:293
    'UI_BOT_HANDLERS_ADMIN_TARIFFS_L293_1': 'Некорректный запрос',
    # bot/handlers/admin/tariffs.py:372
    'UI_BOT_HANDLERS_ADMIN_TARIFFS_L372_1': 'Некорректный запрос',
    # bot/handlers/admin/tariffs.py:450
    'UI_BOT_HANDLERS_ADMIN_TARIFFS_L450_1': 'Некорректный запрос',
    # bot/handlers/admin/tariffs.py:516
    'UI_BOT_HANDLERS_ADMIN_TARIFFS_L516_1': '⚠️ Цена должна быть от 1 до {value_0} ₽.',
    # bot/handlers/admin/users/ban_routes.py:40
    'UI_BOT_HANDLERS_ADMIN_USERS_BAN_ROUTES_L40_1': 'Некорректный запрос',
    # bot/handlers/admin/users/ban_routes.py:87
    'UI_BOT_HANDLERS_ADMIN_USERS_BAN_ROUTES_L87_1': 'Некорректный запрос',
    # bot/handlers/admin/users/ban_routes.py:143
    'UI_BOT_HANDLERS_ADMIN_USERS_BAN_ROUTES_L143_1': 'Некорректный запрос',
    # bot/handlers/admin/users/ban_routes.py:181
    'UI_BOT_HANDLERS_ADMIN_USERS_BAN_ROUTES_L181_1': 'Некорректный запрос',
    # bot/handlers/admin/users/common.py:175
    'UI_BOT_HANDLERS_ADMIN_USERS_COMMON_L175_1': '⬅️',
    # bot/handlers/admin/users/common.py:181
    'UI_BOT_HANDLERS_ADMIN_USERS_COMMON_L181_1': '➡️',
    # bot/handlers/admin/users/common.py:186
    'UI_BOT_HANDLERS_ADMIN_USERS_COMMON_L186_1': '🔍 Поиск по ID',
    # bot/handlers/admin/users/common.py:191
    'UI_BOT_HANDLERS_ADMIN_USERS_COMMON_L191_1': '← В админку',
    # bot/handlers/admin/users/device_routes.py:46
    'UI_BOT_HANDLERS_ADMIN_USERS_DEVICE_ROUTES_L46_1': 'Некорректный запрос',
    # bot/handlers/admin/users/device_routes.py:109
    'UI_BOT_HANDLERS_ADMIN_USERS_DEVICE_ROUTES_L109_1': 'Некорректный запрос',
    # bot/handlers/admin/users/device_routes.py:119
    'UI_BOT_HANDLERS_ADMIN_USERS_DEVICE_ROUTES_L119_1': 'Некорректный запрос',
    # bot/handlers/admin/users/device_routes.py:180
    'UI_BOT_HANDLERS_ADMIN_USERS_DEVICE_ROUTES_L180_1': 'Некорректный запрос',
    # bot/handlers/admin/users/device_routes.py:190
    'UI_BOT_HANDLERS_ADMIN_USERS_DEVICE_ROUTES_L190_1': 'Некорректный запрос',
    # bot/handlers/admin/users/list_routes.py:97
    'UI_BOT_HANDLERS_ADMIN_USERS_LIST_ROUTES_L97_1': 'Некорректный запрос',
    # bot/handlers/admin/users/list_routes.py:204
    'UI_BOT_HANDLERS_ADMIN_USERS_LIST_ROUTES_L204_1': '❌ Пользователь с ID {value_0} не найден.',
    # bot/handlers/admin/users/list_routes.py:234
    'UI_BOT_HANDLERS_ADMIN_USERS_LIST_ROUTES_L234_1': 'Некорректный запрос',
    # bot/handlers/admin/users/subscription_change_routes.py:53
    'UI_BOT_HANDLERS_ADMIN_USERS_SUBSCRIPTION_CHANGE_ROUTES_L53_1': 'Некорректный запрос',
    # bot/handlers/admin/users/subscription_change_routes.py:127
    'UI_BOT_HANDLERS_ADMIN_USERS_SUBSCRIPTION_CHANGE_ROUTES_L127_1': 'Некорректный запрос',
    # bot/handlers/admin/users/subscription_change_routes.py:137
    'UI_BOT_HANDLERS_ADMIN_USERS_SUBSCRIPTION_CHANGE_ROUTES_L137_1': 'Некорректный запрос',
    # bot/handlers/admin/users/subscription_change_routes.py:261
    'UI_BOT_HANDLERS_ADMIN_USERS_SUBSCRIPTION_CHANGE_ROUTES_L261_1': 'Некорректный запрос',
    # bot/handlers/admin/users/subscription_change_routes.py:271
    'UI_BOT_HANDLERS_ADMIN_USERS_SUBSCRIPTION_CHANGE_ROUTES_L271_1': 'Некорректный запрос',
    # bot/handlers/admin/users/subscription_extend_routes.py:57
    'UI_BOT_HANDLERS_ADMIN_USERS_SUBSCRIPTION_EXTEND_ROUTES_L57_1': 'Некорректный запрос',
    # bot/handlers/admin/users/subscription_extend_routes.py:108
    'UI_BOT_HANDLERS_ADMIN_USERS_SUBSCRIPTION_EXTEND_ROUTES_L108_1': 'Некорректный запрос',
    # bot/handlers/admin/users/subscription_extend_routes.py:123
    'UI_BOT_HANDLERS_ADMIN_USERS_SUBSCRIPTION_EXTEND_ROUTES_L123_1': 'Некорректное количество дней',
    # bot/handlers/admin/users/subscription_extend_routes.py:204
    'UI_BOT_HANDLERS_ADMIN_USERS_SUBSCRIPTION_EXTEND_ROUTES_L204_1': 'Некорректный запрос',
    # bot/handlers/admin/users/subscription_extend_routes.py:219
    'UI_BOT_HANDLERS_ADMIN_USERS_SUBSCRIPTION_EXTEND_ROUTES_L219_1': 'Некорректное количество дней',
    # bot/handlers/admin/users/subscription_extend_routes.py:323
    'UI_BOT_HANDLERS_ADMIN_USERS_SUBSCRIPTION_EXTEND_ROUTES_L323_1': 'Некорректный запрос',
    # bot/handlers/admin/users/subscription_grant_routes.py:64
    'UI_BOT_HANDLERS_ADMIN_USERS_SUBSCRIPTION_GRANT_ROUTES_L64_1': 'Некорректный запрос',
    # bot/handlers/admin/users/subscription_grant_routes.py:129
    'UI_BOT_HANDLERS_ADMIN_USERS_SUBSCRIPTION_GRANT_ROUTES_L129_1': 'Некорректный запрос',
    # bot/handlers/admin/users/subscription_grant_routes.py:139
    'UI_BOT_HANDLERS_ADMIN_USERS_SUBSCRIPTION_GRANT_ROUTES_L139_1': 'Некорректный запрос',
    # bot/handlers/admin/users/subscription_grant_routes.py:196
    'UI_BOT_HANDLERS_ADMIN_USERS_SUBSCRIPTION_GRANT_ROUTES_L196_1': 'Некорректный запрос',
    # bot/handlers/admin/users/subscription_grant_routes.py:213
    'UI_BOT_HANDLERS_ADMIN_USERS_SUBSCRIPTION_GRANT_ROUTES_L213_1': 'Некорректное количество дней',
    # bot/handlers/admin/users/subscription_grant_routes.py:290
    'UI_BOT_HANDLERS_ADMIN_USERS_SUBSCRIPTION_GRANT_ROUTES_L290_1': 'Некорректный запрос',
    # bot/handlers/admin/users/subscription_grant_routes.py:300
    'UI_BOT_HANDLERS_ADMIN_USERS_SUBSCRIPTION_GRANT_ROUTES_L300_1': 'Некорректный запрос',
    # bot/handlers/admin/users/subscription_grant_routes.py:448
    'UI_BOT_HANDLERS_ADMIN_USERS_SUBSCRIPTION_GRANT_ROUTES_L448_1': 'Некорректный запрос',
    # bot/handlers/admin/users/subscription_grant_routes.py:465
    'UI_BOT_HANDLERS_ADMIN_USERS_SUBSCRIPTION_GRANT_ROUTES_L465_1': 'Некорректное количество дней',
    # bot/handlers/admin/users/subscription_menu_routes.py:43
    'UI_BOT_HANDLERS_ADMIN_USERS_SUBSCRIPTION_MENU_ROUTES_L43_1': 'Некорректный запрос',
    # bot/handlers/admin/users/subscription_reduce_routes.py:53
    'UI_BOT_HANDLERS_ADMIN_USERS_SUBSCRIPTION_REDUCE_ROUTES_L53_1': 'Некорректный запрос',
    # bot/handlers/admin/users/subscription_reduce_routes.py:175
    'UI_BOT_HANDLERS_ADMIN_USERS_SUBSCRIPTION_REDUCE_ROUTES_L175_1': 'Некорректный запрос',
    # bot/handlers/admin/users/subscription_reduce_routes.py:190
    'UI_BOT_HANDLERS_ADMIN_USERS_SUBSCRIPTION_REDUCE_ROUTES_L190_1': 'Некорректное количество дней',
    # bot/handlers/connection/common.py:145
    'UI_BOT_HANDLERS_CONNECTION_COMMON_L145_1': '🔒 {value_0}',
    # bot/handlers/connection/common.py:150
    'UI_BOT_HANDLERS_CONNECTION_COMMON_L150_1': '⚙️ {value_0}',
    # bot/handlers/connection/common.py:181
    'UI_BOT_HANDLERS_CONNECTION_COMMON_L181_1': '➕ Добавить устройство',
    # bot/handlers/connection/common.py:223
    'UI_BOT_HANDLERS_CONNECTION_COMMON_L223_1': '🚀 Купить доступ',
    # bot/handlers/connection/common.py:227
    'UI_BOT_HANDLERS_CONNECTION_COMMON_L227_1': '🏠 В главное меню',
    # bot/handlers/connection/common.py:242
    'UI_BOT_HANDLERS_CONNECTION_COMMON_L242_1': '🚀 Купить доступ',
    # bot/handlers/connection/common.py:246
    'UI_BOT_HANDLERS_CONNECTION_COMMON_L246_1': '🏠 В главное меню',
    # bot/handlers/connection/common.py:266
    'UI_BOT_HANDLERS_CONNECTION_COMMON_L266_1': '🏠 В главное меню',
    # bot/handlers/connection/device_create_routes.py:51
    'UI_BOT_HANDLERS_CONNECTION_DEVICE_CREATE_ROUTES_L51_1': '🚀 Купить доступ',
    # bot/handlers/connection/device_create_routes.py:52
    'UI_BOT_HANDLERS_CONNECTION_DEVICE_CREATE_ROUTES_L52_1': '🏠 В главное меню',
    # bot/handlers/connection/device_create_routes.py:59
    'UI_BOT_HANDLERS_CONNECTION_DEVICE_CREATE_ROUTES_L59_1': '⚙️ Сменить тариф',
    # bot/handlers/connection/device_create_routes.py:60
    'UI_BOT_HANDLERS_CONNECTION_DEVICE_CREATE_ROUTES_L60_1': '← К списку устройств',
    # bot/handlers/connection/device_create_routes.py:150
    'UI_BOT_HANDLERS_CONNECTION_DEVICE_CREATE_ROUTES_L150_1': '{value_0} {value_1}',
    # bot/handlers/connection/device_create_routes.py:154
    'UI_BOT_HANDLERS_CONNECTION_DEVICE_CREATE_ROUTES_L154_1': '← Назад',
    # bot/handlers/connection/device_create_routes.py:207
    'UI_BOT_HANDLERS_CONNECTION_DEVICE_CREATE_ROUTES_L207_1': 'Некорректный запрос',
    # bot/handlers/connection/device_create_routes.py:418
    'UI_BOT_HANDLERS_CONNECTION_DEVICE_CREATE_ROUTES_L418_1': '⏳ Устройство создаётся.\nОбновите список через несколько секунд.',
    # bot/handlers/connection/device_delete_routes.py:42
    'UI_BOT_HANDLERS_CONNECTION_DEVICE_DELETE_ROUTES_L42_1': 'Некорректный запрос',
    # bot/handlers/connection/device_delete_routes.py:71
    'UI_BOT_HANDLERS_CONNECTION_DEVICE_DELETE_ROUTES_L71_1': 'Некорректный запрос',
    # bot/handlers/connection/device_delete_routes.py:100
    'UI_BOT_HANDLERS_CONNECTION_DEVICE_DELETE_ROUTES_L100_1': 'Некорректный запрос',
    # bot/handlers/connection/device_rename_routes.py:36
    'UI_BOT_HANDLERS_CONNECTION_DEVICE_RENAME_ROUTES_L36_1': 'Некорректный запрос',
    # bot/handlers/connection/device_view_routes.py:51
    'UI_BOT_HANDLERS_CONNECTION_DEVICE_VIEW_ROUTES_L51_1': 'Некорректный запрос',
    # bot/handlers/connection/device_view_routes.py:93
    'UI_BOT_HANDLERS_CONNECTION_DEVICE_VIEW_ROUTES_L93_1': '🗑 Удалить устройство',
    # bot/handlers/connection/device_view_routes.py:94
    'UI_BOT_HANDLERS_CONNECTION_DEVICE_VIEW_ROUTES_L94_1': '← К списку устройств',
    # bot/handlers/connection/device_view_routes.py:95
    'UI_BOT_HANDLERS_CONNECTION_DEVICE_VIEW_ROUTES_L95_1': '🏠 В главное меню',
    # bot/handlers/connection/device_view_routes.py:114
    'UI_BOT_HANDLERS_CONNECTION_DEVICE_VIEW_ROUTES_L114_1': 'Некорректный запрос',
    # bot/handlers/connection/device_view_routes.py:176
    'UI_BOT_HANDLERS_CONNECTION_DEVICE_VIEW_ROUTES_L176_1': 'Некорректный запрос',
    # bot/handlers/payment/balance_routes.py:205
    'UI_BOT_HANDLERS_PAYMENT_BALANCE_ROUTES_L205_1': 'Сервис пополнения временно недоступен.',
    # bot/handlers/payment/balance_routes.py:246
    'UI_BOT_HANDLERS_PAYMENT_BALANCE_ROUTES_L246_1': 'Аккаунт не найден',
    # bot/handlers/payment/balance_routes.py:304
    'UI_BOT_HANDLERS_PAYMENT_BALANCE_ROUTES_L304_1': '➕ <b>Пополнение баланса</b>\n\nТекущий баланс: <b>{value_0} ₽</b>\nВыберите сумму или укажите другую целую сумму в рублях.',
    # bot/handlers/payment/common.py:125
    'UI_BOT_HANDLERS_PAYMENT_COMMON_L125_1': '🔄 Продлить подписку',
    # bot/handlers/payment/common.py:128
    'UI_BOT_HANDLERS_PAYMENT_COMMON_L128_1': '⚙️ Сменить тариф',
    # bot/handlers/payment/common.py:131
    'UI_BOT_HANDLERS_PAYMENT_COMMON_L131_1': '🏠 В главное меню',
    # bot/handlers/payment/purchase_routes.py:135
    'UI_BOT_HANDLERS_PAYMENT_PURCHASE_ROUTES_L135_1': 'Некорректная покупка',
    # bot/handlers/payment/purchase_routes.py:147
    'UI_BOT_HANDLERS_PAYMENT_PURCHASE_ROUTES_L147_1': 'Проводим покупку…',
    # bot/handlers/payment/purchase_routes.py:180
    'UI_BOT_HANDLERS_PAYMENT_PURCHASE_ROUTES_L180_1': '🎉 <b>{value_0}</b>\n\nТариф: <b>{value_1}</b>\nСрок: {value_2} дней\nСписано: <b>{value_3} ₽</b>\n💰 Баланс: <b>{value_4} ₽</b>\n🎁 Бонусный баланс: <b>{value_5} ₽</b>',
    # bot/handlers/payment/purchase_routes.py:209
    'UI_BOT_HANDLERS_PAYMENT_PURCHASE_ROUTES_L209_1': 'Создаём ссылку…',
    # bot/handlers/payment/purchase_routes.py:218
    'UI_BOT_HANDLERS_PAYMENT_PURCHASE_ROUTES_L218_1': 'Котировка устарела',
    # bot/handlers/payment/purchase_routes.py:251
    'UI_BOT_HANDLERS_PAYMENT_PURCHASE_ROUTES_L251_1': 'Котировка устарела',
    # bot/handlers/payment/purchase_routes.py:263
    'UI_BOT_HANDLERS_PAYMENT_PURCHASE_ROUTES_L263_1': 'Введите целую сумму от <b>{value_0} ₽</b> до 5000 ₽.',
    # bot/handlers/payment/purchase_routes.py:300
    'UI_BOT_HANDLERS_PAYMENT_PURCHASE_ROUTES_L300_1': 'Операция устарела. Выберите тариф заново.',
    # bot/handlers/payment/showcase_routes.py:50
    'UI_BOT_HANDLERS_PAYMENT_SHOWCASE_ROUTES_L50_1': '🚀 Начать',
    # bot/handlers/payment/showcase_routes.py:119
    'UI_BOT_HANDLERS_PAYMENT_SHOWCASE_ROUTES_L119_1': 'Некорректный запрос',
    # bot/handlers/payment/showcase_routes.py:125
    'UI_BOT_HANDLERS_PAYMENT_SHOWCASE_ROUTES_L125_1': 'Некорректный запрос',
    # bot/handlers/payment/showcase_routes.py:165
    'UI_BOT_HANDLERS_PAYMENT_SHOWCASE_ROUTES_L165_1': 'У вас уже подключён этот тариф. Для добавления дней используйте продление.',
    # bot/handlers/payment/showcase_routes.py:245
    'UI_BOT_HANDLERS_PAYMENT_SHOWCASE_ROUTES_L245_1': '💱 <b>Смена тарифа</b>\n\nНовый тариф: <b>{value_0}</b>\nЛимит устройств: <b>{value_1}</b>\nСрок после конвертации: <b>{value_2}</b>\nДоплата: <b>{value_3} ₽</b>\n\nБаланс: <b>{value_4} ₽</b>\nПосле смены: <b>{value_5} ₽</b>{value_6}\n\nОстаточная стоимость подписки используется только в этом расчёте и не зачисляется на свободный баланс.',
    # bot/handlers/payment/showcase_routes.py:458
    'UI_BOT_HANDLERS_PAYMENT_SHOWCASE_ROUTES_L458_1': 'Некорректный запрос',
    # bot/handlers/payment/showcase_routes.py:464
    'UI_BOT_HANDLERS_PAYMENT_SHOWCASE_ROUTES_L464_1': 'Некорректный запрос',
    # bot/handlers/payment/tariff_change_routes.py:133
    'UI_BOT_HANDLERS_PAYMENT_TARIFF_CHANGE_ROUTES_L133_1': 'Некорректная операция',
    # bot/handlers/payment/tariff_change_routes.py:145
    'UI_BOT_HANDLERS_PAYMENT_TARIFF_CHANGE_ROUTES_L145_1': 'Меняем тариф…',
    # bot/handlers/payment/tariff_change_routes.py:180
    'UI_BOT_HANDLERS_PAYMENT_TARIFF_CHANGE_ROUTES_L180_1': '🎉 <b>Тариф изменён</b>\n\nНовый тариф: <b>{value_0}</b>\nСрок: {value_1}\nСписано: <b>{value_2} ₽</b>\n💰 Баланс: <b>{value_3} ₽</b>\n🎁 Бонусный баланс: <b>{value_4} ₽</b>',

    # bot/handlers/payment/tariff_change_routes.py:210
    'UI_BOT_HANDLERS_PAYMENT_TARIFF_CHANGE_ROUTES_L210_1': 'Создаём ссылку…',
    # bot/handlers/payment/tariff_change_routes.py:219
    'UI_BOT_HANDLERS_PAYMENT_TARIFF_CHANGE_ROUTES_L219_1': 'Котировка устарела',
    # bot/handlers/payment/tariff_change_routes.py:252
    'UI_BOT_HANDLERS_PAYMENT_TARIFF_CHANGE_ROUTES_L252_1': 'Котировка устарела',
    # bot/handlers/payment/tariff_change_routes.py:264
    'UI_BOT_HANDLERS_PAYMENT_TARIFF_CHANGE_ROUTES_L264_1': 'Введите целую сумму от <b>{value_0} ₽</b> до 5000 ₽.',
    # bot/handlers/profile.py:103
    'UI_BOT_HANDLERS_PROFILE_L103_1': '🚀 Купить доступ',
    # bot/handlers/profile.py:107
    'UI_BOT_HANDLERS_PROFILE_L107_1': '💰 Баланс',
    # bot/handlers/profile.py:111
    'UI_BOT_HANDLERS_PROFILE_L111_1': '🎁 Пригласить друга',
    # bot/handlers/profile.py:115
    'UI_BOT_HANDLERS_PROFILE_L115_1': '🧾 История оплат',
    # bot/handlers/profile.py:119
    'UI_BOT_HANDLERS_PROFILE_L119_1': '🏠 В главное меню',
    # bot/handlers/support.py:18
    'UI_BOT_HANDLERS_SUPPORT_L18_1': '💬 Написать @{value_0}',
    # bot/handlers/support.py:23
    'UI_BOT_HANDLERS_SUPPORT_L23_1': '❓ Частые вопросы',
    # bot/handlers/support.py:28
    'UI_BOT_HANDLERS_SUPPORT_L28_1': '📄 Условия сервиса',
    # bot/handlers/support.py:33
    'UI_BOT_HANDLERS_SUPPORT_L33_1': '🔒 Политика',
    # bot/handlers/support.py:38
    'UI_BOT_HANDLERS_SUPPORT_L38_1': '← В главное меню',
    # bot/keyboards/admin/broadcast.py:9
    'UI_BOT_KEYBOARDS_ADMIN_BROADCAST_L9_1': '✅ Отправить всем',
    # bot/keyboards/admin/broadcast.py:14
    'UI_BOT_KEYBOARDS_ADMIN_BROADCAST_L14_1': '✅ Только активным',
    # bot/keyboards/admin/broadcast.py:19
    'UI_BOT_KEYBOARDS_ADMIN_BROADCAST_L19_1': '❌ Отмена',
    # bot/keyboards/admin/broadcast.py:32
    'UI_BOT_KEYBOARDS_ADMIN_BROADCAST_L32_1': '✅ Ок (Убрать)',
    # bot/keyboards/admin/broadcast.py:45
    'UI_BOT_KEYBOARDS_ADMIN_BROADCAST_L45_1': '✅ Прочитано (убрать)',
    # bot/keyboards/admin/dashboard.py:11
    'UI_BOT_KEYBOARDS_ADMIN_DASHBOARD_L11_1': '👥 Пользователи',
    # bot/keyboards/admin/dashboard.py:15
    'UI_BOT_KEYBOARDS_ADMIN_DASHBOARD_L15_1': '📢 Рассылка',
    # bot/keyboards/admin/dashboard.py:19
    'UI_BOT_KEYBOARDS_ADMIN_DASHBOARD_L19_1': '🌐 Серверы',
    # bot/keyboards/admin/dashboard.py:23
    'UI_BOT_KEYBOARDS_ADMIN_DASHBOARD_L23_1': '💰 Тарифы',
    # bot/keyboards/admin/dashboard.py:27
    'UI_BOT_KEYBOARDS_ADMIN_DASHBOARD_L27_1': '💳 Платежи',
    # bot/keyboards/admin/dashboard.py:31
    'UI_BOT_KEYBOARDS_ADMIN_DASHBOARD_L31_1': '⚠️ Споры',
    # bot/keyboards/admin/dashboard.py:35
    'UI_BOT_KEYBOARDS_ADMIN_DASHBOARD_L35_1': '🧰 Платёжные очереди',
    # bot/keyboards/admin/dashboard.py:39
    'UI_BOT_KEYBOARDS_ADMIN_DASHBOARD_L39_1': '📜 Аудит-лог',
    # bot/keyboards/admin/dashboard.py:45
    'UI_BOT_KEYBOARDS_ADMIN_DASHBOARD_L45_1': '🛠 Техработы: ВКЛ',
    # bot/keyboards/admin/dashboard.py:50
    'UI_BOT_KEYBOARDS_ADMIN_DASHBOARD_L50_1': '🛠 Техработы: ВЫКЛ',
    # bot/keyboards/admin/dashboard.py:55
    'UI_BOT_KEYBOARDS_ADMIN_DASHBOARD_L55_1': '← В главное меню',
    # bot/keyboards/admin/dashboard.py:66
    'UI_BOT_KEYBOARDS_ADMIN_DASHBOARD_L66_1': '🔄 Обновить',
    # bot/keyboards/admin/dashboard.py:70
    'UI_BOT_KEYBOARDS_ADMIN_DASHBOARD_L70_1': '← В админку',
    # bot/keyboards/admin/dashboard.py:80
    'UI_BOT_KEYBOARDS_ADMIN_DASHBOARD_L80_1': '✅ Подтвердить',
    # bot/keyboards/admin/dashboard.py:84
    'UI_BOT_KEYBOARDS_ADMIN_DASHBOARD_L84_1': '❌ Отмена',
    # bot/keyboards/admin/servers.py:12
    'UI_BOT_KEYBOARDS_ADMIN_SERVERS_L12_1': '✏️ Изменить имя',
    # bot/keyboards/admin/servers.py:16
    'UI_BOT_KEYBOARDS_ADMIN_SERVERS_L16_1': '🏳 Изменить флаг',
    # bot/keyboards/admin/servers.py:20
    'UI_BOT_KEYBOARDS_ADMIN_SERVERS_L20_1': '🔗 Изменить URL',
    # bot/keyboards/admin/servers.py:24
    'UI_BOT_KEYBOARDS_ADMIN_SERVERS_L24_1': '🔑 Изменить ключ',
    # bot/keyboards/admin/servers.py:28
    'UI_BOT_KEYBOARDS_ADMIN_SERVERS_L28_1': '👥 Изменить лимит',
    # bot/keyboards/admin/servers.py:42
    'UI_BOT_KEYBOARDS_ADMIN_SERVERS_L42_1': '🗑 Удалить сервер',
    # bot/keyboards/admin/servers.py:46
    'UI_BOT_KEYBOARDS_ADMIN_SERVERS_L46_1': '← К списку серверов',
    # bot/keyboards/admin/servers.py:60
    'UI_BOT_KEYBOARDS_ADMIN_SERVERS_L60_1': '✅ Да, удалить полностью',
    # bot/keyboards/admin/servers.py:64
    'UI_BOT_KEYBOARDS_ADMIN_SERVERS_L64_1': '❌ Отмена',
    # bot/keyboards/admin/servers.py:68
    'UI_BOT_KEYBOARDS_ADMIN_SERVERS_L68_1': '← К списку серверов',
    # bot/keyboards/admin/servers.py:72
    'UI_BOT_KEYBOARDS_ADMIN_SERVERS_L72_1': '🏠 В главное меню',
    # bot/keyboards/admin/tariffs.py:20
    'UI_BOT_KEYBOARDS_ADMIN_TARIFFS_L20_1': '✏️ Изменить цену ₽',
    # bot/keyboards/admin/tariffs.py:34
    'UI_BOT_KEYBOARDS_ADMIN_TARIFFS_L34_1': '← К списку тарифов',
    # bot/keyboards/admin/users.py:15
    'UI_BOT_KEYBOARDS_ADMIN_USERS_L15_1': '📅 Подписка',
    # bot/keyboards/admin/users.py:20
    'UI_BOT_KEYBOARDS_ADMIN_USERS_L20_1': '🔧 Устройства',
    # bot/keyboards/admin/users.py:26
    'UI_BOT_KEYBOARDS_ADMIN_USERS_L26_1': '✅ Разбанить',
    # bot/keyboards/admin/users.py:31
    'UI_BOT_KEYBOARDS_ADMIN_USERS_L31_1': '🚫 Забанить',
    # bot/keyboards/admin/users.py:36
    'UI_BOT_KEYBOARDS_ADMIN_USERS_L36_1': '← К списку пользователей',
    # bot/keyboards/admin/users.py:53
    'UI_BOT_KEYBOARDS_ADMIN_USERS_L53_1': '💎 Сменить тариф',
    # bot/keyboards/admin/users.py:58
    'UI_BOT_KEYBOARDS_ADMIN_USERS_L58_1': '➕ Продлить доступ',
    # bot/keyboards/admin/users.py:63
    'UI_BOT_KEYBOARDS_ADMIN_USERS_L63_1': '➖ Уменьшить дни',
    # bot/keyboards/admin/users.py:68
    'UI_BOT_KEYBOARDS_ADMIN_USERS_L68_1': '🎫 Выдать доступ',
    # bot/keyboards/admin/users.py:73
    'UI_BOT_KEYBOARDS_ADMIN_USERS_L73_1': '← К карточке',
    # bot/keyboards/admin/users.py:112
    'UI_BOT_KEYBOARDS_ADMIN_USERS_L112_1': '← Назад',
    # bot/keyboards/admin/users.py:138
    'UI_BOT_KEYBOARDS_ADMIN_USERS_L138_1': '← Назад',
    # bot/keyboards/admin/users.py:155
    'UI_BOT_KEYBOARDS_ADMIN_USERS_L155_1': '{value_0} дней',
    # bot/keyboards/admin/users.py:163
    'UI_BOT_KEYBOARDS_ADMIN_USERS_L163_1': '∞ Навсегда',
    # bot/keyboards/admin/users.py:171
    'UI_BOT_KEYBOARDS_ADMIN_USERS_L171_1': '⌨️ Ввести вручную',
    # bot/keyboards/admin/users.py:178
    'UI_BOT_KEYBOARDS_ADMIN_USERS_L178_1': '← Назад',
    # bot/keyboards/admin/users.py:194
    'UI_BOT_KEYBOARDS_ADMIN_USERS_L194_1': '{value_0} дней',
    # bot/keyboards/admin/users.py:201
    'UI_BOT_KEYBOARDS_ADMIN_USERS_L201_1': '∞ Навсегда',
    # bot/keyboards/admin/users.py:208
    'UI_BOT_KEYBOARDS_ADMIN_USERS_L208_1': '⌨️ Ввести вручную',
    # bot/keyboards/admin/users.py:213
    'UI_BOT_KEYBOARDS_ADMIN_USERS_L213_1': '← Назад',
    # bot/keyboards/admin/users.py:229
    'UI_BOT_KEYBOARDS_ADMIN_USERS_L229_1': '✅ Подтвердить',
    # bot/keyboards/admin/users.py:234
    'UI_BOT_KEYBOARDS_ADMIN_USERS_L234_1': '❌ Отмена',
    # bot/keyboards/admin/users.py:263
    'UI_BOT_KEYBOARDS_ADMIN_USERS_L263_1': '← К карточке',
    # bot/keyboards/common.py:13
    'UI_BOT_KEYBOARDS_COMMON_L13_1': '⏳ Моя подписка',
    # bot/keyboards/common.py:18
    'UI_BOT_KEYBOARDS_COMMON_L18_1': '🚀 Купить доступ',
    # bot/keyboards/common.py:23
    'UI_BOT_KEYBOARDS_COMMON_L23_1': '🔌 Подключения',
    # bot/keyboards/common.py:28
    'UI_BOT_KEYBOARDS_COMMON_L28_1': '💰 Баланс',
    # bot/keyboards/common.py:38
    'UI_BOT_KEYBOARDS_COMMON_L38_1': '💬 Поддержка',
    # bot/keyboards/common.py:44
    'UI_BOT_KEYBOARDS_COMMON_L44_1': '🛠 Админка',
    # bot/keyboards/device.py:9
    'UI_BOT_KEYBOARDS_DEVICE_L9_1': '✏️ Изменить имя',
    # bot/keyboards/device.py:14
    'UI_BOT_KEYBOARDS_DEVICE_L14_1': '🔑 Показать ключ',
    # bot/keyboards/device.py:15
    'UI_BOT_KEYBOARDS_DEVICE_L15_1': '📥 Скачать файлом',
    # bot/keyboards/device.py:18
    'UI_BOT_KEYBOARDS_DEVICE_L18_1': '🗑 Удалить устройство',
    # bot/keyboards/device.py:23
    'UI_BOT_KEYBOARDS_DEVICE_L23_1': '← К списку устройств',
    # bot/keyboards/device.py:28
    'UI_BOT_KEYBOARDS_DEVICE_L28_1': '🏠 В главное меню',
    # bot/keyboards/device.py:43
    'UI_BOT_KEYBOARDS_DEVICE_L43_1': '✅ Да, удалить',
    # bot/keyboards/device.py:48
    'UI_BOT_KEYBOARDS_DEVICE_L48_1': '❌ Отмена',
    # bot/keyboards/payment.py:52
    'UI_BOT_KEYBOARDS_PAYMENT_L52_1': '← К выбору тарифа',
    # bot/keyboards/payment.py:113
    'UI_BOT_KEYBOARDS_PAYMENT_L113_1': '🔌 Подключить устройство',
    # bot/keyboards/payment.py:116
    'UI_BOT_KEYBOARDS_PAYMENT_L116_1': '⏳ К подписке',
    # bot/keyboards/payment.py:150
    'UI_BOT_KEYBOARDS_PAYMENT_L150_1': '{value_0} ₽',
    # bot/keyboards/payment.py:206
    'UI_BOT_KEYBOARDS_PAYMENT_L206_1': '💰 Купить с баланса',
    # bot/keyboards/payment.py:219
    'UI_BOT_KEYBOARDS_PAYMENT_L219_1': '✅ Подтвердить покупку',
    # bot/keyboards/payment.py:232
    'UI_BOT_KEYBOARDS_PAYMENT_L232_1': '💱 Сменить тариф с баланса',
    # bot/keyboards/payment.py:245
    'UI_BOT_KEYBOARDS_PAYMENT_L245_1': '✅ Подтвердить смену тарифа',
    # bot/keyboards/payment.py:256
    'UI_BOT_KEYBOARDS_PAYMENT_L256_1': '🔄 Перейти к продлению',
    # bot/keyboards/payment.py:269
    'UI_BOT_KEYBOARDS_PAYMENT_L269_1': 'Пополнить на {value_0} ₽',
    # bot/keyboards/payment.py:273
    'UI_BOT_KEYBOARDS_PAYMENT_L273_1': 'Указать другую сумму',
    # bot/keyboards/payment.py:286
    'UI_BOT_KEYBOARDS_PAYMENT_L286_1': 'Пополнить на {value_0} ₽',
    # bot/keyboards/payment.py:290
    'UI_BOT_KEYBOARDS_PAYMENT_L290_1': 'Указать другую сумму',
    # bot/keyboards/payment.py:304
    'UI_BOT_KEYBOARDS_PAYMENT_L304_1': 'Вернуться к покупке',
    # bot/keyboards/payment.py:307
    'UI_BOT_KEYBOARDS_PAYMENT_L307_1': '💰 К балансу',
    # bot/middlewares/action_lock.py:199
    'UI_BOT_MIDDLEWARES_ACTION_LOCK_L199_1': 'Некорректный запрос',
    # bot/middlewares/action_lock.py:217
    'UI_BOT_MIDDLEWARES_ACTION_LOCK_L217_1': 'Сессия подтверждения истекла',
    # bot/middlewares/action_lock.py:237
    'UI_BOT_MIDDLEWARES_ACTION_LOCK_L237_1': 'Некорректный запрос',
    # bot/middlewares/action_lock.py:276
    'UI_BOT_MIDDLEWARES_ACTION_LOCK_L276_1': 'Некорректный запрос',
    # services/workers/account_balance.py:232
    'UI_SERVICES_WORKERS_ACCOUNT_BALANCE_L232_1': '✅ <b>{value_0}</b>\nСрок: <b>{value_1}</b>\nУстройства: <b>до {value_2}</b>',
    # services/workers/account_balance.py:295
    'UI_SERVICES_WORKERS_ACCOUNT_BALANCE_L295_1': '💳 <b>Ссылка на пополнение готова</b>\n\nСумма: <b>{value_0} ₽</b>\n\nПерейдите на защищённую страницу ЮKassa.',
    # services/workers/cleanup.py:184
    'UI_SERVICES_WORKERS_CLEANUP_L184_1': '⚠️ Ваши устройства были удалены из-за истечения подписки. Продлите доступ, чтобы создать новые.',
    # services/workers/notifications.py:251
    'UI_SERVICES_WORKERS_NOTIFICATIONS_L251_1': '💳 Продлить доступ',
    # services/workers/notifications.py:256
    'UI_SERVICES_WORKERS_NOTIFICATIONS_L256_1': '✅ Прочитано (убрать)',
    # services/workers/notifications.py:410
    'UI_SERVICES_WORKERS_NOTIFICATIONS_L410_1': '🚀 Купить доступ',
    # services/workers/notifications.py:415
    'UI_SERVICES_WORKERS_NOTIFICATIONS_L415_1': '💬 Поддержка',
    # services/workers/notifications.py:420
    'UI_SERVICES_WORKERS_NOTIFICATIONS_L420_1': '✅ Прочитано (убрать)',
    # services/workers/traffic.py:319
    'UI_SERVICES_WORKERS_TRAFFIC_L319_1': '👤 Карточка пользователя',
}
