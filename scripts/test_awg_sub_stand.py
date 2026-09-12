#!/usr/bin/env python3
"""MVP Test Stand for AmneziaWG Multi-Server Subscriptions in INCY.

Features:
- Serves GET /sub/awg/{token}
- Captures and logs all INCY headers (x-hwid, x-device-os, x-device-model)
- Implements slot-to-HWID mapping (Slot 1, Slot 2)
- Enforces device limit (max 2 devices) with HTTP 403 response
- Delivers multi-server awg:// Base64 feed compatible with INCY (iOS/Android)
- Supports /status and /reset endpoints for easy testing
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import logging
import os
import sys
from datetime import datetime, timezone
from typing import Sequence, Tuple
from aiohttp import web

# Ensure repository root is on sys.path if running within repository
try:
    _repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if _repo_root not in sys.path:
        sys.path.insert(0, _repo_root)
    from services.awg_subscription_feed_service import AWGSubscriptionFeedService
except Exception:
    # Standalone mode: fallback definition for running directly on VPS (/tmp/awg_stand) without repo
    class AWGSubscriptionFeedService:
        @staticmethod
        def encode_config_to_awg_uri(conf: str, server_name: str, country_flag: str = "") -> str:
            if not conf or not conf.strip():
                raise ValueError("Configuration text cannot be empty")
            clean_conf = conf.strip()
            lower_conf = clean_conf.lower()
            if "[interface]" not in lower_conf or "privatekey" not in lower_conf:
                raise ValueError("Configuration must contain a valid [Interface] section with PrivateKey")
            if "[peer]" not in lower_conf or "endpoint" not in lower_conf:
                raise ValueError("Configuration must contain a valid [Peer] section with Endpoint")
            b64_conf = base64.urlsafe_b64encode(clean_conf.encode("utf-8")).decode("ascii")
            clean_name = (server_name or "Server").strip()
            clean_flag = (country_flag or "").strip()
            fragment = f"{clean_flag} {clean_name}".strip() if clean_flag else clean_name
            return f"awg://{b64_conf}#{fragment}"

        @staticmethod
        def sort_servers(
            server_configs: Sequence[Tuple[str, str, str, int | float | None]],
            mode: str = "ping",
        ) -> list[Tuple[str, str, str]]:
            items = list(server_configs)
            if mode == "ping":
                def ping_key(x: Tuple[str, str, str, int | float | None]) -> float:
                    lat = x[3] if len(x) > 3 else None
                    return float(lat) if (isinstance(lat, (int, float)) and lat > 0) else 999999.0
                items.sort(key=ping_key)
            elif mode == "name":
                items.sort(key=lambda x: (x[1] or "").lower())
            return [(item[0], item[1], item[2]) for item in items]

        @classmethod
        def build_subscription_body(
            cls,
            server_configs: Sequence[Tuple[str, str, str]],
            inline_metadata: list[str] | None = None,
        ) -> str:
            if not server_configs and not inline_metadata:
                return ""
            lines = []
            if inline_metadata:
                for meta in inline_metadata:
                    if meta and meta.strip():
                        lines.append(meta.strip())
            for item in server_configs:
                conf, name, flag = item
                try:
                    uri = cls.encode_config_to_awg_uri(conf, name, flag)
                    lines.append(uri)
                except Exception:
                    continue
            if not lines:
                return ""
            raw_payload = "\n".join(lines)
            return base64.b64encode(raw_payload.encode("utf-8")).decode("ascii")

        @staticmethod
        def build_subscription_headers(
            *,
            profile_title: str = "JUST1K VPN",
            expire_ts: int = 0,
            upload_bytes: int = 0,
            download_bytes: int = 0,
            total_quota_bytes: int = 0,
            update_interval_hours: int = 6,
            support_url: str | None = None,
            hide_url: bool = True,
            hide_check: bool = False,
            web_page_url: str | None = None,
            support_email: str | None = None,
            announce: str | None = None,
            announce_url: str | None = None,
            sort_order: str | None = None,
            profile_description: str | None = None,
            banner_text: str | None = None,
            banner_button_text: str | None = None,
            banner_button_url: str | None = None,
            banner_bg_color: str | None = None,
            banner_button_color: str | None = None,
        ) -> dict[str, str]:
            title_b64 = base64.b64encode(profile_title.strip().encode("utf-8")).decode("ascii")
            headers = {
                "Content-Type": "text/plain; charset=utf-8",
                "Cache-Control": "no-store, private, no-cache, must-revalidate",
                "Pragma": "no-cache",
                "X-Content-Type-Options": "nosniff",
                "Profile-Title": f"base64:{title_b64}",
                "Profile-Update-Interval": str(max(1, update_interval_hours)),
                "Subscription-Userinfo": (
                    f"upload={max(0, upload_bytes)};"
                    f"download={max(0, download_bytes)};"
                    f"total={max(0, total_quota_bytes)};"
                    f"expire={max(0, expire_ts)}"
                ),
            }
            if hide_url:
                headers["hide-url"] = "1"
            if hide_check:
                headers["hide-check"] = "1"
            if profile_description and profile_description.strip():
                desc_b64 = base64.b64encode(profile_description.strip().encode("utf-8")).decode("ascii")
                headers["profile-description"] = f"base64:{desc_b64}"
            if support_url and support_url.strip():
                headers["support-url"] = support_url.strip()
            if web_page_url and web_page_url.strip():
                headers["profile-web-page-url"] = web_page_url.strip()
            if support_email and support_email.strip():
                headers["support-email"] = support_email.strip()
            if sort_order and sort_order.strip():
                headers["sort-order"] = sort_order.strip()
            if announce and announce.strip():
                ann_b64 = base64.b64encode(announce.strip().encode("utf-8")).decode("ascii")
                headers["announce"] = f"base64:{ann_b64}"
            if announce_url and announce_url.strip():
                headers["announce-url"] = announce_url.strip()
            if banner_text and banner_text.strip():
                b_b64 = base64.b64encode(banner_text.strip().encode("utf-8")).decode("ascii")
                headers["banner-text"] = f"base64:{b_b64}"
            if banner_button_text and banner_button_text.strip():
                headers["banner-button-text"] = banner_button_text.strip()
            if banner_button_url and banner_button_url.strip():
                headers["banner-button-url"] = banner_button_url.strip()
            if banner_bg_color and banner_bg_color.strip():
                headers["banner-bg-color"] = banner_bg_color.strip()
            if banner_button_color and banner_button_color.strip():
                headers["banner-button-color"] = banner_button_color.strip()
            return headers


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("awg_stand")

# ---------------------------------------------------------------------------
# Default Test Configurations (Slot 1 & Slot 2 for Netherlands & Poland)
# ---------------------------------------------------------------------------

SLOT_1_NETHERLANDS = """[Interface]
Address = 10.8.0.2/32
DNS = 8.8.8.8, 8.8.4.4
MTU = 1280
PrivateKey = uC6xUgdQDF4+fAOiw37ZQCG7XljilDsnBCl7VH7bAl8=
Jc = 4
Jmin = 10
Jmax = 50
S1 = 79
S2 = 115
S3 = 5
S4 = 1
H1 = 169154911-1234371153
H2 = 2057051984-2121122945
H3 = 2132872968-2133668229
H4 = 2136455412-2141801388

