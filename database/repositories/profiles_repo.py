import re

from sqlalchemy import select, func
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
        stmt = stmt.where(VPNProfile.provisioning_status.notin_(["deleting", "create_cleanup_pending"]))
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
    stmt = select(func.count(VPNProfile.id)).where(VPNProfile.user_id == user_id)
    if not include_deleting:
        stmt = stmt.where(VPNProfile.provisioning_status != "deleting")
    result = await session.execute(stmt)
    return result.scalar_one()
