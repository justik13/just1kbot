"""Domain texts for admin/disputes.py."""
from __future__ import annotations

ADMIN_BTN_BACK_TO_DISPUTES = "← К спорам"

ADMIN_DISPUTES = """⚠️ <b>Спор #{value_0}</b>
Статус: <b>{value_1}</b>
Case ID: <code>{value_2}</code>
YooKassa payment: <code>{value_3}</code>
Сумма: <b>{value_4} RUB</b>
Дата спора: <code>{value_5}</code>
Reservation: <code>{value_6}</code> ({value_7})
Chargeback entry: <code>{value_8}</code>
Заметка: {value_9}"""

ADMIN_DISPUTES_HEADER = """⚖️ <b>Управление платежными спорами (Disputes)</b>

ℹ️ <i>Диспуты возникают при обращении клиентов в банк или платёжный провайдер (чарджбэк). Здесь вы можете просмотреть детали спора и урегулировать вопрос.</i>"""

ADMIN_DISPUTES_INPUT_CANCELLED = "❌ Ввод спора отменён."

ADMIN_DISPUTE_ACCEPT_SUCCESS = "Нужен YooKassa payment ID"

ADMIN_DISPUTE_ACTION_FAILED = "Некорректная дата спора"

ADMIN_DISPUTE_ACTION_SUCCESS = "🛑 Ручная проверка"

ADMIN_DISPUTE_BTN_ACCEPT = """Отправьте одной строкой:
<code>YooKassa_payment_ID | case_ID | сумма | YYYY-MM-DD | open/manual_review/won_by_merchant/lost_by_merchant | заметка</code>

Пример:
<code>2f... | bank-case-17 | 499 | 2026-08-02 | open | ожидаем документы</code>"""

ADMIN_DISPUTE_BTN_BACK_TO_LIST = "Дата должна быть в формате YYYY-MM-DD"

ADMIN_DISPUTE_BTN_LIST = "#{value_0} · {value_1}"

ADMIN_DISPUTE_BTN_REGISTER = "Операция со спором отклонена финансовыми инвариантами"

ADMIN_DISPUTE_BTN_REJECT = "Нужно ровно 6 полей, разделённых символом |"

ADMIN_DISPUTE_BTN_REVIEW = "Некорректный статус спора"

ADMIN_DISPUTE_CARD = "открыт"

ADMIN_DISPUTE_CONFIRM_ACTION = "← В админку"

ADMIN_DISPUTE_CONFIRM_AMOUNT = "Некорректный ID"

ADMIN_DISPUTE_CONFIRM_CASE = "➕ Зарегистрировать спор"

ADMIN_DISPUTE_CONFIRM_NOTE = """⚠️ <b>Подтвердите исход спора</b>

Спор: <code>#{value_0}</code>
Исход: <b>{value_1}</b>
Эффект: {value_2}."""

ADMIN_DISPUTE_CONFIRM_PAYMENT = "Исход спора зафиксирован"

ADMIN_DISPUTE_CONFIRM_PROMPT = "✅ Продавец выиграл"

ADMIN_DISPUTE_CONFIRM_STATUS = "🔄 Обновить"

ADMIN_DISPUTE_CONFIRM_TITLE = "Состояние спора уже изменилось"

ADMIN_DISPUTE_CREATED_PREFIX = """Создан новый спор.

"""

ADMIN_DISPUTE_CREATE_CONFIRM = "По платежу уже открыт спор"

ADMIN_DISPUTE_CREATE_SUCCESS = "Сначала завершите активный refund"

ADMIN_DISPUTE_EXECUTION_NOTICE = "❌ Продавец проиграл"

ADMIN_DISPUTE_EXISTING_PREFIX = """Спор уже существовал.

"""

ADMIN_DISPUTE_GENERAL_ERROR = "Спор уже завершён другим исходом"

ADMIN_DISPUTE_INVALID_AMOUNT = "Этот case ID уже связан с другими данными"

ADMIN_DISPUTE_LIST_EMPTY = "выигран продавцом"

ADMIN_DISPUTE_LIST_HEADER = "проигран продавцом"

ADMIN_DISPUTE_NOT_FOUND_OR_RESOLVED = "Сумма превышает остаток платёжного риска"

ADMIN_DISPUTE_PROMPT_AMOUNT = "Платёж ещё не подтверждён"

ADMIN_DISPUTE_PROMPT_CASE_ID = "Платёж с таким YooKassa ID не найден"

ADMIN_DISPUTE_PROMPT_PAYMENT_ID = "Спор поддерживается только для пополнения баланса"

ADMIN_DISPUTE_PROMPT_REASON = "Пополнение ещё не зачислено в ledger"

ADMIN_DISPUTE_REJECT_SUCCESS = "Нужен ID спора банка/провайдера"

ADMIN_DISPUTE_RESOLVED_ALREADY = "Спор не найден"

ADMIN_DISPUTE_ROW_ITEM = "ручная проверка"

ADMIN_DISPUTE_SET_REVIEW_SUCCESS = "Сумма должна быть целым числом рублей"

ADMIN_DISPUTE_STATUS_CANCELLED_BADGE = "Некорректный ID"

ADMIN_DISPUTE_STATUS_LOST_BADGE = "Спор не найден"

ADMIN_DISPUTE_STATUS_LOST_LABEL = "Споров пока нет."

ADMIN_DISPUTE_STATUS_OPEN_BADGE = "Переведено на ручную проверку"

ADMIN_DISPUTE_STATUS_REVIEW_BADGE = "Некорректный ID"

ADMIN_DISPUTE_STATUS_REVIEW_LABEL = "reservation будет освобождена"

ADMIN_DISPUTE_STATUS_UNDER_REVIEW_LABEL = "будет создан exactly-once chargeback debit; возможен долг"

ADMIN_DISPUTE_STATUS_WON_BADGE = "Некорректный ID"

ADMIN_DISPUTE_STATUS_WON_LABEL = "#{value_0} · {value_1} · {value_2} ₽ · case={value_3}"

DISPUTE_RESERVATION_MISSING = "нет"
