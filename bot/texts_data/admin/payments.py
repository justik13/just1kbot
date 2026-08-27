"""Domain copy catalogue for: admin/payments.py"""

TEXTS = {
    'ADMIN_PAYMENT_USER_ID': 'ID: <code>{user_id}</code>',
    'ADMIN_PAYMENT_USER_ID_COMPACT': 'ID:{user_id}',
    'ADMIN_PAYMENT_USER_WITH_ID': 'ID: <code>{user_id}</code> (@{username})',
    'RUNTIME_BOT_HANDLERS_ADMIN_PAYMENTS_L239_1': '—',
    'RUNTIME_BOT_HANDLERS_ADMIN_PAYMENTS_L249_1': '❓',
    'RUNTIME_BOT_HANDLERS_ADMIN_PAYMENTS_L258_1': """
<b>Причина:</b> {value_0}""",
    'RUNTIME_BOT_HANDLERS_ADMIN_PAYMENTS_L268_1': """
<b>Можно вернуть:</b> {value_0} RUB""",
    'RUNTIME_BOT_HANDLERS_ADMIN_PAYMENTS_L271_1': """🛠 Админка › 💳 Платежи › <b>Платёж #{value_0}</b>
<b>ID:</b> {value_1}
<b>Пользователь:</b> {value_2}
<b>Сумма:</b> {value_3} {value_4}
<b>Статус:</b> {value_5} {value_6}
<b>Provider:</b> {value_7}
<b>Исполнение:</b> {value_8}
<b>Создан:</b> {value_9}
<b>Оплачен:</b> {value_10}
<b>External ID:</b> <code>{value_11}</code>{value_12}{value_13}""",
    'RUNTIME_BOT_HANDLERS_ADMIN_PAYMENTS_L368_1': 'Платёж не найден',
    'RUNTIME_BOT_HANDLERS_ADMIN_PAYMENTS_L369_1': 'Можно вернуть только пополнение баланса',
    'RUNTIME_BOT_HANDLERS_ADMIN_PAYMENTS_L370_1': 'Платёж ещё не подтверждён или уже возвращён',
    'RUNTIME_BOT_HANDLERS_ADMIN_PAYMENTS_L371_1': 'У платежа нет YooKassa ID',
    'RUNTIME_BOT_HANDLERS_ADMIN_PAYMENTS_L372_1': 'Возвращаемого остатка уже нет',
    'RUNTIME_BOT_HANDLERS_ADMIN_PAYMENTS_L373_1': 'Возврат требует ручной проверки',
    'RUNTIME_BOT_HANDLERS_ADMIN_PAYMENTS_L376_1': 'Не удалось поставить возврат в очередь',
    'RUNTIME_BOT_HANDLERS_ADMIN_PAYMENTS_L390_1': 'Возврат поставлен в durable-очередь.',
    'RUNTIME_BOT_HANDLERS_ADMIN_PAYMENTS_L392_1': 'Этот возврат уже находится в durable-очереди.',
    'RUNTIME_BOT_HANDLERS_ADMIN_PAYMENTS_L75_1': """🛠 Админка › 💳 <b>Платежи</b>
(стр. {value_0}/{value_1}) · Всего: {value_2}
""",
    'RUNTIME_BOT_HANDLERS_ADMIN_PAYMENTS_L80_1': """<i>Платежей пока нет</i>
""",
    'RUNTIME_BOT_HANDLERS_ADMIN_PAYMENTS_L86_1': '❓',
    'RUNTIME_BOT_HANDLERS_ADMIN_PAYMENTS_L93_1': '—',
    'RUNTIME_BOT_HANDLERS_ADMIN_PAYMENTS_L95_1': '{value_0} #{value_1} · {value_2} · {value_3}₽',
    'UI_BOT_HANDLERS_ADMIN_PAYMENTS_L105_1': '⬅️',
    'UI_BOT_HANDLERS_ADMIN_PAYMENTS_L110_1': '➡️',
    'UI_BOT_HANDLERS_ADMIN_PAYMENTS_L113_1': '← В админку',
    'UI_BOT_HANDLERS_ADMIN_PAYMENTS_L192_1': 'Некорректный запрос',
    'UI_BOT_HANDLERS_ADMIN_PAYMENTS_L216_1': 'Некорректный запрос',
    'UI_BOT_HANDLERS_ADMIN_PAYMENTS_L224_1': 'Платёж не найден',
    'UI_BOT_HANDLERS_ADMIN_PAYMENTS_L319_1': 'Платёж недоступен для возврата',
    'UI_BOT_HANDLERS_ADMIN_PAYMENTS_L326_1': 'Возвращаемого остатка уже нет',
    'UI_BOT_HANDLERS_ADMIN_PAYMENTS_L331_1': '✅ Подтвердить возврат {value_0} ₽',
    'UI_BOT_HANDLERS_ADMIN_PAYMENTS_L335_1': '← Назад к платежу',
    'UI_BOT_HANDLERS_ADMIN_PAYMENTS_L340_1': """⚠️ <b>Подтверждение возврата</b>

Платёж: <code>#{value_0}</code>
YooKassa ID: <code>{value_1}</code>
Будет возвращено: <b>{value_2} RUB</b>

Сумма сначала будет заморожена на внутреннем балансе, затем durable worker отправит идемпотентный запрос в YooKassa.""",
    'UI_BOT_HANDLERS_ADMIN_PAYMENTS_L363_1': 'Некорректный запрос',
    'UI_BOT_HANDLERS_ADMIN_PAYMENTS_L389_1': '← Назад к платежу',
    'UI_BOT_HANDLERS_ADMIN_PAYMENTS_L392_1': '💳 К списку платежей',
    'UI_BOT_HANDLERS_ADMIN_PAYMENTS_L400_1': """✅ <b>Возврат принят</b>

{value_0}
Сумма: <b>{value_1} RUB</b>
Operation: <code>{value_2}</code>
Статус: <code>{value_3}</code>

Зарезервированная сумма недоступна для новых покупок до подтверждения или безопасного завершения операции.""",
    'UI_BOT_HANDLERS_ADMIN_PAYMENTS_L50_1': '↩️ Вернуть доступный остаток',
    'UI_BOT_HANDLERS_ADMIN_PAYMENTS_L56_1': '👤 Карточка клиента',
    'UI_BOT_HANDLERS_ADMIN_PAYMENTS_L61_1': '← К списку платежей',

    'UI_PAYMENTS_K_LOGAM_POKUPOK_110': '🛒 К логам покупок',
}
