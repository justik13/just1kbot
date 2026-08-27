"""Domain texts for admin/queues.py."""
from __future__ import annotations

ADMIN_PAYMENT_QUEUES = "pending={v0} · retry={v1} · due={v2} · overdue={v3}"

ADMIN_QUEUES_ACTION_CONFIRMED = "Причина обязательна и должна содержать 3–200 символов."

ADMIN_QUEUES_AUTO_RECOVER_DISABLED = "Укажите причину ручного retry (3–200 символов)."

ADMIN_QUEUES_BTN_BACK = "Обновлено: {v0}"

ADMIN_QUEUES_BTN_DETAILS = "Тип: {v0}"

ADMIN_QUEUES_BTN_PURGE_DEAD = "Попытки: {v0}/{v1}"

ADMIN_QUEUES_BTN_QUEUE_DEAD = "🧰 <b>{queue_name}</b>"

ADMIN_QUEUES_BTN_QUEUE_PRIMARY = "#{v0} · {v1} · {v2} · {v3}/{v4} · error={v5} · возраст {v6}"

ADMIN_QUEUES_BTN_QUEUE_RETRY = "#{v0} · {v1} · {v2}"

ADMIN_QUEUES_BTN_REFRESH = "Создано: {v0}"

ADMIN_QUEUES_BTN_RETRY_DEAD = "Статус: {v0}"

ADMIN_QUEUES_CONFIRM_PURGE_PROMPT = "{v0}ч"

ADMIN_QUEUES_CONFIRM_RETRY_PROMPT = "{v0}д"

ADMIN_QUEUES_DEAD_DETAILS_HEADER = "Подготовить retry"

ADMIN_QUEUES_DEAD_RETRY_PROMPT = "← К очереди"

ADMIN_QUEUES_DEAD_ROW_ITEM = "← Диагностика"

ADMIN_QUEUES_FAILURE_RATE_METRIC = "Открыть {v0}"

ADMIN_QUEUES_HEADER = """🔄 <b>Диагностика очередей платежей и задач</b>

ℹ️ <i>Очереди обеспечивают фоновую обработку чеков ЮKassa, автопродлений и синхронизации с серверами. Если транзакция задерживается, она переводится в статус повтора.</i>"""

ADMIN_QUEUES_HEALTH_CRIT_BADGE = "Операция поставлена в retry. Исполнение выполнит фоновый worker."

ADMIN_QUEUES_HEALTH_OK_BADGE = "Ручной retry: {v0}"

ADMIN_QUEUES_INVALID_PAGE_ALERT = "Некорректная страница"

ADMIN_QUEUES_LATENCY_METRIC = "Подтвердить retry"

ADMIN_QUEUES_METRICS_CARD = "{v0}м"

ADMIN_QUEUES_OVERVIEW_HEADER = "Завершено/обработано: {v0}"

ADMIN_QUEUES_PURGE_SUCCESS = "Отменено"

ADMIN_QUEUES_ROW_ITEM = "Retry отклонён: {v0}"

ADMIN_QUEUES_STATE_CHANGED_NOTICE = "Состояние уже изменилось"

ADMIN_QUEUES_STATUS_CRITICAL_LABEL = "Проблемные операции · стр. {v0}/{v1} · всего {v2}"

ADMIN_QUEUES_STATUS_DEGRADED_LABEL = "Старейшая проблема: {v0}"

ADMIN_QUEUES_STATUS_HEALTHY_LABEL = "processing={v0} · stale={v1} · dead={v2}"

ADMIN_QUEUES_STATUS_PAUSED_LABEL = "Проблемных операций нет."

ADMIN_QUEUES_STATUS_SUMMARY = "Подтверждение устарело."

ADMIN_QUEUES_TASK_DETAILS_CARD = "{v0}с"

ADMIN_QUEUES_THROUGHPUT_METRIC = "Подтверждение устарело"

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
