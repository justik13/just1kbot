"""INCY routing profile generator for split-tunneling deep links."""

from __future__ import annotations

import base64
import json


def generate_incy_routing_header(
    profile_name: str = "✦ Just1k",
    *,
    direct_ru: bool = True,
    last_updated: str = "1761625000",
) -> str:
    """Construct incy://routing/onadd/<base64> deep link header for split tunneling.

    - If direct_ru=True (Standard VLESS): routes domain:ru, domain:su, domain:xn--p1ai, geoip:ru directly
      via user's home ISP, sending foreign destinations through the VLESS tunnel.
    - If direct_ru=False (White Internet / CDN relay): sends all domain traffic through
      the Yandex CDN tunnel to the Moscow Origin server (where Origin routes RU directly
      from datacenter fiber and forwards foreign traffic to Exit), preserving LAN bypass.
    - Enforces secure DoH resolution (Cloudflare/Yandex DoH) and FakeDNS for instant TUN resolution.
    - Includes LastUpdated Unix timestamp for reliable profile versioning and updates in INCY.
    """
    clean_name = str(profile_name).strip()[:25] or "✦ Just1k"
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
            "domain:ru",
            "domain:su",
            "domain:xn--p1ai",
        ]
        if direct_ru
        else []
    )
    direct_ips = (["geoip:ru", "77.88.8.8"] + lan_direct_ips) if direct_ru else lan_direct_ips

    profile = {
        "Name": clean_name,
        "GlobalProxy": "true",
        "DomainStrategy": "IPIfNonMatch",
        "LastUpdated": str(last_updated).strip() or "1761625000",
        "RemoteDNSType": "DoH",
        "RemoteDNSDomain": "https://cloudflare-dns.com/dns-query",
        "RemoteDNSIP": "1.1.1.1",
        "DomesticDNSType": "DoH",
        "DomesticDNSDomain": "https://common.dot.dns.yandex.net/dns-query",
        "DomesticDNSIP": "77.88.8.8",
        "Geoipurl": "https://github.com/Loyalsoldier/v2ray-rules-dat/releases/latest/download/geoip.dat",
        "Geositeurl": "https://github.com/Loyalsoldier/v2ray-rules-dat/releases/latest/download/geosite.dat",
        "DnsHosts": {
            "cloudflare-dns.com": "1.1.1.1",
            "common.dot.dns.yandex.net": "77.88.8.8",
        },
        "DirectSites": direct_sites,
        "DirectIp": direct_ips,
        "ProxySites": [],
        "ProxyIp": [],
        "BlockSites": [],
        "BlockIp": [],
        "FakeDNS": "true",
    }
    json_bytes = json.dumps(profile, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    b64_profile = base64.b64encode(json_bytes).decode("ascii")
    return f"incy://routing/onadd/{b64_profile}"
