from datetime import datetime
from typing import List, Optional, TypedDict

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import Server, VPNProfile
from services.slots_cache import get_cached_peer_count


class ServerUpdateFields(TypedDict, total=False):
    name: str
    country_flag: str | None
    api_url: str
    api_key: str
    protocol: str
    max_clients: int
    is_active: bool
    disabled_reason: str | None
    disabled_at: datetime | None
    last_successful_check: datetime | None
    health_state: str
    problem_started_at: datetime | None
    next_check_at: datetime | None
    consecutive_fails: int
    consecutive_successes: int
    recovery_notice_sent: bool
    last_alert_sent_state: str | None


PROTECTED_SERVER_FIELDS = {"id", "created_at"}
HEALTH_UPDATE_FIELDS = {
    "is_active",
    "disabled_reason",
    "disabled_at",
    "last_successful_check",
    "health_state",
    "problem_started_at",
    "next_check_at",
    "consecutive_fails",
    "consecutive_successes",
    "recovery_notice_sent",
    "last_alert_sent_state",
}


async def get_all_servers(session: AsyncSession) -> List[Server]:
    result = await session.execute(select(Server).order_by(Server.name))
    return result.scalars().all()


async def get_active_servers(session: AsyncSession) -> List[Server]:
    result = await session.execute(
        select(Server).where(Server.is_active.is_(True)).order_by(Server.name)
    )
    return result.scalars().all()


async def get_server_peer_counts(session: AsyncSession) -> dict[int, int]:
    result = await session.execute(
        select(VPNProfile.server_id, func.count(VPNProfile.id)).group_by(VPNProfile.server_id)
    )
    return {row[0]: row[1] for row in result.all()}


async def get_available_servers(session: AsyncSession) -> List[Server]:
    servers = await get_active_servers(session)
    if not servers:
        return []

    result = await session.execute(
        select(VPNProfile.server_id, func.count(VPNProfile.id)).group_by(VPNProfile.server_id)
    )
    db_counts = {row[0]: row[1] for row in result.all()}

    available: List[Server] = []
    for server in servers:
        real_count = get_cached_peer_count(server.id)
        if real_count is None:
            real_count = db_counts.get(server.id, 0)
        if real_count < server.max_clients:
            available.append(server)
    return available


async def get_server_by_id(session: AsyncSession, server_id: int) -> Optional[Server]:
    result = await session.execute(select(Server).where(Server.id == server_id))
    return result.scalar_one_or_none()


async def create_server(
    session: AsyncSession,
    name: str,
    api_url: str,
    api_key: str,
    country_flag: str = None,
    protocol: str = "amneziawg2",
    max_clients: int = 50,
) -> Server:
    server = Server(
        name=name,
        api_url=api_url,
        api_key=api_key,
        country_flag=country_flag,
        protocol=protocol,
        max_clients=max_clients,
    )
    session.add(server)
    await session.flush()
    await session.refresh(server)
    return server


async def update_server(
    session: AsyncSession,
    server: Server,
    **kwargs: ServerUpdateFields,
) -> Server:
    """Update a server; serialize only monitor health writes."""
    if any(key in HEALTH_UPDATE_FIELDS for key in kwargs):
        original_health = {
            field: getattr(server, field, None)
            for field in HEALTH_UPDATE_FIELDS
        }
        result = await session.execute(
            select(Server)
            .where(Server.id == server.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        current_server = result.scalar_one_or_none()
        if current_server is None:
            return server

        # The monitor performed a network request outside this transaction.
        # If another writer changed any health field meanwhile, this snapshot
        # is stale. Keep the newer DB health state rather than overwriting it.
        if any(
            original_health[field] != getattr(current_server, field, None)
            for field in HEALTH_UPDATE_FIELDS
            if field in kwargs
        ):
            kwargs = {
                key: value
                for key, value in kwargs.items()
                if key not in HEALTH_UPDATE_FIELDS
            }
        target = current_server
    else:
        target = server

    for key, value in kwargs.items():
        if key in PROTECTED_SERVER_FIELDS:
            continue
        if hasattr(target, key):
            setattr(target, key, value)

    await session.flush()
    await session.refresh(target)
    return target


async def delete_server(session: AsyncSession, server: Server) -> None:
    await session.delete(server)
    await session.flush()


async def get_total_free_ips(session: AsyncSession) -> int:
    active_servers = await get_active_servers(session)
    if not active_servers:
        return 0

    result = await session.execute(
        select(VPNProfile.server_id, func.count(VPNProfile.id)).group_by(VPNProfile.server_id)
    )
    db_counts = {row[0]: row[1] for row in result.all()}

    total_free = 0
    for server in active_servers:
        real_count = get_cached_peer_count(server.id)
        if real_count is None:
            real_count = db_counts.get(server.id, 0)
        total_free += max(0, server.max_clients - real_count)
    return total_free


async def get_server_count(session: AsyncSession) -> int:
    result = await session.execute(select(func.count(Server.id)))
    return result.scalar_one()


async def get_servers_paginated(
    session: AsyncSession,
    page: int = 1,
    per_page: int = 10,
) -> list[Server]:
    offset = (page - 1) * per_page
    result = await session.execute(
        select(Server).order_by(Server.name).offset(offset).limit(per_page)
    )
    return result.scalars().all()


async def get_server_by_api_url(session: AsyncSession, api_url: str) -> Optional[Server]:
    result = await session.execute(select(Server).where(Server.api_url == api_url))
    return result.scalar_one_or_none()


async def delete_profiles_by_server_id(session: AsyncSession, server_id: int) -> int:
    from sqlalchemy import delete as sql_delete

    result = await session.execute(sql_delete(VPNProfile).where(VPNProfile.server_id == server_id))
    await session.flush()
    return result.rowcount