[Peer]
PublicKey = bmXOC+F1FxEMF9dyiK2H5/1SUtzH0JuVo51h2wPfgyo=
AllowedIPs = 0.0.0.0/0, ::/0
Endpoint = 195.133.1.20:443
PersistentKeepalive = 25
"""

SLOT_1_POLAND = """[Interface]
Address = 10.8.1.2/32
DNS = 1.1.1.1, 1.0.0.1
MTU = 1280
PrivateKey = iD8yVheREG5+gBPjx48aRDH8YmkjmEtoCDm8WI8cBm9=
Jc = 3
Jmin = 15
Jmax = 45
S1 = 82
S2 = 110
S3 = 8
S4 = 2
H1 = 170000000-1200000000
H2 = 200000000-2100000000
H3 = 210000000-2130000000
H4 = 213000000-2140000000

[Peer]
PublicKey = cnYPD+G2GyFNF0eziL3H6/2TVu0I1KxWp62i3xQghzp=
AllowedIPs = 0.0.0.0/0, ::/0
Endpoint = 212.77.98.9:443
PersistentKeepalive = 25
"""

SLOT_2_NETHERLANDS = """[Interface]
Address = 10.8.0.3/32
DNS = 8.8.8.8, 8.8.4.4
MTU = 1280
PrivateKey = aB1cDefGHIJ2+kLMno34PQRSTUvwxyz0123456789A=
Jc = 4
Jmin = 10
Jmax = 50
S1 = 79
S2 = 115
S3 = 5
S4 = 1
H1 = 169154911-1234371153
H2 = 2057051984-2121122945
H3 = 2132872968-2133668229
H4 = 2136455412-2141801388

[Peer]
PublicKey = bmXOC+F1FxEMF9dyiK2H5/1SUtzH0JuVo51h2wPfgyo=
AllowedIPs = 0.0.0.0/0, ::/0
Endpoint = 195.133.1.20:443
PersistentKeepalive = 25
"""

SLOT_2_POLAND = """[Interface]
Address = 10.8.1.3/32
DNS = 1.1.1.1, 1.0.0.1
MTU = 1280
PrivateKey = zY9xWvuTSR8+qPONml65KJIHGFEDCba0987654321Z=
Jc = 3
Jmin = 15
Jmax = 45
S1 = 82
S2 = 110
S3 = 8
S4 = 2
H1 = 170000000-1200000000
H2 = 200000000-2100000000
H3 = 210000000-2130000000
H4 = 213000000-2140000000

[Peer]
PublicKey = cnYPD+G2GyFNF0eziL3H6/2TVu0I1KxWp62i3xQghzp=
AllowedIPs = 0.0.0.0/0, ::/0
Endpoint = 212.77.98.9:443
PersistentKeepalive = 25
"""

SLOT_1_GERMANY = """[Interface]
Address = 10.8.2.2/32
DNS = 8.8.8.8, 1.1.1.1
MTU = 1280
PrivateKey = gH9xWvuTSR8+qPONml65KJIHGFEDCba0987654321Z=
Jc = 4
Jmin = 10
Jmax = 50
S1 = 80
S2 = 112
S3 = 6
S4 = 3
H1 = 169000000-1200000000
H2 = 200000000-2100000000
H3 = 210000000-2130000000
H4 = 213000000-2140000000

[Peer]
PublicKey = bmXOC+F1FxEMF9dyiK2H5/1SUtzH0JuVo51h2wPfgyo=
AllowedIPs = 0.0.0.0/0, ::/0
Endpoint = 159.69.0.1:443
PersistentKeepalive = 25
"""

SLOT_2_GERMANY = """[Interface]
Address = 10.8.2.3/32
DNS = 8.8.8.8, 1.1.1.1
MTU = 1280
PrivateKey = kL9xWvuTSR8+qPONml65KJIHGFEDCba0987654321Z=
Jc = 4
Jmin = 10
Jmax = 50
S1 = 80
S2 = 112
S3 = 6
S4 = 3
H1 = 169000000-1200000000
H2 = 200000000-2100000000
H3 = 210000000-2130000000
H4 = 213000000-2140000000

[Peer]
PublicKey = bmXOC+F1FxEMF9dyiK2H5/1SUtzH0JuVo51h2wPfgyo=
AllowedIPs = 0.0.0.0/0, ::/0
Endpoint = 159.69.0.1:443
PersistentKeepalive = 25
"""

SLOT_1_SWEDEN = """[Interface]
Address = 10.8.3.2/32
DNS = 1.1.1.1, 9.9.9.9
MTU = 1280
PrivateKey = pQ9xWvuTSR8+qPONml65KJIHGFEDCba0987654321Z=
Jc = 3
Jmin = 20
Jmax = 60
S1 = 85
S2 = 120
S3 = 7
S4 = 4
H1 = 175000000-1250000000
H2 = 205000000-2150000000
H3 = 215000000-2180000000
H4 = 218000000-2190000000

[Peer]
PublicKey = cnYPD+G2GyFNF0eziL3H6/2TVu0I1KxWp62i3xQghzp=
AllowedIPs = 0.0.0.0/0, ::/0
Endpoint = 194.58.112.18:443
PersistentKeepalive = 25
"""

SLOT_2_SWEDEN = """[Interface]
Address = 10.8.3.3/32
DNS = 1.1.1.1, 9.9.9.9
MTU = 1280
PrivateKey = uV9xWvuTSR8+qPONml65KJIHGFEDCba0987654321Z=
Jc = 3
Jmin = 20
Jmax = 60
S1 = 85
S2 = 120
S3 = 7
S4 = 4
H1 = 175000000-1250000000
H2 = 205000000-2150000000
H3 = 215000000-2180000000
H4 = 218000000-2190000000

