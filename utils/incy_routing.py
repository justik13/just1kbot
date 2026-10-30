"""INCY routing profile generator for split-tunneling deep links."""

from __future__ import annotations

import base64
import json

from config.constants import (
    DEFAULT_VLESS_PROFILE_TITLE,
    DEFAULT_WHITE_INTERNET_PROFILE_TITLE,
)

# Fixed version timestamp used to detect routing profile changes in INCY/Happ clients.
# Bump this timestamp when routing rules or DNS parameters change so clients pull updated rules.
ROUTING_PROFILE_VERSION_TIMESTAMP = "1793404800"


def generate_incy_routing_header(
    profile_name: str | None = None,
    *,
    direct_ru: bool = True,
    last_updated: str | int | None = None,
    scheme: str = "incy",
) -> str:
    """Construct incy:// or happ:// routing deep link header for split tunneling.

    - Supports both "incy" (INCY app) and "happ" (Happ app) schemes.
    - If direct_ru=True (Standard VLESS): routes geosite:category-ru, geosite:tld-ru, domain:ru, domain:su, domain:xn--p1ai directly
      via user's home ISP, while sending foreign destinations and raw UDP/voice/gaming traffic through the VLESS tunnel to avoid Windows Xray Direct UDP drops.
    - If direct_ru=False (White Internet / CDN relay): sends all domain traffic through
      the Yandex CDN tunnel to the Moscow Origin server (where Origin routes RU directly
      from datacenter fiber and forwards foreign traffic to Exit), preserving LAN bypass.
    - Enforces secure DoH resolution (Cloudflare/Google DoH) to prevent TSPU DNS hijacking.
    - Includes LastUpdated Unix timestamp for reliable profile versioning and updates.
    """
    default_name = DEFAULT_VLESS_PROFILE_TITLE if direct_ru else DEFAULT_WHITE_INTERNET_PROFILE_TITLE
    if profile_name is None:
        clean_name = default_name
    else:
        raw_name = str(profile_name).strip()
        clean_name = raw_name[:25] if (raw_name and raw_name.lower() != "none") else default_name

    if last_updated is None:
        clean_last_updated = ROUTING_PROFILE_VERSION_TIMESTAMP
    else:
        raw_updated = str(last_updated).strip()
        clean_last_updated = (
            raw_updated
            if (raw_updated and raw_updated.lower() != "none")
            else ROUTING_PROFILE_VERSION_TIMESTAMP
        )

    lan_direct_ips = [
        "10.0.0.0/8",
        "100.64.0.0/10",
        "172.16.0.0/12",
        "192.168.0.0/16",
        "169.254.0.0/16",
        "224.0.0.0/4",
        "255.255.255.255",
        "::1/128",
        "fc00::/7",
        "fe80::/10",
        "ff00::/8",
    ]
    direct_sites = (
        [
            "geosite:category-ru",
            "geosite:tld-ru",
            "domain:ru",
            "domain:su",
            "domain:xn--p1ai",
        ]
        if direct_ru
        else []
    )
    # Exclude geoip:ru from DirectIp to bypass Windows Xray Direct UDP binding bug (XTLS/Xray-core #5845).
    # All Russian web services match direct_sites and route direct; raw IP traffic (TeamSpeak, games)
    # flows through high-speed VLESS XUDP tunnel with Full-Cone NAT and zero packet loss.
    direct_ips = lan_direct_ips

    profile = {
        "Name": clean_name,
        "GlobalProxy": "true",
        "DomainStrategy": "IPIfNonMatch",
        "LastUpdated": clean_last_updated,
        "useChunkFiles": True,
        "RemoteDNSType": "DoH",
        "RemoteDNSDomain": "https://cloudflare-dns.com/dns-query",
        "RemoteDNSIP": "1.1.1.1",
        "DomesticDNSType": "DoH",
        "DomesticDNSDomain": "https://dns.google/dns-query",
        "DomesticDNSIP": "8.8.8.8",
        "Geoipurl": "https://github.com/Loyalsoldier/v2ray-rules-dat/releases/latest/download/geoip.dat",
        "Geositeurl": "https://github.com/Loyalsoldier/v2ray-rules-dat/releases/latest/download/geosite.dat",
        "DnsHosts": {
            "cloudflare-dns.com": "1.1.1.1",
            "dns.google": "8.8.8.8",
        },
        "DirectSites": direct_sites,
        "DirectIp": direct_ips,
        "ProxySites": [],
        "ProxyIp": [],
        "BlockSites": [],
        "BlockIp": [],
        "FakeDNS": "false",
    }
    json_bytes = json.dumps(profile, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    b64_profile = base64.b64encode(json_bytes).decode("ascii")
    clean_scheme = "happ" if (scheme and str(scheme).strip().lower() == "happ") else "incy"
    return f"{clean_scheme}://routing/onadd/{b64_profile}"
