"""Typed, idempotent YooKassa HTTP transport.

Provider commands receive their key and immutable body from PostgreSQL; this module
never invents idempotency keys or logs credentials/payloads.
"""

import asyncio
import logging
import socket
from dataclasses import dataclass
from enum import Enum
from typing import Generic, TypeVar

import aiohttp

logger = logging.getLogger(__name__)

T = TypeVar("T")
_client_session: aiohttp.ClientSession | None = None
_client_session_factory = None
_client_session_lock = asyncio.Lock()


# Timeout presets:
# Status checks fit comfortably within Telegram's callback budget while
# allowing enough time for DNS resolution, TLS handshake, and network transit.
CHECK_STATUS_TIMEOUT = aiohttp.ClientTimeout(total=7.0, connect=3.0, sock_read=5.0)

# Payment creation involves upstream merchant invoice generation and cross-border TLS handshake:
CREATE_PAYMENT_TIMEOUT = aiohttp.ClientTimeout(total=10.0, connect=5.0, sock_read=7.0)


def _create_tcp_connector() -> aiohttp.TCPConnector:
    return aiohttp.TCPConnector(
        family=socket.AF_INET,
        ttl_dns_cache=10,
    )


async def _get_client_session() -> aiohttp.ClientSession:
    global _client_session, _client_session_factory
    session_factory = aiohttp.ClientSession
    is_closed = bool(getattr(_client_session, "closed", True))
    if (
        _client_session is None
        or is_closed
        or _client_session_factory is not session_factory
    ):
        async with _client_session_lock:
            is_closed = bool(getattr(_client_session, "closed", True))
            if (
                _client_session is None
                or is_closed
                or _client_session_factory is not session_factory
            ):
                if _client_session is not None and not is_closed:
                    close = getattr(_client_session, "close", None)
                    if close is not None:
                        await close()
                timeout = CREATE_PAYMENT_TIMEOUT
                connector = _create_tcp_connector()
                _client_session = session_factory(connector=connector, timeout=timeout)
                _client_session_factory = session_factory
    return _client_session


class YooKassaErrorKind(str, Enum):
    AUTH_FAILED = "auth_failed"
    VALIDATION_FAILED = "validation_failed"
    NOT_FOUND = "not_found"
    RATE_LIMITED = "rate_limited"
    SERVER_ERROR = "server_error"
    NETWORK_ERROR = "network_error"
    TIMEOUT = "timeout"
    INVALID_RESPONSE = "invalid_response"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class YooKassaResult(Generic[T]):
    ok: bool
    value: T | None = None
    error_kind: YooKassaErrorKind | None = None
    status_code: int | None = None
    retryable: bool = False
    ambiguous: bool = False


async def close_yookassa_client():
    global _client_session, _client_session_factory
    if _client_session is not None and not getattr(_client_session, "closed", False):
        close = getattr(_client_session, "close", None)
        if close is not None:
            await close()
    _client_session = None
    _client_session_factory = None