[Peer]
PublicKey = cnYPD+G2GyFNF0eziL3H6/2TVu0I1KxWp62i3xQghzp=
AllowedIPs = 0.0.0.0/0, ::/0
Endpoint = 194.58.112.18:443
PersistentKeepalive = 25
"""

# In-memory test state & simulation controls
REGISTERED_DEVICES: dict[str, dict] = {}
DEVICE_LIMIT = 2
VALID_TOKEN = "test_awg_vip_token"

ENDPOINT_LATENCY_CACHE: dict[str, tuple[float | None, float]] = {}


async def measure_endpoint_latency(endpoint: str, timeout: float = 1.2) -> float | None:
    """Measure real TCP connection latency in milliseconds to host:port."""
    try:
        host, port_str = endpoint.strip().split(":")
        t0 = asyncio.get_event_loop().time()
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(host, int(port_str)),
            timeout=timeout,
        )
        writer.close()
        await writer.wait_closed()
        return round((asyncio.get_event_loop().time() - t0) * 1000, 1)
    except Exception:
        return None


async def get_real_latency(endpoint: str) -> float | None:
    now = asyncio.get_event_loop().time()
    if endpoint in ENDPOINT_LATENCY_CACHE:
        val, ts = ENDPOINT_LATENCY_CACHE[endpoint]
        if now - ts < 15.0:  # 15 seconds TTL for fast cache
            return val
    val = await measure_endpoint_latency(endpoint)
    ENDPOINT_LATENCY_CACHE[endpoint] = (val, now)
    return val


def extract_endpoint_from_conf(conf: str) -> str:
    for line in conf.splitlines():
        clean = line.strip()
        if clean.startswith("Endpoint"):
            parts = clean.split("=", 1)
            if len(parts) == 2:
                return parts[1].strip()
    return ""


TEST_STATE = {
    "se": True,
    "nl": True,
    "pl": True,
    "de": True,
    "quota_exhausted": False,
    "expired": False,
    "block_403": False,
    "title_suffix": "",
    "btn_web": True,            # profile-web-page-url (Кнопка «Сайт / Бот»)
    "btn_tg": True,             # support-url (Кнопка «Telegram-поддержка»)
    "btn_email": True,          # support-email (Кнопка «Email»)
    "sort_mode": "ping",        # "ping" | "name" | "none"
    "show_banner": True,        # In-App интерактивное объявление / баннер (announce + announce-url)
    "hide_check": False,        # hide-check: 1 (Скрыть кнопку «Проверить» на главном экране)
}


# Optional custom live configurations for Slot 1 and Slot 2
CUSTOM_AWG_SLOT_1: str = os.environ.get("CUSTOM_AWG_SLOT_1", "")
CUSTOM_AWG_SLOT_2: str = os.environ.get("CUSTOM_AWG_SLOT_2", "")


async def get_slot_configs(slot: int) -> list[tuple[str, str, str]]:
    sort_mode = TEST_STATE.get("sort_mode", "ping")

    raw_candidates = []
    if TEST_STATE.get("nl"):
        if slot == 1 and CUSTOM_AWG_SLOT_1.strip():
            conf = CUSTOM_AWG_SLOT_1.strip()
        elif slot == 2 and CUSTOM_AWG_SLOT_2.strip():
            conf = CUSTOM_AWG_SLOT_2.strip()
        else:
            conf = SLOT_1_NETHERLANDS if slot == 1 else SLOT_2_NETHERLANDS
        raw_candidates.append((conf, "Нидерланды", "🇳🇱"))
    if TEST_STATE.get("pl"):
        conf = SLOT_1_POLAND if slot == 1 else SLOT_2_POLAND
        raw_candidates.append((conf, "Польша", "🇵🇱"))
    if TEST_STATE.get("de"):
        conf = SLOT_1_GERMANY if slot == 1 else SLOT_2_GERMANY
        raw_candidates.append((conf, "Германия", "🇩🇪"))
    if TEST_STATE.get("se"):
        conf = SLOT_1_SWEDEN if slot == 1 else SLOT_2_SWEDEN
        raw_candidates.append((conf, "Швеция", "🇸🇪"))

    servers = []
    if sort_mode == "ping":
        # Measure real live latency to endpoints concurrently
        tasks = [get_real_latency(extract_endpoint_from_conf(c)) for c, _, _ in raw_candidates]
        latencies = await asyncio.gather(*tasks)
        for (c, name, flag), lat in zip(raw_candidates, latencies):
            logger.info("📡 [PING PROBE] %s -> %s ms", name, lat)
            servers.append((c, name, flag, lat))
    else:
        for c, name, flag in raw_candidates:
            servers.append((c, name, flag, None))

    return AWGSubscriptionFeedService.sort_servers(servers, mode=sort_mode)



async def handle_subscription_feed(request: web.Request) -> web.Response:
    token = request.match_info.get("token", "").strip()
    client_ip = request.headers.get("X-Forwarded-For") or request.remote or "unknown"

    logger.info("==================================================")
    logger.info("📥 Incoming Subscription Request from %s", client_ip)
    logger.info("   Path: %s", request.path)
    logger.info("   User-Agent: %s", request.headers.get("User-Agent", "N/A"))

    # Extract INCY specific headers
    hwid = (
        request.headers.get("x-hwid")
        or request.headers.get("X-HWID")
        or request.headers.get("X-Device-ID")
        or request.headers.get("x-device-id")
        or ""
    ).strip()
    device_os = request.headers.get("x-device-os", "Unknown OS")
    device_model = request.headers.get("x-device-model", "Unknown Model")

    def _mask_val(key: str, val: str) -> str:
        k = key.lower()
        if any(s in k for s in ("hwid", "token", "auth", "secret", "cookie")):
            return f"{val[:6]}...***" if len(val) > 6 else "***"
        return val

    logger.info("   Headers detected:")
    for h_name, h_val in request.headers.items():
        logger.info("     [%s]: %s", h_name, _mask_val(h_name, h_val))

    if token != VALID_TOKEN:
        logger.warning("❌ Invalid subscription token: %s", token)
        return web.Response(status=404, text="Not Found")

    # Check if request comes from a real web browser (navigation event) vs INCY app client
    accept_header = request.headers.get("Accept", "")
    user_agent = request.headers.get("User-Agent", "")
    sec_dest = request.headers.get("Sec-Fetch-Dest", "")
    sec_mode = request.headers.get("Sec-Fetch-Mode", "")
    force_raw = request.query.get("raw") == "1"
    
    # Strictly identify INCY app clients (iOS, Android, Windows Electron, Dalvik)
    is_incy_client = (
        "INCY" in user_agent
        or "Dalvik" in user_agent
        or bool(hwid)
        or bool(request.headers.get("x-client"))
        or bool(request.headers.get("x-app-version"))
        or bool(request.headers.get("x-device-os"))
    )

    # Real browser page navigation sends Sec-Fetch-Dest: document and wants text/html
    is_browser = (
        not force_raw
        and not is_incy_client
        and "text/html" in accept_header
        and (sec_dest == "document" or sec_mode == "navigate" or ("Mozilla" in user_agent and "Chrome" in user_agent))
    )

    if is_browser:
        logger.info("📱 Web browser detected. Rendering 1-Click INCY import page.")
        # Ensure scheme is https:// (Nginx reverse proxy sets X-Forwarded-Proto: https)
        proto = request.headers.get("X-Forwarded-Proto") or ("https" if "best" in request.host or "online" in request.host else request.scheme)
        host = request.headers.get("Host", request.host)
        sub_full_url = f"{proto}://{host}{request.path}"
        deep_link = f"incy://add/{sub_full_url}"
        html_content = f"""<!doctype html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Подписка JUST1K AmneziaWG</title>
  <style>
    :root {{ --bg: #0f172a; --card: #1e293b; --text: #f8fafc; --sub: #94a3b8; --accent: #10b981; --border: #334155; }}
    * {{ box-sizing: border-box; margin: 0; padding: 0; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; }}
    body {{ background: var(--bg); color: var(--text); display: flex; align-items: center; justify-content: center; min-height: 100vh; padding: 16px; }}
    .card {{ background: var(--card); border: 1px solid var(--border); border-radius: 20px; padding: 32px 24px; max-width: 440px; width: 100%; text-align: center; box-shadow: 0 20px 25px -5px rgba(0,0,0,0.5); }}
    .icon {{ font-size: 48px; margin-bottom: 16px; }}
    h1 {{ font-size: 22px; font-weight: 700; margin-bottom: 8px; }}
    p {{ color: var(--sub); font-size: 14px; line-height: 1.5; margin-bottom: 24px; }}
    .btn {{ display: block; width: 100%; padding: 16px; background: var(--accent); color: #fff; text-decoration: none; font-weight: 600; font-size: 16px; border-radius: 12px; margin-bottom: 12px; transition: transform 0.1s, opacity 0.2s; border: none; cursor: pointer; }}
    .btn:active {{ transform: scale(0.98); opacity: 0.9; }}
    .btn-outline {{ background: transparent; border: 1px solid var(--border); color: var(--text); }}
    .servers {{ background: rgba(0,0,0,0.2); border-radius: 12px; padding: 12px; margin-bottom: 20px; text-align: left; font-size: 13px; color: var(--sub); }}
    .server-item {{ display: flex; align-items: center; gap: 8px; margin-bottom: 6px; color: var(--text); font-weight: 500; }}
    .server-item:last-child {{ margin-bottom: 0; }}
    .tag {{ background: #047857; color: #a7f3d0; font-size: 10px; padding: 2px 6px; border-radius: 6px; margin-left: auto; text-transform: uppercase; }}
  </style>
</head>
<body>
  <div class="card">
    <div class="icon">🛡️</div>
    <h1>Подписка AmneziaWG</h1>
    <p>Подписка готова к добавлению в приложение INCY на вашем устройстве.</p>
    
    <div class="servers">
      <div style="font-size: 11px; text-transform: uppercase; letter-spacing: 0.05em; color: #64748b; margin-bottom: 8px;">Доступные локации:</div>
      <div class="server-item"><span>🇳🇱</span> Нидерланды <span class="tag">AWG 2.0</span></div>
      <div class="server-item"><span>🇵🇱</span> Польша <span class="tag">AWG 2.0</span></div>
    </div>

    <a href="{deep_link}" class="btn">🚀 Открыть в приложении INCY</a>
    <button class="btn btn-outline" onclick="navigator.clipboard.writeText('{sub_full_url}'); alert('Ссылка скопирована!');">📋 Скопировать ссылку</button>
  </div>
</body>
</html>"""
        return web.Response(status=200, text=html_content, headers={"Content-Type": "text/html; charset=utf-8"})

    web_url = "https://t.me/just1kbot" if TEST_STATE.get("btn_web", True) else None
    tg_url = "https://t.me/just1k_support" if TEST_STATE.get("btn_tg", True) else None
    email_addr = "support@just1k.best" if TEST_STATE.get("btn_email", True) else None

    # Sorting resolution
    sort_mode = TEST_STATE.get("sort_mode", "ping")
    sort_ord = None if sort_mode == "none" else sort_mode

    # Banner and Announce resolution
    ann_text = None
    ann_url = None
    b_text = None
    b_btn_text = None
    b_btn_url = None
    b_bg = None
    b_btn_color = None

    if TEST_STATE.get("show_banner"):
        ann_text = "⚡ Спецпредложение: скидка 20% при продлении в боте на 3 месяца!"
        ann_url = "https://t.me/just1kbot"
        b_text = ann_text
        b_btn_text = "Получить скидку"
        b_btn_url = ann_url
        b_bg = "#064e3b"
        b_btn_color = "#10b981"
    elif TEST_STATE.get("expired"):
        ann_text = "Подписка истекла. Продлите доступ в Telegram-боте @just1kbot."
        ann_url = "https://t.me/just1kbot"
        b_text = ann_text
        b_btn_text = "Продлить в боте"
        b_btn_url = ann_url
        b_bg = "#450a0a"
        b_btn_color = "#ef4444"
    elif TEST_STATE.get("quota_exhausted"):
        ann_text = "Трафик 100% исчерпан. Пополните баланс в Telegram-боте @just1kbot."
        ann_url = "https://t.me/just1kbot"
        b_text = ann_text
        b_btn_text = "Пополнить в боте"
        b_btn_url = ann_url
        b_bg = "#450a0a"
        b_btn_color = "#ef4444"

    # Inline metadata lines for body fallback
    inline_meta = []
    if ann_text:
        inline_meta.append(f"#announce: {ann_text}")
    if ann_url:
        inline_meta.append(f"#announce-url: {ann_url}")
    if tg_url:
        inline_meta.append(f"#support-url: {tg_url}")
    if email_addr:
        inline_meta.append(f"#support-email: {email_addr}")
    if web_url:
        inline_meta.append(f"#profile-web-page-url: {web_url}")
    if TEST_STATE.get("hide_check"):
        inline_meta.append("#hide-check: 1")

    # Simulation: Forced Device Limit
    if TEST_STATE.get("block_403"):
        logger.warning("🚫 [SIMULATION] Returning 403 Device Limit Exceeded for HWID %s", hwid)
        return web.Response(
            status=403,
            text="Device limit exceeded (2/2). Remove an old device to connect in @just1kbot.\n",
            headers={
                "Content-Type": "text/plain; charset=utf-8",
                "Device-Limit-Exceeded": "1",
                "Device-Limit": "2",
                "Device-Active-Count": "2",
                "x-hwid-max-devices-reached": "true",
            },
        )

    # HWID / Slot Assignment Logic
    assigned_slot = None
    now_iso = datetime.now(timezone.utc).isoformat()

    if not hwid:
        logger.info("ℹ️ No HWID detected (raw client request). Delivering Slot 1.")
        assigned_slot = 1
    elif hwid in REGISTERED_DEVICES:
        device_entry = REGISTERED_DEVICES[hwid]
        device_entry["last_seen"] = now_iso
        device_entry["device_os"] = device_os
        device_entry["device_model"] = device_model
        assigned_slot = device_entry["slot"]
        logger.info("✅ Recognized existing device: Slot #%d (%s)", assigned_slot, hwid[:8])
    else:
        active_count = len(REGISTERED_DEVICES)
        if active_count >= DEVICE_LIMIT:
            logger.warning(
                "🚫 Device limit reached! Active: %d, Max: %d. HWID: %s",
                active_count,
                DEVICE_LIMIT,
                hwid,
            )
            return web.Response(
                status=403,
                text=f"Device limit exceeded ({active_count}/{DEVICE_LIMIT}). Remove an old device to connect in @just1kbot.\n",
                headers={
                    "Content-Type": "text/plain; charset=utf-8",
                    "Device-Limit-Exceeded": "1",
                    "Device-Limit": str(DEVICE_LIMIT),
                    "Device-Active-Count": str(active_count),
                    "x-hwid-max-devices-reached": "true",
                },
            )

        # Allocate next available slot (1 or 2)
        used_slots = {d["slot"] for d in REGISTERED_DEVICES.values()}
        assigned_slot = 1 if 1 not in used_slots else 2
        REGISTERED_DEVICES[hwid] = {
            "slot": assigned_slot,
            "registered_at": now_iso,
            "last_seen": now_iso,
            "device_os": device_os,
            "device_model": device_model,
        }
        logger.info(
            "🎉 Registered NEW device: HWID %s -> Slot #%d (Active devices: %d/%d)",
            hwid,
            assigned_slot,
            len(REGISTERED_DEVICES),
            DEVICE_LIMIT,
        )

    # Build feed
    total_quota = 0
    upload = 1048576
    download = 10485760
    expire_ts = int(datetime(2026, 12, 31, tzinfo=timezone.utc).timestamp())
    title = f"JUST1K AWG (Device #{assigned_slot})"

    if TEST_STATE.get("expired"):
        expire_ts = 1577836800  # 2020-01-01 (Expired in the past)
        configs = []
        title = "JUST1K [ПОДПИСКА ИСТЕКЛА]"
        logger.info("🛑 Subscription expired: delivering empty server list and expire timestamp.")
    elif TEST_STATE.get("quota_exhausted"):
        total_quota = 10 * 1024 * 1024 * 1024  # 10 GB
        upload = 2 * 1024 * 1024 * 1024       # 2 GB
        download = 8 * 1024 * 1024 * 1024     # 8 GB (100% total)
        configs = []
        title = "JUST1K [ТРАФИК 100%]"
        logger.info("⚠️ Quota exhausted: delivering empty server list and 100% quota.")
    else:
        configs = await get_slot_configs(assigned_slot)

    if TEST_STATE.get("title_suffix") and not TEST_STATE.get("expired") and not TEST_STATE.get("quota_exhausted"):
        title += f" {TEST_STATE['title_suffix']}"

    body = AWGSubscriptionFeedService.build_subscription_body(configs, inline_metadata=inline_meta)
    headers = AWGSubscriptionFeedService.build_subscription_headers(
        profile_title=title,
        expire_ts=expire_ts,
        upload_bytes=upload,
        download_bytes=download,
        total_quota_bytes=total_quota,
        update_interval_hours=1 if (TEST_STATE.get("expired") or TEST_STATE.get("quota_exhausted")) else 6,
        support_url=tg_url,
        support_email=email_addr,
        web_page_url=web_url,
        sort_order=sort_ord,
        announce=ann_text,
        announce_url=ann_url,
        profile_description="⚡ Премиальный высокоскоростной AmneziaWG от JUST1K" if not (TEST_STATE.get("expired") or TEST_STATE.get("quota_exhausted")) else "Подписка неактивна",
        banner_text=b_text,
        banner_button_text=b_btn_text,
        banner_button_url=b_btn_url,
        banner_bg_color=b_bg,
        banner_button_color=b_btn_color,
        hide_url=True,
        hide_check=TEST_STATE.get("hide_check", False),
    )

    logger.info("📤 Response 200 OK sent with %d servers for Slot #%d (Title: '%s')", len(configs), assigned_slot, title)
    logger.info("==================================================")
    return web.Response(status=200, text=body, headers=headers)


async def handle_control_action(request: web.Request) -> web.Response:
    act = request.query.get("act", "")
    msg = "Действие выполнено"

    if act == "toggle_server":
        srv = request.query.get("server", "")
        if srv in TEST_STATE:
            TEST_STATE[srv] = not TEST_STATE[srv]
            state_text = "включен" if TEST_STATE[srv] else "отключен"
            msg = f"Сервер [{srv.upper()}] успешно {state_text}!"
            logger.info("🎛️ [CONTROL] Server %s set to %s", srv, TEST_STATE[srv])
    elif act == "toggle_quota":
        TEST_STATE["quota_exhausted"] = not TEST_STATE["quota_exhausted"]
        state_text = "включено (100% исчерпано)" if TEST_STATE["quota_exhausted"] else "отключено (безлимит)"
        msg = f"Ограничение трафика: {state_text}"
        logger.info("🎛️ [CONTROL] Quota exhausted: %s", TEST_STATE["quota_exhausted"])
    elif act == "toggle_expired":
        TEST_STATE["expired"] = not TEST_STATE["expired"]
        state_text = "включено (истёк в 2020)" if TEST_STATE["expired"] else "отключено (активна до 2026)"
        msg = f"Истечение подписки: {state_text}"
        logger.info("🎛️ [CONTROL] Expired flag: %s", TEST_STATE["expired"])
    elif act == "toggle_block":
        TEST_STATE["block_403"] = not TEST_STATE["block_403"]
        state_text = "включена (выдавать 403)" if TEST_STATE["block_403"] else "отключена (выдавать 200 OK)"
        msg = f"Блокировка лимита устройств: {state_text}"
        logger.info("🎛️ [CONTROL] Block 403 flag: %s", TEST_STATE["block_403"])
    elif act == "toggle_vip":
        if TEST_STATE["title_suffix"]:
            TEST_STATE["title_suffix"] = ""
            msg = "Название подписки сброшено на стандартное"
        else:
            TEST_STATE["title_suffix"] = "⚡ VIP TURBO"
            msg = "В название подписки добавлен бейдж ⚡ VIP TURBO"
        logger.info("🎛️ [CONTROL] Title suffix: %s", TEST_STATE["title_suffix"])
    elif act == "toggle_stub":
        TEST_STATE["stub_mode"] = not TEST_STATE["stub_mode"]
        state_text = "ВКЛЮЧЕНА (HTTP 200 OK с сервером-уведомлением)" if TEST_STATE["stub_mode"] else "ВЫКЛЮЧЕНА (Сырая ошибка HTTP 403)"
        msg = f"Умная заглушка {state_text}"
        logger.info("🎛️ [CONTROL] Stub mode: %s", TEST_STATE["stub_mode"])
    elif act == "toggle_web":
        TEST_STATE["btn_web"] = not TEST_STATE.get("btn_web", True)
        state_text = "ВКЛЮЧЕНА" if TEST_STATE["btn_web"] else "ОТКЛЮЧЕНА"
        msg = f"Кнопка «Личный кабинет / Сайт»: {state_text}"
        logger.info("🎛️ [CONTROL] Btn Web: %s", TEST_STATE["btn_web"])
    elif act == "toggle_tg":
        TEST_STATE["btn_tg"] = not TEST_STATE.get("btn_tg", True)
        state_text = "ВКЛЮЧЕНА (скрывает кнопку Email в INCY)" if TEST_STATE["btn_tg"] else "ОТКЛЮЧЕНА (кнопка Email теперь активна в INCY!)"
        msg = f"Кнопка «Telegram-поддержка»: {state_text}"
        logger.info("🎛️ [CONTROL] Btn TG: %s", TEST_STATE["btn_tg"])
    elif act == "toggle_email":
        TEST_STATE["btn_email"] = not TEST_STATE.get("btn_email", True)
        state_text = "ВКЛЮЧЕНА" if TEST_STATE["btn_email"] else "ОТКЛЮЧЕНА"
        msg = f"Кнопка «Email поддержки»: {state_text}"
        logger.info("🎛️ [CONTROL] Btn Email: %s", TEST_STATE["btn_email"])
    elif act == "set_sort":
        mode = request.query.get("mode", "ping")
        if mode in ("ping", "name", "none"):
            TEST_STATE["sort_mode"] = mode
            labels = {
                "ping": "⚡ По пингу (динамический TCP-замер RTT)",
                "name": "🔤 По имени (А-Я)",
                "none": "📄 По умолчанию (исходный порядок)",
            }
            msg = f"Сортировка установлена: {labels.get(mode, mode)}"
            logger.info("🎛️ [CONTROL] Sort mode: %s", mode)
    elif act == "toggle_hide_check":
        TEST_STATE["hide_check"] = not TEST_STATE.get("hide_check", False)
        state_text = "ВКЛЮЧЕНО (кнопка «Проверить» скрыта)" if TEST_STATE["hide_check"] else "ОТКЛЮЧЕНО (кнопка видна)"
        msg = f"Скрытие кнопки «Проверить»: {state_text}"
        logger.info("🎛️ [CONTROL] Hide check: %s", TEST_STATE["hide_check"])
    elif act == "toggle_banner":
        TEST_STATE["show_banner"] = not TEST_STATE.get("show_banner", False)
        state_text = "ВКЛЮЧЕНО (announce + announce-url)" if TEST_STATE["show_banner"] else "ОТКЛЮЧЕНО (скрыто)"
        msg = f"Интерактивное объявление / баннер: {state_text}"
        logger.info("🎛️ [CONTROL] Show banner: %s", TEST_STATE["show_banner"])
    elif act == "reset_hwid":
        count = len(REGISTERED_DEVICES)
        REGISTERED_DEVICES.clear()
        msg = f"Сброшено {count} зарегистрированных устройств!"
        logger.info("🎛️ [CONTROL] Reset %d devices", count)
    elif act == "reset_all":
        TEST_STATE["se"] = True
        TEST_STATE["nl"] = True
        TEST_STATE["pl"] = True
        TEST_STATE["de"] = True
        TEST_STATE["quota_exhausted"] = False
        TEST_STATE["expired"] = False
        TEST_STATE["block_403"] = False
        TEST_STATE["title_suffix"] = ""
        TEST_STATE["btn_web"] = True
        TEST_STATE["btn_tg"] = True
        TEST_STATE["btn_email"] = True
        TEST_STATE["sort_mode"] = "ping"
        TEST_STATE["show_banner"] = True
        TEST_STATE["hide_check"] = False
        REGISTERED_DEVICES.clear()
        msg = "Все настройки сброшены к стандарту!"
        logger.info("🎛️ [CONTROL] Reset ALL state to defaults")

    raise web.HTTPFound(f"/control?msg={msg}")


async def handle_set_conf(request: web.Request) -> web.Response:
    global CUSTOM_AWG_SLOT_1, CUSTOM_AWG_SLOT_2
    data = await request.post()
    slot = str(data.get("slot", "1"))
    conf = str(data.get("conf", "")).strip()

    if conf:
        try:
            # Validate configuration format
            AWGSubscriptionFeedService.encode_config_to_awg_uri(conf, "Validation Test")
        except Exception as exc:
            raise web.HTTPFound(f"/control?msg=Ошибка валидации конфига: {exc}")

    if slot == "1":
        CUSTOM_AWG_SLOT_1 = conf
        msg = "Живой конфиг для Слот #1 (Нидерланды) успешно установлен!" if conf else "Живой конфиг для Слот #1 сброшен."
    else:
        CUSTOM_AWG_SLOT_2 = conf
        msg = "Живой конфиг для Слот #2 (Нидерланды) успешно установлен!" if conf else "Живой конфиг для Слот #2 сброшен."

    logger.info("🎛️ [CONTROL] Set custom live config for Slot #%s (len: %d)", slot, len(conf))
    raise web.HTTPFound(f"/control?msg={msg}")


async def handle_control_dashboard(request: web.Request) -> web.Response:
    flash_msg = request.query.get("msg", "")

    # Measure real live socket latency for all 4 servers
    real_latencies = {}
    for srv_code, conf in [("nl", SLOT_1_NETHERLANDS), ("pl", SLOT_1_POLAND), ("de", SLOT_1_GERMANY), ("se", SLOT_1_SWEDEN)]:
        ep = extract_endpoint_from_conf(conf)
        lat = await get_real_latency(ep)
        real_latencies[srv_code] = f"{lat} мс" if lat is not None else "n/a"

    # Count active servers
    active_servers = [k.upper() for k, v in TEST_STATE.items() if k in ("nl", "pl", "de", "se") and v]

    # Generate device rows
    device_rows = ""
    for hwid, d in REGISTERED_DEVICES.items():
        device_rows += f"""<tr>
            <td style="font-family: monospace; font-size: 11px;">{hwid[:18]}...</td>
            <td><b>Слот #{d['slot']}</b></td>
            <td>{d.get('device_model', 'N/A')} ({d.get('device_os', 'N/A')})</td>
            <td style="font-size: 11px; color: #94a3b8;">{d['last_seen'][11:19]}</td>
        </tr>"""

    if not device_rows:
        device_rows = '<tr><td colspan="4" style="text-align: center; color: #64748b; padding: 16px;">Нет активных устройств</td></tr>'

    cur_sort = TEST_STATE.get("sort_mode", "ping")

    html = f"""<!doctype html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Управление тестами AmneziaWG MVP</title>
  <style>
    :root {{ --bg: #0b0f19; --card: #151d2f; --card-border: #1e293b; --text: #f8fafc; --sub: #94a3b8; --accent: #10b981; --accent-hover: #059669; --danger: #ef4444; --warning: #f59e0b; --blue: #3b82f6; }}
    * {{ box-sizing: border-box; margin: 0; padding: 0; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; }}
    body {{ background: var(--bg); color: var(--text); padding: 16px; display: flex; justify-content: center; min-height: 100vh; }}
    .container {{ max-width: 680px; width: 100%; }}
    .header {{ text-align: center; margin-bottom: 20px; }}
    .header h1 {{ font-size: 22px; font-weight: 700; margin-bottom: 6px; }}
    .header p {{ font-size: 13px; color: var(--sub); }}
    .alert {{ background: #064e3b; border: 1px solid #059669; color: #a7f3d0; padding: 12px 16px; border-radius: 12px; margin-bottom: 20px; font-size: 14px; display: flex; align-items: center; justify-content: space-between; }}
    .section {{ background: var(--card); border: 1px solid var(--card-border); border-radius: 16px; padding: 20px; margin-bottom: 16px; }}
    .section-title {{ font-size: 15px; font-weight: 600; text-transform: uppercase; letter-spacing: 0.05em; color: var(--sub); margin-bottom: 16px; display: flex; align-items: center; justify-content: space-between; }}
    .server-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); gap: 12px; }}
    .item-card {{ background: rgba(0,0,0,0.25); border: 1px solid var(--card-border); border-radius: 12px; padding: 14px; display: flex; align-items: center; justify-content: space-between; gap: 12px; }}
    .item-info {{ display: flex; align-items: center; gap: 10px; font-size: 14px; font-weight: 500; }}
    .btn {{ display: inline-flex; align-items: center; justify-content: center; padding: 8px 14px; font-size: 13px; font-weight: 600; border-radius: 8px; text-decoration: none; border: none; cursor: pointer; transition: 0.15s; white-space: nowrap; }}
    .btn-green {{ background: var(--accent); color: #fff; }}
    .btn-red {{ background: rgba(239,68,68,0.15); color: #fca5a5; border: 1px solid rgba(239,68,68,0.3); }}
    .btn-blue {{ background: var(--blue); color: #fff; }}
    .btn-gray {{ background: #334155; color: #cbd5e1; }}
    .btn-outline {{ background: transparent; border: 1px solid #475569; color: #cbd5e1; }}
    .btn-active {{ background: #2563eb; color: #fff; border: 1px solid #60a5fa; }}
    .badge {{ font-size: 11px; padding: 2px 8px; border-radius: 6px; font-weight: 600; text-transform: uppercase; }}
    .badge-on {{ background: #064e3b; color: #34d399; }}
    .badge-off {{ background: #334155; color: #94a3b8; }}
    table {{ width: 100%; border-collapse: collapse; font-size: 13px; }}
    th {{ text-align: left; padding: 8px; color: var(--sub); font-weight: 500; border-bottom: 1px solid var(--card-border); }}
    td {{ padding: 10px 8px; border-bottom: 1px solid rgba(255,255,255,0.05); }}
    .hint {{ font-size: 12px; color: var(--sub); margin-top: 8px; line-height: 1.4; }}
  </style>
</head>
<body>
  <div class="container">
    <div class="header">
      <h1>🎛️ Управление AmneziaWG MVP</h1>
      <p>Переключайте серверы, кнопки и сортировку, затем нажимайте 🔄 в INCY</p>
    </div>

    {f'<div class="alert"><span>🔔 {flash_msg}</span><span style="font-size: 12px; opacity: 0.8;">Нажмите 🔄 в приложении INCY</span></div>' if flash_msg else ''}

    <!-- Servers Section -->
    <div class="section">
      <div class="section-title">
        <span>Серверы в подписке ({len(active_servers)})</span>
      </div>
      <div class="server-grid">
        <!-- Netherlands -->
        <div class="item-card">
          <div class="item-info">
            <span style="font-size: 20px;">🇳🇱</span>
            <div>Нидерланды <span class="badge {'badge-on' if TEST_STATE['nl'] else 'badge-off'}">{'ВКЛ' if TEST_STATE['nl'] else 'ВЫКЛ'}</span> <span style="font-size: 11px; color: #34d399; margin-left: 6px;">• {real_latencies.get('nl', '...')}</span></div>
          </div>
          <a href="/control/action?act=toggle_server&server=nl" class="btn {'btn-red' if TEST_STATE['nl'] else 'btn-green'}">
            {'Отключить' if TEST_STATE['nl'] else 'Включить'}
          </a>
        </div>

        <!-- Poland -->
        <div class="item-card">
          <div class="item-info">
            <span style="font-size: 20px;">🇵🇱</span>
            <div>Польша <span class="badge {'badge-on' if TEST_STATE['pl'] else 'badge-off'}">{'ВКЛ' if TEST_STATE['pl'] else 'ВЫКЛ'}</span> <span style="font-size: 11px; color: #34d399; margin-left: 6px;">• {real_latencies.get('pl', '...')}</span></div>
          </div>
          <a href="/control/action?act=toggle_server&server=pl" class="btn {'btn-red' if TEST_STATE['pl'] else 'btn-green'}">
            {'Отключить' if TEST_STATE['pl'] else 'Включить'}
          </a>
        </div>

        <!-- Germany -->
        <div class="item-card">
          <div class="item-info">
            <span style="font-size: 20px;">🇩🇪</span>
            <div>Германия <span class="badge {'badge-on' if TEST_STATE['de'] else 'badge-off'}">{'ВКЛ' if TEST_STATE['de'] else 'ВЫКЛ'}</span> <span style="font-size: 11px; color: #34d399; margin-left: 6px;">• {real_latencies.get('de', '...')}</span></div>
          </div>
          <a href="/control/action?act=toggle_server&server=de" class="btn {'btn-red' if TEST_STATE['de'] else 'btn-green'}">
            {'Отключить' if TEST_STATE['de'] else 'Включить'}
          </a>
        </div>

        <!-- Sweden -->
        <div class="item-card">
          <div class="item-info">
            <span style="font-size: 20px;">🇸🇪</span>
            <div>Швеция <span class="badge {'badge-on' if TEST_STATE['se'] else 'badge-off'}">{'ВКЛ' if TEST_STATE['se'] else 'ВЫКЛ'}</span> <span style="font-size: 11px; color: #34d399; margin-left: 6px;">• {real_latencies.get('se', '...')}</span></div>
          </div>
          <a href="/control/action?act=toggle_server&server=se" class="btn {'btn-red' if TEST_STATE['se'] else 'btn-green'}">
            {'Отключить' if TEST_STATE['se'] else 'Включить'}
          </a>
        </div>
      </div>
      <div class="hint">💡 Зелёные цифры задержки замеряются живым TCP-сокетом в реальном времени. При изменении перейдите в INCY и нажмите 🔄 (обновить подписку).</div>
    </div>

    <!-- Sorting Section -->
    <div class="section">
      <div class="section-title">
        <span>📶 Порядок сортировки серверов в подписке</span>
      </div>
      <div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 10px; margin-bottom: 12px;">
        <a href="/control/action?act=set_sort&mode=ping" class="btn {'btn-active' if cur_sort == 'ping' else 'btn-gray'}">
          ⚡ По пингу (живой замер)
        </a>
        <a href="/control/action?act=set_sort&mode=name" class="btn {'btn-active' if cur_sort == 'name' else 'btn-gray'}">
          🔤 По имени (А-Я)
        </a>
        <a href="/control/action?act=set_sort&mode=none" class="btn {'btn-active' if cur_sort == 'none' else 'btn-gray'}">
          📄 По умолчанию
        </a>
      </div>
      <div class="hint">
        <b>💡 Как устроена сортировка серверов:</b><br>
        • <b>⚡ По пингу (sort-order: ping + реальный замер сокетов):</b> стенд опрашивает сетевые порты каждого сервера в реальном времени и расставляет их по фактическому времени отклика (RTT). Самый быстрый сервер всегда встаёт на 1-е место!<br>
        • <b>🔤 По имени (sort-order: name):</b> сервер и клиент сортируют серверы строго по алфавиту.<br>
        • <b>📄 По умолчанию:</b> серверы выдаются в исходном порядке добавления.<br>
      </div>
    </div>

    <!-- Buttons & Banner Section -->
    <div class="section">
      <div class="section-title">
        <span>Кнопки в карточке подписки и Объявления</span>
      </div>

      <div style="display: flex; flex-direction: column; gap: 10px;">
        <!-- In-App Announcement / Banner -->
        <div class="item-card" style="background: {'rgba(16,185,129,0.1)' if TEST_STATE.get('show_banner') else 'rgba(0,0,0,0.25)'}; border: 1px solid {'#10b981' if TEST_STATE.get('show_banner') else 'var(--card-border)'};">
          <div>
            <div style="font-weight: 600; font-size: 14px;">📢 In-App объявление в карточке (announce + announce-url)</div>
            <div class="hint">Официальный заголовок <code>announce</code> (до 200 символов) + ссылка <code>announce-url</code>. Показывает плашку объявления прямо на главном экране INCY. Работает у всех пользователей!</div>
          </div>
          <a href="/control/action?act=toggle_banner" class="btn {'btn-red' if TEST_STATE.get('show_banner') else 'btn-green'}">
            {'Скрыть объявление' if TEST_STATE.get('show_banner') else 'Включить объявление'}
          </a>
        </div>

        <!-- Web Page / Site -->
        <div class="item-card">
          <div>
            <div style="font-weight: 600; font-size: 14px;">🌐 Кнопка «Сайт / Бот» (profile-web-page-url)</div>
            <div class="hint">Добавляет круглую нативную кнопку «Сайт» со ссылкой на бота @just1kbot в карточку подписки</div>
          </div>
          <a href="/control/action?act=toggle_web" class="btn {'btn-red' if TEST_STATE.get('btn_web') else 'btn-green'}">
            {'Отключить' if TEST_STATE.get('btn_web') else 'Включить'}
          </a>
        </div>

        <!-- Telegram Support -->
        <div class="item-card">
          <div>
            <div style="font-weight: 600; font-size: 14px;">💬 Кнопка «Telegram-поддержка» (support-url)</div>
            <div class="hint">Основной канал поддержки. <b>Важно:</b> если Telegram включен, INCY скрывает кнопку Email (приоритет TG). Отключите Telegram, чтобы на карточке появилась кнопка Email.</div>
          </div>
          <a href="/control/action?act=toggle_tg" class="btn {'btn-red' if TEST_STATE.get('btn_tg') else 'btn-green'}">
            {'Отключить TG' if TEST_STATE.get('btn_tg') else 'Включить TG'}
          </a>
        </div>

        <!-- Email Support -->
        <div class="item-card">
          <div>
            <div style="font-weight: 600; font-size: 14px;">✉️ Кнопка «Email» (support-email)</div>
            <div class="hint">Заголовок <code>support-email: support@just1k.best</code>. <b>Видна в INCY как нативная кнопка</b> только когда Telegram (support-url) выключен выше.</div>
          </div>
          <a href="/control/action?act=toggle_email" class="btn {'btn-red' if TEST_STATE.get('btn_email') else 'btn-green'}">
            {'Отключить Email' if TEST_STATE.get('btn_email') else 'Включить Email'}
          </a>
        </div>

        <!-- Hide Check Button -->
        <div class="item-card">
          <div>
            <div style="font-weight: 600; font-size: 14px;">🚫 Скрыть кнопку «Проверить» на главном экране (hide-check: 1)</div>
            <div class="hint">Убирает большую кнопку проверки соединения с главного экрана INCY</div>
          </div>
          <a href="/control/action?act=toggle_hide_check" class="btn {'btn-red' if TEST_STATE.get('hide_check') else 'btn-green'}">
            {'Показать кнопку' if TEST_STATE.get('hide_check') else 'Скрыть кнопку'}
          </a>
        </div>
      </div>
    </div>

    <!-- Scenarios Section -->
    <div class="section">
      <div class="section-title">
        <span>Симуляция сценариев и ограничений</span>
      </div>

      <div style="display: flex; flex-direction: column; gap: 10px;">
        <!-- Quota -->
        <div class="item-card">
          <div>
            <div style="font-weight: 600; font-size: 14px;">⚠️ Исчерпание квоты трафика (10 ГБ / 10 ГБ)</div>
            <div class="hint">Отдаёт 100% заполненный прогресс-бар в заголовке Subscription-Userinfo и объявление о пополнении баланса</div>
          </div>
          <a href="/control/action?act=toggle_quota" class="btn {'btn-red' if TEST_STATE['quota_exhausted'] else 'btn-gray'}">
            {'🔴 Отключить' if TEST_STATE['quota_exhausted'] else 'Включить'}
          </a>
        </div>

        <!-- Expired -->
        <div class="item-card">
          <div>
            <div style="font-weight: 600; font-size: 14px;">⏳ Истечение срока подписки</div>
            <div class="hint">Устанавливает дату expire в прошлом и выводит баннер продления в боте</div>
          </div>
          <a href="/control/action?act=toggle_expired" class="btn {'btn-red' if TEST_STATE['expired'] else 'btn-gray'}">
            {'🔴 Отключить' if TEST_STATE['expired'] else 'Включить'}
          </a>
        </div>

        <!-- 403 Forbidden / Limit -->
        <div class="item-card">
          <div>
            <div style="font-weight: 600; font-size: 14px;">🚫 Превышение лимита устройств (2/2) — HTTP 403</div>
            <div class="hint">Возвращает HTTP 403 Forbidden с заголовком <code>Device-Limit-Exceeded: 1</code></div>
          </div>
          <a href="/control/action?act=toggle_block" class="btn {'btn-red' if TEST_STATE['block_403'] else 'btn-gray'}">
            {'🔴 Отключить' if TEST_STATE['block_403'] else 'Включить'}
          </a>
        </div>

        <!-- Title Customization -->
        <div class="item-card">
          <div>
            <div style="font-weight: 600; font-size: 14px;">⚡ Название: «JUST1K AWG ⚡ VIP TURBO»</div>
            <div class="hint">Проверяет динамическое обновление имени профиля в приложении</div>
          </div>
          <a href="/control/action?act=toggle_vip" class="btn {'btn-green' if TEST_STATE['title_suffix'] else 'btn-gray'}">
            {'Включено' if TEST_STATE['title_suffix'] else 'Применить'}
          </a>
        </div>
      </div>
    </div>

    <!-- Live AmneziaWG Config Input Section -->
    <div class="section" style="border: 1px solid #3b82f6;">
      <div class="section-title" style="color: #60a5fa;">
        <span>⚡ Тестирование реального конфига (Handshake & Трафик)</span>
      </div>
      <form action="/control/set_conf" method="POST" style="display: flex; flex-direction: column; gap: 10px;">
        <div style="font-size: 13px; color: var(--sub);">
          Вставьте сюда реальный рабочий конфиг <code>.conf</code> от вашей ноды AmneziaWG. Он сразу подставится в слот Нидерландов и будет выдан клиенту INCY для проверки реального туннеля и трафика.
        </div>
        <div style="display: flex; gap: 12px; align-items: center; flex-wrap: wrap;">
          <label style="font-size: 13px; font-weight: 600;">Назначить в слот:</label>
          <select name="slot" style="background: #1e293b; color: #fff; border: 1px solid #334155; padding: 6px 12px; border-radius: 8px; font-size: 13px;">
            <option value="1">Слот #1 (Устройство 1) {'[АКТИВЕН ЖИВОЙ]' if CUSTOM_AWG_SLOT_1 else ''}</option>
            <option value="2">Слот #2 (Устройство 2) {'[АКТИВЕН ЖИВОЙ]' if CUSTOM_AWG_SLOT_2 else ''}</option>
          </select>
          <span style="font-size: 12px; color: #34d399;">{'✅ Слот 1: живой' if CUSTOM_AWG_SLOT_1 else '• Слот 1: демо'} | {'✅ Слот 2: живой' if CUSTOM_AWG_SLOT_2 else '• Слот 2: демо'}</span>
        </div>
        <textarea name="conf" rows="6" placeholder="[Interface]&#10;Address = 10.8.0.2/32&#10;PrivateKey = ...&#10;Jc = ...&#10;&#10;[Peer]&#10;PublicKey = ...&#10;Endpoint = your-host:443" style="width: 100%; background: #0f172a; color: #f8fafc; border: 1px solid var(--card-border); border-radius: 8px; padding: 10px; font-family: monospace; font-size: 12px;"></textarea>
        <div style="display: flex; gap: 10px;">
          <button type="submit" class="btn btn-blue">💾 Сохранить и выдать в подписку</button>
        </div>
      </form>
    </div>

    <!-- Active Devices Table -->
    <div class="section">
      <div class="section-title">
        <span>Подключённые устройства ({len(REGISTERED_DEVICES)} / {DEVICE_LIMIT})</span>
        <a href="/control/action?act=reset_hwid" class="btn btn-outline" style="font-size: 11px;">🧹 Сбросить привязку HWID</a>
      </div>
      <table>
        <thead>
          <tr>
            <th>HWID</th>
            <th>Слот</th>
            <th>Устройство</th>
            <th>Время</th>
          </tr>
        </thead>
        <tbody>
          {device_rows}
        </tbody>
      </table>
    </div>

    <!-- Quick Reset -->
    <div style="text-align: center; margin-top: 10px;">
      <a href="/control/action?act=reset_all" class="btn btn-outline" style="color: #94a3b8;">🔄 Сбросить ВСЕ настройки к умолчанию</a>
    </div>
  </div>
</body>
</html>"""
    return web.Response(status=200, text=html, headers={"Content-Type": "text/html; charset=utf-8"})


async def handle_status(_request: web.Request) -> web.Response:
    status_data = {
        "status": "online",
        "device_limit": DEVICE_LIMIT,
        "active_devices_count": len(REGISTERED_DEVICES),
        "devices": REGISTERED_DEVICES,
        "test_state": TEST_STATE,
        "test_sub_url": f"/sub/awg/{VALID_TOKEN}",
    }
    return web.Response(
        status=200,
        text=json.dumps(status_data, indent=2, ensure_ascii=False),
        headers={"Content-Type": "application/json; charset=utf-8"},
    )


async def handle_reset(_request: web.Request) -> web.Response:
    count = len(REGISTERED_DEVICES)
    REGISTERED_DEVICES.clear()
    logger.info("🧹 Reset: Cleared %d registered devices.", count)
    return web.Response(
        status=200,
        text=f"Reset successful. Cleared {count} registered devices.\n",
        headers={"Content-Type": "text/plain; charset=utf-8"},
    )


def make_app() -> web.Application:
    app = web.Application()
    app.router.add_get("/sub/awg/{token}", handle_subscription_feed)
    app.router.add_get("/status", handle_status)
    app.router.add_get("/reset", handle_reset)
    app.router.add_get("/control", handle_control_dashboard)
    app.router.add_get("/control/action", handle_control_action)
    app.router.add_post("/control/set_conf", handle_set_conf)
    return app



if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="AmneziaWG INCY Subscription Test Stand")
    parser.add_argument("--port", type=int, default=8088, help="Port to listen on (default: 8088)")
    parser.add_argument("--host", type=str, default="0.0.0.0", help="Host to bind (default: 0.0.0.0)")
    args = parser.parse_args()

    print("\n" + "=" * 60)
    print("🚀 AMNEZIAWG INCY SUBSCRIPTION MVP TEST STAND")
    print("=" * 60)
    print(f"📡 Server running on http://{args.host}:{args.port}")
    print(f"🔗 Subscription endpoint: http://localhost:{args.port}/sub/awg/{VALID_TOKEN}")
    print(f"🔍 Status dashboard:     http://localhost:{args.port}/status")
    print(f"🧹 Reset registered HWIDs: http://localhost:{args.port}/reset")
    print("=" * 60 + "\n")

    web.run_app(make_app(), host=args.host, port=args.port)
