"""Mocked external services for the local simulation testbed.

Importing this module monkeypatches AmneziaClient, YooKassaService and
XrayNodeClient with in-memory fakes (simulation only, never production).
"""
from __future__ import annotations

import json
import logging
import uuid

from services.amnezia_client import (
    AmneziaAPIResult,
    AmneziaClient,
    AmneziaClientCreateResponse,
    AmneziaClientListItem,
)
from services.xray_node_client import (
    SyncResponse,
    SyncResult,
    XrayNodeClient,
)
from services.yookassa_service import YooKassaResult, YooKassaService
from utils.datetime_helpers import now_utc
from utils.vpn_parser import encode_json_to_vpn_uri

# --- 3. AMNEZIA VPN & YOOKASSA MOCK GENERATORS ---

def generate_mock_amnezia_vpn_uri(
    client_name: str,
    peer_id: str,
    host: str = "nl1.just1k.net",
) -> str:
    """Generate a realistic AmneziaWG 2.0 configuration URI with obfuscation parameters."""
    client_priv = f"MOCK_PRIVKEY_{peer_id[:8]}=="
    server_pub = "MOCK_PUBKEY_SERVER_NL=="
    conf_str = (
        f"[Interface]\n"
        f"PrivateKey = {client_priv}\n"
        f"Address = 10.8.0.2/32\n"
        f"DNS = 1.1.1.1, 8.8.8.8\n"
        f"Jc = 4\n"
        f"Jmin = 40\n"
        f"Jmax = 70\n"
        f"S1 = 15\n"
        f"S2 = 30\n"
        f"S3 = 10\n"
        f"S4 = 20\n"
        f"H1 = 1\n"
        f"H2 = 2\n"
        f"H3 = 3\n"
        f"H4 = 4\n\n"
        f"[Peer]\n"
        f"PublicKey = {server_pub}\n"
        f"Endpoint = {host}:51820\n"
        f"AllowedIPs = 0.0.0.0/0, ::/0\n"
        f"PersistentKeepalive = 25\n"
    )
    last_cfg = {
        "hostName": host,
        "port": 51820,
        "client_ip": "10.8.0.2/32",
        "client_priv_key": client_priv,
        "server_pub_key": server_pub,
        "Jc": 4, "Jmin": 40, "Jmax": 70,
        "S1": 15, "S2": 30, "S3": 10, "S4": 20,
        "H1": 1, "H2": 2, "H3": 3, "H4": 4,
        "config": conf_str,
        "mtu": "1280",
        "persistent_keep_alive": 25,
        "allowed_ips": ["0.0.0.0/0", "::/0"],
    }
    data = {
        "containers": [
            {
                "container": "amnezia-awg2",
                "awg": {
                    "last_config": json.dumps(last_cfg, ensure_ascii=False),
                    "protocol_version": "2",
                    "port": 51820,
                    "Jc": 4,
                    "Jmin": 40,
                    "Jmax": 70,
                    "S1": 15,
                    "S2": 30,
                    "S3": 10,
                    "S4": 20,
                    "H1": 1,
                    "H2": 2,
                    "H3": 3,
                    "H4": 4,
                },
            }
        ],
        "defaultContainer": "amnezia-awg2",
        "description": f"just1k VPN - {client_name}",
        "dns1": "1.1.1.1",
        "dns2": "8.8.8.8",
        "hostName": host,
        "port": 51820,
    }
    return encode_json_to_vpn_uri(data)


async def mock_amnezia_create_user_result(self, client_name: str, expires_at=None) -> AmneziaAPIResult:
    logger = logging.getLogger("simulation.amnezia")
    mock_peer_id = f"peer_{uuid.uuid4().hex[:8]}"
    mock_vpn_uri = generate_mock_amnezia_vpn_uri(client_name, mock_peer_id)
    logger.info("🔌 [MOCK AMNEZIA] Generated simulated VPN profile '%s' (%s)", client_name, mock_peer_id)
    resp = AmneziaClientCreateResponse(
        id=mock_peer_id,
        client_name=client_name,
        config=mock_vpn_uri,
        raw_config=mock_vpn_uri,
    )
    return AmneziaAPIResult(ok=True, value=resp, error_kind=None, status_code=200, retryable=False, ambiguous=False)


async def mock_amnezia_delete_user_result(self, client_id: str) -> AmneziaAPIResult:
    logger = logging.getLogger("simulation.amnezia")
    logger.info("🗑 [MOCK AMNEZIA] Deleted simulated VPN profile (%s)", client_id)
    return AmneziaAPIResult(ok=True, value=None, error_kind=None, status_code=200, retryable=False, ambiguous=False)


async def mock_amnezia_get_all_clients(self):
    return [
        AmneziaClientListItem(id="peer_sim_nl_iphone", username="iPhone 16 Pro", peer_name="iPhone 16 Pro"),
        AmneziaClientListItem(id="peer_sim_de_macbook", username="MacBook Pro M3", peer_name="MacBook Pro M3"),
    ]


