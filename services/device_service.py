import logging
import re
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from config.constants import AMNEZIA_PROTOCOL, DEVICE_DAILY_LIMIT
from database.models import APIOperation, Server, User, VPNProfile
from database.repositories.profiles_repo import ALLOWED_DELETE_STATES
from services.amnezia_capacity import (
    ServerAtCapacity,
    ServerCapacityUnavailable,
    ensure_server_capacity,
)
from services.api_operations_queue import (
    classify_create_side_effect_risk,
    enqueue_api_operation,
    ensure_delete_operation,
    resolve_profile_endpoint_snapshot,
)
from services.audit_service import AuditService
from services.slots_cache import ServerPeerSnapshot
from utils.admin import is_admin
from utils.datetime_helpers import is_expired, now_msk, now_utc

logger = logging.getLogger(__name__)
RESERVING_STATUSES = (
    "pending_create",
    "active",
    "pending_update",
    "update_failed",
    "create_cleanup_pending",
    "delete_failed",
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


def _is_same_day_msk(stored_date: date | None, today: date) -> bool:
    return stored_date == today if stored_date else False


class DeviceService:
    @staticmethod
    async def create_device(
        session: AsyncSession,
        *,
        user_id: int,
        server_id: int,
        device_name: str | None = None,
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
        if not device_name:
            user_profiles = (
                await session.execute(
                    select(VPNProfile).where(VPNProfile.user_id == user.id)
                )
            ).scalars().all()
            used = set()
            for p in user_profiles:
                m = re.search(r"#(\d+)$", p.device_name)
                if m:
                    used.add(int(m.group(1)))
            limit = user.device_limit or 5
            slot_index = 1
            for i in range(1, limit + 1):
                if i not in used:
                    slot_index = i
                    break
            else:
                slot_index = max(used) + 1 if used else 1
            device_name = f"Устройство #{slot_index}"
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
                live_client_count=len(snapshot.peer_ids),
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

        try:
            async with session.begin_nested():
                session.add(profile)
                await session.flush()
        except IntegrityError as e:
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

        await AuditService.log_action(
            session,
            admin_id=0,
            action="DEVICE_CREATE",
            target_type="user",
            target_id=user.id,
            details={
                "device_name": profile.device_name,
                "server_name": server.name,
                "profile_id": profile.id,
            },
        )
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
        if not force:
            if profile.provisioning_status == "pending_create":
                raise DeviceStillCreating("Device still creating")
            if profile.provisioning_status == "deleting":
                return True
            if profile.provisioning_status not in ALLOWED_DELETE_STATES:
                raise DeviceCreationError(f"Deletion not allowed in status: {profile.provisioning_status}")

        # Capture server and device info for audit before deletion
        server_id, server_name, api_url, api_key = await resolve_profile_endpoint_snapshot(session, profile)
        device_name = profile.device_name
        profile_id = profile.id
        user_id = profile.user_id

        create_operation = None
        if force and not profile.peer_id:
            create_operation = (
                await session.execute(
                    select(APIOperation)
                    .where(
                        APIOperation.profile_id == profile.id,
                        APIOperation.operation_type == "create_peer",
                    )
                    .order_by(APIOperation.id.desc())
                    .with_for_update()
                )
            ).scalar_one_or_none()

        cleanup_peer_id = profile.peer_id or (
            create_operation.peer_id if create_operation else None
        )
        if (
            force
            and create_operation is not None
            and create_operation.status in {"pending", "retry"}
        ):
            create_operation.status = "cancelled"
            create_operation.completed_at = now_utc()
            create_operation.locked_at = None
            create_operation.locked_by = None
            create_operation.last_error_code = "create_cancelled_by_force_delete"
        processing_create_without_profile_peer = (
            force
            and create_operation is not None
            and create_operation.status == "processing"
            and profile.peer_id is None
        )
        if cleanup_peer_id and not processing_create_without_profile_peer:
            delete_key = f"delete-peer:{profile.id}:{cleanup_peer_id}"
            if not force:
                profile.provisioning_status = "deleting"
                await ensure_delete_operation(
                    session,
                    idempotency_key=delete_key,
                    server_id=server_id,
                    profile_id=profile.id,
                    server_name_snapshot=server_name,
                    api_url_snapshot=api_url,
                    api_key_snapshot=api_key,
                    peer_id=cleanup_peer_id,
                    client_name=profile.client_name,
                    audit_reason="device_delete",
                )
            else:
                # Force delete: ensure durable delete_peer is enqueued before removing local row.
                # If enqueue fails, exception propagates to maintain fail-closed consistency.
                await ensure_delete_operation(
                    session,
                    idempotency_key=delete_key,
                    server_id=server_id,
                    profile_id=profile.id,
                    server_name_snapshot=server_name,
                    api_url_snapshot=api_url,
                    api_key_snapshot=api_key,
                    peer_id=cleanup_peer_id,
                    client_name=profile.client_name,
                    audit_reason="device_delete_force",
                )
                await session.delete(profile)
        elif (
            force
            and create_operation is not None
            and (
                create_operation.status == "processing"
                or classify_create_side_effect_risk(create_operation)
                != "never_started"
            )
        ):
            # The profile is being force-deleted, but we have a durable CREATE operation
            # that might have a remote side effect (an orphan peer on the node).
            # The API operation will outlive the VPNProfile (its profile_id becomes NULL
            # due to ON DELETE SET NULL).
            # If the operation is dead, we revive it so the API executor can wake up,
            # discover the profile is missing, and execute its orphan cleanup block.
            if create_operation.status in {"dead", "cancelled"}:
                create_operation.status = "retry"
                create_operation.attempts = 0
                create_operation.next_attempt_at = now_utc()
                create_operation.completed_at = None
                create_operation.locked_at = None
                create_operation.locked_by = None
                create_operation.last_error_code = "device_delete_force_revive"
                create_operation.last_error = "Revived by force delete for orphan cleanup"
            
            await session.delete(profile)
        else:
            await session.delete(profile)

        action = "ADMIN_DEVICE_DELETE" if (actor_id and is_admin(actor_id)) else "DEVICE_DELETE"
        admin_id = actor_id if (actor_id and is_admin(actor_id)) else 0
        await AuditService.log_action(
            session,
            admin_id=admin_id,
            action=action,
            target_type="user",
            target_id=user_id,
            details={
                "device_name": device_name,
                "profile_id": profile_id,
                "server_name": server_name or "",
                "force": force,
            },
        )
        return True
