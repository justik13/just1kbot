"""User context in-memory caching service.

Provides decoupled, high-performance in-memory caching for Telegram ID to Database User ID
mapping within the asyncio event loop, safely shared by bot middlewares, ban services,
and subscription lifecycle workflows.
"""

from __future__ import annotations

from cachetools import TTLCache

from config.constants import USER_CONTEXT_CACHE_MAX_SIZE, USER_CONTEXT_CACHE_TTL

_user_cache: TTLCache[int, int | None] = TTLCache(
    maxsize=USER_CONTEXT_CACHE_MAX_SIZE,
    ttl=USER_CONTEXT_CACHE_TTL,
)

_MISSING = object()


def get_cached_user_id(telegram_id: int) -> tuple[bool, int | None]:
    """Retrieve cached user ID for a Telegram ID.

    Returns:
        tuple[bool, int | None]: (is_cached, user_id).
        If not cached, returns (False, None).
        If cached, returns (True, user_id) where user_id may be None (e.g. deleted user tombstone).
    """
    val = _user_cache.get(telegram_id, _MISSING)
    if val is _MISSING:
        return False, None
    return True, val


def set_cached_user_id(telegram_id: int, user_id: int | None) -> None:
    """Store or update user_id (or None for tombstoned deleted users) for a telegram_id."""
    _user_cache[telegram_id] = user_id


def invalidate_user_cache(telegram_id: int) -> None:
    """Invalidate cached context for a specific Telegram ID."""
    _user_cache.pop(telegram_id, None)


def clear_user_cache() -> None:
    """Clear all cached user contexts."""
    _user_cache.clear()


__all__ = [
    "clear_user_cache",
    "get_cached_user_id",
    "invalidate_user_cache",
    "set_cached_user_id",
]
