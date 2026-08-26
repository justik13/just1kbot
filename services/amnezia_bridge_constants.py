"""Backward-compatibility shim for Amnezia Bridge constants."""

from integrations.amnezia_bridge.constants import *  # noqa: F401, F403
from integrations.amnezia_bridge.constants import (
    BRIDGE_TOKEN_MAX_FUTURE_SKEW_SECONDS,
    BRIDGE_TOKEN_TTL_SECONDS,
    BRIDGE_TOKEN_VERSION,
    MAX_BRIDGE_REQUEST_TARGET_BYTES,
    MAX_RAW_CONFIG_BYTES,
    RATE_LIMIT_BURST,
    RATE_LIMIT_REQUESTS_PER_MINUTE,
)

__all__ = [
    "BRIDGE_TOKEN_VERSION",
    "BRIDGE_TOKEN_TTL_SECONDS",
    "BRIDGE_TOKEN_MAX_FUTURE_SKEW_SECONDS",
    "MAX_BRIDGE_REQUEST_TARGET_BYTES",
    "MAX_RAW_CONFIG_BYTES",
    "RATE_LIMIT_REQUESTS_PER_MINUTE",
    "RATE_LIMIT_BURST",
]
