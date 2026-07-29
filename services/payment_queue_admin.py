"""Secret-free admin views and serialized manual retry for payment queues."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import json
from typing import Callable

from sqlalchemy import case, func, or_, select

from database.models import (
    AuditLog,
    Payment,
    PaymentFulfillmentOperation,
    PaymentProviderOperation,
    WebhookInbox,
)
from services.payment_fulfillment import retry_dead_fulfillment_operation
from services.payment_provider_operations import retry_dead_provider_operation
from services.payment_queue_timing import (
    BACKLOG_GRACE_SECONDS,
    FULFILLMENT_LEASE_SECONDS,
    HEALTH_LEASE_GRACE_SECONDS,
    PROVIDER_LEASE_SECONDS,
    WEBHOOK_LEASE_SECONDS,
)
from services.workers.webhook_inbox import retry_dead_webhook_operation
from utils.logging_security import sanitize_short

QUEUE_TYPES = ("provider", "fulfillment", "webhook")
PAGE_SIZE = 10


@dataclass(frozen=True)
class QueueRow:
    queue: str
    operation_id: int
    payment_id: int | None
    operation_type: str
    status: str
    attempts: int
    max_attempts: int
    last_error_code: str | None
    created_at: datetime
    updated_at: datetime | None
    terminal_at: datetime | None
    locked_at: datetime | None
    lease_status: str
    age_seconds: int

    @property
    def retry_allowed(self) -> bool:
        return self.status == "dead"

    @property
    def confirmation_version(self) -> str:
        return _confirmation_version(
            queue=self.queue, operation_id=self.operation_id, status=self.status,
            attempts=self.attempts, max_attempts=self.max_attempts,
            terminal_at=self.terminal_at, updated_at=self.updated_at,
            last_error_code=self.last_error_code,
        )


@dataclass(frozen=True)
class QueuePage:
    rows: tuple[QueueRow, ...]
    page: int
    total_pages: int
    total: int


@dataclass(frozen=True)
class ManualRetryResult:
    outcome: str
    queue: str
    operation_id: int
    payment_id: int | None = None
    rejection_code: str | None = None


def _spec(queue: str):
    if queue == "provider":
        return (PaymentProviderOperation, PaymentProviderOperation.operation_type,
                PaymentProviderOperation.created_at, PaymentProviderOperation.updated_at,
                PaymentProviderOperation.completed_at, PaymentProviderOperation.payment_id,
                PROVIDER_LEASE_SECONDS)
    if queue == "fulfillment":
        return (PaymentFulfillmentOperation, PaymentFulfillmentOperation.operation_type,
                PaymentFulfillmentOperation.created_at, PaymentFulfillmentOperation.updated_at,
                PaymentFulfillmentOperation.completed_at, PaymentFulfillmentOperation.payment_id,
                FULFILLMENT_LEASE_SECONDS)
    if queue == "webhook":
        return (WebhookInbox, WebhookInbox.event_type, WebhookInbox.received_at,
                WebhookInbox.received_at, WebhookInbox.processed_at, None,
                WEBHOOK_LEASE_SECONDS)
    raise ValueError("invalid queue type")


def _conditions(model, updated, terminal, lease_seconds: int, now: datetime):
    overdue = model.status.in_(("pending", "retry")) & (
        model.next_attempt_at < now - timedelta(seconds=BACKLOG_GRACE_SECONDS)
    )
    stale = (model.status == "processing") & or_(
        model.locked_at.is_(None), model.locked_by.is_(None), model.locked_by == "",
        model.locked_at < now - timedelta(
            seconds=lease_seconds + HEALTH_LEASE_GRACE_SECONDS
        ),
    )
    dead = model.status == "dead"
    problem = or_(dead, stale, overdue)
    processing_age = func.coalesce(model.locked_at, updated)
    terminal_age = func.coalesce(terminal, updated)
    age_source = case((dead, terminal_age), (stale, processing_age), else_=model.next_attempt_at)
    priority = case((dead, 0), (stale, 1), else_=2)
    return problem, stale, age_source, priority


def _age(now: datetime, value: datetime | None) -> int:
    if value is None:
        return 0
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return max(0, int((now - value.astimezone(timezone.utc)).total_seconds()))


def _canonical_timestamp(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds")


def _confirmation_version(*, queue: str, operation_id: int, status: str,
                          attempts: int, max_attempts: int,
                          terminal_at: datetime | None,
                          updated_at: datetime | None,
                          last_error_code: str | None) -> str:
    canonical = json.dumps({
        "attempts": attempts,
        "last_error_code": sanitize_short(last_error_code, 100)[:100] or None,
        "max_attempts": max_attempts,
        "operation_id": operation_id,
        "queue": queue,
        "status": status,
        "terminal_at": _canonical_timestamp(terminal_at),
        "updated_at": _canonical_timestamp(updated_at),
    }, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _orm_confirmation_version(queue: str, row) -> str:
    terminal_at = row.processed_at if queue == "webhook" else row.completed_at
    updated_at = None if queue == "webhook" else row.updated_at
    return _confirmation_version(
        queue=queue, operation_id=row.id, status=row.status,
        attempts=row.attempts, max_attempts=row.max_attempts,
        terminal_at=terminal_at, updated_at=updated_at,
        last_error_code=row.last_error_code,
    )


async def list_problem_operations(session, queue: str, page: int, *,
                                  clock: Callable[[], datetime] | None = None) -> QueuePage:
    if page < 1:
        raise ValueError("invalid page")
    model, type_col, created, updated, terminal, payment_col, lease = _spec(queue)
    now = (clock() if clock else datetime.now(timezone.utc)).astimezone(timezone.utc)
    problem, stale, age_source, priority = _conditions(model, updated, terminal, lease, now)
    total = int(await session.scalar(select(func.count(model.id)).where(problem)) or 0)
    pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
    if page > pages:
        raise ValueError("invalid page")

    if queue == "webhook":
        payment_expr = Payment.id
        stmt = select(model.id, payment_expr, type_col, model.status, model.attempts,
                      model.max_attempts, model.last_error_code, created, updated,
                      terminal, model.locked_at, stale, age_source).outerjoin(
                          Payment, Payment.external_id == WebhookInbox.payment_external_id)
    else:
        stmt = select(model.id, payment_col, type_col, model.status, model.attempts,
                      model.max_attempts, model.last_error_code, created, updated,
                      terminal, model.locked_at, stale, age_source)
    rows = (await session.execute(stmt.where(problem).order_by(
        priority.asc(), age_source.asc(), model.id.asc()
    ).offset((page - 1) * PAGE_SIZE).limit(PAGE_SIZE))).all()
    safe_rows = tuple(QueueRow(
        queue=queue, operation_id=int(r[0]), payment_id=r[1], operation_type=str(r[2]),
        status=str(r[3]), attempts=int(r[4]), max_attempts=int(r[5]),
        last_error_code=sanitize_short(r[6], 100) or None, created_at=r[7],
        updated_at=r[8], terminal_at=r[9], locked_at=r[10],
        lease_status="stale_or_malformed" if r[11] else (
            "active" if r[3] == "processing" else "not_locked"),
        age_seconds=_age(now, r[12]),
    ) for r in rows)
    return QueuePage(safe_rows, page, pages, total)


async def get_operation_card(session, queue: str, operation_id: int) -> QueueRow | None:
    if operation_id < 1:
        raise ValueError("invalid operation id")
    model, type_col, created, updated, terminal, payment_col, lease = _spec(queue)
    now = datetime.now(timezone.utc)
    _, stale, age_source, _ = _conditions(model, updated, terminal, lease, now)
    if queue == "webhook":
        stmt = select(model.id, Payment.id, type_col, model.status, model.attempts,
            model.max_attempts, model.last_error_code, created, updated, terminal,
            model.locked_at, stale, age_source).outerjoin(
                Payment, Payment.external_id == WebhookInbox.payment_external_id)
    else:
        stmt = select(model.id, payment_col, type_col, model.status, model.attempts,
            model.max_attempts, model.last_error_code, created, updated, terminal,
            model.locked_at, stale, age_source)
    r = (await session.execute(stmt.where(model.id == operation_id))).one_or_none()
    if not r:
        return None
    return QueueRow(queue, int(r[0]), r[1], str(r[2]), str(r[3]), int(r[4]),
        int(r[5]), sanitize_short(r[6], 100) or None, r[7], r[8], r[9], r[10],
        "stale_or_malformed" if r[11] else ("active" if r[3] == "processing" else "not_locked"),
        _age(now, r[12]))


async def _audit(session, *, admin_id: int, queue: str, operation_id: int,
                 payment_id: int | None, original_status: str | None,
                 attempts: int | None, outcome: str, reason: str,
                 rejection_code: str | None = None) -> None:
    # Flush is intentional: audit is mandatory and shares the caller transaction.
    sanitized_reason = sanitize_short(reason, 200)[:200]
    details = json.dumps({
        "attempts": attempts,
        "original_status": original_status,
        "outcome": outcome,
        "payment_id": payment_id,
        "queue": queue,
        "reason": sanitized_reason,
        "rejection_code": sanitize_short(rejection_code, 100)[:100] or None,
    }, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    session.add(AuditLog(admin_id=admin_id, action="PAYMENT_QUEUE_MANUAL_RETRY",
                         target_type=queue, target_id=operation_id, details=details))
    await session.flush()


async def confirm_manual_retry(session, *, admin_id: int, queue: str,
                               operation_id: int, reason: str,
                               expected_version: str) -> ManualRetryResult:
    """Lock current state, call the existing retry primitive, and audit atomically."""
    if queue not in QUEUE_TYPES:
        raise ValueError("invalid queue type")
    if operation_id < 1:
        raise ValueError("invalid operation id")
    if not isinstance(expected_version, str) or len(expected_version) != 64:
        raise ValueError("invalid confirmation version")
    reason = reason.strip()
    if not 3 <= len(reason) <= 200:
        raise ValueError("invalid reason")
    model, *_ = _spec(queue)
    row = await session.scalar(select(model).where(model.id == operation_id).with_for_update())
    if row is None:
        await _audit(session, admin_id=admin_id, queue=queue, operation_id=operation_id,
                     payment_id=None, original_status=None, attempts=None,
                     outcome="not_found", reason=reason)
        return ManualRetryResult("not_found", queue, operation_id)
    payment_id = row.payment_id if queue != "webhook" else await session.scalar(
        select(Payment.id).where(Payment.external_id == row.payment_external_id))
    status, attempts = row.status, row.attempts
    if status != "dead" or _orm_confirmation_version(queue, row) != expected_version:
        await _audit(session, admin_id=admin_id, queue=queue, operation_id=operation_id,
                     payment_id=payment_id, original_status=status, attempts=attempts,
                     outcome="already_changed", reason=reason)
        return ManualRetryResult("already_changed", queue, operation_id, payment_id)
    rejection = None
    if queue == "provider":
        decision = await retry_dead_provider_operation(
            session, operation_id, reset_attempts=True, reason=reason)
        outcome = "retry_scheduled" if decision.accepted else "rejected"
        rejection = None if decision.accepted else sanitize_short(decision.reason, 100)
    elif queue == "fulfillment":
        await retry_dead_fulfillment_operation(
            session, operation_id, reset_attempts=True, reason=reason)
        outcome = "retry_scheduled"
    else:
        await retry_dead_webhook_operation(
            session, operation_id, reset_attempts=True, reason=reason)
        outcome = "retry_scheduled"
    await _audit(session, admin_id=admin_id, queue=queue, operation_id=operation_id,
                 payment_id=payment_id, original_status=status, attempts=attempts,
                 outcome=outcome, reason=reason, rejection_code=rejection)
    return ManualRetryResult(outcome, queue, operation_id, payment_id, rejection)
