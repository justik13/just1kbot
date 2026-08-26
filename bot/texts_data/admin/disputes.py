"""Domain copy catalogue for: admin/disputes.py"""

TEXTS = {
    'ADMIN_DISPUTES_HEADER': """⚖️ <b>Управление платежными спорами (Disputes)</b>

ℹ️ <i>Диспуты возникают при обращении клиентов в банк или платёжный провайдер (чарджбэк). Здесь вы можете просмотреть детали спора и урегулировать вопрос.</i>""",
    'ADMIN_DISPUTES_INPUT_CANCELLED': '❌ Ввод спора отменён.',
    'ADMIN_DISPUTE_CREATED_PREFIX': """Создан новый спор.

""",
    'ADMIN_DISPUTE_EXISTING_PREFIX': """Спор уже существовал.

""",
    'DISPUTE_RESERVATION_MISSING': 'нет',
    'RUNTIME_BOT_HANDLERS_ADMIN_DISPUTES_L109_1': """⚠️ <b>Спор #{value_0}</b>
Статус: <b>{value_1}</b>
Case ID: <code>{value_2}</code>
YooKassa payment: <code>{value_3}</code>
Сумма: <b>{value_4} RUB</b>
Дата спора: <code>{value_5}</code>
Reservation: <code>{value_6}</code> ({value_7})
Chargeback entry: <code>{value_8}</code>
Заметка: {value_9}""",
    'RUNTIME_BOT_HANDLERS_ADMIN_DISPUTES_L147_1': '#{value_0} · {value_1} · {value_2} ₽ · case={value_3}',
    'RUNTIME_BOT_HANDLERS_ADMIN_DISPUTES_L157_1': 'Споров пока нет.',
    'RUNTIME_BOT_HANDLERS_ADMIN_DISPUTES_L333_1': 'reservation будет освобождена',
    'RUNTIME_BOT_HANDLERS_ADMIN_DISPUTES_L335_1': 'будет создан exactly-once chargeback debit; возможен долг',
    'RUNTIME_BOT_HANDLERS_ADMIN_DISPUTES_L35_1': 'открыт',
    'RUNTIME_BOT_HANDLERS_ADMIN_DISPUTES_L36_1': 'ручная проверка',
    'RUNTIME_BOT_HANDLERS_ADMIN_DISPUTES_L37_1': 'выигран продавцом',
    'RUNTIME_BOT_HANDLERS_ADMIN_DISPUTES_L38_1': 'проигран продавцом',
    'RUNTIME_BOT_HANDLERS_ADMIN_DISPUTES_L44_1': 'Нужен YooKassa payment ID',
    'RUNTIME_BOT_HANDLERS_ADMIN_DISPUTES_L45_1': 'Нужен ID спора банка/провайдера',
    'RUNTIME_BOT_HANDLERS_ADMIN_DISPUTES_L46_1': 'Сумма должна быть целым числом рублей',
    'RUNTIME_BOT_HANDLERS_ADMIN_DISPUTES_L47_1': 'Некорректная дата спора',
    'RUNTIME_BOT_HANDLERS_ADMIN_DISPUTES_L48_1': 'Платёж с таким YooKassa ID не найден',
    'RUNTIME_BOT_HANDLERS_ADMIN_DISPUTES_L50_1': 'Спор поддерживается только для пополнения баланса',
    'RUNTIME_BOT_HANDLERS_ADMIN_DISPUTES_L52_1': 'Платёж ещё не подтверждён',
    'RUNTIME_BOT_HANDLERS_ADMIN_DISPUTES_L53_1': 'Пополнение ещё не зачислено в ledger',
    'RUNTIME_BOT_HANDLERS_ADMIN_DISPUTES_L54_1': 'Этот case ID уже связан с другими данными',
    'RUNTIME_BOT_HANDLERS_ADMIN_DISPUTES_L55_1': 'По платежу уже открыт спор',
    'RUNTIME_BOT_HANDLERS_ADMIN_DISPUTES_L56_1': 'Сначала завершите активный refund',
    'RUNTIME_BOT_HANDLERS_ADMIN_DISPUTES_L58_1': 'Сумма превышает остаток платёжного риска',
    'RUNTIME_BOT_HANDLERS_ADMIN_DISPUTES_L60_1': 'Спор не найден',
    'RUNTIME_BOT_HANDLERS_ADMIN_DISPUTES_L61_1': 'Спор уже завершён другим исходом',
    'RUNTIME_BOT_HANDLERS_ADMIN_DISPUTES_L62_1': 'Операция со спором отклонена финансовыми инвариантами',
    'UI_BOT_HANDLERS_ADMIN_DISPUTES_L152_1': '#{value_0} · {value_1}',
    'UI_BOT_HANDLERS_ADMIN_DISPUTES_L175_1': """Отправьте одной строкой:
<code>YooKassa_payment_ID | case_ID | сумма | YYYY-MM-DD | open/manual_review/won_by_merchant/lost_by_merchant | заметка</code>

Пример:
<code>2f... | bank-case-17 | 499 | 2026-08-02 | open | ожидаем документы</code>""",
    'UI_BOT_HANDLERS_ADMIN_DISPUTES_L198_1': 'Нужно ровно 6 полей, разделённых символом |',
    'UI_BOT_HANDLERS_ADMIN_DISPUTES_L202_1': 'Некорректный статус спора',
    'UI_BOT_HANDLERS_ADMIN_DISPUTES_L209_1': 'Дата должна быть в формате YYYY-MM-DD',
    'UI_BOT_HANDLERS_ADMIN_DISPUTES_L258_1': 'Некорректный ID',
    'UI_BOT_HANDLERS_ADMIN_DISPUTES_L262_1': 'Спор не найден',
    'UI_BOT_HANDLERS_ADMIN_DISPUTES_L286_1': 'Некорректный ID',
    'UI_BOT_HANDLERS_ADMIN_DISPUTES_L297_1': 'Переведено на ручную проверку',
    'UI_BOT_HANDLERS_ADMIN_DISPUTES_L313_1': 'Некорректный запрос',
    'UI_BOT_HANDLERS_ADMIN_DISPUTES_L318_1': 'Некорректный ID',
    'UI_BOT_HANDLERS_ADMIN_DISPUTES_L322_1': 'Состояние спора уже изменилось',
    'UI_BOT_HANDLERS_ADMIN_DISPUTES_L326_1': '✅ Подтвердить',
    'UI_BOT_HANDLERS_ADMIN_DISPUTES_L330_1': 'Отмена',
    'UI_BOT_HANDLERS_ADMIN_DISPUTES_L340_1': """⚠️ <b>Подтвердите исход спора</b>

Спор: <code>#{value_0}</code>
Исход: <b>{value_1}</b>
Эффект: {value_2}.""",
    'UI_BOT_HANDLERS_ADMIN_DISPUTES_L360_1': 'Некорректный запрос',
    'UI_BOT_HANDLERS_ADMIN_DISPUTES_L370_1': 'Некорректный ID',
    'UI_BOT_HANDLERS_ADMIN_DISPUTES_L381_1': 'Исход спора зафиксирован',
    'UI_BOT_HANDLERS_ADMIN_DISPUTES_L67_1': '➕ Зарегистрировать спор',
    'UI_BOT_HANDLERS_ADMIN_DISPUTES_L68_1': '🔄 Обновить',
    'UI_BOT_HANDLERS_ADMIN_DISPUTES_L69_1': '← В админку',
    'UI_BOT_HANDLERS_ADMIN_DISPUTES_L78_1': '✅ Продавец выиграл',
    'UI_BOT_HANDLERS_ADMIN_DISPUTES_L82_1': '❌ Продавец проиграл',
    'UI_BOT_HANDLERS_ADMIN_DISPUTES_L87_1': '🛑 Ручная проверка',
    'UI_BOT_HANDLERS_ADMIN_DISPUTES_L90_1': '← К спорам',
}
