"""Parser for VPN client names and application versions from User-Agent headers."""

from __future__ import annotations

import re
import urllib.parse
from typing import Final

# Pre-compiled definitions for recognized VPN clients.
# Format: (canonical_display_name, compiled_regex)
# Specific variants (e.g. Clash Verge, Clash Meta) must appear before generic variants (Clash).
_VPN_CLIENT_RULES: Final[list[tuple[str, re.Pattern[str]]]] = [
    # Clash derivatives & modern clients
    ("Clash Verge", re.compile(r"\bclash[-\s_]?verge(?:[-\s_]?rev)?(?=[^a-zA-Z0-9]|$)(?:(?:/|\s+)(?:v|V)?([0-9][0-9a-zA-Z.\-_]*))?", re.IGNORECASE)),
    ("Clash Meta", re.compile(r"\bclash[-\s_.]?meta(?=[^a-zA-Z0-9]|$)(?:(?:/|\s+)(?:v|V)?([0-9][0-9a-zA-Z.\-_]*))?", re.IGNORECASE)),
    ("Clash for Android", re.compile(r"\bclash[-\s_]?for[-\s_]?android(?=[^a-zA-Z0-9]|$)(?:(?:/|\s+)(?:v|V)?([0-9][0-9a-zA-Z.\-_]*))?", re.IGNORECASE)),
    ("Clash for Windows", re.compile(r"\bclash[-\s_]?for[-\s_]?windows(?=[^a-zA-Z0-9]|$)(?:(?:/|\s+)(?:v|V)?([0-9][0-9a-zA-Z.\-_]*))?", re.IGNORECASE)),
    ("ClashX", re.compile(r"\bclashx(?:[-\s_.]?(?:pro|meta))?(?=[^a-zA-Z0-9]|$)(?:(?:/|\s+)(?:v|V)?([0-9][0-9a-zA-Z.\-_]*))?", re.IGNORECASE)),
    ("Mihomo", re.compile(r"\bmihomo(?:[-\s_]?party)?(?=[^a-zA-Z0-9]|$)(?:(?:/|\s+)(?:v|V)?([0-9][0-9a-zA-Z.\-_]*))?", re.IGNORECASE)),
    ("FlClash", re.compile(r"\bflclash(?=[^a-zA-Z0-9]|$)(?:(?:/|\s+)(?:v|V)?([0-9][0-9a-zA-Z.\-_]*))?", re.IGNORECASE)),
    ("Clash", re.compile(r"\bclash(?=[^a-zA-Z0-9]|$)(?:(?:/|\s+)(?:v|V)?([0-9][0-9a-zA-Z.\-_]*))?", re.IGNORECASE)),

    # Happ
    ("Happ", re.compile(r"\bhapp(?:[-\s_]?client)?(?=[^a-zA-Z0-9]|$)(?:(?:/|\s+)(?:v|V)?([0-9][0-9a-zA-Z.\-_]*))?", re.IGNORECASE)),

    # INCY
    ("INCY", re.compile(r"\bincy(?:[-\s_]?client)?(?=[^a-zA-Z0-9]|$)(?:(?:/|\s+)(?:v|V)?([0-9][0-9a-zA-Z.\-_]*))?", re.IGNORECASE)),

    # v2rayNG & v2rayN
    ("v2rayNG", re.compile(r"\bv2rayng(?=[^a-zA-Z0-9]|$)(?:(?:/|\s+)(?:v|V)?([0-9][0-9a-zA-Z.\-_]*))?", re.IGNORECASE)),
    ("v2rayN", re.compile(r"\bv2rayn(?=[^a-zA-Z0-9]|$)(?:(?:/|\s+)(?:v|V)?([0-9][0-9a-zA-Z.\-_]*))?", re.IGNORECASE)),

    # FoXray
    ("FoXray", re.compile(r"\bfoxray(?=[^a-zA-Z0-9]|$)(?:(?:/|\s+)(?:v|V)?([0-9][0-9a-zA-Z.\-_]*))?", re.IGNORECASE)),

    # sing-box / SFI / SFM / SFA
    ("sing-box", re.compile(r"\b(?:sing[-\s_]?box|sfi|sfm|sfa)(?=[^a-zA-Z0-9]|$)(?:(?:/|\s+)(?:v|V)?([0-9][0-9a-zA-Z.\-_]*))?", re.IGNORECASE)),

    # Streisand
    ("Streisand", re.compile(r"\bstreisand(?=[^a-zA-Z0-9]|$)(?:(?:/|\s+)(?:v|V)?([0-9][0-9a-zA-Z.\-_]*))?", re.IGNORECASE)),

    # Shadowrocket
    ("Shadowrocket", re.compile(r"\bshadowrocket(?=[^a-zA-Z0-9]|$)(?:(?:/|\s+)(?:v|V)?([0-9][0-9a-zA-Z.\-_]*))?", re.IGNORECASE)),

    # Karing
    ("Karing", re.compile(r"\bkaring(?=[^a-zA-Z0-9]|$)(?:(?:/|\s+)(?:v|V)?([0-9][0-9a-zA-Z.\-_]*))?", re.IGNORECASE)),

    # Hiddify
    ("Hiddify", re.compile(r"\bhiddify(?:[-\s_]?(?:next|cli))?(?=[^a-zA-Z0-9]|$)(?:(?:/|\s+)(?:v|V)?([0-9][0-9a-zA-Z.\-_]*))?", re.IGNORECASE)),

    # NekoBox / NekoRay
    ("NekoBox", re.compile(r"\bnekobox(?:[-\s_]?for[-\s_]?android)?(?=[^a-zA-Z0-9]|$)(?:(?:/|\s+)(?:v|V)?([0-9][0-9a-zA-Z.\-_]*))?", re.IGNORECASE)),
    ("NekoRay", re.compile(r"\bnekoray(?=[^a-zA-Z0-9]|$)(?:(?:/|\s+)(?:v|V)?([0-9][0-9a-zA-Z.\-_]*))?", re.IGNORECASE)),

    # V2Box
    ("V2Box", re.compile(r"\bv2box(?=[^a-zA-Z0-9]|$)(?:(?:/|\s+)(?:v|V)?([0-9][0-9a-zA-Z.\-_]*))?", re.IGNORECASE)),

    # Surge
    ("Surge", re.compile(r"\bsurge(?:[-\s_]+(?:ios|mac))?(?=[^a-zA-Z0-9]|$)(?:(?:/|\s+)(?:v|V)?([0-9][0-9a-zA-Z.\-_]*))?", re.IGNORECASE)),

    # Loon
    ("Loon", re.compile(r"\bloon(?=[^a-zA-Z0-9]|$)(?:(?:/|\s+)(?:v|V)?([0-9][0-9a-zA-Z.\-_]*))?", re.IGNORECASE)),

    # Stash
    ("Stash", re.compile(r"\bstash(?=[^a-zA-Z0-9]|$)(?:(?:/|\s+)(?:v|V)?([0-9][0-9a-zA-Z.\-_]*))?", re.IGNORECASE)),

    # Quantumult X & Quantumult
    ("Quantumult X", re.compile(r"\bquantumult[-\s_]*x(?=[^a-zA-Z0-9]|$)(?:(?:/|\s+)(?:v|V)?([0-9][0-9a-zA-Z.\-_]*))?", re.IGNORECASE)),
    ("Quantumult", re.compile(r"\bquantumult(?=[^a-zA-Z0-9]|$)(?:(?:/|\s+)(?:v|V)?([0-9][0-9a-zA-Z.\-_]*))?", re.IGNORECASE)),

    # Wings X
    ("Wings X", re.compile(r"\bwings[-\s_]*x(?=[^a-zA-Z0-9]|$)(?:(?:/|\s+)(?:v|V)?([0-9][0-9a-zA-Z.\-_]*))?", re.IGNORECASE)),

    # Amnezia
    ("AmneziaVPN", re.compile(r"\bamnezia(?:[-\s_]?vpn)?(?=[^a-zA-Z0-9]|$)(?:(?:/|\s+)(?:v|V)?([0-9][0-9a-zA-Z.\-_]*))?", re.IGNORECASE)),
    ("AmneziaWG", re.compile(r"\bamnezia[-\s_]?wg(?=[^a-zA-Z0-9]|$)(?:(?:/|\s+)(?:v|V)?([0-9][0-9a-zA-Z.\-_]*))?", re.IGNORECASE)),

    # Xray & v2ray
    ("Xray", re.compile(r"\bxray(?:[-\s_]?core)?(?=[^a-zA-Z0-9]|$)(?:(?:/|\s+)(?:v|V)?([0-9][0-9a-zA-Z.\-_]*))?", re.IGNORECASE)),
    ("v2ray", re.compile(r"\bv2ray(?:[-\s_]?core)?(?=[^a-zA-Z0-9]|$)(?:(?:/|\s+)(?:v|V)?([0-9][0-9a-zA-Z.\-_]*))?", re.IGNORECASE)),

    # Egern & OneClick
    ("Egern", re.compile(r"\begern(?=[^a-zA-Z0-9]|$)(?:(?:/|\s+)(?:v|V)?([0-9][0-9a-zA-Z.\-_]*))?", re.IGNORECASE)),
    ("OneClick", re.compile(r"\bone[-\s_]?click(?=[^a-zA-Z0-9]|$)(?:(?:/|\s+)(?:v|V)?([0-9][0-9a-zA-Z.\-_]*))?", re.IGNORECASE)),

    # Other VPN clients
    ("Matsuri", re.compile(r"\b(?:matsuridayo|matsuri)(?=[^a-zA-Z0-9]|$)(?:(?:/|\s+)(?:v|V)?([0-9][0-9a-zA-Z.\-_]*))?", re.IGNORECASE)),
    ("BifrostV", re.compile(r"\bbifrostv(?=[^a-zA-Z0-9]|$)(?:(?:/|\s+)(?:v|V)?([0-9][0-9a-zA-Z.\-_]*))?", re.IGNORECASE)),
    ("Fair VPN", re.compile(r"\bfair[-\s_]?vpn(?=[^a-zA-Z0-9]|$)(?:(?:/|\s+)(?:v|V)?([0-9][0-9a-zA-Z.\-_]*))?", re.IGNORECASE)),
    ("Potatso", re.compile(r"\bpotatso(?:[-\s_]*2)?(?=[^a-zA-Z0-9]|$)(?:(?:/|\s+)(?:v|V)?([0-9][0-9a-zA-Z.\-_]*))?", re.IGNORECASE)),
    ("Shadowsocks", re.compile(r"\bshadowsocks(?:[-\s_]?[a-zA-Z0-9_\-]+)?(?=[^a-zA-Z0-9]|$)(?:(?:/|\s+)(?:v|V)?([0-9][0-9a-zA-Z.\-_]*))?", re.IGNORECASE)),
    ("Outline", re.compile(r"\boutline(?=[^a-zA-Z0-9]|$)(?:(?:/|\s+)(?:v|V)?([0-9][0-9a-zA-Z.\-_]*))?", re.IGNORECASE)),
    ("WireGuard", re.compile(r"\bwireguard(?=[^a-zA-Z0-9]|$)(?:(?:/|\s+)(?:v|V)?([0-9][0-9a-zA-Z.\-_]*))?", re.IGNORECASE)),
]