async def mock_yookassa_create_payment_result(cls, payload: dict, *, idempotency_key: str | None = None, **kwargs) -> YooKassaResult:
    logger = logging.getLogger("simulation.yookassa")
    amount_str = payload.get("amount", {}).get("value", "100.00")
    order_id = payload.get("metadata", {}).get("order_id", str(uuid.uuid4())[:8])
    mock_id = f"mock_pay_{order_id}"
    logger.info("💳 [MOCK YOOKASSA] Created test invoice for %s RUB (ID: %s)", amount_str, mock_id)
    return YooKassaResult(
        ok=True,
        value={
            "id": mock_id,
            "status": "pending",
            "paid": False,
            "amount": {"value": amount_str, "currency": "RUB"},
            "confirmation": {
                "type": "redirect",
                "confirmation_url": f"https://t.me/just1kbot?start=pay_test_{mock_id}",
            },
            "created_at": now_utc().isoformat(),
        },
        status_code=200,
    )


async def mock_yookassa_get_payment_result(cls, payment_id: str, **kwargs) -> YooKassaResult:
    logger = logging.getLogger("simulation.yookassa")
    logger.info("✅ [MOCK YOOKASSA] Verifying payment %s -> AUTO-APPROVING AS SUCCEEDED", payment_id)
    return YooKassaResult(
        ok=True,
        value={
            "id": payment_id,
            "status": "succeeded",
            "paid": True,
            "amount": {"value": "100.00", "currency": "RUB"},
            "created_at": now_utc().isoformat(),
            "captured_at": now_utc().isoformat(),
        },
        status_code=200,
    )


async def mock_amnezia_healthcheck(self) -> bool:
    return True


async def mock_amnezia_get_server_load(self, timeout: float = 10.0) -> dict | None:
    return {
        "cpu_percent": 12.5,
        "ram_percent": 34.0,
        "disk_percent": 25.0,
        "active_peers": 3,
    }


# Apply monkeypatches to external service clients
AmneziaClient.create_user_result = mock_amnezia_create_user_result
AmneziaClient.delete_user_result = mock_amnezia_delete_user_result
AmneziaClient.get_all_clients = mock_amnezia_get_all_clients
AmneziaClient.healthcheck = mock_amnezia_healthcheck
AmneziaClient.get_server_load = mock_amnezia_get_server_load
YooKassaService.create_payment_result = classmethod(mock_yookassa_create_payment_result)
YooKassaService.get_payment_result = classmethod(mock_yookassa_get_payment_result)


async def mock_xray_check_health(self, api_url: str, api_key: str):
    logger = logging.getLogger("simulation.xray")
    logger.debug("🩺 [MOCK XRAY] Healthcheck OK (%s)", api_url)
    return True, "sim_epoch_1", {
        "status": "ok",
        "xray_running": True,
        "grpc_ok": True,
        "node_epoch": "sim_epoch_1",
    }


async def mock_xray_sync_client(self, api_url: str, api_key: str, client_uuid: str, is_active: bool, **kwargs):
    logger = logging.getLogger("simulation.xray")
    logger.info("⚡ [MOCK XRAY] Synced client %s (active=%s)", client_uuid, is_active)
    return SyncResponse(
        SyncResult.APPLIED,
        verified_epoch="sim_epoch_1",
        verified_inbounds=[
            "just1k-wl-default",
            "just1k-wl-inbound-de-relay-01",
            "just1k-wl-inbound-se-relay-01",
            "just1k-vless-direct",
        ],
    )


async def mock_xray_get_inventory(self, api_url: str, api_key: str, client_ids=None):
    return True, {"clients": [], "epoch": "sim_epoch_1"}, None


async def mock_xray_remove_client(self, api_url: str, api_key: str, client_uuid: str, version=None):
    logger = logging.getLogger("simulation.xray")
    logger.info("🗑 [MOCK XRAY] Removed client %s", client_uuid)
    return SyncResult.APPLIED, None


async def mock_xray_get_traffic_snapshot(self, api_url: str, api_key: str):
    return "sim_epoch_1", "sim_boot_1", 1700000000, {}


async def mock_xray_get_relays_health(self, api_url: str, api_key: str):
    logger = logging.getLogger("simulation.xray")
    logger.info("📡 [MOCK XRAY] Queried real-time relay health from Origin (%s)", api_url)
    return True, {
        "status": "ok",
        "count": 2,
        "all_healthy": True,
        "relays": [
            {
                "code": "de-relay-01",
                "tag": "de-relay-01",
                "name": "Германия Релей #1",
                "flag": "🇩🇪",
                "ip": "185.190.140.1",
                "port": 10443,
                "healthy": True,
                "status": "online",
                "rtt_ms": 14.2,
                "reachable": True,
                "error": None,
            },
            {
                "code": "se-relay-01",
                "tag": "se-relay-01",
                "name": "Швеция Релей #1",
                "flag": "🇸🇪",
                "ip": "194.26.229.2",
                "port": 10443,
                "healthy": True,
                "status": "online",
                "rtt_ms": 28.5,
                "reachable": True,
                "error": None,
            },
        ],
    }, None


XrayNodeClient.check_health = mock_xray_check_health
XrayNodeClient.sync_client = mock_xray_sync_client
XrayNodeClient.get_inventory = mock_xray_get_inventory
XrayNodeClient.remove_client = mock_xray_remove_client
XrayNodeClient.get_traffic_snapshot = mock_xray_get_traffic_snapshot
XrayNodeClient.get_relays_health = mock_xray_get_relays_health
