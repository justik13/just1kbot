import logging
import re
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from bot.constants import AMNEZIA_PROTOCOL, DEVICE_DAILY_LIMIT
from database.models import Server, User, VPNProfile
from services.amnezia_capacity import (
    ServerAtCapacity,
    ServerCapacityUnavailable,
    ensure_server_capacity,
)
from services.api_operations_queue import enqueue_api_operation, ensure_delete_operation
from services.slots_cache import ServerPeerSnapshot
from utils.admin import is_admin
from utils.datetime_helpers import is_expired, now_msk

logger = logging.getLogger(__name__)
RESERVING_STATUSES = (
    "pending_create",
    "active",
    "pending_update",
    "deleting",
    "delete_failed",
    "create_cleanup_pending",
)


class DeviceCreationError(Exception):
    pass


class NoActiveSubscription(DeviceCreationError):
    pass


class DailyLimitExceeded(DeviceCreationError):
    pass


class DeviceLimitExceeded(DeviceCreationError):
    pass


class ServerUnavailable(DeviceCreationError):
    pass


class InvalidConfig(DeviceCreationError):
    pass


class DeviceStillCreating(DeviceCreationError):
    pass


class DuplicateDeviceName(DeviceCreationError):
    pass


async def close_redis() -> None:
    return None


def _is_same_day_msk(stored_date: date | None, today: date) -> bool:
    return stored_date == today if stored_date else False


