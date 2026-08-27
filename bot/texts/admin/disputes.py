"""Domain texts for admin/disputes.py."""
from __future__ import annotations

ADMIN_BTN_BACK_TO_DISPUTES = "← К спорам"

DISPUTE_CARD_TEMPLATE = """⚠️ <b>Спор #{dispute_id}</b>
Статус: <b>{status}</b>
Case ID: <code>{case_id}</code>
YooKassa payment: <code>{payment_id}</code>
Сумма: <b>{amount_rub} RUB</b>
Дата спора: <code>{disputed_at}</code>
Reservation: <code>{reservation_id}</code> ({reservation_status})
Chargeback entry: <code>{chargeback_entry_id}</code>
Заметка: {note}"""

ADMIN_DISPUTES_HEADER = """⚖️ <b>Управление платежными спорами (Disputes)</b>

ℹ️ <i>Диспуты возникают при обращении клиентов в банк или платёжный провайдер (чарджбэк). Здесь вы можете просмотреть детали спора и урегулировать вопрос.</i>"""

ADMIN_DISPUTES_INPUT_CANCELLED = "❌ Ввод спора отменён."

DISPUTE_ERR_PAYMENT_ID_REQUIRED = "Нужен YooKassa payment ID"

DISPUTE_ERR_DATE_INVALID = "Некорректная дата спора"

DISPUTE_BTN_SET_MANUAL_REVIEW = "🛑 Ручная проверка"

DISPUTE_PROMPT_INPUT_FORMAT = """Отправьте одной строкой:
<code>YooKassa_payment_ID | case_ID | сумма | YYYY-MM-DD | open/manual_review/won_by_merchant/lost_by_merchant | заметка</code>

Пример:
<code>2f... | bank-case-17 | 499 | 2026-08-02 | open | ожидаем документы</code>"""

DISPUTE_ERR_INVALID_DATE_FORMAT = "Дата должна быть в формате YYYY-MM-DD"

DISPUTE_LIST_BUTTON_FORMAT = "#{dispute_id} · {status}"

DISPUTE_ERR_FINANCIAL_INVARIANT = "Операция со спором отклонена финансовыми инвариантами"

DISPUTE_ERR_WRONG_FIELD_COUNT = "Нужно ровно 6 полей, разделённых символом |"

DISPUTE_ERR_INVALID_STATUS = "Некорректный статус спора"

DISPUTE_STATUS_OPEN_LABEL = "открыт"

DISPUTE_BTN_CREATE = "➕ Зарегистрировать спор"

DISPUTE_CONFIRM_RESULT_NOTE = """⚠️ <b>Подтвердите исход спора</b>

Спор: <code>#{dispute_id}</code>
Исход: <b>{status}</b>
Эффект: {amount_rub}."""

DISPUTE_SET_RESULT_SUCCESS = "Исход спора зафиксирован"

DISPUTE_BTN_OUTCOME_WON = "✅ Продавец выиграл"

DISPUTE_ERR_STATE_CHANGED = "Состояние спора уже изменилось"

ADMIN_DISPUTE_CREATED_PREFIX = """Создан новый спор.

"""

DISPUTE_ERR_ACTIVE_DISPUTE = "По платежу уже открыт спор"

DISPUTE_ERR_REFUND_IN_PROGRESS = "Сначала завершите активный refund"

DISPUTE_BTN_OUTCOME_LOST = "❌ Продавец проиграл"

ADMIN_DISPUTE_EXISTING_PREFIX = """Спор уже существовал.

"""

DISPUTE_ERR_ALREADY_RESOLVED = "Спор уже завершён другим исходом"

DISPUTE_ERR_CASE_ID_CONFLICT = "Этот case ID уже связан с другими данными"

DISPUTE_STATUS_WON_LABEL = "выигран продавцом"

DISPUTE_STATUS_LOST_LABEL = "проигран продавцом"

ADMIN_DISPUTE_NOT_FOUND_ALERT = "Спор не найден"

DISPUTE_ERR_EXCEEDS_RISK_LIMIT = "Сумма превышает остаток платёжного риска"

DISPUTE_ERR_PAYMENT_NOT_SETTLED = "Платёж ещё не подтверждён"

DISPUTE_ERR_PAYMENT_NOT_FOUND = "Платёж с таким YooKassa ID не найден"

DISPUTE_ERR_NOT_TOPUP = "Спор поддерживается только для пополнения баланса"

DISPUTE_ERR_PAYMENT_NOT_CREDITED = "Пополнение ещё не зачислено в ledger"

DISPUTE_ERR_CASE_ID_REQUIRED = "Нужен ID спора банка/провайдера"

DISPUTE_STATUS_MANUAL_REVIEW_LABEL = "ручная проверка"

DISPUTE_ERR_AMOUNT_INVALID = "Сумма должна быть целым числом рублей"

DISPUTE_LIST_EMPTY = "Споров пока нет."

DISPUTE_SET_MANUAL_REVIEW_NOTICE = "Переведено на ручную проверку"

DISPUTE_EFFECT_RESERVATION_RELEASED = "reservation будет освобождена"

DISPUTE_EFFECT_CHARGEBACK_DEBIT = "будет создан exactly-once chargeback debit; возможен долг"

DISPUTE_LIST_ROW_FORMAT = "#{dispute_id} · {status} · {amount_rub} ₽ · case={case}"

BTN_REFRESH_ACTION = "🔄 Обновить"

DISPUTE_RESERVATION_MISSING = "нет"

ERROR_INVALID_ID = "Некорректный ID"
