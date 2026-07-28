"""The sole production boundary allowed to mutate Amnezia peers."""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import select

from database.connection import session_scope
from database.models import Server, VPNProfile
from services.amnezia_client import AmneziaClient
from services.api_operations_queue import (
    ClaimedAPIOperation, mark_api_operation_cancelled,
    mark_api_operation_failed, mark_api_operation_succeeded,
)
from services.api_operations_finalizer import (
    finalize_create_cancelled, finalize_create_success,
    finalize_delete_success, finalize_existing_create_success,
    finalize_update_success,
)
from utils.vpn_parser import build_conf_file, is_valid_vpn_uri

logger = logging.getLogger(__name__)


def _error_code(result) -> str:
    return result.error_kind.value if result.error_kind else "unknown_error"


async def _fail(op, *, retryable: bool, code: str, message: str = ""):
    await mark_api_operation_failed(op.id, worker_id=op.locked_by,
        expected_attempt_number=op.attempt_number, retryable=retryable,
        error_code=code, error_message=message or code)


async def _client(op):
    async with session_scope() as session:
        server = await session.get(Server, op.server_id) if op.server_id else None
        url = server.api_url if server else op.api_url_snapshot
        key = server.api_key if server else op.api_key_snapshot
    return AmneziaClient(url, key) if url and key else None


async def _set_profile_failure(op, status: str, error: str):
    if not op.profile_id:
        return
    async with session_scope() as session:
        profile = await session.get(VPNProfile, op.profile_id, with_for_update=True)
        if profile:
            profile.provisioning_status = status
            profile.last_sync_error = error[:2000]


async def _execute_create(op, client):
    if not op.profile_id or not op.client_name:
        return await _fail(op, retryable=False, code="configuration")
    async with session_scope() as session:
        profile = await session.get(VPNProfile, op.profile_id)
        if profile is None:
            profile_state = "missing"
        else:
            profile_state = profile.provisioning_status
            sent_version = profile.desired_version
            sent_active = profile.desired_is_active
            sent_expires = profile.desired_expires_at
            existing_peer_id = profile.peer_id
            has_config = bool(profile.raw_config)
    # Every repeat is reconciled.  This also covers a crash after HTTP success
    # but before the database finalization commit.
    if op.attempt_number > 1 or op.last_error_code == "create_ambiguous_reconcile":
        clients = await client.get_all_clients()
        if clients is None:
            return await _fail(op, retryable=True, code="create_reconciliation_unavailable")
        exact = [item for item in clients if item.clientName == op.client_name]
        if len(exact) > 1:
            logger.critical("duplicate exact client name for operation_id=%s", op.id)
            return await _fail(op, retryable=False, code="duplicate_exact_client_name")
        if profile_state == "active" and exact and exact[0].id == existing_peer_id and has_config:
            return await finalize_existing_create_success(op.id, worker_id=op.locked_by,
                expected_attempt_number=op.attempt_number)
        if profile_state in {"missing", "deleting"}:
            if exact:
                deleted = await client.delete_user_result(exact[0].id)
                if not deleted.ok:
                    return await _fail(op, retryable=deleted.retryable, code=_error_code(deleted))
            return await finalize_create_cancelled(op.id, worker_id=op.locked_by,
                expected_attempt_number=op.attempt_number,
                reason=f"profile_{profile_state}", delete_profile=True)
        if profile_state != "pending_create":
            return await mark_api_operation_cancelled(op.id, worker_id=op.locked_by,
                expected_attempt_number=op.attempt_number, reason="profile_not_pending_create")
        if exact:
            deleted = await client.delete_user_result(exact[0].id)
            if not deleted.ok:
                return await _fail(op, retryable=deleted.retryable,
                    code=_error_code(deleted), message="exact orphan cleanup failed")
    if profile_state in {"missing", "deleting"}:
        return await finalize_create_cancelled(op.id, worker_id=op.locked_by,
            expected_attempt_number=op.attempt_number, reason=f"profile_{profile_state}",
            delete_profile=True)
    if profile_state != "pending_create":
        return await mark_api_operation_cancelled(op.id, worker_id=op.locked_by,
            expected_attempt_number=op.attempt_number, reason="profile_not_pending_create")
    expires = None if sent_expires and sent_expires.year >= 2100 else sent_expires
    result = await client.create_user_result(op.client_name,
        int(expires.timestamp()) if expires else None)
    if not result.ok:
        code = "create_ambiguous_reconcile" if result.ambiguous else _error_code(result)
        return await _fail(op, retryable=result.retryable or result.ambiguous, code=code)
    created = result.value
    valid = bool(created and created.id and is_valid_vpn_uri(created.config)
                 and build_conf_file(created.config))
    if not valid:
        cleanup = await client.delete_user_result(created.id) if created and created.id else None
        await _set_profile_failure(op, "create_failed", "invalid configuration from API")
        return await _fail(op, retryable=bool(cleanup and not cleanup.ok and cleanup.retryable),
                           code="invalid_created_config")
    try:
        await finalize_create_success(op.id, worker_id=op.locked_by,
            expected_attempt_number=op.attempt_number, peer_id=created.id,
            raw_config=created.config, sent_desired_version=sent_version,
            sent_is_active=sent_active, sent_expires_at=expires)
    except RuntimeError as error:
        if str(error) != "create_cancel_requested":
            raise
        cleanup = await client.delete_user_result(created.id)
        if not cleanup.ok:
            return await _fail(op, retryable=cleanup.retryable, code=_error_code(cleanup))
        await finalize_create_cancelled(op.id, worker_id=op.locked_by,
            expected_attempt_number=op.attempt_number,
            reason="create_cancelled_after_post", delete_profile=True)


