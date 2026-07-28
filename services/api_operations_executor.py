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
        if exact:
            deleted = await client.delete_user_result(exact[0].id)
            if not deleted.ok:
                return await _fail(op, retryable=deleted.retryable,
                    code=_error_code(deleted), message="exact orphan cleanup failed")
    async with session_scope() as session:
        profile = await session.get(VPNProfile, op.profile_id)
        if not profile or profile.provisioning_status != "pending_create":
            return await mark_api_operation_cancelled(op.id, worker_id=op.locked_by,
                expected_attempt_number=op.attempt_number, reason="profile_not_pending_create")
        expires = profile.desired_expires_at
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
    async with session_scope() as session:
        profile = (await session.execute(select(VPNProfile).where(
            VPNProfile.id == op.profile_id).with_for_update())).scalar_one_or_none()
        if not profile or profile.provisioning_status != "pending_create":
            raise RuntimeError("profile changed during create finalization")
        profile.peer_id, profile.raw_config = created.id, created.config
        profile.provisioning_status = "active"
        profile.actual_is_active = profile.desired_is_active
        profile.actual_expires_at = profile.desired_expires_at
        profile.last_synced_at = datetime.now(timezone.utc)
        profile.last_sync_error = None
    await mark_api_operation_succeeded(op.id, worker_id=op.locked_by,
                                       expected_attempt_number=op.attempt_number)


async def _execute_update(op, client):
    async with session_scope() as session:
        profile = await session.get(VPNProfile, op.profile_id)
        if not profile or op.payload.get("desired_version") != profile.desired_version:
            return await mark_api_operation_cancelled(op.id, worker_id=op.locked_by,
                expected_attempt_number=op.attempt_number, reason="stale_desired_version")
        if profile.provisioning_status in {"pending_create", "deleting", "create_failed", "delete_failed"}:
            return await mark_api_operation_cancelled(op.id, worker_id=op.locked_by,
                expected_attempt_number=op.attempt_number, reason="profile_not_updatable")
    result = await client.update_client_result(op.peer_id, status=op.payload.get("status"),
        expires_at=op.payload.get("expires_at"),
        clear_expires_at=bool(op.payload.get("clear_expires_at")))
    if not result.ok:
        if not result.retryable:
            await _set_profile_failure(op, "update_failed", _error_code(result))
        return await _fail(op, retryable=result.retryable, code=_error_code(result))
    async with session_scope() as session:
        profile = await session.get(VPNProfile, op.profile_id, with_for_update=True)
        if profile:
            profile.actual_is_active = profile.desired_is_active
            profile.actual_expires_at = profile.desired_expires_at
            profile.provisioning_status = "active"
            profile.last_synced_at = datetime.now(timezone.utc)
            profile.last_sync_error = None
    await mark_api_operation_succeeded(op.id, worker_id=op.locked_by,
                                       expected_attempt_number=op.attempt_number)


async def _execute_delete(op, client):
    if not op.peer_id or not op.payload.get("managed_workflow"):
        return await _fail(op, retryable=False, code="unmanaged_delete_forbidden")
    result = await client.delete_user_result(op.peer_id)
    if not result.ok:
        await _set_profile_failure(op, "delete_failed", _error_code(result))
        return await _fail(op, retryable=result.retryable, code=_error_code(result))
    if op.profile_id:
        async with session_scope() as session:
            profile = await session.get(VPNProfile, op.profile_id, with_for_update=True)
            if profile:
                await session.delete(profile)
    await mark_api_operation_succeeded(op.id, worker_id=op.locked_by,
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
