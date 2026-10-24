"""Parser for VPN client names and application versions from User-Agent headers."""

from __future__ import annotations

import re
import urllib.parse
from typing import Final

# Pre-compiled definitions for recognized VPN clients.
# Format: (canonical_display_name, compiled_regex)
# Specific variants (e.g. Clash Verge, Clash Meta) must appear before generic variants (Clash).
_VPN_CLIENT_RULES: Final[list[tuple[str, re.Pattern[str]]]] = [
    # Clash derivatives
    ("Clash Verge", re.compile(r"\b(?:clash[-_]?verge(?:[-_]?rev)?)(?=[^a-zA-Z0-9]|$)(?:(?:/|\s+)(?:v|V)?([0-9][0-9a-zA-Z.\-_]*))?", re.IGNORECASE)),
    ("Clash Meta", re.compile(r"\b(?:clash[-_.]?meta)(?=[^a-zA-Z0-9]|$)(?:(?:/|\s+)(?:v|V)?([0-9][0-9a-zA-Z.\-_]*))?", re.IGNORECASE)),
    ("Clash for Android", re.compile(r"\b(?:clash[-_]?for[-_]?android)(?=[^a-zA-Z0-9]|$)(?:(?:/|\s+)(?:v|V)?([0-9][0-9a-zA-Z.\-_]*))?", re.IGNORECASE)),
    ("Clash for Windows", re.compile(r"\b(?:clash[-_]?for[-_]?windows)(?=[^a-zA-Z0-9]|$)(?:(?:/|\s+)(?:v|V)?([0-9][0-9a-zA-Z.\-_]*))?", re.IGNORECASE)),
    ("Mihomo", re.compile(r"\b(?:mihomo(?:[-_]?party)?)(?=[^a-zA-Z0-9]|$)(?:(?:/|\s+)(?:v|V)?([0-9][0-9a-zA-Z.\-_]*))?", re.IGNORECASE)),
    ("FlClash", re.compile(r"\bflclash(?=[^a-zA-Z0-9]|$)(?:(?:/|\s+)(?:v|V)?([0-9][0-9a-zA-Z.\-_]*))?", re.IGNORECASE)),
    ("Clash", re.compile(r"\bclash(?=[^a-zA-Z0-9]|$)(?:(?:/|\s+)(?:v|V)?([0-9][0-9a-zA-Z.\-_]*))?", re.IGNORECASE)),

    # Happ
    ("Happ", re.compile(r"\b(?:happclient|happ)(?=[^a-zA-Z0-9]|$)(?:(?:/|\s+)(?:v|V)?([0-9][0-9a-zA-Z.\-_]*))?", re.IGNORECASE)),

    # INCY
    ("INCY", re.compile(r"\b(?:incyclient|incy)(?=[^a-zA-Z0-9]|$)(?:(?:/|\s+)(?:v|V)?([0-9][0-9a-zA-Z.\-_]*))?", re.IGNORECASE)),

    # v2rayNG
    ("v2rayNG", re.compile(r"\bv2rayng(?=[^a-zA-Z0-9]|$)(?:(?:/|\s+)(?:v|V)?([0-9][0-9a-zA-Z.\-_]*))?", re.IGNORECASE)),

    # FoXray
    ("FoXray", re.compile(r"\bfoxray(?=[^a-zA-Z0-9]|$)(?:(?:/|\s+)(?:v|V)?([0-9][0-9a-zA-Z.\-_]*))?", re.IGNORECASE)),

    # sing-box / SFI / SFM / SFA
    ("sing-box", re.compile(r"\b(?:sing[-_]?box|sfi|sfm|sfa)(?=[^a-zA-Z0-9]|$)(?:(?:/|\s+)(?:v|V)?([0-9][0-9a-zA-Z.\-_]*))?", re.IGNORECASE)),

    # Streisand
    ("Streisand", re.compile(r"\bstreisand(?=[^a-zA-Z0-9]|$)(?:(?:/|\s+)(?:v|V)?([0-9][0-9a-zA-Z.\-_]*))?", re.IGNORECASE)),

    # Shadowrocket
    ("Shadowrocket", re.compile(r"\bshadowrocket(?=[^a-zA-Z0-9]|$)(?:(?:/|\s+)(?:v|V)?([0-9][0-9a-zA-Z.\-_]*))?", re.IGNORECASE)),

    # Karing
    ("Karing", re.compile(r"\bkaring(?=[^a-zA-Z0-9]|$)(?:(?:/|\s+)(?:v|V)?([0-9][0-9a-zA-Z.\-_]*))?", re.IGNORECASE)),

    # Hiddify
    ("Hiddify", re.compile(r"\b(?:hiddifynext|hiddifycli|hiddify)(?=[^a-zA-Z0-9]|$)(?:(?:/|\s+)(?:v|V)?([0-9][0-9a-zA-Z.\-_]*))?", re.IGNORECASE)),

    # NekoBox / NekoRay
    ("NekoBox", re.compile(r"\b(?:nekoboxforandroid|nekobox)(?=[^a-zA-Z0-9]|$)(?:(?:/|\s+)(?:v|V)?([0-9][0-9a-zA-Z.\-_]*))?", re.IGNORECASE)),
    ("NekoRay", re.compile(r"\bnekoray(?=[^a-zA-Z0-9]|$)(?:(?:/|\s+)(?:v|V)?([0-9][0-9a-zA-Z.\-_]*))?", re.IGNORECASE)),

    # V2Box
    ("V2Box", re.compile(r"\bv2box(?=[^a-zA-Z0-9]|$)(?:(?:/|\s+)(?:v|V)?([0-9][0-9a-zA-Z.\-_]*))?", re.IGNORECASE)),

    # Surge
    ("Surge", re.compile(r"\bsurge(?:\s+(?:ios|mac))?(?=[^a-zA-Z0-9]|$)(?:(?:/|\s+)(?:v|V)?([0-9][0-9a-zA-Z.\-_]*))?", re.IGNORECASE)),

    # Loon
    ("Loon", re.compile(r"\bloon(?=[^a-zA-Z0-9]|$)(?:(?:/|\s+)(?:v|V)?([0-9][0-9a-zA-Z.\-_]*))?", re.IGNORECASE)),

    # Stash
    ("Stash", re.compile(r"\bstash(?=[^a-zA-Z0-9]|$)(?:(?:/|\s+)(?:v|V)?([0-9][0-9a-zA-Z.\-_]*))?", re.IGNORECASE)),

    # Quantumult X
    ("Quantumult X", re.compile(r"\b(?:quantumult[\s_%20]*x|quantumultx)(?=[^a-zA-Z0-9]|$)(?:(?:/|\s+)(?:v|V)?([0-9][0-9a-zA-Z.\-_]*))?", re.IGNORECASE)),

    # Wings X
    ("Wings X", re.compile(r"\b(?:wings[\s_%20]*x|wingsx)(?=[^a-zA-Z0-9]|$)(?:(?:/|\s+)(?:v|V)?([0-9][0-9a-zA-Z.\-_]*))?", re.IGNORECASE)),

    # Amnezia
    ("AmneziaVPN", re.compile(r"\b(?:amneziavpn|amnezia)(?=[^a-zA-Z0-9]|$)(?:(?:/|\s+)(?:v|V)?([0-9][0-9a-zA-Z.\-_]*))?", re.IGNORECASE)),
    ("AmneziaWG", re.compile(r"\bamneziawg(?=[^a-zA-Z0-9]|$)(?:(?:/|\s+)(?:v|V)?([0-9][0-9a-zA-Z.\-_]*))?", re.IGNORECASE)),

    # Xray & v2ray
    ("Xray", re.compile(r"\b(?:xray[-_]?core|xray)(?=[^a-zA-Z0-9]|$)(?:(?:/|\s+)(?:v|V)?([0-9][0-9a-zA-Z.\-_]*))?", re.IGNORECASE)),
    ("v2ray", re.compile(r"\b(?:v2ray[-_]?core|v2ray)(?=[^a-zA-Z0-9]|$)(?:(?:/|\s+)(?:v|V)?([0-9][0-9a-zA-Z.\-_]*))?", re.IGNORECASE)),

    # Other VPN clients
    ("Matsuri", re.compile(r"\b(?:matsuridayo|matsuri)(?=[^a-zA-Z0-9]|$)(?:(?:/|\s+)(?:v|V)?([0-9][0-9a-zA-Z.\-_]*))?", re.IGNORECASE)),
    ("BifrostV", re.compile(r"\bbifrostv(?=[^a-zA-Z0-9]|$)(?:(?:/|\s+)(?:v|V)?([0-9][0-9a-zA-Z.\-_]*))?", re.IGNORECASE)),
    ("Fair VPN", re.compile(r"\b(?:fair[-_\s]?vpn)(?=[^a-zA-Z0-9]|$)(?:(?:/|\s+)(?:v|V)?([0-9][0-9a-zA-Z.\-_]*))?", re.IGNORECASE)),
    ("Potatso", re.compile(r"\b(?:potatso(?:\s*2)?)(?=[^a-zA-Z0-9]|$)(?:(?:/|\s+)(?:v|V)?([0-9][0-9a-zA-Z.\-_]*))?", re.IGNORECASE)),
    ("Shadowsocks", re.compile(r"\b(?:shadowsocks[a-zA-Z0-9_\-]*)(?=[^a-zA-Z0-9]|$)(?:(?:/|\s+)(?:v|V)?([0-9][0-9a-zA-Z.\-_]*))?", re.IGNORECASE)),
]

