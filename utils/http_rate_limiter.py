import asyncio
import collections
import ipaddress
import math
import socket
import time
from urllib.parse import urlsplit

from aiohttp import web

from config.constants import RATE_LIMIT_BURST, RATE_LIMIT_REQUESTS_PER_MINUTE
from config.enums import ServerLifecycleStatus

_dynamic_trusted_proxy_ips: set[str] = set()
_dynamic_proxy_host_cache: dict[str, set[str]] = {}


def set_dynamic_trusted_proxy_ips(ips: set[str] | list[str] | tuple[str, ...]) -> None:
    """Replace runtime set of auto-discovered trusted proxy IPs (e.g. active ru_hub hosts)."""
    normalized: set[str] = set()
    for raw in ips:
        cand = str(raw or "").strip()
        if not cand:
            continue
        try:
            normalized.add(str(ipaddress.ip_address(cand)))
        except ValueError:
            continue
    _dynamic_trusted_proxy_ips.clear()
    _dynamic_trusted_proxy_ips.update(normalized)


def get_dynamic_trusted_proxy_ips() -> set[str]:
    """Return a copy of runtime auto-discovered trusted proxy IPs."""
    return set(_dynamic_trusted_proxy_ips)


def _normalize_ip_obj(raw_ip: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    try:
        return ipaddress.ip_address(raw_ip.strip())
    except ValueError:
        return None


def _normalize_hostname(raw_host: str) -> str:
    clean = str(raw_host or "").strip()
    if not clean:
        return ""
    if "://" in clean:
        return (urlsplit(clean).hostname or "").strip()
    if ":" in clean and not clean.startswith("["):
        try:
            ipaddress.ip_address(clean)
            return clean
        except ValueError:
            parsed = urlsplit(f"//{clean}")
            return (parsed.hostname or "").strip()
    return clean.strip("[]")


async def _resolve_host_ips(host: str) -> set[str] | None:
    """Resolve host to IPs with bounded timeout. Returns None on transient error/timeout."""
    clean = _normalize_hostname(host)
    if not clean:
        return set()
    direct = _normalize_ip_obj(clean)
    if direct is not None:
        return {str(direct)}
    try:
        loop = asyncio.get_running_loop()
        infos = await asyncio.wait_for(
            loop.getaddrinfo(clean, None, family=socket.AF_INET, type=socket.SOCK_STREAM),
            timeout=3.0,
        )
        out: set[str] = set()
        for info in infos:
            addr = info[4][0] if info and len(info) >= 5 and info[4] else ""
            ip_obj = _normalize_ip_obj(addr) if addr else None
            if ip_obj is not None:
                out.add(str(ip_obj))
        return out
    except Exception:
        return None


async def refresh_dynamic_trusted_proxies(servers: list | tuple) -> set[str]:
    """Resolve and cache IP addresses of active ru_hub servers for proxy IP unwrapping."""
    hosts: set[str] = set()
    direct_ips: set[str] = set()
    allowed_lifecycles = {
        None,
        ServerLifecycleStatus.ACTIVE,
        ServerLifecycleStatus.DECOMMISSIONING,
        "ACTIVE",
        "DECOMMISSIONING",
    }
    for srv in servers:
        if not bool(getattr(srv, "is_active", False)):
            continue
        lifecycle = getattr(srv, "lifecycle_status", None)
        if lifecycle not in allowed_lifecycles:
            continue
        role = str(getattr(srv, "role", "") or "").strip().lower()
        caps = getattr(srv, "capabilities", None) or []
        if role != "ru_hub" and "ru_hub" not in caps:
            continue
        api_url = str(getattr(srv, "api_url", "") or "").strip()
        if api_url:
            parsed_host = (urlsplit(api_url).hostname or "").strip()
            if parsed_host:
                hosts.add(parsed_host)
        extra = getattr(srv, "extra_data", None)
        if isinstance(extra, dict):
            dom = _normalize_hostname(str(extra.get("domain") or ""))
            if dom:
                hosts.add(dom)
            ip_val = str(extra.get("ip") or extra.get("public_ip") or "").strip()
            if ip_val:
                direct_ips.add(ip_val)

    # Drop cache entries for hosts no longer present in active ru_hub set
    for stale_host in [h for h in _dynamic_proxy_host_cache if h not in hosts]:
        _dynamic_proxy_host_cache.pop(stale_host, None)

    resolved: set[str] = set()
    if hosts:
        host_list = sorted(hosts)
        results = await asyncio.gather(*(_resolve_host_ips(h) for h in host_list), return_exceptions=True)
        for host, res in zip(host_list, results, strict=True):
            if isinstance(res, set):
                if res:
                    _dynamic_proxy_host_cache[host] = res
                else:
                    _dynamic_proxy_host_cache.pop(host, None)
                resolved.update(res)
            else:
                # Transient DNS failure/timeout: preserve previously resolved IPs for this host
                resolved.update(_dynamic_proxy_host_cache.get(host, set()))

    for raw_ip in direct_ips:
        ip_obj = _normalize_ip_obj(raw_ip)
        if ip_obj is not None:
            resolved.add(str(ip_obj))

    set_dynamic_trusted_proxy_ips(resolved)
    return resolved


async def refresh_dynamic_trusted_proxies_from_db() -> set[str]:
    """Load active ru_hub servers from DB and refresh runtime trusted proxy IPs."""
    from database.connection import session_scope
    from database.repositories import servers_repo

    async with session_scope() as session:
        servers = await servers_repo.get_all_servers(session)
    return await refresh_dynamic_trusted_proxies(servers)


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
        self._last_cleanup: float = 0.0

    def check(self, key: str, now: float | None = None) -> tuple[bool, int]:
        """Check if request is allowed.

        Returns (is_allowed, retry_after_seconds).
        """
        if now is None:
            now = time.monotonic()

        # O(1) LRU eviction when capacity is reached + throttled background cleanup
        if key not in self.buckets and len(self.buckets) >= self.max_entries:
            if now - self._last_cleanup >= 30.0:
                self._last_cleanup = now
                self._cleanup(now)
            if len(self.buckets) >= self.max_entries:
                self.buckets.popitem(last=False)  # pop least recently used in O(1)

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


def _is_trusted_proxy_peer(
    peer_ip: str,
    trusted_proxies: str | set | list | None = None,
) -> bool:
    """Determine whether peer IP matches explicitly configured trusted proxy subnets or addresses."""
    if trusted_proxies is None:
        try:
            from config.settings import get_settings
            trusted_proxies = get_settings().TRUSTED_PROXIES
        except (AttributeError, KeyError, TypeError, ValueError):
            trusted_proxies = "127.0.0.1,::1,10.0.0.0/8,172.16.0.0/12,192.168.0.0/16"

    if isinstance(trusted_proxies, str):
        trusted_items = [p.strip() for p in trusted_proxies.split(",") if p.strip()]
    else:
        trusted_items = list(trusted_proxies)

    ip = _normalize_ip_obj(peer_ip)
    if ip is None:
        return False
    if str(ip) in _dynamic_trusted_proxy_ips:
        return True
    for item in trusted_items:
        try:
            if "/" in item:
                if ip in ipaddress.ip_network(item, strict=False):
                    return True
            else:
                target_ip = _normalize_ip_obj(item)
                if target_ip is not None and ip == target_ip:
                    return True
        except ValueError:
            continue

    return False


def _extract_rightmost_untrusted_ip(
    header_val: str | None,
    trusted_proxies: str | set | list | None,
) -> str | None:
    if not header_val:
        return None
    ips = [item.strip() for item in header_val.split(",") if item.strip()]
    for raw_ip in reversed(ips):
        ip_obj = _normalize_ip_obj(raw_ip)
        if ip_obj is not None and not _is_trusted_proxy_peer(str(ip_obj), trusted_proxies):
            return str(ip_obj)
    return None


def get_trusted_client_ip(
    request: web.Request,
    *,
    allow_yandex_header: bool = False,
) -> str:
    """Extract trusted client IP address.

    When requests pass through trusted internal ingress (Caddy/Docker/loopback),
    extracts the client IP from X-Real-IP or X-Forwarded-For.
    Direct connections from arbitrary external peers use request.remote directly.

    X-Forwarded-For handling is rightmost-untrusted: entries are walked from the
    right and trusted proxy hops are skipped, so a client-supplied first entry
    cannot spoof the resolved address. X-Real-IP is honoured when it is an
    untrusted client IP; if X-Real-IP itself belongs to TRUSTED_PROXIES (for
    example, an upstream ru_hub node proxying /sub/* requests with
    X-Forwarded-For: <client_ip>, <ru_hub_ip>), X-Forwarded-For is inspected to
    extract the rightmost untrusted client IP. Add ru_hub IPs/CIDRs to
    TRUSTED_PROXIES to unwrap multi-hop CDN -> ru_hub -> Bot requests.
    """
    peer_ip = request.remote or "127.0.0.1"
    trusted_proxies = None
    if hasattr(request, "app") and request.app is not None:
        try:
            raw_proxies = request.app.get("trusted_proxies")
            if isinstance(raw_proxies, (str, list, set, tuple)):
                trusted_proxies = raw_proxies
        except (AttributeError, KeyError, TypeError):
            trusted_proxies = None

    if _is_trusted_proxy_peer(peer_ip, trusted_proxies):
        # 1. Prefer X-Real-IP set (overwritten) by the trusted reverse proxy,
        # unless X-Real-IP itself is a trusted proxy hop (e.g., ru_hub in TRUSTED_PROXIES).
        valid_real_ip: str | None = None
        real_ip = request.headers.get("X-Real-IP")
        if real_ip:
            ip_obj = _normalize_ip_obj(real_ip)
            if ip_obj is not None:
                candidate = str(ip_obj)
                valid_real_ip = candidate
                if not _is_trusted_proxy_peer(candidate, trusted_proxies):
                    return candidate

        # 2. Fallback to rightmost untrusted IP in X-Forwarded-For
        forwarded = request.headers.get("X-Forwarded-For")
        xff_ip = _extract_rightmost_untrusted_ip(forwarded, trusted_proxies)
        if xff_ip is not None:
            return xff_ip

        # 3. Yandex CDN fallback header ONLY on subscription routes when proxied through a known ru_hub
        if allow_yandex_header and valid_real_ip and valid_real_ip in _dynamic_trusted_proxy_ips:
            yff_ip = _extract_rightmost_untrusted_ip(
                request.headers.get("X-Forwarded-For-Y"),
                trusted_proxies,
            )
            if yff_ip is not None:
                return yff_ip

        if valid_real_ip:
            return valid_real_ip

    return peer_ip
