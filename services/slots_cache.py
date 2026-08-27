import asyncio
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone

from cachetools import TTLCache

from database.models import Server
from services.amnezia_client import AmneziaClient

logger = logging.getLogger(__name__)

@dataclass(frozen=True)
class ServerPeerSnapshot:
    server_id: int
    peer_ids: frozenset[str]
    captured_at: datetime

async def capture_server_peer_snapshot(server_id: int) -> ServerPeerSnapshot:
    from database.connection import session_scope
    from services.device_service import ServerUnavailable

    async with session_scope() as session:
        server = await session.get(Server, server_id)
        if not server:
            raise LookupError("server not found")
        endpoint = (server.api_url, server.api_key)
    clients = await AmneziaClient(*endpoint).get_all_clients()
    if clients is None:
        raise ServerUnavailable("server peer snapshot unavailable")
    return ServerPeerSnapshot(
        server_id,
        frozenset(item.id for item in clients),
        datetime.now(timezone.utc),
    )

_slots_cache: TTLCache[int, tuple[int, float, int]] = TTLCache(maxsize=100, ttl=1800)
_server_generations: dict[int, int] = {}
_locks: dict[int, tuple[asyncio.Lock, float]] = {}
_last_cleanup_time: float = 0.0
_CLEANUP_INTERVAL = 3600.0
_LOCK_TTL = 3600.0


def get_server_generation(server_id: int) -> int:
    """Get current configuration generation for server_id."""
    return _server_generations.get(server_id, 0)


def get_cached_peer_count(server_id: int) -> int | None:
    entry = _slots_cache.get(server_id)
    if entry is None:
        return None
    if isinstance(entry, tuple):
        count, _, gen = entry
        current_gen = _server_generations.get(server_id, 0)
        if gen != current_gen:
            _slots_cache.pop(server_id, None)
            return None
        return count
    return entry


def update_cached_peer_count(
    server_id: int,
    count: int,
    timestamp: float | None = None,
    generation: int | None = None,
) -> bool:
    """
    Updates cached peer count if snapshot is fresher than current entry and matches server generation.
    Returns True if cache was updated, False if discarded.
    """
    now = timestamp if timestamp is not None else time.monotonic()
    current_gen = _server_generations.get(server_id, 0)
    req_gen = generation if generation is not None else current_gen

    # Discard snapshot if it was requested under a prior server configuration generation
    if req_gen != current_gen:
        return False

    entry = _slots_cache.get(server_id)
    if entry is not None and isinstance(entry, tuple):
        _, last_ts, entry_gen = entry
        if entry_gen == current_gen and now < last_ts:
            return False

    _slots_cache[server_id] = (count, now, current_gen)
    return True


def invalidate_server_cache(server_id: int) -> None:
    """Evict a specific server from slots cache and bump generation to discard in-flight requests."""
    _server_generations[server_id] = _server_generations.get(server_id, 0) + 1
    _slots_cache.pop(server_id, None)


def clear_slots_cache() -> None:
    """Clear all cached peer counts and generations."""
    _slots_cache.clear()
    _server_generations.clear()


async def get_real_peer_count(server: Server, force_refresh: bool = False) -> int:
    global _last_cleanup_time
    now = time.monotonic()
    if now - _last_cleanup_time > _CLEANUP_INTERVAL:
        _cleanup_old_locks(now)
        _last_cleanup_time = now

    cached = get_cached_peer_count(server.id)
    if not force_refresh and cached is not None:
        return cached

    if server.id not in _locks:
        _locks[server.id] = (asyncio.Lock(), now)
    else:
        lock, _ = _locks[server.id]
        _locks[server.id] = (lock, now)

    lock = _locks[server.id][0]
    async with lock:
        cached = get_cached_peer_count(server.id)
        if not force_refresh and cached is not None:
            return cached

        client = AmneziaClient(server.api_url, server.api_key)
        try:
            clients = await client.get_all_clients()
        except Exception as e:
            logger.error(
                "Failed to get real peer count for server %s (%s): %s",
                server.id, server.name, e,
            )
            return -1

        if clients is None:
            logger.warning(
                "API returned no data for server %s (%s). "
                "Peer count is unknown, returning -1.",
                server.id, server.name,
            )
            return -1

        count = len(clients)
        update_cached_peer_count(server.id, count, now)
        logger.info(
            "Cached real peer count for server %s (%s): %s/%s",
            server.id, server.name, count, server.max_clients,
        )
        return count


def _cleanup_old_locks(now: float) -> None:
    old_servers = [
        sid for sid, (lock, last_used) in _locks.items()
        if now - last_used > _LOCK_TTL and not lock.locked()
    ]
    for sid in old_servers:
        del _locks[sid]
    if old_servers:
        logger.debug(
            "Slots cache locks cleanup: removed %s old locks, "
            "%s remaining",
            len(old_servers), len(_locks),
        )