# Blacklisted tokens that represent browsers, system networking stacks, OS/hardware,
# or generic dev tools, which must never be recognized as VPN client names.
_SYSTEM_AND_BROWSER_TOKENS: Final[frozenset[str]] = frozenset({
    # Browsers & rendering engines
    "mozilla",
    "chrome",
    "safari",
    "webkit",
    "applewebkit",
    "gecko",
    "firefox",
    "edge",
    "edg",
    "opera",
    "opr",
    "khtml",
    "crios",
    "fxios",
    "version",
    "mobile",
    # System networking libraries & tools
    "cfnetwork",
    "darwin",
    "okhttp",
    "curl",
    "wget",
    "python",
    "python-requests",
    "requests",
    "urllib",
    "urllib3",
    "httpx",
    "aiohttp",
    "go-http-client",
    "dalvik",
    "postmanruntime",
    "postman",
    "axios",
    "libfetch",
    "nsurlsessiond",
    "nsurlsession",
    "apache-httpclient",
    "java",
    "node-fetch",
    "undici",
    "req",
    # OS / platform keywords
    "windows",
    "android",
    "iphone",
    "ipad",
    "linux",
    "macintosh",
    "x11",
    "cpu",
    "intel",
    "arm64",
    "x86_64",
    # Internal & monitoring
    "just1kbot-monitor",
    "just1k",
    "monitor",
    "probe",
    "zabbix",
    "prometheus",
    "uptimerobot",
    "netdata",
    "nagios",
})

