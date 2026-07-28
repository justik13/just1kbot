import logging
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from database.models import Server, VPNProfile
from services.api_operations_queue import enqueue_api_operation
logger = logging.getLogger(__name__)

class ProfileDeletionService:
    @staticmethod
    async def delete_profiles_for_user(session: AsyncSession, user_id: int, *, reason: str, background: bool=True) -> int:
        profiles = list((await session.execute(select(VPNProfile).where(VPNProfile.user_id == user_id).with_for_update())).scalars())
        return await ProfileDeletionService._delete_profiles(session, profiles, reason=reason, background=background)
    @staticmethod
    async def delete_profiles_list(session: AsyncSession, profiles: list, *, reason: str, background: bool=True) -> int:
        return await ProfileDeletionService._delete_profiles(session, profiles, reason=reason, background=background)
    @staticmethod
    async def _delete_profiles(session, profiles, *, reason, background):
        count = 0
        for profile in profiles:
            if profile.provisioning_status == "pending_create":
                logger.warning("pending profile retained during bulk deletion profile_id=%s", profile.id)
                continue
            if profile.provisioning_status == "create_failed" and not profile.peer_id:
                await session.delete(profile); count += 1; continue
            if not profile.peer_id:
                continue
            server = await session.get(Server, profile.server_id)
            profile.provisioning_status = "deleting"
            await enqueue_api_operation(session, operation_type="delete_peer",
                idempotency_key=f"delete-peer:{profile.id}:{profile.peer_id}",
                server_id=server.id if server else None, profile_id=profile.id,
                server_name_snapshot=server.name if server else None,
                api_url_snapshot=server.api_url if server else None,
                api_key_snapshot=server.api_key if server else None,
                peer_id=profile.peer_id, client_name=profile.client_name,
                payload={"managed_workflow": True, "reason": reason})
            count += 1
        await session.flush()
        return count
