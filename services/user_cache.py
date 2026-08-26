"""User context in-memory caching service.

Provides a thread-safe, decoupled cache for Telegram ID to Database User ID mapping
allowing safe usage by bot middlewares, ban services, and subscription workflows.
"""

from __future__ import annotations

from cachetools import TTLCache

from config.constants import USER_CONTEXT_CACHE_MAX_SIZE, USER_CONTEXT_CACHE_TTL

_user_cache: TTLCache[int, int | None] = TTLCache(
    maxsize=USER_CONTEXT_CACHE_MAX_SIZE,
    ttl=USER_CONTEXT_CACHE_TTL,
)


def get_user_cache() -> TTLCache[int, int | None]:
    """Return the global user context cache instance."""
    return _user_cache


def invalidate_user_cache(telegram_id: int) -> None:
    """Invalidate cached context for a specific Telegram ID."""
    _user_cache.pop(telegram_id, None)


def clear_user_cache() -> None:
    """Clear all cached user contexts."""
    _user_cache.clear()


__all__ = [
    "clear_user_cache",
    "get_user_cache",
    "invalidate_user_cache",
]
