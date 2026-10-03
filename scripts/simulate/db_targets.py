"""Database target safety for the local simulation testbed."""
from __future__ import annotations

from urllib.parse import urlsplit

_LOCAL_DB_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})


def is_local_db_url(db_url: str | None) -> bool:
    """True for SQLite and loopback-only database URLs (safe simulation targets).

    Anything else (remote hosts, unparseable values) counts as remote:
    fail-closed, the caller must require explicit acknowledgment.
    """
    url = (db_url or "").strip().lower()
    if url.startswith("sqlite"):
        return True
    try:
        host = urlsplit(url).hostname or ""
    except ValueError:
        return False
    return host in _LOCAL_DB_HOSTS