async def _execute_update(op, client):
    async with session_scope() as session:
        profile = await session.get(VPNProfile, op.profile_id)
        if not profile or op.payload.get("desired_version") != profile.desired_version:
            return await mark_api_operation_cancelled(op.id, worker_id=op.locked_by,
                expected_attempt_number=op.attempt_number, reason="stale_desired_version")
        if profile.provisioning_status in {"pending_create", "deleting", "create_failed", "delete_failed"}:
            return await mark_api_operation_cancelled(op.id, worker_id=op.locked_by,
                expected_attempt_number=op.attempt_number, reason="profile_not_updatable")
    sent_version = op.payload.get("desired_version")
    sent_status = op.payload.get("status")
    sent_expires = op.payload.get("expires_at")
    sent_clear = bool(op.payload.get("clear_expires_at"))
    result = await client.update_client_result(op.peer_id, status=sent_status,
        expires_at=sent_expires, clear_expires_at=sent_clear)
    if not result.ok:
        if not result.retryable:
            await _set_profile_failure(op, "update_failed", _error_code(result))
        return await _fail(op, retryable=result.retryable, code=_error_code(result))
    sent_expires_dt = (datetime.fromtimestamp(sent_expires, timezone.utc)
                       if sent_expires is not None else None)
    await finalize_update_success(op.id, worker_id=op.locked_by,
        expected_attempt_number=op.attempt_number, sent_version=sent_version,
        sent_is_active=sent_status != "disabled", sent_expires_at=sent_expires_dt)


async def _execute_delete(op, client):
    if not op.peer_id or not op.payload.get("managed_workflow"):
        return await _fail(op, retryable=False, code="unmanaged_delete_forbidden")
    result = await client.delete_user_result(op.peer_id)
    if not result.ok:
        await _set_profile_failure(op, "delete_failed", _error_code(result))
        return await _fail(op, retryable=result.retryable, code=_error_code(result))
    await finalize_delete_success(op.id, worker_id=op.locked_by,
                                  expected_attempt_number=op.attempt_number)


async def execute_claimed_api_operation(operation: ClaimedAPIOperation) -> None:
    client = await _client(operation)
    if client is None:
        return await _fail(operation, retryable=False, code="configuration")
    if operation.operation_type == "create_peer":
        return await _execute_create(operation, client)
    if operation.operation_type == "update_peer":
        return await _execute_update(operation, client)
    if operation.operation_type == "delete_peer":
        return await _execute_delete(operation, client)
    await _fail(operation, retryable=False, code="unknown_operation")
