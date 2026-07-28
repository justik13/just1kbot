import logging
from datetime import date

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.constants import AMNEZIA_PROTOCOL, DEVICE_DAILY_LIMIT
from database.models import APIOperation, Server, User, VPNProfile
from services.api_operations_queue import enqueue_api_operation, ensure_delete_operation
from services.audit_service import AuditService
from services.slots_cache import get_real_peer_count
from utils.admin import is_admin
from utils.datetime_helpers import is_expired, now_msk

logger = logging.getLogger(__name__)
RESERVING_STATUSES = ("pending_create", "active", "pending_update", "deleting", "delete_failed")

class DeviceCreationError(Exception): pass
class NoActiveSubscription(DeviceCreationError): pass
class DailyLimitExceeded(DeviceCreationError): pass
class DeviceLimitExceeded(DeviceCreationError): pass
class ServerUnavailable(DeviceCreationError): pass
class InvalidConfig(DeviceCreationError): pass
class DeviceStillCreating(DeviceCreationError): pass

async def close_redis() -> None:
    return None

def _is_same_day_msk(stored_date: date | None, today: date) -> bool:
    return stored_date == today if stored_date else False

class DeviceService:
    @staticmethod
    async def create_device(session: AsyncSession, user: User, server_id: int,
                            device_name: str) -> VPNProfile:
        endpoint = await session.get(Server, server_id)
        if not endpoint:
            raise ServerUnavailable("Invalid server")
        real_total = await get_real_peer_count(endpoint, force_refresh=True)
        if real_total < 0:
            raise ServerUnavailable("Cannot verify server slots")
        user = (await session.execute(select(User).where(User.id == user.id).with_for_update())).scalar_one()
        server = (await session.execute(select(Server).where(Server.id == server_id).with_for_update())).scalar_one_or_none()
        if not server or server.protocol != AMNEZIA_PROTOCOL or not server.is_active:
            raise ServerUnavailable("Invalid or disabled server")
        if user.is_banned or not user.subscription_end or is_expired(user.subscription_end):
            raise NoActiveSubscription("No active subscription")
        if not is_admin(user.telegram_id):
            today = now_msk().date()
            if not _is_same_day_msk(user.last_creation_date, today):
                user.device_creations_today, user.last_creation_date = 0, today
            if user.device_creations_today >= DEVICE_DAILY_LIMIT:
                raise DailyLimitExceeded("Daily limit exceeded")
        user_count = (await session.execute(select(func.count(VPNProfile.id)).where(
            VPNProfile.user_id == user.id, VPNProfile.provisioning_status.in_(RESERVING_STATUSES)))).scalar_one()
        if user_count >= user.device_limit:
            raise DeviceLimitExceeded("Device limit reached")
        server_count = (await session.execute(select(func.count(VPNProfile.id)).where(
            VPNProfile.server_id == server.id, VPNProfile.provisioning_status.in_(RESERVING_STATUSES)))).scalar_one()
        externalized = (await session.execute(select(func.count(VPNProfile.id)).where(
            VPNProfile.server_id == server.id, VPNProfile.peer_id.is_not(None)))).scalar_one()
        manual_estimate = max(0, real_total - externalized)
        if manual_estimate + server_count >= server.max_clients:
            raise ServerUnavailable("Server is full")
        profile = VPNProfile(user_id=user.id, server_id=server.id, device_name=device_name,
            peer_id=None, raw_config=None, provisioning_status="pending_create",
            desired_is_active=True, actual_is_active=None,
            desired_expires_at=user.subscription_end, desired_version=1, is_active=True)
        session.add(profile)
        await session.flush()
        profile.client_name = f"tg_{user.telegram_id}_p{profile.id}"
        await enqueue_api_operation(session, operation_type="create_peer",
            idempotency_key=f"create-peer:{profile.id}:v1", server_id=server.id,
            profile_id=profile.id, client_name=profile.client_name,
            server_name_snapshot=server.name, api_url_snapshot=server.api_url,
            api_key_snapshot=server.api_key,
            payload={"desired_version": 1})
        if not is_admin(user.telegram_id):
            user.device_creations_today += 1
        return profile

    @staticmethod
    async def delete_device(session: AsyncSession, profile: VPNProfile,
                            actor_id: int | None = None) -> bool:
        profile = (await session.execute(select(VPNProfile).where(
            VPNProfile.id == profile.id).with_for_update())).scalar_one_or_none()
        if not profile:
            return True
        if profile.provisioning_status == "pending_create":
            raise DeviceStillCreating("Устройство ещё создаётся")
        if profile.provisioning_status == "create_failed" and not profile.peer_id:
            await session.delete(profile)
            return True
        if not profile.peer_id:
            raise DeviceCreationError("Profile has no managed peer")
        server = await session.get(Server, profile.server_id)
        profile.provisioning_status = "deleting"
        await ensure_delete_operation(session,
            idempotency_key=f"delete-peer:{profile.id}:{profile.peer_id}",
            server_id=server.id if server else None, profile_id=profile.id,
            server_name_snapshot=server.name if server else None,
            api_url_snapshot=server.api_url if server else None,
            api_key_snapshot=server.api_key if server else None,
            peer_id=profile.peer_id, client_name=profile.client_name,
            audit_reason="device_delete")
        return True