# Patterns that unmistakably signify web browsers or rendering engines.
_BROWSER_SIGNATURE_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"\b(?:mozilla/\d|applewebkit/\d|gecko/\d|safari/\d|chrome/\d|firefox/\d|edg(?:e|a|ios)?/\d|"
    r"opera/\d|opr/\d|yabrowser/\d|samsungbrowser/\d|ucbrowser/\d|vivaldi/\d|miuibrowser/\d|"
    r"brave/\d|duckduckgo/\d|crios/\d|fxios/\d|khtml/\d|trident/\d)\b|"
    r"\b[a-zA-Z0-9_.-]*browser\b",
    re.IGNORECASE,
)

# Patterns that signify system HTTP libraries, tools, runtimes, bots, probes.
_SYSTEM_STACK_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"\b(?:cfnetwork/\d|darwin/\d|okhttp/\d|curl/\d|libcurl/\d|wget/\d|python(?:-urllib|-requests)?/\d|"
    r"urllib3?/\d|httpx/\d|aiohttp/\d|go-http-client/\d|dalvik/\d|postman(?:runtime)?/\d|"
    r"axios/\d|node-fetch/\d|undici/\d|reqwest/\d|guzzle(?:http)?/\d|winhttp(?:\.dll)?/\d|"
    r"nsurlsessiond?/\d|apache-httpclient/\d|java/\d|faraday/\d|dart/\d)\b|"
    r"\b(?:just1kbot-monitor|just1k|uptimerobot|zabbix|prometheus|netdata|nagios)\b",
    re.IGNORECASE,
)

