import logging
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from database.models import APIOperation, VPNProfile
from services.api_operations_queue import (
    classify_create_side_effect_risk,
    ensure_delete_operation,
    resolve_profile_endpoint_snapshot,
)
from utils.datetime_helpers import now_utc

logger = logging.getLogger(__name__)


class ProfileDeletionService:
    @staticmethod
    async def delete_profiles_for_user(
        session: AsyncSession, user_id: int, *, reason: str, background: bool = True
    ) -> int:
        profiles = list(
            (
                await session.execute(
                    select(VPNProfile)
                    .where(VPNProfile.user_id == user_id)
                    .order_by(VPNProfile.id)
                    .with_for_update()
                )
            ).scalars()
        )
        return await ProfileDeletionService._delete_profiles(
            session, profiles, reason=reason, background=background
        )

    @staticmethod
    async def delete_profiles_list(
        session: AsyncSession, profiles: list, *, reason: str, background: bool = True
    ) -> int:
        profile_ids = [
            profile.id for profile in profiles if getattr(profile, "id", None)
        ]
        if not profile_ids:
            return 0
        current = list(
            (
                await session.execute(
                    select(VPNProfile)
                    .where(VPNProfile.id.in_(profile_ids))
                    .order_by(VPNProfile.id)
                    .with_for_update()
                )
            )
            .scalars()
            .all()
        )
        return await ProfileDeletionService._delete_profiles(
            session, current, reason=reason, background=background
        )

    @staticmethod
    async def _delete_profiles(session, profiles, *, reason, background):
        count = 0
        for profile in profiles:
            if profile.provisioning_status == "create_cleanup_pending":
                await session.execute(
                    update(APIOperation)
                    .where(
                        APIOperation.profile_id == profile.id,
                        APIOperation.operation_type == "create_peer",
                        APIOperation.status.in_(["dead", "cancelled"]),
                    )
                    .values(
                        status="retry",
                        attempts=0,
                        next_attempt_at=func.now(),
                        completed_at=None,
                        locked_at=None,
                        locked_by=None,
                        updated_at=func.now(),
                        last_error_code="cleanup_requeued_by_deletion",
                        last_error=f"cleanup requeued: {reason}"[:2000],
                    )
                )
                count += 1
                continue
            if profile.provisioning_status == "pending_create":
                profile.desired_is_active = False
                profile.is_active = False
                create = (
                    await session.execute(
                        select(APIOperation)
                        .where(
                            APIOperation.profile_id == profile.id,
                            APIOperation.operation_type == "create_peer",
                        )
                    )
                ).scalar_one_or_none()
                risk = (
                    classify_create_side_effect_risk(create)
                    if create
                    else "may_have_created_peer"
                )
                if create and risk == "never_started":
                    cancel_res = await session.execute(
                        update(APIOperation)
                        .where(
                            APIOperation.id == create.id,
                            APIOperation.status == "pending",
                            APIOperation.attempts == 0,
                        )
                        .values(
                            status="cancelled",
                            completed_at=now_utc(),
                            locked_at=None,
                            locked_by=None,
                            last_error_code="create_cancelled_by_deletion",
                            updated_at=func.now(),
                        )
                    )
                    if cancel_res.rowcount > 0:
                        await session.delete(profile)
                    else:
                        profile.provisioning_status = "deleting"
                else:
                    profile.provisioning_status = (
                        "create_cleanup_pending"
                        if risk == "cleanup_required"
                        else "deleting"
                    )
                    if create:
                        await session.execute(
                            update(APIOperation)
                            .where(
                                APIOperation.id == create.id,
                                APIOperation.status.in_(["dead", "cancelled", "retry"]),
                            )
                            .values(
                                status="retry",
                                attempts=0,
                                next_attempt_at=func.now(),
                                completed_at=None,
                                locked_at=None,
                                locked_by=None,
                                updated_at=func.now(),
                                last_error=f"cleanup requeued: {reason}"[:2000],
                            )
                        )
                # A processing CREATE observes `deleting` before/after POST and
                # owns the exact-peer cleanup and local profile removal.
                count += 1
                continue
            if profile.provisioning_status == "create_failed" and not profile.peer_id:
                await session.delete(profile)
                count += 1
                continue
            if not profile.peer_id:
                await session.delete(profile)
                count += 1
                continue
            server_id, server_name, api_url, api_key = await resolve_profile_endpoint_snapshot(session, profile)
            profile.provisioning_status = "deleting"
            await ensure_delete_operation(
                session,
                idempotency_key=f"delete-peer:{profile.id}:{profile.peer_id}",
                server_id=server_id,
                profile_id=profile.id,
                server_name_snapshot=server_name,
                api_url_snapshot=api_url,
                api_key_snapshot=api_key,
                peer_id=profile.peer_id,
                client_name=profile.client_name,
                audit_reason=reason,
            )
            count += 1
        await session.flush()
        return count
