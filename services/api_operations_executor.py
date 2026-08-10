"""The sole production boundary allowed to mutate Amnezia peers."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from types import SimpleNamespace
from datetime import datetime, timezone


from database.connection import session_scope
from database.models import Server, VPNProfile
from services.amnezia_client import AmneziaClient
from services.api_operations_queue import (
    ClaimedAPIOperation, mark_api_operation_cancelled,
)
from services.api_operations_finalizer import (
    CreateCompensationRequired,
    finalize_create_cancelled, finalize_create_success,
    finalize_create_cleanup, finalize_operation_failure,
    finalize_delete_success, finalize_existing_create_success,
    finalize_update_success, prepare_create_cleanup,
)

logger = logging.getLogger(__name__)


class _ServerEndpointChanged(RuntimeError):
    """Stored operation identity no longer matches the current server."""


@dataclass(frozen=True)
class CleanupDecision:
    outcome: str
    peer_id: str | None = None


def _select_create_cleanup_target(*, clients, saved_peer_id: str | None,
        expected_client_name: str,
        allow_name_only_reconciliation: bool) -> CleanupDecision:
    exact = [item for item in clients if item.clientName == expected_client_name]
    if saved_peer_id:
        saved = next((item for item in clients if item.id == saved_peer_id), None)
        if saved:
            if saved.clientName != expected_client_name:
                return CleanupDecision("identity_mismatch")
            return CleanupDecision("delete", saved.id)
        return CleanupDecision("identity_mismatch" if exact else "clean")
    if not allow_name_only_reconciliation:
        return CleanupDecision("identity_mismatch" if exact else "clean")
    if len(exact) > 1:
        return CleanupDecision("multiple_exact")
    return CleanupDecision("delete", exact[0].id) if exact else CleanupDecision("clean")


def _error_code(result) -> str:
    return result.error_kind.value if result.error_kind else "unknown_error"


async def _fail(op, *, retryable: bool, code: str, message: str = ""):
    await finalize_operation_failure(op.id, worker_id=op.locked_by,
        expected_attempt_number=op.attempt_number, retryable=retryable,
        error_code=code, error_message=message or code)


async def _client(op):
    url = op.api_url_snapshot
    key = op.api_key_snapshot

    if not url or not key:
        return None

    # The durable operation always executes against its immutable snapshot.
    # The current Server row is used only to detect identity changes.
    if op.server_id:
        async with session_scope() as session:
            server = await session.get(Server, op.server_id)
            if server is not None and (
                server.api_url != url
                or server.api_key != key
            ):
                raise _ServerEndpointChanged

    # A deleted Server row does not erase the encrypted operation snapshot.
    return AmneziaClient(url, key)


async def _execute_create(op, client):
    if not op.client_name:
        return await _fail(op, retryable=False, code="configuration")
    async with session_scope() as session:
        profile = await session.get(VPNProfile, op.profile_id) if op.profile_id else None
        if profile is None:
            profile_state = "missing"
        else:
            profile_state = profile.provisioning_status
            sent_version = profile.desired_version
            sent_active = profile.desired_is_active
            sent_expires = profile.desired_expires_at
            existing_peer_id = profile.peer_id
            has_config = bool(profile.raw_config)
    needs_reconciliation = (
        profile_state in {"missing", "deleting", "create_cleanup_pending"}
        or op.attempt_number > 1 or bool(op.peer_id)
        or op.last_error_code in {"create_ambiguous_reconcile", "invalid_created_config_cleanup"}
    )
    # Every repeat is reconciled.  This also covers a crash after HTTP success
    # but before the database finalization commit.
    if needs_reconciliation:
        clients = await client.get_all_clients()
        if clients is None:
            return await _fail(op, retryable=True, code="create_reconciliation_unavailable")
        exact = [item for item in clients if item.clientName == op.client_name]
        if len(exact) > 1:
            logger.critical("duplicate exact client name for operation_id=%s", op.id)
            return await _fail(op, retryable=False, code="duplicate_exact_client_name")
        if profile_state == "create_cleanup_pending":
            decision = _select_create_cleanup_target(clients=clients,
                saved_peer_id=op.peer_id, expected_client_name=op.client_name,
                allow_name_only_reconciliation=not op.peer_id)
            if decision.outcome in {"identity_mismatch", "multiple_exact"}:
                return await _fail(op, retryable=False,
                    code="cleanup_peer_identity_mismatch")
            if decision.outcome == "delete":
                deleted = await client.delete_user_result(decision.peer_id)
                if not deleted.ok:
                    return await _fail(op, retryable=deleted.retryable or deleted.ambiguous,
                        code="invalid_created_config_cleanup")
            return await finalize_create_cleanup(op.id, worker_id=op.locked_by,
                expected_attempt_number=op.attempt_number,
                reason="invalid_created_config_cleaned")
        if profile_state == "active" and exact and exact[0].id == existing_peer_id and has_config:
            return await finalize_existing_create_success(op.id, worker_id=op.locked_by,
                expected_attempt_number=op.attempt_number)
        if profile_state in {"missing", "deleting"}:
            decision = _select_create_cleanup_target(clients=clients,
                saved_peer_id=op.peer_id, expected_client_name=op.client_name,
                allow_name_only_reconciliation=(op.attempt_number > 1 or bool(op.last_error_code)))
            if decision.outcome in {"identity_mismatch", "multiple_exact"}:
                return await _fail(op, retryable=False,
                    code="cleanup_peer_identity_mismatch")
            if decision.outcome == "delete":
                deleted = await client.delete_user_result(decision.peer_id)
                if not deleted.ok:
                    return await _fail(op, retryable=deleted.retryable, code=_error_code(deleted))
            return await finalize_create_cancelled(op.id, worker_id=op.locked_by,
                expected_attempt_number=op.attempt_number,
                reason=f"profile_{profile_state}", delete_profile=True)
        if profile_state != "pending_create":
            return await mark_api_operation_cancelled(op.id, worker_id=op.locked_by,
                expected_attempt_number=op.attempt_number, reason="profile_not_pending_create")
        if exact:
            decision = _select_create_cleanup_target(clients=clients,
                saved_peer_id=op.peer_id, expected_client_name=op.client_name,
                allow_name_only_reconciliation=True)
            if decision.outcome != "delete":
                return await _fail(op, retryable=False,
                    code="cleanup_peer_identity_mismatch")
            deleted = await client.delete_user_result(decision.peer_id)
            if not deleted.ok:
                return await _fail(op, retryable=deleted.retryable,
                    code=_error_code(deleted), message="exact orphan cleanup failed")
    if profile_state in {"missing", "deleting"}:
        return await finalize_create_cancelled(op.id, worker_id=op.locked_by,
            expected_attempt_number=op.attempt_number, reason=f"profile_{profile_state}",
            delete_profile=True)
    if not sent_active:
        return await finalize_create_cancelled(op.id, worker_id=op.locked_by,
            expected_attempt_number=op.attempt_number,
            reason="desired_access_inactive", delete_profile=True)
    if profile_state != "pending_create":
        return await mark_api_operation_cancelled(op.id, worker_id=op.locked_by,
            expected_attempt_number=op.attempt_number, reason="profile_not_pending_create")
    from utils.datetime_helpers import is_permanent_subscription
    expires = None if sent_expires and is_permanent_subscription(sent_expires) else sent_expires
    result = await client.create_user_result(op.client_name,
        int(expires.timestamp()) if expires else None)
    if not result.ok:
        code = "create_ambiguous_reconcile" if result.ambiguous else _error_code(result)
        return await _fail(op, retryable=result.retryable or result.ambiguous, code=code)
    created = result.value
    valid = bool(created and created.id and created.config and str(created.config).strip())
    if not valid:
        cleanup = None
        if created and created.id:
            decision = _select_create_cleanup_target(
                clients=[SimpleNamespace(id=created.id, clientName=op.client_name)],
                saved_peer_id=created.id, expected_client_name=op.client_name,
                allow_name_only_reconciliation=False,
            )
            cleanup = await client.delete_user_result(decision.peer_id)
        if cleanup and cleanup.ok:
            return await finalize_create_cleanup(op.id, worker_id=op.locked_by,
                expected_attempt_number=op.attempt_number,
                reason="invalid_created_config_cleaned")
        if created and created.id:
            return await prepare_create_cleanup(op.id, worker_id=op.locked_by,
                expected_attempt_number=op.attempt_number, peer_id=created.id,
                error_code="invalid_created_config_cleanup",
                retryable=bool(cleanup and (cleanup.retryable or cleanup.ambiguous)))
        return await _fail(op, retryable=False, code="invalid_created_config")
    try:
        await finalize_create_success(op.id, worker_id=op.locked_by,
            expected_attempt_number=op.attempt_number, peer_id=created.id,
            raw_config=created.config, sent_desired_version=sent_version,
            sent_is_active=True, sent_expires_at=expires)
    except (RuntimeError, CreateCompensationRequired) as error:
        compensation = isinstance(error, CreateCompensationRequired)
        if not compensation and str(error) != "create_cancel_requested":
            raise
        decision = _select_create_cleanup_target(
            clients=[SimpleNamespace(id=created.id, clientName=op.client_name)],
            saved_peer_id=created.id, expected_client_name=op.client_name,
            allow_name_only_reconciliation=False,
        )
        cleanup = await client.delete_user_result(decision.peer_id)
        if not cleanup.ok:
            return await prepare_create_cleanup(op.id, worker_id=op.locked_by,
                expected_attempt_number=op.attempt_number, peer_id=created.id,
                error_code="create_compensation_required",
                retryable=cleanup.retryable or cleanup.ambiguous)
        await finalize_create_cancelled(op.id, worker_id=op.locked_by,
            expected_attempt_number=op.attempt_number,
            reason="create_compensated_after_post", delete_profile=True)


async def _execute_update(op, client):
    async with session_scope() as session:
        profile = await session.get(VPNProfile, op.profile_id)
        if not profile or op.payload.get("desired_version") != profile.desired_version:
            return await mark_api_operation_cancelled(op.id, worker_id=op.locked_by,
                expected_attempt_number=op.attempt_number, reason="stale_desired_version")
        if profile.provisioning_status in {"pending_create", "create_failed", "create_cleanup_pending"}:
            return await mark_api_operation_cancelled(op.id, worker_id=op.locked_by,
                expected_attempt_number=op.attempt_number, reason="profile_not_updatable")
    sent_version = op.payload.get("desired_version")
    sent_status = op.payload.get("status")
    sent_expires = op.payload.get("expires_at")
    sent_clear = bool(op.payload.get("clear_expires_at"))
    result = await client.update_client_result(op.peer_id, status=sent_status,
        expires_at=sent_expires, clear_expires_at=sent_clear)
    if not result.ok:
        return await _fail(op, retryable=result.retryable, code=_error_code(result))
    sent_expires_dt = (datetime.fromtimestamp(sent_expires, timezone.utc)
                       if sent_expires is not None else None)
    await finalize_update_success(op.id, worker_id=op.locked_by,
        expected_attempt_number=op.attempt_number, sent_version=sent_version,
        sent_is_active=sent_status != "disabled", sent_expires_at=sent_expires_dt,
        sent_clear_expires_at=sent_clear)


async def _execute_delete(op, client):
    if not op.peer_id or not op.payload.get("managed_workflow"):
        return await _fail(op, retryable=False, code="unmanaged_delete_forbidden")
    result = await client.delete_user_result(op.peer_id)
    if not result.ok:
        return await _fail(op, retryable=result.retryable, code=_error_code(result))
    await finalize_delete_success(op.id, worker_id=op.locked_by,
                                  expected_attempt_number=op.attempt_number)


async def execute_claimed_api_operation(
    operation: ClaimedAPIOperation,
) -> None:
    try:
        client = await _client(operation)
    except _ServerEndpointChanged:
        logger.error(
            "Amnezia operation endpoint changed: "
            "operation_id=%s server_id=%s operation_type=%s",
            operation.id,
            operation.server_id,
            operation.operation_type,
        )
        return await _fail(
            operation,
            retryable=False,
            code="server_endpoint_changed",
            message=(
                "operation endpoint snapshot does not match "
                "the current server identity"
            ),
        )

    if client is None:
        return await _fail(
            operation,
            retryable=False,
            code="configuration",
        )

    if operation.operation_type == "create_peer":
        return await _execute_create(operation, client)
    if operation.operation_type == "update_peer":
        return await _execute_update(operation, client)
    if operation.operation_type == "delete_peer":
        return await _execute_delete(operation, client)

    await _fail(
        operation,
        retryable=False,
        code="unknown_operation",
    )