# Blacklisted individual tokens for fallback matching.
_BLACKLISTED_TOKENS: Final[frozenset[str]] = frozenset({
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
    "yabrowser",
    "samsungbrowser",
    "ucbrowser",
    "vivaldi",
    "miuibrowser",
    "brave",
    "duckduckgo",
    "whale",
    "electron",
    "browser",
    # System networking libraries & tools
    "cfnetwork",
    "darwin",
    "okhttp",
    "curl",
    "libcurl",
    "wget",
    "python",
    "python-requests",
    "python-urllib",
    "requests",
    "urllib",
    "urllib3",
    "httpx",
    "aiohttp",
    "go-http-client",
    "dalvik",
    "dart",
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
    "reqwest",
    "guzzle",
    "guzzlehttp",
    "winhttp",
    "faraday",
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
    "like",
    "build",
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
    ver = raw_version.strip().strip("()[]")
    if ver.lower().startswith("v") and len(ver) > 1 and ver[1].isdigit():
        ver = ver[1:]
    ver = ver.rstrip(".,;:-_ )]")
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

    # 2. Reject browsers and system networking stacks before falling back to unknown clients
    if _BROWSER_SIGNATURE_PATTERN.search(unquoted):
        return None
    if _SYSTEM_STACK_PATTERN.search(unquoted):
        return None

    # 3. Fallback: check for clean unknown client tokens (<Client>/<Version>)
    for match in _GENERIC_TOKEN_PATTERN.finditer(unquoted):
        name = match.group(1).strip()
        raw_ver = match.group(2)
        name_lower = name.lower()
        if (
            name_lower not in _BLACKLISTED_TOKENS
            and not name_lower.endswith("browser")
            and not any(b in name_lower for b in ("mozilla", "webkit", "gecko", "chrome", "safari", "python", "curl", "http"))
        ):
            clean_ver = _clean_version(raw_ver)
            if clean_ver:
                return f"{name} {clean_ver}"
            return name

    return None