class DeviceService:
    @staticmethod
    async def create_device(
        session: AsyncSession,
        *,
        user_id: int,
        server_id: int,
        device_name: str,
        snapshot: ServerPeerSnapshot,
    ) -> VPNProfile:
        if snapshot.server_id != server_id or datetime.now(
            timezone.utc
        ) - snapshot.captured_at > timedelta(minutes=5):
            raise ServerUnavailable("Server capacity snapshot is stale")
        user = (
            await session.execute(
                select(User).where(User.id == user_id).with_for_update()
            )
        ).scalar_one()
        server = (
            await session.execute(
                select(Server).where(Server.id == server_id).with_for_update()
            )
        ).scalar_one_or_none()
        if not server or server.protocol != AMNEZIA_PROTOCOL or not server.is_active:
            raise ServerUnavailable("Invalid or disabled server")
        if (
            user.is_banned
            or not user.subscription_end
            or is_expired(user.subscription_end)
        ):
            raise NoActiveSubscription("No active subscription")
        duplicate = (
            await session.execute(
                select(VPNProfile.id).where(
                    VPNProfile.user_id == user.id,
                    VPNProfile.server_id == server.id,
                    func.lower(VPNProfile.device_name) == device_name.lower(),
                )
            )
        ).scalar_one_or_none()
        if duplicate:
            raise DuplicateDeviceName("Duplicate device name")
        if not is_admin(user.telegram_id):
            today = now_msk().date()
            if not _is_same_day_msk(user.last_creation_date, today):
                user.device_creations_today, user.last_creation_date = 0, today
            if user.device_creations_today >= DEVICE_DAILY_LIMIT:
                raise DailyLimitExceeded("Daily limit exceeded")
        user_count = (
            await session.execute(
                select(func.count(VPNProfile.id)).where(
                    VPNProfile.user_id == user.id,
                    VPNProfile.provisioning_status.in_(RESERVING_STATUSES),
                )
            )
        ).scalar_one()
        if user_count >= user.device_limit:
            raise DeviceLimitExceeded("Device limit reached")
        server_count = (
            await session.execute(
                select(func.count(VPNProfile.id)).where(
                    VPNProfile.server_id == server.id,
                    VPNProfile.provisioning_status.in_(RESERVING_STATUSES),
                )
            )
        ).scalar_one()
        bot_peer_ids = frozenset(
            (
                await session.execute(
                    select(VPNProfile.peer_id).where(
                        VPNProfile.server_id == server.id,
                        VPNProfile.peer_id.is_not(None),
                    )
                )
            )
            .scalars()
            .all()
        )
        manual_peer_ids = snapshot.peer_ids - bot_peer_ids
        if len(manual_peer_ids) + server_count >= server.max_clients:
            raise ServerUnavailable("Server is full")

        try:
            await ensure_server_capacity(
                api_url=server.api_url,
                api_key=server.api_key,
                max_clients=server.max_clients,
            )
        except ServerAtCapacity as exc:
            raise ServerUnavailable("Server is full") from exc
        except ServerCapacityUnavailable as exc:
            raise ServerUnavailable("Unable to verify server capacity") from exc

        profile = VPNProfile(
            user_id=user.id,
            server_id=server.id,
            device_name=device_name,
            peer_id=None,
            raw_config=None,
            provisioning_status="pending_create",
            desired_is_active=True,
            actual_is_active=None,
            desired_expires_at=user.subscription_end,
            desired_version=1,
            is_active=True,
        )
        session.add(profile)

        try:
            await session.flush()
        except IntegrityError as e:
            await session.rollback()
            error_str = str(e.orig).lower() if e.orig else ""
            if (
                "duplicate" in error_str
                or "unique" in error_str
                or "uq_vpn_profiles" in error_str
            ):
                raise DuplicateDeviceName(
                    "Device name already exists on this server"
                ) from e
            raise DeviceCreationError("Database integrity error") from e

        m = re.search(r'#(\d+)$', profile.device_name)
        slot_suffix = f"_n{m.group(1)}" if m else ""
        profile.client_name = f"tg_{user.telegram_id}_p{profile.id}{slot_suffix}"
        await enqueue_api_operation(
            session,
            operation_type="create_peer",
            idempotency_key=f"create-peer:{profile.id}:v1",
            server_id=server.id,
            profile_id=profile.id,
            client_name=profile.client_name,
            server_name_snapshot=server.name,
            api_url_snapshot=server.api_url,
            api_key_snapshot=server.api_key,
            payload={"desired_version": 1},
        )
        if not is_admin(user.telegram_id):
            user.device_creations_today += 1
        return profile

    @staticmethod
    async def delete_device(
        session: AsyncSession,
        profile: VPNProfile,
        actor_id: int | None = None,
        force: bool = False,
    ) -> bool:
        profile = (
            await session.execute(
                select(VPNProfile).where(VPNProfile.id == profile.id).with_for_update()
            )
        ).scalar_one_or_none()
        if not profile:
            return True
        if profile.provisioning_status == "pending_create" and not force:
            raise DeviceStillCreating("Устройство ещё создаётся")
        if not profile.peer_id or force:
            if profile.peer_id:
                server = await session.get(Server, profile.server_id)
                try:
                    await ensure_delete_operation(
                        session,
                        idempotency_key=f"delete-peer:{profile.id}:{profile.peer_id}",
                        server_id=server.id if server else None,
                        profile_id=profile.id,
                        server_name_snapshot=server.name if server else None,
                        api_url_snapshot=server.api_url if server else None,
                        api_key_snapshot=server.api_key if server else None,
                        peer_id=profile.peer_id,
                        client_name=profile.client_name,
                        audit_reason="device_delete_force" if force else "device_delete",
                    )
                except Exception as exc:
                    logger.warning("Failed to enqueue background delete_peer operation: %s", exc)
            session.delete(profile)
            return True

        server = await session.get(Server, profile.server_id)
        profile.provisioning_status = "deleting"
        await ensure_delete_operation(
            session,
            idempotency_key=f"delete-peer:{profile.id}:{profile.peer_id}",
            server_id=server.id if server else None,
            profile_id=profile.id,
            server_name_snapshot=server.name if server else None,
            api_url_snapshot=server.api_url if server else None,
            api_key_snapshot=server.api_key if server else None,
            peer_id=profile.peer_id,
            client_name=profile.client_name,
            audit_reason="device_delete",
        )
        if not server:
            session.delete(profile)
        return True