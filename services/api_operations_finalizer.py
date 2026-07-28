"""Atomic fenced database finalization for fulfilled API operations."""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select

from database.connection import session_scope
from database.models import APIOperation, Server, VPNProfile
from services.api_operations_queue import (
    APIOperationOwnershipError,
    enqueue_api_operation,
)


async def _locked(session, operation_id, worker_id, attempt):
    operation = (await session.execute(select(APIOperation).where(
        APIOperation.id == operation_id).with_for_update())).scalar_one_or_none()
    if not operation or operation.status != "processing" or operation.locked_by != worker_id or operation.attempts != attempt:
        raise APIOperationOwnershipError("operation is not leased by this worker")
    return operation


def _complete(operation, status="succeeded", reason=None):
    now = datetime.now(timezone.utc)
    operation.status = status
    operation.completed_at = now
    operation.updated_at = now
    operation.locked_at = None
    operation.locked_by = None
    operation.last_error_code = reason
    operation.last_error = None


async def finalize_create_success(operation_id: int, *, worker_id: str,
        expected_attempt_number: int, peer_id: str, raw_config: str,
        sent_desired_version: int, sent_is_active: bool,
        sent_expires_at: datetime | None, session_factory=None) -> None:
    async with _scope(session_factory) as session:
        operation = await _locked(session, operation_id, worker_id, expected_attempt_number)
        profile = (await session.execute(select(VPNProfile).where(
            VPNProfile.id == operation.profile_id).with_for_update())).scalar_one_or_none()
        if profile is None:
            _complete(operation, "cancelled", "profile_missing_after_create")
            return
        if profile.provisioning_status == "deleting":
            raise RuntimeError("create_cancel_requested")
        if profile.provisioning_status not in {"pending_create", "active"}:
            raise RuntimeError("profile_not_create_finalizable")
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


async def finalize_existing_create_success(operation_id: int, *, worker_id: str,
        expected_attempt_number: int, session_factory=None) -> None:
    """Repair an old split-commit window without touching the external peer."""
    async with _scope(session_factory) as session:
        operation = await _locked(session, operation_id, worker_id, expected_attempt_number)
        profile = (await session.execute(select(VPNProfile).where(
            VPNProfile.id == operation.profile_id).with_for_update())).scalar_one_or_none()
        if not profile or profile.provisioning_status != "active" or not profile.peer_id or not profile.raw_config:
            raise RuntimeError("profile is not a finalized create")
        _complete(operation)


async def finalize_create_cancelled(operation_id: int, *, worker_id: str,
        expected_attempt_number: int, reason: str, delete_profile: bool,
        session_factory=None) -> None:
    async with _scope(session_factory) as session:
        operation = await _locked(session, operation_id, worker_id, expected_attempt_number)
        profile = (await session.execute(select(VPNProfile).where(
            VPNProfile.id == operation.profile_id).with_for_update())).scalar_one_or_none()
        if profile and delete_profile:
            await session.delete(profile)
        _complete(operation, "cancelled", reason)


async def finalize_update_success(operation_id: int, *, worker_id: str,
        expected_attempt_number: int, sent_version: int, sent_is_active: bool,
        sent_expires_at: datetime | None, session_factory=None) -> None:
    async with _scope(session_factory) as session:
        operation = await _locked(session, operation_id, worker_id, expected_attempt_number)
        profile = (await session.execute(select(VPNProfile).where(
            VPNProfile.id == operation.profile_id).with_for_update())).scalar_one_or_none()
        if profile:
            profile.actual_is_active = sent_is_active
            profile.actual_expires_at = sent_expires_at
            profile.last_synced_at = datetime.now(timezone.utc)
            profile.last_sync_error = None
            profile.provisioning_status = "active" if profile.desired_version == sent_version else "pending_update"
        _complete(operation)


async def finalize_delete_success(operation_id: int, *, worker_id: str,
        expected_attempt_number: int, session_factory=None) -> None:
    async with _scope(session_factory) as session:
        operation = await _locked(session, operation_id, worker_id, expected_attempt_number)
        profile = (await session.execute(select(VPNProfile).where(
            VPNProfile.id == operation.profile_id).with_for_update())).scalar_one_or_none()
        if profile:
            await session.delete(profile)
        _complete(operation)


async def _ensure_current_update(session, profile):
    active = profile.desired_is_active
    permanent = bool(profile.desired_expires_at and profile.desired_expires_at.year >= 2100)
    expires = None if permanent or not active else int(profile.desired_expires_at.timestamp()) if profile.desired_expires_at else None
    server = await session.get(Server, profile.server_id)
    await enqueue_api_operation(session, operation_type="update_peer",
        idempotency_key=f"update-peer:{profile.id}:v{profile.desired_version}",
        server_id=profile.server_id, profile_id=profile.id, peer_id=profile.peer_id,
        server_name_snapshot=server.name if server else None,
        api_url_snapshot=server.api_url if server else None,
        api_key_snapshot=server.api_key if server else None,
        client_name=profile.client_name, payload={"desired_version": profile.desired_version,
        "status": "active" if active else "disabled", "expires_at": expires,
        "clear_expires_at": active and expires is None})


class _scope:
    def __init__(self, factory): self.factory = factory; self.context = None
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
