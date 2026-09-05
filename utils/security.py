import asyncio
import ipaddress
import re
import socket
from urllib.parse import urlparse

from aiohttp.resolver import DefaultResolver

_BLOCKED_HOSTNAMES = {
    "169.254.169.254",
    "metadata.google.internal",
    "100.100.100.200",
    "169.254.170.2",
}

_LOCAL_HOSTNAMES = {
    "localhost",
    "127.0.0.1",
    "::1",
    "0.0.0.0",
}


def _allow_local_http() -> bool:
    from config.settings import get_settings
    return get_settings().ALLOW_LOCAL_HTTP


def _allow_local_https() -> bool:
    from config.settings import get_settings
    return get_settings().ALLOW_LOCAL_HTTPS


def allow_local_networks() -> bool:
    return _allow_local_http() or _allow_local_https()


def _host_is_localish(hostname: str) -> bool:
    h = (hostname or "").lower().strip().strip("[]")
    if not h:
        return False
    if h in _LOCAL_HOSTNAMES:
        return True
    try:
        ip = ipaddress.ip_address(h)
    except ValueError:
        return False
    if ip.is_link_local or ip.is_reserved or ip.is_multicast:
        return False
    return ip.is_loopback or ip.is_private or ip.is_unspecified


def is_ip_allowed(ip, *, allow_local: bool = False) -> bool:
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    if ip.is_link_local or ip.is_reserved or ip.is_multicast:
        return False
    if ip.is_loopback or ip.is_private or ip.is_unspecified:
        return allow_local
    return True


class SafeResolver(DefaultResolver):
    def __init__(self, *, allow_local: bool = False, **kwargs):
        super().__init__(**kwargs)
        self.allow_local = allow_local

    async def resolve(self, host, port=0, family=socket.AF_INET):
        host_lower = (host or "").lower()
        if host_lower in _BLOCKED_HOSTNAMES:
            raise OSError(f"Blocked hostname: {host}")

        records = await super().resolve(host, port=port, family=family)
        allow_for_host = self.allow_local and _host_is_localish(host)

        safe_records = []
        for record in records:
            try:
                ip = ipaddress.ip_address(record["host"])
            except ValueError:
                continue
            if is_ip_allowed(ip, allow_local=allow_for_host):
                safe_records.append(record)

        if not safe_records:
            raise OSError(f"Unsafe or forbidden address resolved for host: {host}")
        return safe_records


async def _resolved_ips_are_safe(hostname: str, *, allow_local: bool = False) -> bool:
    effective_allow_local = allow_local and _host_is_localish(hostname)
    try:
        loop = asyncio.get_running_loop()
        addr_info = await asyncio.wait_for(loop.getaddrinfo(hostname, None), timeout=5.0)
    except (asyncio.TimeoutError, socket.gaierror, Exception):
        return False

    if not addr_info:
        return False

    for *_metadata, sockaddr in addr_info:
        ip_str = sockaddr[0]
        try:
            ip = ipaddress.ip_address(ip_str)
        except ValueError:
            return False
        if not is_ip_allowed(ip, allow_local=effective_allow_local):
            return False
    return True


async def is_safe_url(url: str) -> bool:
    try:
        parsed = urlparse(url)
        hostname = parsed.hostname
        scheme = (parsed.scheme or "").lower()

        if not hostname:
            return False
        if scheme not in {"http", "https"}:
            return False

        hostname = hostname.lower()
        if hostname in _BLOCKED_HOSTNAMES:
            return False

        allow_local_https = _allow_local_https()

        if hostname in _LOCAL_HOSTNAMES:
            if scheme == "http":
                return _allow_local_http()
            if scheme == "https":
                return allow_local_https
            return False

        if scheme == "http":
            return False

        try:
            ip = ipaddress.ip_address(hostname)
        except ValueError:
            pass
        else:
            return is_ip_allowed(ip, allow_local=allow_local_https and _host_is_localish(hostname))

        return await _resolved_ips_are_safe(hostname, allow_local=allow_local_https)
    except Exception:
        return False


def normalize_hostname(raw: str | None) -> str | None:
    """Normalize domain/hostname or URL input by extracting cleaned lowercase hostname.

    Strips scheme, userinfo (user:pass@), port, path, query parameters, fragments,
    and trailing dots. Returns None if input is empty or invalid.
    """
    if not raw or not isinstance(raw, str):
        return None
    val = raw.strip()
    if not val:
        return None
    if "://" not in val and "@" in val:
        return None
    try:
        if "://" in val:
            parsed = urlparse(val)
        else:
            parsed = urlparse(f"//{val}")
        _ = parsed.port
        host = parsed.hostname
        if not host:
            netloc = parsed.netloc or val.split("/")[0].split("?")[0].split("#")[0]
            if "@" in netloc:
                netloc = netloc.split("@")[-1]
            if ":" in netloc and not netloc.startswith("["):
                netloc = netloc.split(":")[0]
            host = netloc
        if host:
            cleaned = host.strip().rstrip(".").lower()
            return cleaned if cleaned else None
    except Exception:
        pass
    return None


def validate_public_fqdn(host: str | None) -> bool:
    """Validate that host is a syntactically valid public RFC 1123 FQDN.

    Rejects:
    - Non-strings, empty strings, strings > 253 characters
    - IP addresses (IPv4 / IPv6)
    - localhost, .localhost, .local, .internal, .arpa
    - Control characters, whitespaces, shell metacharacters, semicolons, quotes, etc.
    - Underscores (_) anywhere in the domain
    - Single-label hostnames (must have at least 2 labels)
    - Labels with invalid characters, leading/trailing hyphens, or length > 63
    - TLDs shorter than 2 characters or containing non-alphabetic characters (except punycode)
    """
    if not host or not isinstance(host, str):
        return False
    cleaned = host.rstrip(".").strip()
    if not cleaned or len(cleaned) > 253:
        return False
    if not re.fullmatch(r"[a-zA-Z0-9.-]+", cleaned):
        return False
    try:
        ipaddress.ip_address(cleaned)
        return False
    except ValueError:
        pass
    lower = cleaned.lower()
    if lower == "localhost" or lower.endswith((".localhost", ".local", ".internal", ".arpa")):
        return False
    labels = lower.split(".")
    if len(labels) < 2:
        return False
    for label in labels:
        if not (1 <= len(label) <= 63):
            return False
        if not re.fullmatch(r"[a-z0-9]([a-z0-9-]*[a-z0-9])?", label):
            return False
    tld = labels[-1]
    if not re.fullmatch(r"([a-z]{2,}|xn--[a-z0-9]+)", tld):
        return False
    return True


def normalize_public_domain(raw: str | None, require_fqdn: bool = True) -> str | None:
    """Normalize and optionally validate a public domain / hostname.

    1. Extracts cleaned lowercase hostname via normalize_hostname.
    2. If require_fqdn is True (default), validates via validate_public_fqdn.
    Returns clean lowercase hostname/domain, or None if invalid.
    """
    host = normalize_hostname(raw)
    if not host:
        return None
    if require_fqdn and not validate_public_fqdn(host):
        return None
    return host