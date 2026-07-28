"""PostgreSQL-backed state transitions for the durable API operation queue.

This module deliberately contains no API executor.  Transactions only coordinate
queue state; callers must perform network work after :func:`claim_api_operations`
has returned and committed its lease.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from copy import deepcopy
from dataclasses import dataclass
from datetime import timedelta
from typing import AsyncIterator, Callable

from sqlalchemy import or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import API_OPERATION_TYPES, APIOperation


class APIOperationValidationError(Exception):
    pass


class APIOperationIdempotencyConflict(Exception):
    pass


class APIOperationOwnershipError(Exception):
    pass


@dataclass(frozen=True)
class ClaimedAPIOperation:
    id: int
    operation_type: str
    idempotency_key: str
    server_id: int | None
    profile_id: int | None
    server_name_snapshot: str | None
    api_url_snapshot: str | None
    api_key_snapshot: str | None
    peer_id: str | None
    client_name: str | None
    payload: dict
    attempt_number: int
    max_attempts: int
    locked_by: str
    last_error_code: str | None
    last_error: str | None


SessionFactory = Callable[[], AsyncSession]
_SECRET_PAYLOAD_KEYS = frozenset(
    {"api_key", "api_key_snapshot", "authorization", "password", "secret", "token"}
)
_COMMAND_FIELDS = (
    "operation_type",
    "idempotency_key",
    "server_id",
    "profile_id",
    "server_name_snapshot",
    "api_url_snapshot",
    "api_key_snapshot",
    "peer_id",
    "client_name",
    "payload",
    "max_attempts",
)


def calculate_retry_delay(attempt_number: int) -> timedelta:
    """Return deterministic exponential backoff, capped at one hour."""
    if attempt_number < 1:
        raise APIOperationValidationError("attempt_number must be positive")
    # The cap also bounds integer work for unexpectedly large corrupt values.
    return timedelta(seconds=min(30 * (2 ** min(attempt_number - 1, 7)), 3600))


def _contains_secret_key(value: object) -> bool:
    if isinstance(value, dict):
        return any(
            str(key).lower() in _SECRET_PAYLOAD_KEYS or _contains_secret_key(child)
            for key, child in value.items()
        )
    if isinstance(value, (list, tuple)):
        return any(_contains_secret_key(child) for child in value)
    return False


def _validate_command(values: dict) -> None:
    operation_type = values["operation_type"]
    if operation_type not in API_OPERATION_TYPES:
        raise APIOperationValidationError("unknown operation_type")
    if not values["idempotency_key"]:
        raise APIOperationValidationError("idempotency_key must not be empty")
    if values["max_attempts"] <= 0:
        raise APIOperationValidationError("max_attempts must be positive")
    if operation_type == "create_peer" and not values["client_name"]:
        raise APIOperationValidationError("create_peer requires client_name")
    if operation_type in {"update_peer", "delete_peer"} and not values["peer_id"]:
        raise APIOperationValidationError(f"{operation_type} requires peer_id")
    if values["server_id"] is None and not (
        values["api_url_snapshot"] and values["api_key_snapshot"]
    ):
        raise APIOperationValidationError(
            "server_id or a complete endpoint snapshot is required"
        )
    if _contains_secret_key(values["payload"]):
        raise APIOperationValidationError("payload must not contain secrets")


async def enqueue_api_operation(
    session: AsyncSession,
    *,
    operation_type: str,
    idempotency_key: str,
    server_id: int | None = None,
    profile_id: int | None = None,
    server_name_snapshot: str | None = None,
    api_url_snapshot: str | None = None,
    api_key_snapshot: str | None = None,
    peer_id: str | None = None,
    client_name: str | None = None,
    payload: dict | None = None,
    max_attempts: int = 10,
) -> APIOperation:
    """Atomically enqueue a command without committing the caller's transaction."""
    if not isinstance(idempotency_key, str):
        raise APIOperationValidationError("idempotency_key must be a string")
    normalized_idempotency_key = idempotency_key.strip()
    if not normalized_idempotency_key:
        raise APIOperationValidationError("idempotency_key must not be empty")
    if len(normalized_idempotency_key) > 255:
        raise APIOperationValidationError(
            "idempotency_key must not exceed 255 characters"
        )
    if payload is not None and not isinstance(payload, dict):
        raise APIOperationValidationError("payload must be a dict")
    values = {
        "operation_type": operation_type,
        "idempotency_key": normalized_idempotency_key,
        "server_id": server_id,
        "profile_id": profile_id,
        "server_name_snapshot": server_name_snapshot,
        "api_url_snapshot": api_url_snapshot,
        "api_key_snapshot": api_key_snapshot,
        "peer_id": peer_id,
        "client_name": client_name,
        "payload": deepcopy(payload or {}),
        "max_attempts": max_attempts,
    }
    _validate_command(values)

    statement = (
        insert(APIOperation)
        .values(**values)
        .on_conflict_do_nothing(index_elements=[APIOperation.idempotency_key])
        .returning(APIOperation.id)
    )
    operation_id = (await session.execute(statement)).scalar_one_or_none()
    if operation_id is None:
        existing = (
            await session.execute(
                select(APIOperation).where(
                    APIOperation.idempotency_key == values["idempotency_key"]
                )
            )
        ).scalar_one()
        if any(getattr(existing, field) != values[field] for field in _COMMAND_FIELDS):
            raise APIOperationIdempotencyConflict(
                "idempotency key is already associated with a different command"
            )
        return existing
    return await session.get(APIOperation, operation_id)


