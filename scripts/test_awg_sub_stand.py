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

# In-memory test state
REGISTERED_DEVICES: dict[str, dict] = {}
DEVICE_LIMIT = 2
VALID_TOKEN = "test_awg_vip_token"


def get_slot_configs(slot: int) -> list[tuple[str, str, str]]:
    if slot == 1:
        return [
            (SLOT_1_NETHERLANDS, "Netherlands (Slot 1)", "🇳🇱"),
            (SLOT_1_POLAND, "Poland (Slot 1)", "🇵🇱"),
        ]
    elif slot == 2:
        return [
            (SLOT_2_NETHERLANDS, "Netherlands (Slot 2)", "🇳🇱"),
            (SLOT_2_POLAND, "Poland (Slot 2)", "🇵🇱"),
        ]
    return []


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
                "🚫 Device limit reached! Active: %d, Max: %d. Rejecting HWID: %s",
                active_count,
                DEVICE_LIMIT,
                hwid,
            )
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
    body = AWGSubscriptionFeedService.build_subscription_body(configs)
    headers = AWGSubscriptionFeedService.build_subscription_headers(
        profile_title=f"JUST1K AWG (Device #{assigned_slot})",
        expire_ts=int(datetime(2026, 12, 31, tzinfo=timezone.utc).timestamp()),
        upload_bytes=1048576,
        download_bytes=10485760,
        total_quota_bytes=0,  # Unlimited
        update_interval_hours=6,
        support_url="https://t.me/just1k_support",
        hide_url=True,
    )

    logger.info("📤 Response 200 OK sent with %d servers for Slot #%d", len(configs), assigned_slot)
    logger.info("==================================================")
    return web.Response(status=200, text=body, headers=headers)


async def handle_status(_request: web.Request) -> web.Response:
    status_data = {
        "status": "online",
        "device_limit": DEVICE_LIMIT,
        "active_devices_count": len(REGISTERED_DEVICES),
        "devices": REGISTERED_DEVICES,
        "test_sub_url": f"/sub/awg/{VALID_TOKEN}",
        "deep_link_sample": f"incy://add/http://YOUR_HOST_OR_IP:8088/sub/awg/{VALID_TOKEN}",
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
