import math
import time

from aiohttp import web

from services.amnezia_bridge_constants import (
    RATE_LIMIT_BURST,
    RATE_LIMIT_REQUESTS_PER_MINUTE,
)


class HttpRateLimiter:
    """Non-blocking in-memory token bucket rate limiter for HTTP endpoints.

    Process-local defense-in-depth protection returning 429 immediately without
    blocking asyncio tasks or event loops.
    """

    def __init__(
        self,
        rate_per_minute: float = RATE_LIMIT_REQUESTS_PER_MINUTE,
        burst: int = RATE_LIMIT_BURST,
    ):
        self.rate = rate_per_minute / 60.0  # tokens per second
        self.burst = float(burst)
        self.buckets: dict[str, tuple[float, float]] = {}  # key -> (tokens, last_refill)

    def check(self, key: str, now: float | None = None) -> tuple[bool, int]:
        """Check if request is allowed.

        Returns (is_allowed, retry_after_seconds).
        """
        if now is None:
            now = time.monotonic()

        tokens, last_refill = self.buckets.get(key, (self.burst, now))
        elapsed = max(0.0, now - last_refill)
        tokens = min(self.burst, tokens + elapsed * self.rate)

        if tokens >= 1.0:
            self.buckets[key] = (tokens - 1.0, now)
            return True, 0

        # Denied: compute truthful lower bound for next token
        wait_seconds = max(1, math.ceil((1.0 - tokens) / self.rate))
        self.buckets[key] = (tokens, now)
        return False, wait_seconds

    def reset(self) -> None:
        """Clear all rate limit buckets (useful for tests)."""
        self.buckets.clear()


amnezia_bridge_rate_limiter = HttpRateLimiter()


def get_trusted_client_ip(request: web.Request) -> str:
    """Extract trusted client IP address.

    Never trusts X-Forwarded-For or X-Real-IP from arbitrary untrusted clients.
    Uses request.remote directly unless an explicit trusted proxy layer is configured.
    """
    # Check if request has trusted proxy context configured in app
    trusted_proxies = request.app.get("trusted_proxies", set())
    peer_ip = request.remote or "127.0.0.1"

    if trusted_proxies and peer_ip in trusted_proxies:
        forwarded = request.headers.get("X-Forwarded-For")
        if forwarded:
            # First IP in X-Forwarded-For list is the client IP
            client_ip = forwarded.split(",")[0].strip()
            if client_ip:
                return client_ip

    return peer_ip