@asynccontextmanager
async def _transaction(session_factory: SessionFactory | None) -> AsyncIterator[AsyncSession]:
    if session_factory is None:
        from database.connection import session_scope

        async with session_scope() as session:
            yield session
        return
    async with session_factory() as session:
        async with session.begin():
            yield session


def _dto(operation: APIOperation) -> ClaimedAPIOperation:
    return ClaimedAPIOperation(
        id=operation.id,
        operation_type=operation.operation_type,
        idempotency_key=operation.idempotency_key,
        server_id=operation.server_id,
        profile_id=operation.profile_id,
        server_name_snapshot=operation.server_name_snapshot,
        api_url_snapshot=operation.api_url_snapshot,
        api_key_snapshot=operation.api_key_snapshot,
        peer_id=operation.peer_id,
        client_name=operation.client_name,
        payload=deepcopy(operation.payload),
        attempt_number=operation.attempts,
        max_attempts=operation.max_attempts,
        locked_by=operation.locked_by,
        last_error_code=operation.last_error_code,
        last_error=operation.last_error,
    )


async def claim_api_operations(
    *, worker_id: str, limit: int = 20, session_factory: SessionFactory | None = None
) -> list[ClaimedAPIOperation]:
    worker_id = worker_id.strip()
    if not worker_id:
        raise APIOperationValidationError("worker_id must not be empty")
    if not 1 <= limit <= 100:
        raise APIOperationValidationError("limit must be between 1 and 100")

    claimed: list[ClaimedAPIOperation] = []
    async with _transaction(session_factory) as session:
        # Use the database clock for every transition in this transaction.
        from sqlalchemy import func

        now = func.now()
        await session.execute(
            update(APIOperation)
            .where(
                APIOperation.status.in_(("pending", "retry")),
                APIOperation.attempts >= APIOperation.max_attempts,
            )
            .values(
                status="dead",
                completed_at=now,
                updated_at=now,
                last_error_code="max_attempts_exhausted",
                locked_at=None,
                locked_by=None,
            )
        )
        operations = (
            await session.execute(
                select(APIOperation)
                .where(
                    APIOperation.status.in_(("pending", "retry")),
                    APIOperation.next_attempt_at <= now,
                    APIOperation.attempts < APIOperation.max_attempts,
                )
                .order_by(
                    APIOperation.next_attempt_at,
                    APIOperation.created_at,
                    APIOperation.id,
                )
                .limit(limit)
                .with_for_update(skip_locked=True)
            )
        ).scalars().all()
        for operation in operations:
            operation.status = "processing"
            operation.locked_at = now
            operation.locked_by = worker_id
            operation.attempts += 1
            operation.updated_at = now
        await session.flush()
        claimed = [_dto(operation) for operation in operations]
    return claimed


async def mark_api_operation_succeeded(
    operation_id: int,
    *,
    worker_id: str,
    expected_attempt_number: int,
    session_factory: SessionFactory | None = None,
) -> None:
    """Finish an owned lease; repeated completion is an ownership error."""
    from sqlalchemy import func

    _validate_expected_attempt_number(expected_attempt_number)
    async with _transaction(session_factory) as session:
        result = await session.execute(
            update(APIOperation)
            .where(
                APIOperation.id == operation_id,
                APIOperation.status == "processing",
                APIOperation.locked_by == worker_id,
                APIOperation.attempts == expected_attempt_number,
            )
            .values(
                status="succeeded",
                completed_at=func.now(),
                updated_at=func.now(),
                locked_at=None,
                locked_by=None,
                last_error_code=None,
                last_error=None,
            )
        )
        if result.rowcount != 1:
            raise APIOperationOwnershipError("operation is not leased by this worker")


