"""Central catalogue for runtime Telegram labels, messages, and alerts.

Entries retain source locations for easy copy editing.
"""

TEXTS = {
    # bot/handlers/admin/broadcast.py:628
    'RUNTIME_BOT_HANDLERS_ADMIN_BROADCAST_L628_1': 'Всего',
    # bot/handlers/admin/dashboard.py:111
    'RUNTIME_BOT_HANDLERS_ADMIN_DASHBOARD_L111_1': ' · {value_0}',
    # bot/handlers/admin/disputes.py:35
    'RUNTIME_BOT_HANDLERS_ADMIN_DISPUTES_L35_1': 'открыт',
    # bot/handlers/admin/disputes.py:36
    'RUNTIME_BOT_HANDLERS_ADMIN_DISPUTES_L36_1': 'ручная проверка',
    # bot/handlers/admin/disputes.py:37
    'RUNTIME_BOT_HANDLERS_ADMIN_DISPUTES_L37_1': 'выигран продавцом',
    # bot/handlers/admin/disputes.py:38
    'RUNTIME_BOT_HANDLERS_ADMIN_DISPUTES_L38_1': 'проигран продавцом',
    # bot/handlers/admin/disputes.py:44
    'RUNTIME_BOT_HANDLERS_ADMIN_DISPUTES_L44_1': 'Нужен YooKassa payment ID',
    # bot/handlers/admin/disputes.py:45
    'RUNTIME_BOT_HANDLERS_ADMIN_DISPUTES_L45_1': 'Нужен ID спора банка/провайдера',
    # bot/handlers/admin/disputes.py:46
    'RUNTIME_BOT_HANDLERS_ADMIN_DISPUTES_L46_1': 'Сумма должна быть целым числом рублей',
    # bot/handlers/admin/disputes.py:47
    'RUNTIME_BOT_HANDLERS_ADMIN_DISPUTES_L47_1': 'Некорректная дата спора',
    # bot/handlers/admin/disputes.py:48
    'RUNTIME_BOT_HANDLERS_ADMIN_DISPUTES_L48_1': 'Платёж с таким YooKassa ID не найден',
    # bot/handlers/admin/disputes.py:50
    'RUNTIME_BOT_HANDLERS_ADMIN_DISPUTES_L50_1': 'Спор поддерживается только для пополнения баланса',
    # bot/handlers/admin/disputes.py:52
    'RUNTIME_BOT_HANDLERS_ADMIN_DISPUTES_L52_1': 'Платёж ещё не подтверждён',
    # bot/handlers/admin/disputes.py:53
    'RUNTIME_BOT_HANDLERS_ADMIN_DISPUTES_L53_1': 'Пополнение ещё не зачислено в ledger',
    # bot/handlers/admin/disputes.py:54
    'RUNTIME_BOT_HANDLERS_ADMIN_DISPUTES_L54_1': 'Этот case ID уже связан с другими данными',
    # bot/handlers/admin/disputes.py:55
    'RUNTIME_BOT_HANDLERS_ADMIN_DISPUTES_L55_1': 'По платежу уже открыт спор',
    # bot/handlers/admin/disputes.py:56
    'RUNTIME_BOT_HANDLERS_ADMIN_DISPUTES_L56_1': 'Сначала завершите активный refund',
    # bot/handlers/admin/disputes.py:58
    'RUNTIME_BOT_HANDLERS_ADMIN_DISPUTES_L58_1': 'Сумма превышает остаток платёжного риска',
    # bot/handlers/admin/disputes.py:60
    'RUNTIME_BOT_HANDLERS_ADMIN_DISPUTES_L60_1': 'Спор не найден',
    # bot/handlers/admin/disputes.py:61
    'RUNTIME_BOT_HANDLERS_ADMIN_DISPUTES_L61_1': 'Спор уже завершён другим исходом',
    # bot/handlers/admin/disputes.py:62
    'RUNTIME_BOT_HANDLERS_ADMIN_DISPUTES_L62_1': 'Операция со спором отклонена финансовыми инвариантами',
    # bot/handlers/admin/disputes.py:109
    'RUNTIME_BOT_HANDLERS_ADMIN_DISPUTES_L109_1': '⚠️ <b>Спор #{value_0}</b>\nСтатус: <b>{value_1}</b>\nCase ID: <code>{value_2}</code>\nYooKassa payment: <code>{value_3}</code>\nСумма: <b>{value_4} RUB</b>\nДата спора: <code>{value_5}</code>\nReservation: <code>{value_6}</code> ({value_7})\nChargeback entry: <code>{value_8}</code>\nЗаметка: {value_9}',
    # bot/handlers/admin/disputes.py:143
    'RUNTIME_BOT_HANDLERS_ADMIN_DISPUTES_L143_1': '🛠 Админка › ⚠️ <b>Платёжные споры</b>',
    # bot/handlers/admin/disputes.py:147
    'RUNTIME_BOT_HANDLERS_ADMIN_DISPUTES_L147_1': '#{value_0} · {value_1} · {value_2} ₽ · case={value_3}',
    # bot/handlers/admin/disputes.py:157
    'RUNTIME_BOT_HANDLERS_ADMIN_DISPUTES_L157_1': 'Споров пока нет.',
    # bot/handlers/admin/disputes.py:333
    'RUNTIME_BOT_HANDLERS_ADMIN_DISPUTES_L333_1': 'reservation будет освобождена',
    # bot/handlers/admin/disputes.py:335
    'RUNTIME_BOT_HANDLERS_ADMIN_DISPUTES_L335_1': 'будет создан exactly-once chargeback debit; возможен долг',
    # bot/handlers/admin/payment_queues.py:57
    'RUNTIME_BOT_HANDLERS_ADMIN_PAYMENT_QUEUES_L57_1': '—',
    # bot/handlers/admin/payment_queues.py:59
    'RUNTIME_BOT_HANDLERS_ADMIN_PAYMENT_QUEUES_L59_1': '{value_0}д',
    # bot/handlers/admin/payment_queues.py:61
    'RUNTIME_BOT_HANDLERS_ADMIN_PAYMENT_QUEUES_L61_1': '{value_0}ч',
    # bot/handlers/admin/payment_queues.py:63
    'RUNTIME_BOT_HANDLERS_ADMIN_PAYMENT_QUEUES_L63_1': '{value_0}м',
    # bot/handlers/admin/payment_queues.py:64
    'RUNTIME_BOT_HANDLERS_ADMIN_PAYMENT_QUEUES_L64_1': '{value_0}с',
    # bot/handlers/admin/payment_queues.py:89
    'RUNTIME_BOT_HANDLERS_ADMIN_PAYMENT_QUEUES_L89_1': '🛠 Админка › 🧰 <b>Платёжные очереди</b>',
    # bot/handlers/admin/payment_queues.py:105
    'RUNTIME_BOT_HANDLERS_ADMIN_PAYMENT_QUEUES_L105_1': 'pending={value_0} · retry={value_1} · due={value_2} · overdue={value_3}',
    # bot/handlers/admin/payment_queues.py:106
    'RUNTIME_BOT_HANDLERS_ADMIN_PAYMENT_QUEUES_L106_1': 'processing={value_0} · stale={value_1} · dead={value_2}',
    # bot/handlers/admin/payment_queues.py:107
    'RUNTIME_BOT_HANDLERS_ADMIN_PAYMENT_QUEUES_L107_1': 'Старейшая проблема: {value_0}',
    # bot/handlers/admin/payment_queues.py:126
    'RUNTIME_BOT_HANDLERS_ADMIN_PAYMENT_QUEUES_L126_1': '🧰 <b>{value_0}</b>',
    # bot/handlers/admin/payment_queues.py:127
    'RUNTIME_BOT_HANDLERS_ADMIN_PAYMENT_QUEUES_L127_1': 'Проблемные операции · стр. {value_0}/{value_1} · всего {value_2}',
    # bot/handlers/admin/payment_queues.py:132
    'RUNTIME_BOT_HANDLERS_ADMIN_PAYMENT_QUEUES_L132_1': 'Проблемных операций нет.',
    # bot/handlers/admin/payment_queues.py:135
    'RUNTIME_BOT_HANDLERS_ADMIN_PAYMENT_QUEUES_L135_1': '#{value_0} · {value_1} · {value_2} · {value_3}/{value_4} · error={value_5} · возраст {value_6}',
    # bot/handlers/admin/payment_queues.py:140
    'RUNTIME_BOT_HANDLERS_ADMIN_PAYMENT_QUEUES_L140_1': '#{value_0} · {value_1} · {value_2}',
    # bot/handlers/admin/payment_queues.py:155
    'RUNTIME_BOT_HANDLERS_ADMIN_PAYMENT_QUEUES_L155_1': '🧰 <b>{value_0}</b>',
    # bot/handlers/admin/payment_queues.py:157
    'RUNTIME_BOT_HANDLERS_ADMIN_PAYMENT_QUEUES_L157_1': '—',
    # bot/handlers/admin/payment_queues.py:158
    'RUNTIME_BOT_HANDLERS_ADMIN_PAYMENT_QUEUES_L158_1': 'Тип: {value_0}',
    # bot/handlers/admin/payment_queues.py:159
    'RUNTIME_BOT_HANDLERS_ADMIN_PAYMENT_QUEUES_L159_1': 'Статус: {value_0}',
    # bot/handlers/admin/payment_queues.py:160
    'RUNTIME_BOT_HANDLERS_ADMIN_PAYMENT_QUEUES_L160_1': 'Попытки: {value_0}/{value_1}',
    # bot/handlers/admin/payment_queues.py:161
    'RUNTIME_BOT_HANDLERS_ADMIN_PAYMENT_QUEUES_L161_1': '—',
    # bot/handlers/admin/payment_queues.py:162
    'RUNTIME_BOT_HANDLERS_ADMIN_PAYMENT_QUEUES_L162_1': 'Создано: {value_0}',
    # bot/handlers/admin/payment_queues.py:163
    'RUNTIME_BOT_HANDLERS_ADMIN_PAYMENT_QUEUES_L163_1': 'Обновлено: {value_0}',
    # bot/handlers/admin/payment_queues.py:164
    'RUNTIME_BOT_HANDLERS_ADMIN_PAYMENT_QUEUES_L164_1': 'Завершено/обработано: {value_0}',
    # bot/handlers/admin/payment_queues.py:167
    'RUNTIME_BOT_HANDLERS_ADMIN_PAYMENT_QUEUES_L167_1': 'Ручной retry: {value_0}',
    # bot/handlers/admin/payment_queues.py:231
    'RUNTIME_BOT_HANDLERS_ADMIN_PAYMENT_QUEUES_L231_1': 'Операция не найдена',
    # bot/handlers/admin/payment_queues.py:358
    'RUNTIME_BOT_HANDLERS_ADMIN_PAYMENT_QUEUES_L358_1': 'Операция поставлена в retry. Исполнение выполнит фоновый worker.',
    # bot/handlers/admin/payment_queues.py:359
    'RUNTIME_BOT_HANDLERS_ADMIN_PAYMENT_QUEUES_L359_1': 'Retry отклонён: {value_0}',
    # bot/handlers/admin/payment_queues.py:360
    'RUNTIME_BOT_HANDLERS_ADMIN_PAYMENT_QUEUES_L360_1': 'Операция не найдена',
    # bot/handlers/admin/payment_queues.py:361
    'RUNTIME_BOT_HANDLERS_ADMIN_PAYMENT_QUEUES_L361_1': 'Состояние уже изменилось',
    # bot/handlers/admin/payment_queues.py:365
    'RUNTIME_BOT_HANDLERS_ADMIN_PAYMENT_QUEUES_L365_1': 'Состояние уже изменилось',
    # bot/handlers/admin/payment_queues.py:383
    'RUNTIME_BOT_HANDLERS_ADMIN_PAYMENT_QUEUES_L383_1': 'Отменено',
    # bot/handlers/admin/payments.py:75
    'RUNTIME_BOT_HANDLERS_ADMIN_PAYMENTS_L75_1': '🛠 Админка › 💳 <b>Платежи</b>\n(стр. {value_0}/{value_1}) · Всего: {value_2}\n',
    # bot/handlers/admin/payments.py:80
    'RUNTIME_BOT_HANDLERS_ADMIN_PAYMENTS_L80_1': '<i>Платежей пока нет</i>\n',
    # bot/handlers/admin/payments.py:86
    'RUNTIME_BOT_HANDLERS_ADMIN_PAYMENTS_L86_1': '❓',
    # bot/handlers/admin/payments.py:93
    'RUNTIME_BOT_HANDLERS_ADMIN_PAYMENTS_L93_1': '—',
    # bot/handlers/admin/payments.py:95
    'RUNTIME_BOT_HANDLERS_ADMIN_PAYMENTS_L95_1': '{value_0} #{value_1} · {value_2} · {value_3}₽',
    # bot/handlers/admin/payments.py:239
    'RUNTIME_BOT_HANDLERS_ADMIN_PAYMENTS_L239_1': '—',
    # bot/handlers/admin/payments.py:249
    'RUNTIME_BOT_HANDLERS_ADMIN_PAYMENTS_L249_1': '❓',
    # bot/handlers/admin/payments.py:258
    'RUNTIME_BOT_HANDLERS_ADMIN_PAYMENTS_L258_1': '\n<b>Причина:</b> {value_0}',
    # bot/handlers/admin/payments.py:268
    'RUNTIME_BOT_HANDLERS_ADMIN_PAYMENTS_L268_1': '\n<b>Можно вернуть:</b> {value_0} RUB',
    # bot/handlers/admin/payments.py:271
    'RUNTIME_BOT_HANDLERS_ADMIN_PAYMENTS_L271_1': '🛠 Админка › 💳 Платежи › <b>Платёж #{value_0}</b>\n<b>ID:</b> {value_1}\n<b>Пользователь:</b> {value_2}\n<b>Сумма:</b> {value_3} {value_4}\n<b>Статус:</b> {value_5} {value_6}\n<b>Provider:</b> {value_7}\n<b>Исполнение:</b> {value_8}\n<b>Создан:</b> {value_9}\n<b>Оплачен:</b> {value_10}\n<b>External ID:</b> <code>{value_11}</code>{value_12}{value_13}',
    # bot/handlers/admin/payments.py:368
    'RUNTIME_BOT_HANDLERS_ADMIN_PAYMENTS_L368_1': 'Платёж не найден',
    # bot/handlers/admin/payments.py:369
    'RUNTIME_BOT_HANDLERS_ADMIN_PAYMENTS_L369_1': 'Можно вернуть только пополнение баланса',
    # bot/handlers/admin/payments.py:370
    'RUNTIME_BOT_HANDLERS_ADMIN_PAYMENTS_L370_1': 'Платёж ещё не подтверждён или уже возвращён',
    # bot/handlers/admin/payments.py:371
    'RUNTIME_BOT_HANDLERS_ADMIN_PAYMENTS_L371_1': 'У платежа нет YooKassa ID',
    # bot/handlers/admin/payments.py:372
    'RUNTIME_BOT_HANDLERS_ADMIN_PAYMENTS_L372_1': 'Возвращаемого остатка уже нет',
    # bot/handlers/admin/payments.py:373
    'RUNTIME_BOT_HANDLERS_ADMIN_PAYMENTS_L373_1': 'Возврат требует ручной проверки',
    # bot/handlers/admin/payments.py:376
    'RUNTIME_BOT_HANDLERS_ADMIN_PAYMENTS_L376_1': 'Не удалось поставить возврат в очередь',
    # bot/handlers/admin/payments.py:390
    'RUNTIME_BOT_HANDLERS_ADMIN_PAYMENTS_L390_1': 'Возврат поставлен в durable-очередь.',
    # bot/handlers/admin/payments.py:392
    'RUNTIME_BOT_HANDLERS_ADMIN_PAYMENTS_L392_1': 'Этот возврат уже находится в durable-очереди.',
    # bot/handlers/admin/servers/add_routes.py:254
    'RUNTIME_BOT_HANDLERS_ADMIN_SERVERS_ADD_ROUTES_L254_1': 'неизвестно',
    # bot/handlers/admin/servers/card_routes.py:99
    'RUNTIME_BOT_HANDLERS_ADMIN_SERVERS_CARD_ROUTES_L99_1': '🌍',
    # bot/handlers/admin/servers/common.py:48
    'RUNTIME_BOT_HANDLERS_ADMIN_SERVERS_COMMON_L48_1': '🛠 Админка › 🌍 <b>Серверы</b>\n(стр. {value_0}/{value_1}) · Всего: {value_2}\n',
    # bot/handlers/admin/servers/common.py:53
    'RUNTIME_BOT_HANDLERS_ADMIN_SERVERS_COMMON_L53_1': '<i>Серверов пока нет</i>\n',
    # bot/handlers/admin/servers/common.py:56
    'RUNTIME_BOT_HANDLERS_ADMIN_SERVERS_COMMON_L56_1': '🌍',
    # bot/handlers/admin/servers/common.py:57
    'RUNTIME_BOT_HANDLERS_ADMIN_SERVERS_COMMON_L57_1': '🟢',
    # bot/handlers/admin/servers/common.py:59
    'RUNTIME_BOT_HANDLERS_ADMIN_SERVERS_COMMON_L59_1': '{value_0} {value_1} {value_2} · {value_3}',
    # bot/handlers/admin/servers/common.py:105
    'RUNTIME_BOT_HANDLERS_ADMIN_SERVERS_COMMON_L105_1': '🌍',
    # bot/handlers/admin/servers/common.py:106
    'RUNTIME_BOT_HANDLERS_ADMIN_SERVERS_COMMON_L106_1': '🟢 Активен',
    # bot/handlers/admin/servers/delete_routes.py:92
    'RUNTIME_BOT_HANDLERS_ADMIN_SERVERS_DELETE_ROUTES_L92_1': '🌍',
    # bot/handlers/admin/servers/edit_routes.py:199
    'RUNTIME_BOT_HANDLERS_ADMIN_SERVERS_EDIT_ROUTES_L199_1': '🌍',
    # bot/handlers/admin/servers/edit_routes.py:476
    'RUNTIME_BOT_HANDLERS_ADMIN_SERVERS_EDIT_ROUTES_L476_1': 'неизвестно',
    # bot/handlers/admin/servers/edit_routes.py:652
    'RUNTIME_BOT_HANDLERS_ADMIN_SERVERS_EDIT_ROUTES_L652_1': 'неизвестно',
    # bot/handlers/admin/tariffs.py:45
    'RUNTIME_BOT_HANDLERS_ADMIN_TARIFFS_L45_1': '🛠 Админка › 💰 <b>Тарифы</b>\n(стр. {value_0}/{value_1}) · Всего: {value_2}\n',
    # bot/handlers/admin/tariffs.py:52
    'RUNTIME_BOT_HANDLERS_ADMIN_TARIFFS_L52_1': '<i>Тарифов пока нет</i>\n',
    # bot/handlers/admin/tariffs.py:55
    'RUNTIME_BOT_HANDLERS_ADMIN_TARIFFS_L55_1': '🟢',
    # bot/handlers/admin/tariffs.py:57
    'RUNTIME_BOT_HANDLERS_ADMIN_TARIFFS_L57_1': '{value_0} {value_1} · {value_2} дн. · {value_3}₽',
    # bot/handlers/admin/tariffs.py:191
    'RUNTIME_BOT_HANDLERS_ADMIN_TARIFFS_L191_1': '🟢 Активен',
    # bot/handlers/admin/tariffs.py:193
    'RUNTIME_BOT_HANDLERS_ADMIN_TARIFFS_L193_1': '🔴 Отключён',
    # bot/handlers/admin/tariffs.py:197
    'RUNTIME_BOT_HANDLERS_ADMIN_TARIFFS_L197_1': '🛠 Админка › 💰 Тарифы › <b>Тариф</b>\n<b>ID:</b> {value_0}\n<b>Название:</b> {value_1}\n<b>Описание:</b> {value_2}\n<b>Дней:</b> {value_3}\n<b>Устройств:</b> {value_4}\n<b>Цена ₽:</b> {value_5}\n<b>Статус:</b> {value_6}',
    # bot/handlers/admin/tariffs.py:312
    'RUNTIME_BOT_HANDLERS_ADMIN_TARIFFS_L312_1': '⚠️ <b>Подтверждение включения тарифа</b>\nТариф: <b>{value_0} ({value_1} дн. / {value_2} устр.)</b>\nТариф снова будет доступен пользователям\nпри покупке доступа.\n<i>Уже купленные подписки продолжат работать.</i>',
    # bot/handlers/admin/tariffs.py:323
    'RUNTIME_BOT_HANDLERS_ADMIN_TARIFFS_L323_1': '⚠️ <b>Подтверждение отключения тарифа</b>\nТариф: <b>{value_0} ({value_1} дн. / {value_2} устр.)</b>\nТариф будет скрыт из списка доступных\nпри покупке доступа.\n<i>Уже купленные подписки продолжат работать.</i>',
    # bot/handlers/admin/users/common.py:55
    'RUNTIME_BOT_HANDLERS_ADMIN_USERS_COMMON_L55_1': '—',
    # bot/handlers/admin/users/common.py:64
    'RUNTIME_BOT_HANDLERS_ADMIN_USERS_COMMON_L64_1': 'истекла',
    # bot/handlers/admin/users/common.py:73
    'RUNTIME_BOT_HANDLERS_ADMIN_USERS_COMMON_L73_1': '{value_0} дн. {value_1} ч.',
    # bot/handlers/admin/users/common.py:77
    'RUNTIME_BOT_HANDLERS_ADMIN_USERS_COMMON_L77_1': '{value_0} ч. {value_1} мин.',
    # bot/handlers/admin/users/common.py:149
    'RUNTIME_BOT_HANDLERS_ADMIN_USERS_COMMON_L149_1': '🟢',
    # bot/handlers/admin/users/common.py:151
    'RUNTIME_BOT_HANDLERS_ADMIN_USERS_COMMON_L151_1': '🔴',
    # bot/handlers/admin/users/common.py:154
    'RUNTIME_BOT_HANDLERS_ADMIN_USERS_COMMON_L154_1': '🚫',
    # bot/handlers/admin/users/common.py:165
    'RUNTIME_BOT_HANDLERS_ADMIN_USERS_COMMON_L165_1': '{value_0}{value_1} {value_2} · {value_3} · {value_4} устр.',
    # bot/handlers/admin/users/device_routes.py:77
    'RUNTIME_BOT_HANDLERS_ADMIN_USERS_DEVICE_ROUTES_L77_1': 'Устройство #{value_0}',
    # bot/handlers/admin/users/device_routes.py:79
    'RUNTIME_BOT_HANDLERS_ADMIN_USERS_DEVICE_ROUTES_L79_1': '\n• {value_0}',
    # bot/handlers/admin/users/device_routes.py:135
    'RUNTIME_BOT_HANDLERS_ADMIN_USERS_DEVICE_ROUTES_L135_1': '🌍',
    # bot/handlers/admin/users/device_routes.py:136
    'RUNTIME_BOT_HANDLERS_ADMIN_USERS_DEVICE_ROUTES_L136_1': 'Неизвестно',
    # bot/handlers/admin/users/subscription_change_routes.py:76
    'RUNTIME_BOT_HANDLERS_ADMIN_USERS_SUBSCRIPTION_CHANGE_ROUTES_L76_1': '—',
    # bot/handlers/admin/users/subscription_change_routes.py:202
    'RUNTIME_BOT_HANDLERS_ADMIN_USERS_SUBSCRIPTION_CHANGE_ROUTES_L202_1': '—',
    # bot/handlers/admin/users/subscription_extend_routes.py:158
    'RUNTIME_BOT_HANDLERS_ADMIN_USERS_SUBSCRIPTION_EXTEND_ROUTES_L158_1': '{value_0} дн.',
    # bot/handlers/admin/users/subscription_extend_routes.py:256
    'RUNTIME_BOT_HANDLERS_ADMIN_USERS_SUBSCRIPTION_EXTEND_ROUTES_L256_1': '{value_0} дн.',
    # bot/handlers/admin/users/subscription_extend_routes.py:271
    'RUNTIME_BOT_HANDLERS_ADMIN_USERS_SUBSCRIPTION_EXTEND_ROUTES_L271_1': '—',
    # bot/handlers/admin/users/subscription_extend_routes.py:415
    'RUNTIME_BOT_HANDLERS_ADMIN_USERS_SUBSCRIPTION_EXTEND_ROUTES_L415_1': '{value_0} дн.',
    # bot/handlers/admin/users/subscription_grant_routes.py:240
    'RUNTIME_BOT_HANDLERS_ADMIN_USERS_SUBSCRIPTION_GRANT_ROUTES_L240_1': '{value_0} дн.',
    # bot/handlers/admin/users/subscription_grant_routes.py:403
    'RUNTIME_BOT_HANDLERS_ADMIN_USERS_SUBSCRIPTION_GRANT_ROUTES_L403_1': '{value_0} дн.',
    # bot/handlers/admin/users/subscription_grant_routes.py:521
    'RUNTIME_BOT_HANDLERS_ADMIN_USERS_SUBSCRIPTION_GRANT_ROUTES_L521_1': '{value_0} дн.',
    # bot/handlers/admin/users/subscription_grant_routes.py:543
    'RUNTIME_BOT_HANDLERS_ADMIN_USERS_SUBSCRIPTION_GRANT_ROUTES_L543_1': '—',
    # bot/handlers/admin/users/subscription_menu_routes.py:65
    'RUNTIME_BOT_HANDLERS_ADMIN_USERS_SUBSCRIPTION_MENU_ROUTES_L65_1': '—',
    # bot/handlers/admin/users/subscription_menu_routes.py:78
    'RUNTIME_BOT_HANDLERS_ADMIN_USERS_SUBSCRIPTION_MENU_ROUTES_L78_1': '{value_0} ({value_1} устр.)',
    # bot/handlers/connection/common.py:37
    'RUNTIME_BOT_HANDLERS_CONNECTION_COMMON_L37_1': '—',
    # bot/handlers/connection/common.py:69
    'RUNTIME_BOT_HANDLERS_CONNECTION_COMMON_L69_1': 'в ближайшее время',
    # bot/handlers/connection/common.py:75
    'RUNTIME_BOT_HANDLERS_CONNECTION_COMMON_L75_1': 'в ближайшее время',
    # bot/handlers/connection/common.py:81
    'RUNTIME_BOT_HANDLERS_CONNECTION_COMMON_L81_1': '{value_0} дн. {value_1} ч.',
    # bot/handlers/connection/common.py:84
    'RUNTIME_BOT_HANDLERS_CONNECTION_COMMON_L84_1': '{value_0} ч. {value_1} мин.',
    # bot/handlers/connection/common.py:140
    'RUNTIME_BOT_HANDLERS_CONNECTION_COMMON_L140_1': '🌍',
    # bot/handlers/connection/common.py:141
    'RUNTIME_BOT_HANDLERS_CONNECTION_COMMON_L141_1': 'Неизвестно',
    # bot/handlers/connection/common.py:171
    'RUNTIME_BOT_HANDLERS_CONNECTION_COMMON_L171_1': 'создаётся',
    # bot/handlers/connection/common.py:172
    'RUNTIME_BOT_HANDLERS_CONNECTION_COMMON_L172_1': 'удаляется',
    # bot/handlers/connection/common.py:173
    'RUNTIME_BOT_HANDLERS_CONNECTION_COMMON_L173_1': 'очистка после ошибки создания',
    # bot/handlers/connection/common.py:174
    'RUNTIME_BOT_HANDLERS_CONNECTION_COMMON_L174_1': 'ошибка обновления',
    # bot/handlers/connection/common.py:177
    'RUNTIME_BOT_HANDLERS_CONNECTION_COMMON_L177_1': '\n⏳ Статус: {value_0}\n',
    # bot/handlers/connection/device_create_routes.py:148
    'RUNTIME_BOT_HANDLERS_CONNECTION_DEVICE_CREATE_ROUTES_L148_1': '🌍',
    # bot/handlers/connection/device_create_routes.py (creating screen)
    'RUNTIME_BOT_HANDLERS_CONNECTION_DEVICE_CREATE_ROUTES_CREATING_SCREEN': (
        '⏳ <b>Настраиваем подключение...</b>\n\n'
        '🌍 Сервер: <b>{value_0}</b>\n\n'
        '<i>Подготавливаем защищенный доступ...</i>'
    ),
    'RUNTIME_BOT_HANDLERS_CONNECTION_DEVICE_CREATE_ROUTES_DEFAULT_SERVER_NAME': 'Сервер подключения',
    # bot/handlers/connection/device_create_routes.py:234
    'RUNTIME_BOT_HANDLERS_CONNECTION_DEVICE_CREATE_ROUTES_L234_1': '🌍',
    # bot/handlers/connection/device_view_routes.py:60
    'RUNTIME_BOT_HANDLERS_CONNECTION_DEVICE_VIEW_ROUTES_L60_1': '🌍',
    # bot/handlers/connection/device_view_routes.py:61
    'RUNTIME_BOT_HANDLERS_CONNECTION_DEVICE_VIEW_ROUTES_L61_1': 'Неизвестно',
    # bot/handlers/connection/device_view_routes.py:73
    'RUNTIME_BOT_HANDLERS_CONNECTION_DEVICE_VIEW_ROUTES_L73_1': 'Нет данных',
    # bot/handlers/connection/device_view_routes.py:87
    'RUNTIME_BOT_HANDLERS_CONNECTION_DEVICE_VIEW_ROUTES_L87_1': '\n⚠️ <b>Доступ неактивен</b>\nКлюч и файлы конфигурации недоступны.\nУстройство можно удалить.\n',
    # bot/handlers/payment/balance_routes.py:83
    'RUNTIME_BOT_HANDLERS_PAYMENT_BALANCE_ROUTES_L83_1': 'Операция',
    # bot/handlers/payment/balance_routes.py:84
    'RUNTIME_BOT_HANDLERS_PAYMENT_BALANCE_ROUTES_L84_1': '−',
    # bot/handlers/payment/balance_routes.py:87
    'RUNTIME_BOT_HANDLERS_PAYMENT_BALANCE_ROUTES_L87_1': '• {value_0} · {value_1}: <b>{value_2}{value_3} ₽</b>',
    # bot/handlers/payment/balance_routes.py:104
    'RUNTIME_BOT_HANDLERS_PAYMENT_BALANCE_ROUTES_L104_1': 'Доступно: <b>{value_0} ₽</b>',
    # bot/handlers/payment/balance_routes.py:106
    'RUNTIME_BOT_HANDLERS_PAYMENT_BALANCE_ROUTES_L106_1': 'Зарезервировано: <b>{value_0} ₽</b>',
    # bot/handlers/payment/balance_routes.py:108
    'RUNTIME_BOT_HANDLERS_PAYMENT_BALANCE_ROUTES_L108_1': 'Задолженность: <b>{value_0} ₽</b>',
    # bot/handlers/payment/balance_routes.py:111
    'RUNTIME_BOT_HANDLERS_PAYMENT_BALANCE_ROUTES_L111_1': '{value_0}💰 <b>Баланс</b>\n\n',
    # bot/handlers/payment/balance_routes.py:113
    'RUNTIME_BOT_HANDLERS_PAYMENT_BALANCE_ROUTES_L113_1': '\n\n<b>Последние операции</b>\n',
    # bot/handlers/payment/balance_routes.py:151
    'RUNTIME_BOT_HANDLERS_PAYMENT_BALANCE_ROUTES_L151_1': '💳 <b>Пополнение баланса</b>\n\nСумма: <b>{value_0} ₽</b>\nТекущий баланс: <b>{value_1} ₽</b>\n\nСсылка ведёт на защищённую страницу ЮKassa.',
    # bot/handlers/payment/balance_routes.py:172
    'RUNTIME_BOT_HANDLERS_PAYMENT_BALANCE_ROUTES_L172_1': '⏳ <b>Создаём ссылку на пополнение</b>\n\nСумма: <b>{value_0} ₽</b>\nТекущий баланс: <b>{value_1} ₽</b>\n\nСсылка появится здесь автоматически. Ручная проверка остаётся доступна.',
    # bot/handlers/payment/common.py:70
    'RUNTIME_BOT_HANDLERS_PAYMENT_COMMON_L70_1': 'Не удалось надёжно определить текущий тариф. Покупка временно недоступна — обратитесь в поддержку для проверки подписки.',
    # bot/handlers/payment/common.py:73
    'RUNTIME_BOT_HANDLERS_PAYMENT_COMMON_L73_1': 'Безопасная смена тарифа временно недоступна. Продление текущего тарифа продолжает работать.',
    # bot/handlers/payment/purchase_routes.py:38
    'RUNTIME_BOT_HANDLERS_PAYMENT_PURCHASE_ROUTES_L38_1': 'Котировка не найдена. Выберите тариф ещё раз.',
    # bot/handlers/payment/purchase_routes.py:39
    'RUNTIME_BOT_HANDLERS_PAYMENT_PURCHASE_ROUTES_L39_1': 'Цена устарела. Выберите тариф ещё раз.',
    # bot/handlers/payment/purchase_routes.py:40
    'RUNTIME_BOT_HANDLERS_PAYMENT_PURCHASE_ROUTES_L40_1': 'Эта покупка больше не активна.',
    # bot/handlers/payment/purchase_routes.py:41
    'RUNTIME_BOT_HANDLERS_PAYMENT_PURCHASE_ROUTES_L41_1': 'Тариф больше недоступен.',
    # bot/handlers/payment/purchase_routes.py:42
    'RUNTIME_BOT_HANDLERS_PAYMENT_PURCHASE_ROUTES_L42_1': 'Цена тарифа изменилась. Проверьте новую цену.',
    # bot/handlers/payment/purchase_routes.py:43
    'RUNTIME_BOT_HANDLERS_PAYMENT_PURCHASE_ROUTES_L43_1': 'Цена изменилась. Выберите тариф ещё раз.',
    # bot/handlers/payment/purchase_routes.py:44
    'RUNTIME_BOT_HANDLERS_PAYMENT_PURCHASE_ROUTES_L44_1': 'Состояние подписки изменилось. Начните операцию заново.',
    # bot/handlers/payment/purchase_routes.py:45
    'RUNTIME_BOT_HANDLERS_PAYMENT_PURCHASE_ROUTES_L45_1': 'На балансе недостаточно средств.',
    # bot/handlers/payment/purchase_routes.py:46
    'RUNTIME_BOT_HANDLERS_PAYMENT_PURCHASE_ROUTES_L46_1': 'Покупки временно заблокированы из-за финансового спора.',
    # bot/handlers/payment/purchase_routes.py:47
    'RUNTIME_BOT_HANDLERS_PAYMENT_PURCHASE_ROUTES_L47_1': 'Покупки недоступны до погашения задолженности.',
    # bot/handlers/payment/purchase_routes.py:48
    'RUNTIME_BOT_HANDLERS_PAYMENT_PURCHASE_ROUTES_L48_1': 'Сначала удалите лишние устройства.',
    # bot/handlers/payment/purchase_routes.py:79
    'RUNTIME_BOT_HANDLERS_PAYMENT_PURCHASE_ROUTES_L79_1': 'Не удалось открыть покупку.',
    # bot/handlers/payment/purchase_routes.py:90
    'RUNTIME_BOT_HANDLERS_PAYMENT_PURCHASE_ROUTES_L90_1': 'Продление',
    # bot/handlers/payment/purchase_routes.py:92
    'RUNTIME_BOT_HANDLERS_PAYMENT_PURCHASE_ROUTES_L92_1': '✅ <b>Подтверждение: {value_0}</b>\n\nТариф: <b>{value_1}</b>\nСрок: <b>{value_2} дней</b>\nЛимит устройств: <b>{value_3}</b>\nЦена: <b>{value_4} ₽</b>\n\nБаланс до покупки: <b>{value_5} ₽</b>\nБаланс после покупки: <b>{value_6} ₽</b>',
    # bot/handlers/payment/purchase_routes.py:106
    'RUNTIME_BOT_HANDLERS_PAYMENT_PURCHASE_ROUTES_L106_1': '\n\nНе хватает {value_0} ₽. Минимальное пополнение — {value_1} ₽; после покупки останется {value_2} ₽.',
    # bot/handlers/payment/purchase_routes.py:111
    'RUNTIME_BOT_HANDLERS_PAYMENT_PURCHASE_ROUTES_L111_1': '\n\nНе хватает: <b>{value_0} ₽</b>.',
    # bot/handlers/payment/purchase_routes.py:167
    'RUNTIME_BOT_HANDLERS_PAYMENT_PURCHASE_ROUTES_L167_1': 'Покупка не выполнена. Деньги не списаны.',
    # bot/handlers/payment/purchase_routes.py:176
    'RUNTIME_BOT_HANDLERS_PAYMENT_PURCHASE_ROUTES_L176_1': 'Продление выполнено',
    # bot/handlers/payment/purchase_routes.py:316
    'RUNTIME_BOT_HANDLERS_PAYMENT_PURCHASE_ROUTES_L316_1': 'Операция устарела. Выберите тариф заново.',
    # bot/handlers/payment/showcase_routes.py:46
    'RUNTIME_BOT_HANDLERS_PAYMENT_SHOWCASE_ROUTES_L46_1': '{value_0} дн.',
    # bot/handlers/payment/showcase_routes.py:197
    'RUNTIME_BOT_HANDLERS_PAYMENT_SHOWCASE_ROUTES_L197_1': 'У вас уже подключён этот тариф. Для добавления дней используйте продление.',
    # bot/handlers/payment/showcase_routes.py:201
    'RUNTIME_BOT_HANDLERS_PAYMENT_SHOWCASE_ROUTES_L201_1': 'Смена тарифа заблокирована из-за финансового спора.',
    # bot/handlers/payment/showcase_routes.py:204
    'RUNTIME_BOT_HANDLERS_PAYMENT_SHOWCASE_ROUTES_L204_1': 'Смена тарифа недоступна до погашения задолженности.',
    # bot/handlers/payment/showcase_routes.py:207
    'RUNTIME_BOT_HANDLERS_PAYMENT_SHOWCASE_ROUTES_L207_1': 'Не удалось надёжно рассчитать остаток подписки. Обратитесь в поддержку.',
    # bot/handlers/payment/showcase_routes.py:216
    'RUNTIME_BOT_HANDLERS_PAYMENT_SHOWCASE_ROUTES_L216_1': 'Не удалось подготовить смену тарифа. Попробуйте ещё раз.',
    # bot/handlers/payment/showcase_routes.py:233
    'RUNTIME_BOT_HANDLERS_PAYMENT_SHOWCASE_ROUTES_L233_1': '\n⚠️ Не хватает: <b>{value_0} ₽</b>',
    # bot/handlers/payment/showcase_routes.py:274
    'RUNTIME_BOT_HANDLERS_PAYMENT_SHOWCASE_ROUTES_L274_1': 'Покупки временно заблокированы из-за открытого финансового спора.',
    # bot/handlers/payment/showcase_routes.py:275
    'RUNTIME_BOT_HANDLERS_PAYMENT_SHOWCASE_ROUTES_L275_1': 'Покупки недоступны до погашения задолженности.',
    # bot/handlers/payment/showcase_routes.py:276
    'RUNTIME_BOT_HANDLERS_PAYMENT_SHOWCASE_ROUTES_L276_1': 'Для этого варианта используйте раздел «Сменить тариф».',
    # bot/handlers/payment/showcase_routes.py:277
    'RUNTIME_BOT_HANDLERS_PAYMENT_SHOWCASE_ROUTES_L277_1': 'Сначала завершите или отмените смену тарифа.',
    # bot/handlers/payment/showcase_routes.py:282
    'RUNTIME_BOT_HANDLERS_PAYMENT_SHOWCASE_ROUTES_L282_1': 'Не удалось подготовить покупку. Попробуйте ещё раз.',
    # bot/handlers/payment/showcase_routes.py:292
    'RUNTIME_BOT_HANDLERS_PAYMENT_SHOWCASE_ROUTES_L292_1': '\n⚠️ Не хватает: <b>{value_0} ₽</b>',
    # bot/handlers/payment/showcase_routes.py:297
    'RUNTIME_BOT_HANDLERS_PAYMENT_SHOWCASE_ROUTES_L297_1': '💳 <b>Оформление заказа</b>\n\n📦 Тариф: <b>{value_0}</b>\n⏱ Срок: {value_1} дней\n🔌 Устройства: до {value_2}\n💰 Цена: <b>{value_3} ₽</b>\n\nБаланс: <b>{value_4} ₽</b>\nПосле покупки: <b>{value_5} ₽</b>{value_6}\n\nПокупка выполняется только после отдельного подтверждения.',
    # bot/handlers/payment/tariff_change_routes.py:36
    'RUNTIME_BOT_HANDLERS_PAYMENT_TARIFF_CHANGE_ROUTES_L36_1': 'Котировка не найдена. Выберите тариф ещё раз.',
    # bot/handlers/payment/tariff_change_routes.py:37
    'RUNTIME_BOT_HANDLERS_PAYMENT_TARIFF_CHANGE_ROUTES_L37_1': 'Расчёт устарел. Выберите тариф ещё раз.',
    # bot/handlers/payment/tariff_change_routes.py:38
    'RUNTIME_BOT_HANDLERS_PAYMENT_TARIFF_CHANGE_ROUTES_L38_1': 'Эта смена тарифа больше не активна.',
    # bot/handlers/payment/tariff_change_routes.py:39
    'RUNTIME_BOT_HANDLERS_PAYMENT_TARIFF_CHANGE_ROUTES_L39_1': 'Выбранный тариф больше недоступен.',
    # bot/handlers/payment/tariff_change_routes.py:40
    'RUNTIME_BOT_HANDLERS_PAYMENT_TARIFF_CHANGE_ROUTES_L40_1': 'Цена тарифа изменилась. Проверьте новый расчёт.',
    # bot/handlers/payment/tariff_change_routes.py:41
    'RUNTIME_BOT_HANDLERS_PAYMENT_TARIFF_CHANGE_ROUTES_L41_1': 'Подписка изменилась. Создайте новый расчёт.',
    # bot/handlers/payment/tariff_change_routes.py:42
    'RUNTIME_BOT_HANDLERS_PAYMENT_TARIFF_CHANGE_ROUTES_L42_1': 'Экономика подписки изменилась. Создайте новый расчёт.',
    # bot/handlers/payment/tariff_change_routes.py:43
    'RUNTIME_BOT_HANDLERS_PAYMENT_TARIFF_CHANGE_ROUTES_L43_1': 'Состояние подписки изменилось. Начните заново.',
    # bot/handlers/payment/tariff_change_routes.py:44
    'RUNTIME_BOT_HANDLERS_PAYMENT_TARIFF_CHANGE_ROUTES_L44_1': 'Не удалось надёжно рассчитать остаток подписки.',
    # bot/handlers/payment/tariff_change_routes.py:45
    'RUNTIME_BOT_HANDLERS_PAYMENT_TARIFF_CHANGE_ROUTES_L45_1': 'На балансе недостаточно средств.',
    # bot/handlers/payment/tariff_change_routes.py:46
    'RUNTIME_BOT_HANDLERS_PAYMENT_TARIFF_CHANGE_ROUTES_L46_1': 'Смена тарифа заблокирована из-за финансового спора.',
    # bot/handlers/payment/tariff_change_routes.py:47
    'RUNTIME_BOT_HANDLERS_PAYMENT_TARIFF_CHANGE_ROUTES_L47_1': 'Смена тарифа недоступна до погашения задолженности.',
    # bot/handlers/payment/tariff_change_routes.py:48
    'RUNTIME_BOT_HANDLERS_PAYMENT_TARIFF_CHANGE_ROUTES_L48_1': 'Сначала удалите лишние устройства.',
    # bot/handlers/payment/tariff_change_routes.py:61
    'RUNTIME_BOT_HANDLERS_PAYMENT_TARIFF_CHANGE_ROUTES_L61_1': '{value_0} дн.',
    # bot/handlers/payment/tariff_change_routes.py:80
    'RUNTIME_BOT_HANDLERS_PAYMENT_TARIFF_CHANGE_ROUTES_L80_1': 'Не удалось открыть смену тарифа.',
    # bot/handlers/payment/tariff_change_routes.py:90
    'RUNTIME_BOT_HANDLERS_PAYMENT_TARIFF_CHANGE_ROUTES_L90_1': '✅ <b>Подтверждение смены тарифа</b>\n\nНовый тариф: <b>{value_0}</b>\nЛимит устройств: <b>{value_1}</b>\nСрок после конвертации: <b>{value_2}</b>\nДоплата: <b>{value_3} ₽</b>\n\nБаланс до операции: <b>{value_4} ₽</b>\nБаланс после операции: <b>{value_5} ₽</b>',
    # bot/handlers/payment/tariff_change_routes.py:104
    'RUNTIME_BOT_HANDLERS_PAYMENT_TARIFF_CHANGE_ROUTES_L104_1': '\n\nНе хватает {value_0} ₽. Минимальное пополнение — {value_1} ₽; после смены останется {value_2} ₽.',
    # bot/handlers/payment/tariff_change_routes.py:109
    'RUNTIME_BOT_HANDLERS_PAYMENT_TARIFF_CHANGE_ROUTES_L109_1': '\n\nНе хватает: <b>{value_0} ₽</b>.',
    # bot/handlers/payment/tariff_change_routes.py:167
    'RUNTIME_BOT_HANDLERS_PAYMENT_TARIFF_CHANGE_ROUTES_L167_1': 'Смена тарифа не выполнена. Деньги не списаны.',
    # bot/handlers/profile.py:63
    'RUNTIME_BOT_HANDLERS_PROFILE_L63_1': '{value_0} ({value_1} устр.)',
    # bot/handlers/profile.py:65
    'RUNTIME_BOT_HANDLERS_PROFILE_L65_1': '—',
    # bot/handlers/profile.py:75
    'RUNTIME_BOT_HANDLERS_PROFILE_L75_1': '{value_0} ({value_1} устр.)',
    # bot/handlers/profile.py:80
    'RUNTIME_BOT_HANDLERS_PROFILE_L80_1': 'Пользователь',
    # bot/handlers/profile.py:93
    'RUNTIME_BOT_HANDLERS_PROFILE_L93_1': 'Пользователь',
    # bot/handlers/profile.py:208
    'RUNTIME_BOT_HANDLERS_PROFILE_L208_1': '⏳',
    # bot/handlers/profile.py:213
    'RUNTIME_BOT_HANDLERS_PROFILE_L213_1': '₽',
    # bot/handlers/profile.py:306
    'RUNTIME_BOT_HANDLERS_PROFILE_L306_1': '• {value_0}\n',
    # bot/handlers/profile.py:310
    'RUNTIME_BOT_HANDLERS_PROFILE_L310_1': '\n<i>... и ещё {value_0} рефералов</i>',
    # bot/handlers/start.py:140
    'RUNTIME_BOT_HANDLERS_START_L140_1': 'Пользователь',
    # bot/handlers/start.py:206
    'RUNTIME_BOT_HANDLERS_START_L206_1': 'Пользователь',
    # bot/keyboards/admin/servers.py:34
    'RUNTIME_BOT_KEYBOARDS_ADMIN_SERVERS_L34_1': '🔴 Выключить',
    # bot/keyboards/admin/servers.py:36
    'RUNTIME_BOT_KEYBOARDS_ADMIN_SERVERS_L36_1': '🟢 Включить',
    # bot/keyboards/admin/tariffs.py:26
    'RUNTIME_BOT_KEYBOARDS_ADMIN_TARIFFS_L26_1': '🔴 Выключить',
    # bot/keyboards/admin/tariffs.py:28
    'RUNTIME_BOT_KEYBOARDS_ADMIN_TARIFFS_L28_1': '🟢 Включить',
    # bot/keyboards/admin/users.py:103
    'RUNTIME_BOT_KEYBOARDS_ADMIN_USERS_L103_1': ' ✅',
    # bot/keyboards/admin/users.py:253
    'RUNTIME_BOT_KEYBOARDS_ADMIN_USERS_L253_1': 'Устройство #{value_0}',
    # bot/keyboards/admin/users.py:257
    'RUNTIME_BOT_KEYBOARDS_ADMIN_USERS_L257_1': '🗑 {value_0}',
    # bot/keyboards/common.py:63
    'RUNTIME_BOT_KEYBOARDS_COMMON_L63_1': '← В главное меню',
    # bot/keyboards/common.py:65
    'RUNTIME_BOT_KEYBOARDS_COMMON_L65_1': '← Назад',
    # bot/keyboards/payment.py:34
    'RUNTIME_BOT_KEYBOARDS_PAYMENT_L34_1': '⏱ {value_0} дн. — {value_1}₽',
    # bot/keyboards/payment.py:36
    'RUNTIME_BOT_KEYBOARDS_PAYMENT_L36_1': ' 🔥',
    # bot/keyboards/payment.py:38
    'RUNTIME_BOT_KEYBOARDS_PAYMENT_L38_1': ' 🌟',
    # bot/keyboards/payment.py:62
    'RUNTIME_BOT_KEYBOARDS_PAYMENT_L62_1': '⏱ {value_0} дн. — {value_1}₽',
    # bot/keyboards/payment.py:64
    'RUNTIME_BOT_KEYBOARDS_PAYMENT_L64_1': ' 🔥',
    # bot/keyboards/payment.py:66
    'RUNTIME_BOT_KEYBOARDS_PAYMENT_L66_1': ' 🌟',
    # bot/keyboards/payment.py:94
    'RUNTIME_BOT_KEYBOARDS_PAYMENT_L94_1': ' 🔽',
    # bot/keyboards/payment.py:96
    'RUNTIME_BOT_KEYBOARDS_PAYMENT_L96_1': ' ✅',
    # bot/keyboards/payment.py:98
    'RUNTIME_BOT_KEYBOARDS_PAYMENT_L98_1': ' 🔼',
    # services/workers/__init__.py:119
    'RUNTIME_SERVICES_WORKERS_INIT_L119_1': '🚨 <b>{value_0}</b>\n🧩 <b>Воркер:</b> <code>{value_1}</code>\n🔁 <b>Падений:</b> {value_2}\n⚠️ <b>Тип ошибки:</b> <code>{value_3}</code>',
    # services/workers/__init__.py:174
    'RUNTIME_SERVICES_WORKERS_INIT_L174_1': 'Критическая остановка фоновых задач',
    # services/workers/__init__.py:233
    'RUNTIME_SERVICES_WORKERS_INIT_L233_1': 'Фоновый воркер упал',
    # services/workers/account_balance.py:223
    'RUNTIME_SERVICES_WORKERS_ACCOUNT_BALANCE_L223_1': '{value_0} дней',
    # services/workers/account_balance.py:224
    'RUNTIME_SERVICES_WORKERS_ACCOUNT_BALANCE_L224_1': ' {value_0} ч.',
    # services/workers/account_balance.py:227
    'RUNTIME_SERVICES_WORKERS_ACCOUNT_BALANCE_L227_1': 'Смена тарифа с баланса выполнена',
    # services/workers/account_balance.py:229
    'RUNTIME_SERVICES_WORKERS_ACCOUNT_BALANCE_L229_1': 'Покупка с баланса выполнена',
    # services/workers/account_balance.py:340
    'RUNTIME_SERVICES_WORKERS_ACCOUNT_BALANCE_L340_1': '\n\nТариф готов к покупке. Подтвердите покупку с баланса.',
    # services/workers/account_balance.py:345
    'RUNTIME_SERVICES_WORKERS_ACCOUNT_BALANCE_L345_1': '✅ <b>Баланс пополнен на +{value_0} ₽!</b>\n\n💰 Баланс: <b>{value_1} ₽</b>\n🎁 Бонусный баланс: <b>{value_2} ₽</b>{value_3}',

    # services/workers/account_balance.py:361
    'RUNTIME_SERVICES_WORKERS_ACCOUNT_BALANCE_L361_1': '⚠️ Поздняя оплата превысила лимит баланса\nPayment: <code>{value_0}</code>\nUser: <code>{value_1}</code>\nБаланс: <b>{value_2} ₽</b>',
    # services/workers/cleanup.py:90
    'RUNTIME_SERVICES_WORKERS_CLEANUP_L90_1': 'Критическая ошибка в цикле очистки: %s',
    # services/workers/cleanup.py:255
    'RUNTIME_SERVICES_WORKERS_CLEANUP_L255_1': 'Ошибка получения списка пиров на %s: %s',
    # services/workers/heartbeat.py:99
    'RUNTIME_SERVICES_WORKERS_HEARTBEAT_L99_1': '⚠️ <b>Сервер Amnezia недоступен!</b>\n🌍 <b>{value_0}</b>\n🔗 <code>{value_1}</code>\n❌ CircuitBreaker перешёл в OPEN\n🔄 Попытки восстановления каждые {value_2:.0f}с\n💡 Проверьте сервер вручную',
    # services/workers/notifications.py:43
    'RUNTIME_SERVICES_WORKERS_NOTIFICATIONS_L43_1': 'в ближайшее время',
    # services/workers/notifications.py:49
    'RUNTIME_SERVICES_WORKERS_NOTIFICATIONS_L49_1': '{value_0} дн. {value_1} ч.',
    # services/workers/notifications.py:53
    'RUNTIME_SERVICES_WORKERS_NOTIFICATIONS_L53_1': '{value_0} ч. {value_1} мин.',
    # services/workers/notifications.py:122
    'RUNTIME_SERVICES_WORKERS_NOTIFICATIONS_L122_1': 'Критическая ошибка в цикле уведомлений: %s',
    # services/workers/payments.py:139
    'RUNTIME_SERVICES_WORKERS_PAYMENTS_L139_1': '🧪',
    # services/workers/payments.py:141
    'RUNTIME_SERVICES_WORKERS_PAYMENTS_L141_1': '⏳',
    # services/workers/payments.py:147
    'RUNTIME_SERVICES_WORKERS_PAYMENTS_L147_1': '—',
    # services/workers/queue_health.py:230
    'RUNTIME_SERVICES_WORKERS_QUEUE_HEALTH_L230_1': '🚨 <b>Durable queue unhealthy</b>\nQueue: <code>{value_0}</code>\nProblems: {value_1}{value_2}',
    # services/workers/queue_health.py:237
    'RUNTIME_SERVICES_WORKERS_QUEUE_HEALTH_L237_1': '✅ <b>Durable queue recovered</b>\nQueue: <code>{value_0}</code>',
    # services/workers/traffic.py:67
    'RUNTIME_SERVICES_WORKERS_TRAFFIC_L67_1': 'Критическая ошибка в цикле трафика (crash #%s, next retry in %ss): %s',
    # services/workers/traffic.py:128
    'RUNTIME_SERVICES_WORKERS_TRAFFIC_L128_1': 'Ошибка трафика с %s: %s',
    'RUNTIME_SERVICES_WORKERS_TRAFFIC_L304_1': '⚠️ <b>Fair Usage Policy: Превышение квоты трафика!</b>\n{value_0}\n👤 <b>Пользователь:</b> <code>{value_1}</code>\n🌍 <b>Сервер:</b> {value_2}\n📊 <b>Использовано:</b> <b>{value_3:.2f} TiB</b>\n🆔 <b>Profile ID:</b> <code>{value_4}</code>\n{value_5}\n<i>Пользователь скачал более 1 TiB трафика.\nРекомендуется связаться с ним или принять меры.\nДоступ НЕ отключён автоматически (Fair Usage Policy).</i>',
    'STALE_TOPUP_ALERT_ROW': '{icon} <b>Пополнение #{payment_id}</b> от <code>{telegram_id}</code>: <b>{amount} {currency}</b> ({method})',
    'STALE_TOPUP_ALERT_MORE': '\n<i>... и ещё {count} платежей</i>',
    'STALE_TOPUP_ALERT': '🚨 <b>Обнаружено зависших пополнений: {count}</b>\n\n{details}\n\nРекомендуется проверить их в админке.',
}


