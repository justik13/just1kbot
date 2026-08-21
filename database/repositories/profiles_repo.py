import re

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from database.models import VPNProfile

ALLOWED_PROFILE_UPDATE_FIELDS = {
    'device_name',
    'last_connected',
    'traffic_down',
    'traffic_up',
    'is_active',
}

# Lifecycle states hidden from the user's UI connection list.
# Only in-flight deletions are hidden; recoverable and failure states remain visible.
PROFILE_LIST_HIDDEN_STATUSES = (
    "deleting",
)

# Lifecycle states excluded from active quota / capacity count calculations.
# delete_failed and create_cleanup_pending still have active server peers,
# so they MUST consume quota to prevent downgrade exploits.
PROFILE_QUOTA_EXCLUDED_STATUSES = (
    "deleting",
    "create_failed",
)

# Provisioning states in which a profile can be deleted by a user or service (Fail-Closed).
ALLOWED_DELETE_STATES = frozenset({
    "active",
    "pending_update",
    "update_failed",
    "create_failed",
    "delete_failed",
    "create_cleanup_pending",
})


def sort_profiles_naturally(profiles: list[VPNProfile]) -> list[VPNProfile]:
    def _extract_slot_num(p: VPNProfile) -> int:
        if p.device_name:
            match = re.search(r'#(\d+)', p.device_name)
            if match:
                return int(match.group(1))
        return p.id or 0

    return sorted(profiles, key=lambda p: (_extract_slot_num(p), p.created_at or 0, p.id or 0), reverse=True)


async def get_user_profiles(
    session: AsyncSession, user_id: int, include_deleting: bool = False
) -> list[VPNProfile]:
    stmt = select(VPNProfile).where(VPNProfile.user_id == user_id)
    if not include_deleting:
        stmt = stmt.where(
            VPNProfile.provisioning_status.notin_(PROFILE_LIST_HIDDEN_STATUSES)
        )
    stmt = stmt.options(selectinload(VPNProfile.server)).order_by(VPNProfile.created_at.asc(), VPNProfile.id.asc())
    result = await session.execute(stmt)
    profiles = list(result.scalars().all())
    return sort_profiles_naturally(profiles)


async def get_profile_by_id(session: AsyncSession, profile_id: int) -> VPNProfile | None:
    stmt = select(VPNProfile).where(VPNProfile.id == profile_id).options(selectinload(VPNProfile.server))
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


async def create_profile(
    session: AsyncSession,
    user_id: int,
    server_id: int,
    device_name: str,
    peer_id: str,
    raw_config: str,
) -> VPNProfile:
    profile = VPNProfile(
        user_id=user_id,
        server_id=server_id,
        device_name=device_name,
        peer_id=peer_id,
        raw_config=raw_config,
    )
    session.add(profile)
    await session.flush()
    await session.refresh(profile)
    return profile


async def update_profile(session: AsyncSession, profile: VPNProfile, **kwargs) -> VPNProfile:
    for key, value in kwargs.items():
        if key in ALLOWED_PROFILE_UPDATE_FIELDS:
            setattr(profile, key, value)

    await session.flush()
    await session.refresh(profile)
    return profile


async def delete_profile(session: AsyncSession, profile: VPNProfile) -> None:
    await session.delete(profile)
    await session.flush()


async def get_user_profiles_count(
    session: AsyncSession, user_id: int, include_deleting: bool = False
) -> int:
    """Return count of active/reserving profiles for quota checks.

    Excludes PROFILE_QUOTA_EXCLUDED_STATUSES ('deleting', 'create_failed') by default.
    """
    stmt = select(func.count(VPNProfile.id)).where(VPNProfile.user_id == user_id)
    if not include_deleting:
        stmt = stmt.where(
            VPNProfile.provisioning_status.notin_(PROFILE_QUOTA_EXCLUDED_STATUSES)
        )
    result = await session.execute(stmt)
    return result.scalar_one()


get_user_quota_profiles_count = get_user_profiles_count
