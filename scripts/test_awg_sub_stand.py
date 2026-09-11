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
import json
import logging
import os
import base64
import sys
from datetime import datetime, timezone
from typing import Sequence, Tuple
from aiohttp import web

class AWGSubscriptionFeedService:
    """Generates subscription feeds with multi-server AmneziaWG profiles for INCY."""

    @staticmethod
    def encode_config_to_awg_uri(conf: str, server_name: str, country_flag: str = "") -> str:
        if not conf or not conf.strip():
            raise ValueError("Configuration text cannot be empty")

        clean_conf = conf.strip()
        b64_conf = base64.urlsafe_b64encode(clean_conf.encode("utf-8")).decode("ascii")

        clean_name = (server_name or "Server").strip()
        clean_flag = (country_flag or "").strip()
        fragment = f"{clean_flag} {clean_name}".strip() if clean_flag else clean_name

        return f"awg://{b64_conf}#{fragment}"

    @classmethod
    def build_subscription_body(cls, server_configs: Sequence[Tuple[str, str, str]]) -> str:
        if not server_configs:
            return ""

        lines = []
        for item in server_configs:
            conf, name, flag = item
            try:
                uri = cls.encode_config_to_awg_uri(conf, name, flag)
                lines.append(uri)
            except Exception as exc:
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

        if support_url and support_url.strip():
            headers["support-url"] = support_url.strip()

        return headers

    DUMMY_SINKHOLE_CONFIG: str = """[Interface]
Address = 10.255.255.2/32
DNS = 127.0.0.1
MTU = 1280
PrivateKey = aAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=
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
PublicKey = aAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=
AllowedIPs = 0.0.0.0/0, ::/0
Endpoint = 127.0.0.1:1
PersistentKeepalive = 25
"""

    @classmethod
    def create_stub_server(cls, message: str, flag: str = "🛑") -> Tuple[str, str, str]:
        """Create a non-routable stub server item for informative paywalls/notices."""
        return (cls.DUMMY_SINKHOLE_CONFIG, message, flag)


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
Endpoint = 185.220.1.50:443
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
Endpoint = 185.220.1.50:443
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
Endpoint = 142.132.1.80:443
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
Endpoint = 142.132.1.80:443
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
Endpoint = 193.180.1.20:443
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
Endpoint = 193.180.1.20:443
PersistentKeepalive = 25
"""

# In-memory test state & simulation controls
REGISTERED_DEVICES: dict[str, dict] = {}
DEVICE_LIMIT = 2
VALID_TOKEN = "test_awg_vip_token"

TEST_STATE = {
    "nl": True,
    "pl": True,
    "de": False,
    "se": False,
    "quota_exhausted": False,
    "expired": False,
    "block_403": False,
    "stub_mode": True,  # True = Smart Stub (200 OK + notice server), False = Raw HTTP 403
    "title_suffix": "",
}


def get_slot_configs(slot: int) -> list[tuple[str, str, str]]:
    result = []
    suffix = f"(Slot {slot})"
    if TEST_STATE.get("nl"):
        conf = SLOT_1_NETHERLANDS if slot == 1 else SLOT_2_NETHERLANDS
        result.append((conf, f"Netherlands {suffix}", "🇳🇱"))
    if TEST_STATE.get("pl"):
        conf = SLOT_1_POLAND if slot == 1 else SLOT_2_POLAND
        result.append((conf, f"Poland {suffix}", "🇵🇱"))
    if TEST_STATE.get("de"):
        conf = SLOT_1_GERMANY if slot == 1 else SLOT_2_GERMANY
        result.append((conf, f"Germany {suffix}", "🇩🇪"))
    if TEST_STATE.get("se"):
        conf = SLOT_1_SWEDEN if slot == 1 else SLOT_2_SWEDEN
        result.append((conf, f"Sweden {suffix}", "🇸🇪"))
    return result



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

    logger.info("   Headers detected:")
    for h_name, h_val in request.headers.items():
        logger.info("     [%s]: %s", h_name, h_val)

    if token != VALID_TOKEN:
        logger.warning("❌ Invalid subscription token: %s", token)
        return web.Response(status=404, text="Not Found")

    # Check if request comes from a real web browser (navigation event) vs INCY app client
    accept_header = request.headers.get("Accept", "")
    user_agent = request.headers.get("User-Agent", "")
    sec_dest = request.headers.get("Sec-Fetch-Dest", "")
    sec_mode = request.headers.get("Sec-Fetch-Mode", "")
    force_raw = request.query.get("raw") == "1"
    
    # Real browser page navigation sends Sec-Fetch-Dest: document or Sec-Fetch-Mode: navigate
    is_browser = not force_raw and not hwid and "INCY" not in user_agent and (
        sec_dest == "document" or sec_mode == "navigate" or (
            "Mozilla" in user_agent and "Chrome" in user_agent and "text/html" in accept_header
        )
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

    # Simulation: Forced Device Limit
    if TEST_STATE.get("block_403"):
        if TEST_STATE.get("stub_mode", True):
            logger.warning("🚫 [SMART STUB] Forced limit exceeded: delivering 200 OK notice stub for HWID %s", hwid)
            stub_configs = [
                AWGSubscriptionFeedService.create_stub_server(
                    "Превышен лимит устройств — отключите в @just1kbot", "🚫"
                )
            ]
            stub_body = AWGSubscriptionFeedService.build_subscription_body(stub_configs)
            stub_headers = AWGSubscriptionFeedService.build_subscription_headers(
                profile_title="JUST1K [ЛИМИТ УСТРОЙСТВ]",
                expire_ts=0,
                upload_bytes=1048576,
                download_bytes=1048576,
                total_quota_bytes=1048576,
                update_interval_hours=1,
                support_url="https://t.me/just1k_support",
                hide_url=True,
            )
            stub_headers["Device-Limit-Exceeded"] = "1"
            return web.Response(status=200, text=stub_body, headers=stub_headers)
        else:
            logger.warning("🚫 [SIMULATION] Returning 403 Device Limit Exceeded for HWID %s", hwid)
            return web.Response(
                status=403,
                text="Device limit exceeded (2/2). Remove an old device to connect.",
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
        # Existing device: refresh last_seen
        device_entry = REGISTERED_DEVICES[hwid]
        device_entry["last_seen"] = now_iso
        device_entry["device_os"] = device_os
        device_entry["device_model"] = device_model
        assigned_slot = device_entry["slot"]
        logger.info("✅ Recognized existing device: Slot #%d (%s)", assigned_slot, hwid[:8])
    else:
        # New device: check limit
        active_count = len(REGISTERED_DEVICES)
        if active_count >= DEVICE_LIMIT:
            logger.warning(
                "🚫 Device limit reached! Active: %d, Max: %d. HWID: %s",
                active_count,
                DEVICE_LIMIT,
                hwid,
            )
            if TEST_STATE.get("stub_mode", True):
                stub_configs = [
                    AWGSubscriptionFeedService.create_stub_server(
                        f"Лимит устройств ({active_count}/{DEVICE_LIMIT}) — отключите в @just1kbot", "🚫"
                    )
                ]
                stub_body = AWGSubscriptionFeedService.build_subscription_body(stub_configs)
                stub_headers = AWGSubscriptionFeedService.build_subscription_headers(
                    profile_title=f"JUST1K [ЛИМИТ {active_count}/{DEVICE_LIMIT}]",
                    expire_ts=0,
                    upload_bytes=1048576,
                    download_bytes=1048576,
                    total_quota_bytes=1048576,
                    update_interval_hours=1,
                    support_url="https://t.me/just1k_support",
                    hide_url=True,
                )
                stub_headers["Device-Limit-Exceeded"] = "1"
                return web.Response(status=200, text=stub_body, headers=stub_headers)
            else:
                response_headers = {
                    "Content-Type": "text/plain; charset=utf-8",
                    "Device-Limit-Exceeded": "1",
                    "Device-Limit": str(DEVICE_LIMIT),
                    "Device-Active-Count": str(active_count),
                    "x-hwid-max-devices-reached": "true",
                }
                return web.Response(
                    status=403,
                    text=f"Device limit exceeded ({active_count}/{DEVICE_LIMIT}). Remove an old device to connect.",
                    headers=response_headers,
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
    configs = get_slot_configs(assigned_slot)

    # Quotas & metadata customization via TEST_STATE
    total_quota = 0
    upload = 1048576
    download = 10485760
    expire_ts = int(datetime(2026, 12, 31, tzinfo=timezone.utc).timestamp())
    title = f"JUST1K AWG (Device #{assigned_slot})"

    if TEST_STATE.get("expired"):
        expire_ts = 1577836800  # 2020-01-01 (Expired in the past)
        if TEST_STATE.get("stub_mode", True):
            configs = [
                AWGSubscriptionFeedService.create_stub_server(
                    "Срок подписки истёк — продлите в @just1kbot", "🛑"
                )
            ]
            title = "JUST1K [ПОДПИСКА ИСТЕКЛА]"
            logger.info("🛑 [SMART STUB] Subscription expired! Replaced servers with expiration notice stub.")

    elif TEST_STATE.get("quota_exhausted"):
        total_quota = 10 * 1024 * 1024 * 1024  # 10 GB
        upload = 2 * 1024 * 1024 * 1024       # 2 GB
        download = 8 * 1024 * 1024 * 1024     # 8 GB (100% total)
        if TEST_STATE.get("stub_mode", True):
            configs = [
                AWGSubscriptionFeedService.create_stub_server(
                    "Трафик исчерпан — пополните в @just1kbot", "⚠️"
                )
            ]
            title = "JUST1K [ТРАФИК 100%]"
            logger.info("⚠️ [SMART STUB] Quota exhausted! Replaced servers with quota notice stub.")

    if not configs:
        configs = [
            AWGSubscriptionFeedService.create_stub_server(
                "Нет доступных серверов — @just1kbot", "⚠️"
            )
        ]
        title = "JUST1K [НЕТ СЕРВЕРОВ]"

    if TEST_STATE.get("title_suffix") and not TEST_STATE.get("expired") and not TEST_STATE.get("quota_exhausted"):
        title += f" {TEST_STATE['title_suffix']}"

    body = AWGSubscriptionFeedService.build_subscription_body(configs)
    headers = AWGSubscriptionFeedService.build_subscription_headers(
        profile_title=title,
        expire_ts=expire_ts,
        upload_bytes=upload,
        download_bytes=download,
        total_quota_bytes=total_quota,
        update_interval_hours=1 if (TEST_STATE.get("expired") or TEST_STATE.get("quota_exhausted")) else 6,
        support_url="https://t.me/just1k_support",
        hide_url=True,
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
    elif act == "reset_hwid":
        count = len(REGISTERED_DEVICES)
        REGISTERED_DEVICES.clear()
        msg = f"Сброшено {count} зарегистрированных устройств!"
        logger.info("🎛️ [CONTROL] Reset %d devices", count)
    elif act == "reset_all":
        TEST_STATE["nl"] = True
        TEST_STATE["pl"] = True
        TEST_STATE["de"] = False
        TEST_STATE["se"] = False
        TEST_STATE["quota_exhausted"] = False
        TEST_STATE["expired"] = False
        TEST_STATE["block_403"] = False
        TEST_STATE["stub_mode"] = True
        TEST_STATE["title_suffix"] = ""
        REGISTERED_DEVICES.clear()
        msg = "Все настройки сброшены к стандартным (Нидерланды + Польша, Умная заглушка)!"
        logger.info("🎛️ [CONTROL] Reset ALL state to defaults")

    raise web.HTTPFound(f"/control?msg={msg}")


async def handle_control_dashboard(request: web.Request) -> web.Response:
    flash_msg = request.query.get("msg", "")

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
    .item-card {{ background: rgba(0,0,0,0.25); border: 1px solid var(--card-border); border-radius: 12px; padding: 14px; display: flex; align-items: center; justify-content: space-between; }}
    .item-info {{ display: flex; align-items: center; gap: 10px; font-size: 14px; font-weight: 500; }}
    .btn {{ display: inline-flex; align-items: center; justify-content: center; padding: 8px 14px; font-size: 13px; font-weight: 600; border-radius: 8px; text-decoration: none; border: none; cursor: pointer; transition: 0.15s; }}
    .btn-green {{ background: var(--accent); color: #fff; }}
    .btn-red {{ background: rgba(239,68,68,0.15); color: #fca5a5; border: 1px solid rgba(239,68,68,0.3); }}
    .btn-blue {{ background: var(--blue); color: #fff; }}
    .btn-gray {{ background: #334155; color: #cbd5e1; }}
    .btn-outline {{ background: transparent; border: 1px solid #475569; color: #cbd5e1; }}
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
      <p>Переключайте серверы и лимиты, затем нажимайте 🔄 в INCY</p>
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
            <div>Нидерланды <span class="badge {'badge-on' if TEST_STATE['nl'] else 'badge-off'}">{'ВКЛ' if TEST_STATE['nl'] else 'ВЫКЛ'}</span></div>
          </div>
          <a href="/control/action?act=toggle_server&server=nl" class="btn {'btn-red' if TEST_STATE['nl'] else 'btn-green'}">
            {'Отключить' if TEST_STATE['nl'] else 'Включить'}
          </a>
        </div>

        <!-- Poland -->
        <div class="item-card">
          <div class="item-info">
            <span style="font-size: 20px;">🇵🇱</span>
            <div>Польша <span class="badge {'badge-on' if TEST_STATE['pl'] else 'badge-off'}">{'ВКЛ' if TEST_STATE['pl'] else 'ВЫКЛ'}</span></div>
          </div>
          <a href="/control/action?act=toggle_server&server=pl" class="btn {'btn-red' if TEST_STATE['pl'] else 'btn-green'}">
            {'Отключить' if TEST_STATE['pl'] else 'Включить'}
          </a>
        </div>

        <!-- Germany -->
        <div class="item-card">
          <div class="item-info">
            <span style="font-size: 20px;">🇩🇪</span>
            <div>Германия <span class="badge {'badge-on' if TEST_STATE['de'] else 'badge-off'}">{'ВКЛ' if TEST_STATE['de'] else 'ВЫКЛ'}</span></div>
          </div>
          <a href="/control/action?act=toggle_server&server=de" class="btn {'btn-red' if TEST_STATE['de'] else 'btn-green'}">
            {'Отключить' if TEST_STATE['de'] else '➕ Добавить'}
          </a>
        </div>

        <!-- Sweden -->
        <div class="item-card">
          <div class="item-info">
            <span style="font-size: 20px;">🇸🇪</span>
            <div>Швеция <span class="badge {'badge-on' if TEST_STATE['se'] else 'badge-off'}">{'ВКЛ' if TEST_STATE['se'] else 'ВЫКЛ'}</span></div>
          </div>
          <a href="/control/action?act=toggle_server&server=se" class="btn {'btn-red' if TEST_STATE['se'] else 'btn-green'}">
            {'Отключить' if TEST_STATE['se'] else '➕ Добавить'}
          </a>
        </div>
      </div>
      <div class="hint">💡 При добавлении или удалении сервера перейдите в INCY и нажмите 🔄 (обновить подписку). Список обновится мгновенно без пересоздания профиля.</div>
    </div>

    <!-- Scenarios Section -->
    <div class="section">
      <div class="section-title">
        <span>Симуляция сценариев и обработка ограничений</span>
      </div>

      <div style="display: flex; flex-direction: column; gap: 10px;">
        <!-- Smart Stub Mode vs Raw 403 -->
        <div class="item-card" style="background: {'rgba(16,185,129,0.08)' if TEST_STATE['stub_mode'] else 'rgba(239,68,68,0.08)'}; border: 1px solid {'#10b981' if TEST_STATE['stub_mode'] else '#ef4444'};">
          <div>
            <div style="font-weight: 700; font-size: 15px; display: flex; align-items: center; gap: 8px;">
              <span>{'💡 Режим «Умная заглушка (200 OK)»' if TEST_STATE['stub_mode'] else '⚠️ Режим «Сырая ошибка (HTTP 403)»'}</span>
              <span class="badge {'badge-on' if TEST_STATE['stub_mode'] else 'badge-off'}">{'Рекомендуется' if TEST_STATE['stub_mode'] else 'Классический'}</span>
            </div>
            <div class="hint" style="color: {'#6ee7b7' if TEST_STATE['stub_mode'] else '#fca5a5'}; margin-top: 4px;">
              {'✅ Вместо сбоев INCY получает HTTP 200 OK, заменяя серверы на понятные плашки: «🛑 Подписка истекла / ⚠️ Трафик исчерпан / 🚫 Лимит устройств — продлите в @just1kbot». Работает одинаково на телефонах и ПК!' if TEST_STATE['stub_mode'] else '❌ Выдаёт HTTP 403. Телефон показывает пугающую «ошибку 403», а ПК тихо игнорирует запрос.'}
            </div>
          </div>
          <a href="/control/action?act=toggle_stub" class="btn {'btn-blue' if TEST_STATE['stub_mode'] else 'btn-green'}">
            {'Переключить на 403' if TEST_STATE['stub_mode'] else 'Включить заглушку 200 OK'}
          </a>
        </div>

        <!-- Quota -->
        <div class="item-card">
          <div>
            <div style="font-weight: 600; font-size: 14px;">⚠️ Исчерпание квоты трафика (10 ГБ / 10 ГБ)</div>
            <div class="hint">В режиме заглушки заменяет сервер на «⚠️ Трафик исчерпан — пополните в @just1kbot» и ставит 100% прогресс-бар</div>
          </div>
          <a href="/control/action?act=toggle_quota" class="btn {'btn-red' if TEST_STATE['quota_exhausted'] else 'btn-gray'}">
            {'🔴 Отключить' if TEST_STATE['quota_exhausted'] else 'Включить'}
          </a>
        </div>

        <!-- Expired -->
        <div class="item-card">
          <div>
            <div style="font-weight: 600; font-size: 14px;">⏳ Истечение срока подписки</div>
            <div class="hint">В режиме заглушки заменяет сервер на «🛑 Срок подписки истёк — продлите в @just1kbot» и меняет название профиля</div>
          </div>
          <a href="/control/action?act=toggle_expired" class="btn {'btn-red' if TEST_STATE['expired'] else 'btn-gray'}">
            {'🔴 Отключить' if TEST_STATE['expired'] else 'Включить'}
          </a>
        </div>

        <!-- 403 Forbidden / Limit -->
        <div class="item-card">
          <div>
            <div style="font-weight: 600; font-size: 14px;">🚫 Превышение лимита устройств (2/2)</div>
            <div class="hint">В режиме заглушки отдаёт сервер «🚫 Превышен лимит устройств». В режиме 403 отдаёт сырой HTTP 403.</div>
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
      <a href="/control/action?act=reset_all" class="btn btn-outline" style="color: #94a3b8;">🔄 Сбросить ВСЕ настройки к умолчанию (NL + PL)</a>
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