async def mark_api_operation_cancelled(
    operation_id: int, *, worker_id: str, expected_attempt_number: int,
    reason: str, session_factory: SessionFactory | None = None,
) -> None:
    from sqlalchemy import func
    _validate_expected_attempt_number(expected_attempt_number)
    async with _transaction(session_factory) as session:
        result = await session.execute(update(APIOperation).where(
            APIOperation.id == operation_id, APIOperation.status == "processing",
            APIOperation.locked_by == worker_id,
            APIOperation.attempts == expected_attempt_number,
        ).values(status="cancelled", completed_at=func.now(), updated_at=func.now(),
                 locked_at=None, locked_by=None, last_error_code=reason[:100]))
        if result.rowcount != 1:
            raise APIOperationOwnershipError("operation is not leased by this worker")


async def retry_dead_api_operation(
    operation_id: int, *, reason: str, reset_attempts: bool,
    session_factory: SessionFactory | None = None,
) -> None:
    """Administrative repair primitive; the reason is retained for audit."""
    from sqlalchemy import func
    if not reason.strip():
        raise APIOperationValidationError("audit reason must not be empty")
    async with _transaction(session_factory) as session:
        operation = (await session.execute(select(APIOperation).where(
            APIOperation.id == operation_id, APIOperation.status == "dead"
        ).with_for_update())).scalar_one_or_none()
        if operation is None:
            raise APIOperationValidationError("only dead operations can be retried")
        operation.status = "retry"
        operation.next_attempt_at = func.now()
        operation.completed_at = None
        operation.locked_at = operation.locked_by = None
        operation.last_error_code = "manual_retry"
        operation.last_error = reason[:2000]
        if reset_attempts:
            operation.attempts = 0


async def mark_api_operation_failed(
    operation_id: int,
    *,
    worker_id: str,
    expected_attempt_number: int,
    retryable: bool,
    error_code: str,
    error_message: str,
    session_factory: SessionFactory | None = None,
) -> str:
    """Fail an owned fenced lease; error inputs must already be safe strings."""
    from sqlalchemy import func

    _validate_expected_attempt_number(expected_attempt_number)
    if not isinstance(error_code, str):
        raise APIOperationValidationError("error_code must be a string")
    if not isinstance(error_message, str):
        raise APIOperationValidationError("error_message must be a string")
    safe_error_code = (error_code.strip() or "unknown_error")[:100]
    safe_error_message = error_message[:2000]

    async with _transaction(session_factory) as session:
        operation = (
            await session.execute(
                select(APIOperation)
                .where(
                    APIOperation.id == operation_id,
                    APIOperation.status == "processing",
                    APIOperation.locked_by == worker_id,
                    APIOperation.attempts == expected_attempt_number,
                )
                .with_for_update()
            )
        ).scalar_one_or_none()
        if operation is None:
            raise APIOperationOwnershipError("operation is not leased by this worker")
        should_retry = retryable and operation.attempts < operation.max_attempts
        operation.status = "retry" if should_retry else "dead"
        operation.next_attempt_at = (
            func.now() + calculate_retry_delay(operation.attempts) if should_retry else operation.next_attempt_at
        )
        operation.completed_at = None if should_retry else func.now()
        operation.updated_at = func.now()
        operation.locked_at = None
        operation.locked_by = None
        operation.last_error_code = safe_error_code
        operation.last_error = safe_error_message
        return operation.status


def _validate_expected_attempt_number(expected_attempt_number: int) -> None:
    if not isinstance(expected_attempt_number, int) or isinstance(
        expected_attempt_number, bool
    ) or expected_attempt_number <= 0:
        raise APIOperationValidationError(
            "expected_attempt_number must be a positive integer"
        )


async def recover_stale_api_operations(
    *,
    lease_timeout: timedelta,
    session_factory: SessionFactory | None = None,
) -> tuple[int, int]:
    if lease_timeout <= timedelta(0):
        raise APIOperationValidationError("lease_timeout must be positive")
    from sqlalchemy import func

    retried = dead = 0
    async with _transaction(session_factory) as session:
        operations = (
            await session.execute(
                select(APIOperation)
                .where(
                    APIOperation.status == "processing",
                    or_(
                        APIOperation.locked_at.is_(None),
                        APIOperation.locked_at < func.now() - lease_timeout,
                    ),
                )
                .with_for_update(skip_locked=True)
            )
        ).scalars().all()
        for operation in operations:
            operation.locked_at = None
            operation.locked_by = None
            operation.updated_at = func.now()
            if operation.attempts < operation.max_attempts:
                operation.status = "retry"
                operation.next_attempt_at = func.now()
                operation.completed_at = None
                retried += 1
            else:
                operation.status = "dead"
                operation.completed_at = func.now()
                operation.last_error_code = "stale_lease_max_attempts"
                dead += 1
    return retried, dead
