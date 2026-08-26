"""Neutral, dependency-free system and protocol constants.

This module sits at Level 0 in the application layered architecture and has zero
internal dependencies, allowing safe downward imports by utils, database,
integrations, services, and bot layers without architectural cycles.
"""

# Protocol
AMNEZIA_PROTOCOL = "amneziawg2"

# VPN Configuration transport limits
MAX_RAW_CONFIG_BYTES = 65536  # 64 KiB max raw config payload

# Default HTTP rate limiter settings
RATE_LIMIT_REQUESTS_PER_MINUTE = 30.0
RATE_LIMIT_BURST = 10

__all__ = [
    "AMNEZIA_PROTOCOL",
    "MAX_RAW_CONFIG_BYTES",
    "RATE_LIMIT_BURST",
    "RATE_LIMIT_REQUESTS_PER_MINUTE",
]
