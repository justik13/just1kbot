"""Read-only, secret-free health snapshots for durable payment queues."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable

from sqlalchemy import case, func, or_, select

from database.models import PaymentFulfillmentOperation, PaymentProviderOperation, WebhookInbox
from services.payment_queue_timing import (
    BACKLOG_GRACE_SECONDS,
    FULFILLMENT_LEASE_SECONDS,
    HEALTH_LEASE_GRACE_SECONDS,
    PROVIDER_LEASE_SECONDS,
    WEBHOOK_LEASE_SECONDS,
)
from utils.logging_security import sanitize_short

MAX_EXAMPLES = 5

@dataclass(frozen=True)
class QueueExample:
    operation_id: int
    payment_id: int | None
    operation_type: str
    status: str
    attempts: int
    max_attempts: int
    last_error_code: str | None
    age_seconds: int

@dataclass(frozen=True)
class QueueSnapshot:
    name: str
    pending: int
    retry: int
    due: int
    overdue: int
    processing: int
    stale_processing: int
    dead: int
    oldest_due_age_seconds: int | None
    oldest_stale_age_seconds: int | None
    oldest_dead_age_seconds: int | None
    examples: tuple[QueueExample, ...]

    @property
    def healthy(self) -> bool:
        return not (self.overdue or self.stale_processing or self.dead)

@dataclass(frozen=True)
class PaymentQueueHealthSnapshot:
    observed_at: datetime
    queues: tuple[QueueSnapshot, ...]

    @property
    def healthy(self) -> bool:
        return all(queue.healthy for queue in self.queues)


def _age(now: datetime, value: datetime | None) -> int | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return max(0, int((now - value.astimezone(timezone.utc)).total_seconds()))


async def _snapshot_queue(session, *, name, model, created_column, type_column,
                          payment_column, lease_seconds, now):
    due = model.status.in_(("pending", "retry")) & (model.next_attempt_at <= now)
    overdue_cutoff = now - timedelta(seconds=BACKLOG_GRACE_SECONDS)
    overdue = due & (model.next_attempt_at < overdue_cutoff)
    stale_cutoff = now - timedelta(seconds=lease_seconds + HEALTH_LEASE_GRACE_SECONDS)
    stale = (model.status == "processing") & (model.locked_at < stale_cutoff)
    dead = model.status == "dead"
    stmt = select(
        func.count().filter(model.status == "pending"),
        func.count().filter(model.status == "retry"),
        func.count().filter(due),
        func.count().filter(overdue),
        func.count().filter(model.status == "processing"),
        func.count().filter(stale),
        func.count().filter(dead),
        func.min(model.next_attempt_at).filter(due),
        func.min(model.locked_at).filter(stale),
        func.min(created_column).filter(dead),
    )
    values = (await session.execute(stmt)).one()
    problem = or_(overdue, stale, dead)
    age_source = case(
        (overdue, model.next_attempt_at),
        (stale, model.locked_at),
        else_=created_column,
    )
    sample_stmt = select(
        model.id, payment_column, type_column, model.status, model.attempts,
        model.max_attempts, model.last_error_code, age_source,
    ).where(problem).order_by(age_source.asc(), model.id.asc()).limit(MAX_EXAMPLES)
    rows = (await session.execute(sample_stmt)).all()
    examples = tuple(QueueExample(
        operation_id=row[0], payment_id=row[1], operation_type=row[2], status=row[3],
        attempts=row[4], max_attempts=row[5],
        last_error_code=sanitize_short(row[6], 100) or None,
        age_seconds=_age(now, row[7]) or 0,
    ) for row in rows)
    return QueueSnapshot(name, *(int(value or 0) for value in values[:7]),
                         _age(now, values[7]), _age(now, values[8]),
                         _age(now, values[9]), examples)


async def get_payment_queue_health_snapshot(
    session, *, clock: Callable[[], datetime] | None = None
) -> PaymentQueueHealthSnapshot:
    """Inspect all payment queues without selecting sensitive columns or mutating rows."""
    now = (clock() if clock else datetime.now(timezone.utc)).astimezone(timezone.utc)
    queues = (
        await _snapshot_queue(session, name="provider_operations",
            model=PaymentProviderOperation, created_column=PaymentProviderOperation.created_at,
            type_column=PaymentProviderOperation.operation_type,
            payment_column=PaymentProviderOperation.payment_id,
            lease_seconds=PROVIDER_LEASE_SECONDS, now=now),
        await _snapshot_queue(session, name="fulfillment_operations",
            model=PaymentFulfillmentOperation, created_column=PaymentFulfillmentOperation.created_at,
            type_column=PaymentFulfillmentOperation.operation_type,
            payment_column=PaymentFulfillmentOperation.payment_id,
            lease_seconds=FULFILLMENT_LEASE_SECONDS, now=now),
        await _snapshot_queue(session, name="webhook_inbox", model=WebhookInbox,
            created_column=WebhookInbox.received_at, type_column=WebhookInbox.event_type,
            payment_column=func.cast(None, PaymentProviderOperation.payment_id.type),
            lease_seconds=WEBHOOK_LEASE_SECONDS, now=now),
    )
    return PaymentQueueHealthSnapshot(now, queues)
