"""Atomic fenced database finalization for fulfilled API operations."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy import func

from database.connection import session_scope
from database.models import APIOperation, Server, VPNProfile
from services.api_operations_queue import (
    APIOperationOwnershipError,
    calculate_retry_delay,
    enqueue_api_operation,
)


class CreateCompensationRequired(Exception):
    """The external CREATE succeeded but its local profile disappeared."""


async def _locked(session, operation_id, worker_id, attempt):
    operation = (
        await session.execute(
            select(APIOperation)
            .where(APIOperation.id == operation_id)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if (
        not operation
        or getattr(operation, "status", None) != "processing"
        or getattr(operation, "locked_by", None) != worker_id
        or getattr(operation, "attempts", None) != attempt
    ):
        raise APIOperationOwnershipError("operation is not leased by this worker")
    return operation


async def _lock_operation_and_profile(session, operation_id, worker_id, attempt):
    """Acquires locks in canonical global hierarchy: VPNProfile -> APIOperation.

    This strictly prevents PostgreSQL deadlocks between background workers (finalizer)
    and foreground services (ProfileDeletionService, server deletion, user ban).
    """
    op_profile_res = await session.execute(
        select(APIOperation.profile_id).where(APIOperation.id == operation_id)
    )
    profile_id = op_profile_res.scalar_one_or_none()
    profile = None
    if profile_id is not None:
        profile_res = await session.execute(
            select(VPNProfile)
            .where(VPNProfile.id == profile_id)
            .with_for_update()
        )
        profile = profile_res.scalar_one_or_none()

    operation = await _locked(session, operation_id, worker_id, attempt)
    if profile is None and operation and getattr(operation, "profile_id", None):
        profile_res = await session.execute(
            select(VPNProfile)
            .where(VPNProfile.id == operation.profile_id)
            .with_for_update()
        )
        profile = profile_res.scalar_one_or_none()

    return operation, profile


def _complete(operation, status="succeeded", reason=None):
    now = datetime.now(timezone.utc)
    operation.status = status
    operation.completed_at = now
    operation.updated_at = now
    operation.locked_at = None
    operation.locked_by = None
    operation.last_error_code = reason
    operation.last_error = None


async def finalize_create_success(
    operation_id: int,
    *,
    worker_id: str,
    expected_attempt_number: int,
    peer_id: str,
    raw_config: str,
    sent_desired_version: int,
    sent_is_active: bool,
    sent_expires_at: datetime | None,
    session_factory=None,
) -> None:
    """Atomically persist a previously validated CREATE result.

    Validation of the external Amnezia response belongs to the executor boundary.
    This finalizer is deliberately limited to the fenced database commit so that
    a successful external CREATE and its local state transition remain one
    atomic transaction.
    """
    async with _scope(session_factory) as session:
        operation, profile = await _lock_operation_and_profile(
            session, operation_id, worker_id, expected_attempt_number
        )
        compensation_required = False
        if profile is None:
            compensation_required = True
        elif profile.provisioning_status not in {"pending_create", "active"}:
            profile.peer_id = peer_id
            if profile.provisioning_status != "deleting":
                profile.provisioning_status = "create_cleanup_pending"
            compensation_required = True
        else:
            profile.peer_id = peer_id
            profile.raw_config = raw_config
            profile.actual_is_active = sent_is_active
            profile.actual_expires_at = sent_expires_at
            profile.last_synced_at = datetime.now(timezone.utc)
            profile.last_sync_error = None
            if profile.desired_version == sent_desired_version:
                profile.provisioning_status = "active"
            else:
                profile.provisioning_status = "pending_update"
                await _ensure_current_update(session, profile)
            _complete(operation)

    if compensation_required:
        raise CreateCompensationRequired()


async def finalize_existing_create_success(
    operation_id: int,
    *,
    worker_id: str,
    expected_attempt_number: int,
    session_factory=None,
) -> None:
    """Repair an old split-commit window without touching the external peer."""
    async with _scope(session_factory) as session:
        operation, profile = await _lock_operation_and_profile(
            session, operation_id, worker_id, expected_attempt_number
        )
        if (
            not profile
            or profile.provisioning_status != "active"
            or not profile.peer_id
            or not profile.raw_config
        ):
            raise RuntimeError("profile is not a finalized create")
        _complete(operation)


async def finalize_create_cancelled(
    operation_id: int,
    *,
    worker_id: str,
    expected_attempt_number: int,
    reason: str,
    delete_profile: bool,
    session_factory=None,
) -> None:
    async with _scope(session_factory) as session:
        operation, profile = await _lock_operation_and_profile(
            session, operation_id, worker_id, expected_attempt_number
        )
        if profile:
            if delete_profile:
                await session.delete(profile)
            else:
                profile.peer_id = None
        _complete(operation, "cancelled", reason)


async def finalize_update_success(
    operation_id: int,
    *,
    worker_id: str,
    expected_attempt_number: int,
    sent_version: int,
    sent_is_active: bool,
    sent_expires_at: datetime | None,
    sent_clear_expires_at: bool = False,
    session_factory=None,
) -> None:
    async with _scope(session_factory) as session:
        operation, profile = await _lock_operation_and_profile(
            session, operation_id, worker_id, expected_attempt_number
        )
        if profile:
            profile.actual_is_active = sent_is_active
            if sent_is_active or sent_clear_expires_at:
                profile.actual_expires_at = sent_expires_at
            profile.last_synced_at = datetime.now(timezone.utc)
            profile.last_sync_error = None
            if profile.provisioning_status not in {"deleting", "delete_failed"}:
                profile.provisioning_status = (
                    "active"
                    if profile.desired_version == sent_version
                    else "pending_update"
                )
        _complete(operation)


async def finalize_operation_failure(
    operation_id: int,
    *,
    worker_id: str,
    expected_attempt_number: int,
    retryable: bool,
    error_code: str,
    error_message: str,
    session_factory=None,
) -> str:
    """Atomically release/dead-letter a lease and synchronize profile state."""
    async with _scope(session_factory) as session:
        operation, profile = await _lock_operation_and_profile(
            session, operation_id, worker_id, expected_attempt_number
        )
        should_retry = retryable and operation.attempts < operation.max_attempts
        operation.status = "retry" if should_retry else "dead"
        operation.next_attempt_at = (
            func.now() + calculate_retry_delay(operation.attempts)
            if should_retry
            else operation.next_attempt_at
        )
        operation.completed_at = None if should_retry else func.now()
        operation.updated_at = func.now()
        operation.locked_at = operation.locked_by = None
        operation.last_error_code = (error_code or "unknown_error")[:100]
        operation.last_error = error_message[:2000]
        if profile:
            # P2-5: Человекочитаемый текст для last_sync_error
            error_mapping = {
                "server_full": "Сервер переполнен",
                "auth_failed": "Неверный API ключ сервера",
                "invalid_raw_config": "Ошибка шифрования конфига",
                "create_ambiguous_reconcile": "Не удалось проверить создание пира",
                "invalid_created_config_cleanup": "Конфиг не сгенерирован (очистка)",
                "duplicate_exact_client_name": "Такое имя уже существует на сервере",
                "network_error": "Ошибка сети при обращении к серверу",
                "timeout": "Таймаут обращения к серверу",
            }
            human_readable = error_mapping.get(operation.last_error_code, f"Operation failed: {operation.last_error_code}")
            if error_message:
                human_readable = f"{human_readable} ({error_message[:1000]})"
            profile.last_sync_error = human_readable[:2000]
            if not should_retry:
                if operation.operation_type == "create_peer":
                    from services.api_operations_queue import CREATE_CLEANUP_REQUIRED_CODES
                    cleanup = bool(operation.peer_id) or error_code in CREATE_CLEANUP_REQUIRED_CODES
                    profile.provisioning_status = (
                        "create_cleanup_pending" if cleanup else "create_failed"
                    )
                elif (
                    operation.operation_type == "update_peer"
                    and profile.provisioning_status not in {"deleting", "delete_failed"}
                ):
                    profile.provisioning_status = "update_failed"
                elif operation.operation_type == "delete_peer":
                    profile.provisioning_status = "delete_failed"
        return operation.status


async def prepare_create_cleanup(
    operation_id: int,
    *,
    worker_id: str,
    expected_attempt_number: int,
    peer_id: str,
    error_code: str,
    retryable: bool,
    session_factory=None,
) -> str:
    """Persist the exact peer to clean before releasing the CREATE lease."""
    async with _scope(session_factory) as session:
        operation, profile = await _lock_operation_and_profile(
            session, operation_id, worker_id, expected_attempt_number
        )
        operation.peer_id = peer_id
        if profile:
            profile.provisioning_status = "create_cleanup_pending"
            profile.last_sync_error = error_code
        should_retry = retryable and operation.attempts < operation.max_attempts
        operation.status = "retry" if should_retry else "dead"
        operation.next_attempt_at = func.now() + calculate_retry_delay(
            operation.attempts
        )
        operation.completed_at = None if should_retry else func.now()
        operation.locked_at = operation.locked_by = None
        operation.last_error_code = error_code
        return operation.status


async def finalize_create_cleanup(
    operation_id: int,
    *,
    worker_id: str,
    expected_attempt_number: int,
    reason: str,
    session_factory=None,
) -> None:
    async with _scope(session_factory) as session:
        operation, profile = await _lock_operation_and_profile(
            session, operation_id, worker_id, expected_attempt_number
        )
        if profile:
            profile.provisioning_status = "create_failed"
            profile.last_sync_error = reason
        _complete(operation, "cancelled", reason)


async def finalize_delete_success(
    operation_id: int,
    *,
    worker_id: str,
    expected_attempt_number: int,
    session_factory=None,
) -> None:
    async with _scope(session_factory) as session:
        operation, profile = await _lock_operation_and_profile(
            session, operation_id, worker_id, expected_attempt_number
        )
        if profile:
            await session.delete(profile)
        _complete(operation)


async def _ensure_current_update(session, profile):
    active = profile.desired_is_active
    from utils.datetime_helpers import is_permanent_subscription
    permanent = bool(
        profile.desired_expires_at and is_permanent_subscription(profile.desired_expires_at)
    )
    expires = (
        None
        if permanent or not active
        else int(profile.desired_expires_at.timestamp())
        if profile.desired_expires_at
        else None
    )
    server = await session.get(Server, profile.server_id)
    await enqueue_api_operation(
        session,
        operation_type="update_peer",
        idempotency_key=f"update-peer:{profile.id}:v{profile.desired_version}",
        server_id=profile.server_id,
        profile_id=profile.id,
        peer_id=profile.peer_id,
        server_name_snapshot=server.name if server else None,
        api_url_snapshot=server.api_url if server else None,
        api_key_snapshot=server.api_key if server else None,
        client_name=profile.client_name,
        payload={
            "desired_version": profile.desired_version,
            "status": "active" if active else "disabled",
            "expires_at": expires,
            "clear_expires_at": active and expires is None,
        },
    )


class _scope:
    def __init__(self, factory):
        self.factory = factory
        self.context = None

    async def __aenter__(self):
        if self.factory is None:
            self.context = session_scope()
        else:
            session = self.factory()
            self.context = session.begin()
            await self.context.__aenter__()
            self._session = session
            return session
        return await self.context.__aenter__()

    async def __aexit__(self, *args):
        result = await self.context.__aexit__(*args)
        if self.factory is not None:
            await self._session.close()
        return result
