import logging
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from database.models import APIOperation, Server, VPNProfile
from services.api_operations_queue import ensure_delete_operation
logger = logging.getLogger(__name__)

class ProfileDeletionService:
    @staticmethod
    async def delete_profiles_for_user(session: AsyncSession, user_id: int, *, reason: str, background: bool=True) -> int:
        profiles = list((await session.execute(select(VPNProfile).where(VPNProfile.user_id == user_id).with_for_update())).scalars())
        return await ProfileDeletionService._delete_profiles(session, profiles, reason=reason, background=background)
    @staticmethod
    async def delete_profiles_list(session: AsyncSession, profiles: list, *, reason: str, background: bool=True) -> int:
        profile_ids = [profile.id for profile in profiles if getattr(profile, "id", None)]
        if not profile_ids:
            return 0
        current = list((await session.execute(select(VPNProfile).where(
            VPNProfile.id.in_(profile_ids)).with_for_update())).scalars().all())
        return await ProfileDeletionService._delete_profiles(session, current, reason=reason, background=background)
    @staticmethod
    async def _delete_profiles(session, profiles, *, reason, background):
        count = 0
        for profile in profiles:
            if profile.provisioning_status == "create_cleanup_pending":
                create = (await session.execute(select(APIOperation).where(
                    APIOperation.profile_id == profile.id,
                    APIOperation.operation_type == "create_peer").with_for_update())).scalar_one_or_none()
                if create and create.status in {"dead", "cancelled"}:
                    create.status = "retry"
                    create.attempts = 0
                    create.completed_at = None
                    create.last_error_code = "cleanup_requeued_by_deletion"
                count += 1
                continue
            if profile.provisioning_status == "pending_create":
                profile.desired_is_active = False
                profile.is_active = False
                profile.provisioning_status = "deleting"
                create = (await session.execute(select(APIOperation).where(
                    APIOperation.profile_id == profile.id,
                    APIOperation.operation_type == "create_peer").with_for_update())).scalar_one_or_none()
                if create and create.status in {"pending", "retry"}:
                    create.status = "cancelled"
                    create.completed_at = __import__("utils.datetime_helpers", fromlist=["now_utc"]).now_utc()
                    create.locked_at = create.locked_by = None
                    create.last_error_code = "create_cancelled_by_deletion"
                    await session.delete(profile)
                elif create and create.status in {"dead", "cancelled"} and not create.peer_id:
                    await session.delete(profile)
                # A processing CREATE observes `deleting` before/after POST and
                # owns the exact-peer cleanup and local profile removal.
                count += 1
                continue
            if profile.provisioning_status == "create_failed" and not profile.peer_id:
                await session.delete(profile); count += 1; continue
            if not profile.peer_id:
                continue
            server = await session.get(Server, profile.server_id)
            profile.provisioning_status = "deleting"
            await ensure_delete_operation(session,
                idempotency_key=f"delete-peer:{profile.id}:{profile.peer_id}",
                server_id=server.id if server else None, profile_id=profile.id,
                server_name_snapshot=server.name if server else None,
                api_url_snapshot=server.api_url if server else None,
                api_key_snapshot=server.api_key if server else None,
                peer_id=profile.peer_id, client_name=profile.client_name,
                audit_reason=reason)
            count += 1
        await session.flush()
        return count