_GENERIC_TOKEN_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"\b([a-zA-Z][a-zA-Z0-9_.-]{1,31})/(?:v|V)?([0-9][0-9a-zA-Z.\-_]*)",
)


def _clean_version(raw_version: str | None) -> str:
    """Normalize and clean client version string."""
    if not raw_version:
        return ""
    ver = raw_version.strip()
    if ver.lower().startswith("v") and len(ver) > 1 and ver[1].isdigit():
        ver = ver[1:]
    ver = ver.rstrip(".,;:-_ ")
    return ver[:24]


def parse_vpn_client(ua: str | None) -> str | None:
    """Extract VPN client name and version from a User-Agent header.

    Returns:
        Formatted string like "Happ 5.5.0", "INCY 3.7.0", "sing-box 1.9.0",
        or "Happ" if version is absent.
        Returns None if User-Agent is empty, is a web browser, a system networking stack
        (CFNetwork, okhttp, curl), or no valid client is found.
    """
    if not ua or not isinstance(ua, str):
        return None

    unquoted = urllib.parse.unquote(ua).strip()
    if not unquoted:
        return None

    # 1. Match against known VPN clients
    for canonical_name, pattern in _VPN_CLIENT_RULES:
        match = pattern.search(unquoted)
        if match:
            raw_ver = match.group(1) if match.lastindex and match.lastindex >= 1 else None
            clean_ver = _clean_version(raw_ver)
            if clean_ver:
                return f"{canonical_name} {clean_ver}"
            return canonical_name

    # 2. Fallback: check for unknown client tokens (<Client>/<Version>)
    # while rejecting blacklisted browsers and system libraries.
    for match in _GENERIC_TOKEN_PATTERN.finditer(unquoted):
        name = match.group(1).strip()
        raw_ver = match.group(2)
        if name.lower() not in _SYSTEM_AND_BROWSER_TOKENS:
            clean_ver = _clean_version(raw_ver)
            if clean_ver:
                return f"{name} {clean_ver}"
            return name

    return None
