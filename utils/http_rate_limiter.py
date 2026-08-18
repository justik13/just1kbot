import collections
import ipaddress
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
    Bounded memory footprint with LRU eviction and periodic idle pruning.
    """

    def __init__(
        self,
        rate_per_minute: float = RATE_LIMIT_REQUESTS_PER_MINUTE,
        burst: int = RATE_LIMIT_BURST,
        max_entries: int = 10000,
    ):
        self.rate = rate_per_minute / 60.0  # tokens per second
        self.burst = float(burst)
        self.max_entries = max_entries
        self.buckets: collections.OrderedDict[str, tuple[float, float]] = collections.OrderedDict()

    def check(self, key: str, now: float | None = None) -> tuple[bool, int]:
        """Check if request is allowed.

        Returns (is_allowed, retry_after_seconds).
        """
        if now is None:
            now = time.monotonic()

        # Evict old / least recently used entries if capacity is reached
        if key not in self.buckets and len(self.buckets) >= self.max_entries:
            self._cleanup(now)
            if len(self.buckets) >= self.max_entries:
                self.buckets.popitem(last=False)  # pop least recently used

        tokens, last_refill = self.buckets.get(key, (self.burst, now))
        elapsed = max(0.0, now - last_refill)
        tokens = min(self.burst, tokens + elapsed * self.rate)

        if tokens >= 1.0:
            self.buckets[key] = (tokens - 1.0, now)
            self.buckets.move_to_end(key)
            return True, 0

        # Denied: compute truthful lower bound for next token
        wait_seconds = max(1, math.ceil((1.0 - tokens) / self.rate))
        self.buckets[key] = (tokens, now)
        self.buckets.move_to_end(key)
        return False, wait_seconds

    def _cleanup(self, now: float) -> None:
        """Prune full-bucket stale entries that have been idle for > 2 minutes."""
        stale_keys = []
        for k, (toks, last_time) in self.buckets.items():
            current_tokens = min(self.burst, toks + max(0.0, now - last_time) * self.rate)
            if current_tokens >= self.burst and (now - last_time) > 120.0:
                stale_keys.append(k)
        for k in stale_keys:
            del self.buckets[k]

    def reset(self) -> None:
        """Clear all rate limit buckets (useful for tests)."""
        self.buckets.clear()


amnezia_bridge_rate_limiter = HttpRateLimiter()


def _is_trusted_proxy_peer(peer_ip: str, trusted_proxies: set | None = None) -> bool:
    """Determine whether peer IP is an internal reverse proxy or local container."""
    if trusted_proxies and peer_ip in trusted_proxies:
        return True

    try:
        ip = ipaddress.ip_address(peer_ip)
        return bool(ip.is_private or ip.is_loopback or ip.is_link_local)
    except ValueError:
        return False


def get_trusted_client_ip(request: web.Request) -> str:
    """Extract trusted client IP address.

    When requests pass through trusted internal ingress (Caddy/Docker/loopback),
    extracts the client IP from X-Real-IP or X-Forwarded-For.
    Direct connections from arbitrary external peers use request.remote directly.
    """
    peer_ip = request.remote or "127.0.0.1"
    trusted_proxies = request.app.get("trusted_proxies") if hasattr(request, "app") and request.app else None

    if _is_trusted_proxy_peer(peer_ip, trusted_proxies):
        # 1. Prefer X-Real-IP set by Caddy
        real_ip = request.headers.get("X-Real-IP")
        if real_ip:
            candidate = real_ip.strip()
            try:
                ipaddress.ip_address(candidate)
                return candidate
            except ValueError:
                pass

        # 2. Fallback to first IP in X-Forwarded-For
        forwarded = request.headers.get("X-Forwarded-For")
        if forwarded:
            candidate = forwarded.split(",")[0].strip()
            try:
                ipaddress.ip_address(candidate)
                return candidate
            except ValueError:
                pass

    return peer_ip