TEXTS.update({
    "WORD_PURCHASE": "Покупка",
    "PURCHASE_COMPLETED": "Тариф куплен",
    "DURATION_HOURS_SUFFIX": " {hours} ч.",
    "PROVISIONING_UPDATING": "обновляется",
    "PROVISIONING_CREATE_FAILED": "ошибка создания",
    "PROVISIONING_DELETE_FAILED": "ошибка удаления",
    "BROADCAST_ACTIVE_LABEL": "Активных",
    "STATUS_INACTIVE_ICON": "🔴",
    "PLACEHOLDER_DASH": "—",
    "QUEUE_OPERATION_NOT_FOUND": "Операция не найдена",
    "QUEUE_RETRY_AVAILABLE": "доступен",
    "QUEUE_RETRY_UNAVAILABLE": "недоступен",
    "DISPUTE_RESERVATION_MISSING": "нет",
    "SERVER_DISABLED_LABEL": "🔴 Отключен",
    "SEPARATOR_LINE": "─",
})


TEXTS.update({
    "USER_ID_LABEL": "ID: {user_id}",
    "ADMIN_QUEUE_PROVIDER_LABEL": "Provider operations",
    "ADMIN_QUEUE_WEBHOOK_LABEL": "Webhook inbox",
    "ADMIN_QUEUE_PROVIDER_SHORT": "Provider",
    "ADMIN_QUEUE_REFUNDS_SHORT": "Refunds",
    "ADMIN_QUEUE_WEBHOOK_SHORT": "Webhook",
    "ADMIN_QUEUE_NAME": "<b>{name}</b>",
    "ADMIN_QUEUE_CARD_ID": "ID: <code>{operation_id}</code>",
    "ADMIN_QUEUE_CARD_PAYMENT": "Payment ID: {payment_id}",
    "ADMIN_QUEUE_CARD_ERROR": "Error code: {error_code}",
    "ADMIN_QUEUE_CARD_LOCK": "Lock timestamp: {locked_at}",
    "ADMIN_QUEUE_CARD_LEASE": "Lease: {lease}",
    "ADMIN_PAYMENT_USER_ID_COMPACT": "ID:{user_id}",
    "ADMIN_PAYMENT_USER_ID": "ID: <code>{user_id}</code>",
    "ADMIN_PAYMENT_USER_WITH_ID": "@{username} (ID: <code>{user_id}</code>)",
    "QUEUE_HEALTH_PROBLEM_DEAD": "dead={count} oldest={age}s",
    "QUEUE_HEALTH_PROBLEM_OVERDUE": "overdue={count} oldest={age}s",
    "QUEUE_HEALTH_PROBLEM_STALE": "stale_processing={count} oldest={age}s",
    "QUEUE_HEALTH_PAYMENT_FRAGMENT": " payment={payment_id}",
    "QUEUE_HEALTH_CODE_FRAGMENT": " code={code}",
    "QUEUE_HEALTH_EXAMPLE": "<code>id={operation_id}{payment} type={operation_type} status={status} attempts={attempts}/{max_attempts} age={age}s{code}</code>",
})
