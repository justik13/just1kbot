"""Domain texts for admin/queues.py."""
from __future__ import annotations

QUEUE_STATE_COUNTS = "pending={pending} · retry={retry} · due={due} · overdue={overdue}"

QUEUE_ERR_REASON_LENGTH = "Причина обязательна и должна содержать 3–200 символов."

QUEUE_ERR_REASON_REQUIRED = "Укажите причину ручного retry (3–200 символов)."

QUEUE_CARD_UPDATED = "Обновлено: {updated_at}"

QUEUE_CARD_TYPE = "Тип: {operation_type}"

QUEUE_CARD_ATTEMPTS = "Попытки: {attempts}/{max_attempts}"

ADMIN_QUEUES_BTN_QUEUE_DEAD = "🧰 <b>{queue_name}</b>"

QUEUE_OPERATION_ROW = "#{operation_id} · {operation_type} · {status} · {attempts}/{max_attempts} · error={error} · возраст {age}"

QUEUE_RETRY_ROW = "#{operation_id} · {status} · {operation_type}"

QUEUE_CARD_CREATED = "Создано: {created_at}"

QUEUE_CARD_STATUS = "Статус: {status}"

QUEUE_DURATION_HOURS = "{hours}ч"

QUEUE_DURATION_DAYS = "{days}д"

QUEUE_BTN_PREPARE_RETRY = "Подготовить retry"

QUEUE_BTN_BACK_TO_QUEUE = "← К очереди"

QUEUE_BTN_BACK_TO_DIAGNOSTICS = "← Диагностика"

QUEUE_BTN_OPEN = "Открыть {queue}"

ADMIN_QUEUES_HEADER = """🔄 <b>Диагностика очередей платежей и задач</b>

ℹ️ <i>Очереди обеспечивают фоновую обработку чеков ЮKassa, автопродлений и синхронизации с серверами. Если транзакция задерживается, она переводится в статус повтора.</i>"""

QUEUE_RETRY_SCHEDULED = "Операция поставлена в retry. Исполнение выполнит фоновый worker."

QUEUE_RETRY_STATUS = "Ручной retry: {retry_status}"

ADMIN_QUEUES_INVALID_PAGE_ALERT = "Некорректная страница"

QUEUE_BTN_CONFIRM_RETRY = "Подтвердить retry"

QUEUE_DURATION_MINUTES = "{minutes}м"

QUEUE_CARD_TERMINATED = "Завершено/обработано: {terminated_at}"

ADMIN_QUEUES_PURGE_SUCCESS = "Отменено"

QUEUE_RETRY_REJECTED = "Retry отклонён: {code}"

ADMIN_QUEUES_STATE_CHANGED_NOTICE = "Состояние уже изменилось"

QUEUE_PROBLEM_LIST_TITLE = "Проблемные операции · стр. {page}/{total_pages} · всего {total}"

QUEUE_OLDEST_PROBLEM = "Старейшая проблема: {oldest}"

QUEUE_HEALTH_COUNTS = "processing={processing} · stale={stale} · dead={dead}"

QUEUE_PROBLEM_LIST_EMPTY = "Проблемных операций нет."

QUEUE_ERR_CONFIRMATION_EXPIRED = "Подтверждение устарело."

QUEUE_DURATION_SECONDS = "{seconds}с"

QUEUE_ERR_STALE_ACTION = "Подтверждение устарело"

ADMIN_QUEUE_CARD_ERROR = "Error code: {error_code}"

ADMIN_QUEUE_CARD_ID = "ID: <code>{operation_id}</code>"

ADMIN_QUEUE_CARD_LEASE = "Lease: {lease}"

ADMIN_QUEUE_CARD_LOCK = "Lock timestamp: {locked_at}"

ADMIN_QUEUE_CARD_PAYMENT = "Payment ID: {payment_id}"

ADMIN_QUEUE_NAME = "<b>{name}</b>"

ADMIN_QUEUE_PROVIDER_LABEL = "Provider operations"

ADMIN_QUEUE_PROVIDER_SHORT = "Provider"

ADMIN_QUEUE_REFUNDS_SHORT = "Refunds"

ADMIN_QUEUE_RETRY_CONFIRMATION = """{card}

⚠️ Операция может быть обработана повторно."""

ADMIN_QUEUE_WEBHOOK_LABEL = "Webhook inbox"

ADMIN_QUEUE_WEBHOOK_SHORT = "Webhook"

QUEUE_HEALTH_CODE_FRAGMENT = " code={code}"

QUEUE_HEALTH_EXAMPLE = "<code>id={operation_id}{payment} type={operation_type} status={status} attempts={attempts}/{max_attempts} age={age}s{code}</code>"

QUEUE_HEALTH_PAYMENT_FRAGMENT = " payment={payment_id}"

QUEUE_HEALTH_PROBLEM_DEAD = "dead={count} oldest={age}s"

QUEUE_HEALTH_PROBLEM_OVERDUE = "overdue={count} oldest={age}s"

QUEUE_HEALTH_PROBLEM_STALE = "stale_processing={count} oldest={age}s"

QUEUE_OPERATION_NOT_FOUND = "Операция не найдена"

QUEUE_RETRY_AVAILABLE = "доступен"

QUEUE_RETRY_UNAVAILABLE = "недоступен"
