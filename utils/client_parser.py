"""Universal parser for subscription client identification from User-Agent."""

from __future__ import annotations

import re
import urllib.parse

_IGNORED_CLIENTS = {
    "mozilla", "applewebkit", "chrome", "safari", "firefox", "gecko", "edg",
    "edge", "opera", "opr", "browser", "mobile", "version", "khtml", "curl",
    "okhttp", "cfnetwork", "python", "dart", "wget", "urllib", "aiohttp",
    "httpx", "http", "go-http-client", "java", "postmanruntime", "axios",
    "darwin", "android", "linux", "windows", "os", "cpu", "mac", "macintosh", "iphone", "ipad",
}

_CLIENT_TOKEN_RE = re.compile(
    r"\b([A-Za-z][A-Za-z0-9_\-\.]*)(?:\s*/\s*|\s+)[vV]?([0-9]+(?:\.[0-9A-Za-z_\-]+)*)\b"
)


def parse_client_from_user_agent(ua: str | None) -> str | None:
    """Dynamically parse client name and version from User-Agent (e.g. 'INCY 3.7.0')."""
    if not ua or not isinstance(ua, str):
        return None

    clean_ua = urllib.parse.unquote(ua).strip()
    for _ in range(3):
        new_ua = re.sub(r"\([^()]*\)", " ", clean_ua)
        if new_ua == clean_ua:
            break
        clean_ua = new_ua
    clean_ua = clean_ua.strip()

    for match in _CLIENT_TOKEN_RE.finditer(clean_ua):
        client_name, version = match.group(1), match.group(2)
        c_low = client_name.lower()
        if c_low in _IGNORED_CLIENTS:
            continue
        if any(c_low.startswith(p) for p in ("python", "curl", "wget", "okhttp", "cfnetwork", "urllib", "aiohttp", "go-http-client")):
            continue
        return f"{client_name} {version}"

    return None
