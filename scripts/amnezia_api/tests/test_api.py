"""Comprehensive unit and integration tests for scripts/amnezia_api (FastAPI microservice).

Verifies:
- Native Curve25519 keypair generation and base64 format
- awg0.conf parser for Interface and Peer sections (including AWG 2.0 & 3.x params)
- Dynamic IP allocation overcoming the 254-peer limit (/22 subnet support)
- Amnezia vpn:// URI packing and unpacking for Awg2 (protocol_version 3.1)
- Non-destructive peer addition (appending [Peer]) and removal
- API endpoints: /healthz, /server, /server/load, /clients (CRUD & disable/enable)
- Fail-closed authentication
- Full contract compatibility with services/amnezia_client.py models
"""

import asyncio
import base64
import json
import struct
import zlib
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

try:
    import app as amnezia_app
except ImportError:
    from scripts.amnezia_api import app as amnezia_app

from services.amnezia_client import (
    AmneziaClient,
    AmneziaClientCreateResponse,
    AmneziaServerInfo,
)


# Helper for testing vpn:// decoder (from docs/amnezia_docs.md)
def decode_vpn_uri(uri: str) -> dict:
    payload = uri[6:]  # remove vpn://
    b64 = payload.replace("-", "+").replace("_", "/")
    b64 += "=" * ((4 - len(b64) % 4) % 4)
    data = base64.b64decode(b64)
    orig_len = struct.unpack(">I", data[:4])[0]
    json_bytes = zlib.decompress(data[4:])
    assert len(json_bytes) == orig_len, f"Length mismatch: {len(json_bytes)} != {orig_len}"
    return json.loads(json_bytes.decode("utf-8"))


# Sample AWG awg0.conf with AWG 2.0 & 3.x parameters
SAMPLE_AWG0_CONF = """[Interface]
Address = 10.8.1.1/24
ListenPort = 44321
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
# I1 = 1234
HeaderProtectionKey = hpk_secret_key_123=
ContentPaddingAddition = 10-100

[Peer]
PublicKey = bRqF9LY7lnONibMDWH3u0QbeC7QbrLYPufdO4QMm53o=
PresharedKey = PGh2rNsBmWVJC7qpa3fZ1dwB6tLjBUVKsxSZK6pMQRY=
AllowedIPs = 10.8.1.2/32
"""


@pytest.fixture
def mock_awg_env(tmp_path):
    """Set up temporary AWG environment directory and files."""
    awg_dir = tmp_path / "awg"
    awg_dir.mkdir()

    conf_file = awg_dir / "awg0.conf"
    conf_file.write_text(SAMPLE_AWG0_CONF, encoding="utf-8")

    clients_file = awg_dir / "clientsTable"
    # Official upstream Amnezia clientsTable schema
    sample_clients = [
        {
            "clientId": "bRqF9LY7lnONibMDWH3u0QbeC7QbrLYPufdO4QMm53o=",
            "userData": {
                "clientName": "test_peer_1",
                "creationDate": "2026-09-25T12:00:00Z",
            },
            "status": "active",
        }
    ]
    clients_file.write_text(json.dumps(sample_clients), encoding="utf-8")

    psk_file = awg_dir / "wireguard_psk.key"
    psk_file.write_text("PGh2rNsBmWVJC7qpa3fZ1dwB6tLjBUVKsxSZK6pMQRY=", encoding="utf-8")

    pub_file = awg_dir / "wireguard_server_public_key.key"
    pub_file.write_text("serverpubkey1234567890=", encoding="utf-8")

    with patch.object(amnezia_app, "AWG_DIR", str(awg_dir)), \
         patch.object(amnezia_app, "AWG_CONF_PATH", str(conf_file)), \
         patch.object(amnezia_app, "CLIENTS_TABLE_PATH", str(clients_file)), \
         patch.object(amnezia_app, "SERVER_PSK_PATH", str(psk_file)), \
         patch.object(amnezia_app, "SERVER_PUBKEY_PATH", str(pub_file)), \
         patch.object(amnezia_app, "API_KEY", "secret-test-api-key"), \
         patch.object(amnezia_app, "SERVER_HOST_NAME", "vpn.example.com"), \
         patch.object(amnezia_app, "AWG_CONTAINER_NAME", "amnezia-awg2"), \
         patch.object(amnezia_app, "run_docker_exec_async", new=AsyncMock(return_value=(0, "ok", ""))), \
         patch.object(amnezia_app, "is_container_running", new=AsyncMock(return_value=True)):
        yield {
            "awg_dir": awg_dir,
            "conf_file": conf_file,
            "clients_file": clients_file,
            "psk_file": psk_file,
            "pub_file": pub_file,
        }


# =============================================================================
# Unit Tests: Cryptography & Key Generation
# =============================================================================
def test_generate_keypair():
    priv, pub = amnezia_app.generate_keypair()
    assert isinstance(priv, str) and len(priv) == 44  # 32 bytes base64 with '='
    assert isinstance(pub, str) and len(pub) == 44
    assert priv != pub
    # Check valid base64
    assert len(base64.b64decode(priv)) == 32
    assert len(base64.b64decode(pub)) == 32


def test_generate_psk():
    psk = amnezia_app.generate_psk()
    assert isinstance(psk, str) and len(psk) == 44
    assert len(base64.b64decode(psk)) == 32


# =============================================================================
# Unit Tests: Config Parser & Public Key Derivation
# =============================================================================
def test_parse_awg_conf():
    parsed = amnezia_app.parse_awg_conf(SAMPLE_AWG0_CONF)
    iface = parsed["interface"]
    peers = parsed["peers"]

    assert iface["Address"] == "10.8.1.1/24"
    assert iface["ListenPort"] == "44321"
    assert iface["Jc"] == "4"
    assert iface["Jmin"] == "10"
    assert iface["Jmax"] == "50"
    assert iface["S1"] == "79"
    assert iface["S4"] == "1"
    assert iface["H1"] == "169154911-1234371153"
    assert iface["I1"] == "1234"
    assert iface["HeaderProtectionKey"] == "hpk_secret_key_123="
    assert iface["ContentPaddingAddition"] == "10-100"

    assert len(peers) == 1
    assert peers[0]["PublicKey"] == "bRqF9LY7lnONibMDWH3u0QbeC7QbrLYPufdO4QMm53o="
    assert peers[0]["AllowedIPs"] == "10.8.1.2/32"


def test_get_server_public_key_derived():
    # When file doesn't exist, derives from PrivateKey
    with patch.object(amnezia_app, "SERVER_PUBKEY_PATH", "/non/existent/path"):
        iface = {"PrivateKey": "uC6xUgdQDF4+fAOiw37ZQCG7XljilDsnBCl7VH7bAl8="}
        pub = asyncio.run(amnezia_app.get_server_public_key_async("amnezia-awg2", iface))
        assert pub
        assert len(base64.b64decode(pub)) == 32


# =============================================================================
# Unit Tests: Dynamic IP Allocation (Exceeding 254-peer limit)
# =============================================================================
def test_allocate_next_ip_standard_24():
    peers = [{"AllowedIPs": "10.8.1.2/32"}]
    ip = amnezia_app.allocate_next_ip("10.8.1.1/24", peers)
    assert ip == "10.8.1.3"


def test_allocate_next_ip_subnet_22_support():
    peers = [{"AllowedIPs": f"10.8.1.{i}/32"} for i in range(2, 255)]
    ip = amnezia_app.allocate_next_ip("10.8.0.1/22", peers)
    assert ip == "10.8.0.2"


def test_allocate_next_ip_exhaustion():
    peers = [{"AllowedIPs": "10.8.1.2/32"}]
    with pytest.raises(HTTPException) as excinfo:
        amnezia_app.allocate_next_ip("10.8.1.1/30", peers)
    assert excinfo.value.status_code == 507


# =============================================================================
# Unit Tests: Non-destructive Peer Management
# =============================================================================
def test_append_peer_to_conf_text():
    orig_conf = "[Interface]\nAddress = 10.8.1.1/24\n"
    new_conf = amnezia_app.append_peer_to_conf_text(orig_conf, "newpubkey=", "newpsk=", "10.8.1.3")
    assert "[Interface]" in new_conf
    assert "PublicKey = newpubkey=" in new_conf
    assert "PresharedKey = newpsk=" in new_conf
    assert "AllowedIPs = 10.8.1.3/32" in new_conf


def test_remove_peer_from_conf_text():
    conf_with_2_peers = (
        "[Interface]\nAddress = 10.8.1.1/24\n"
        "[Peer]\nPublicKey = pub1=\nPresharedKey = psk1=\nAllowedIPs = 10.8.1.2/32\n"
        "[Peer]\nPublicKey = pub2=\nPresharedKey = psk2=\nAllowedIPs = 10.8.1.3/32\n"
    )
    updated, removed = amnezia_app.remove_peer_from_conf_text(conf_with_2_peers, "pub1=")
    assert removed is True
    assert "pub1=" not in updated
    assert "pub2=" in updated
    assert "[Interface]" in updated


# =============================================================================
# Unit Tests: Amnezia vpn:// and .conf Config Builder (AWG 3.1)
# =============================================================================
def test_build_client_configs_and_vpn_uri():
    client = {
        "clientIp": "10.8.1.5",
        "clientPrivKey": "privkey5=",
        "clientPubKey": "pubkey5=",
        "psk": "psk5=",
    }
    interface_params = {
        "ListenPort": "44321",
        "Jc": "4",
        "Jmin": "10",
        "Jmax": "50",
        "S1": "79",
        "S2": "115",
        "S3": "5",
        "S4": "1",
        "H1": "100-200",
        "H2": "300-400",
        "H3": "500-600",
        "H4": "700-800",
        "I1": "9999",
        "HeaderProtectionKey": "hpk_test=",
    }
    raw_conf, vpn_uri = amnezia_app.build_client_configs(
        client,
        interface_params,
        server_pubkey="srvpub=",
        host_name="vpn.example.com",
        dns1="1.1.1.1",
        dns2="1.0.0.1",
        container_name="amnezia-awg2",
    )

    # Check raw .conf
    assert "[Interface]" in raw_conf
    assert "Address = 10.8.1.5/32" in raw_conf
    assert "PrivateKey = privkey5=" in raw_conf
    assert "Jc = 4" in raw_conf
    assert "H1 = 100-200" in raw_conf
    assert "I1 = 9999" in raw_conf
    assert "HeaderProtectionKey = hpk_test=" in raw_conf
    assert "[Peer]" in raw_conf
    assert "PublicKey = srvpub=" in raw_conf
    assert "Endpoint = vpn.example.com:44321" in raw_conf

    # Check vpn:// URI
    assert vpn_uri.startswith("vpn://")
    decoded = decode_vpn_uri(vpn_uri)
    assert decoded["defaultContainer"] == "amnezia-awg2"
    assert decoded["hostName"] == "vpn.example.com"
    awg = decoded["containers"][0]["awg"]
    assert awg["protocol_version"] == "3.1"
    assert awg["port"] == "44321"
    assert awg["HeaderProtectionKey"] == "hpk_test="
    last_cfg = json.loads(awg["last_config"])
    assert last_cfg["client_ip"] == "10.8.1.5"
    assert last_cfg["client_pub_key"] == "pubkey5="


# =============================================================================
# Integration Tests: FastAPI Endpoints
# =============================================================================
def test_healthz_endpoint(mock_awg_env):
    client = TestClient(amnezia_app.app)
    response = client.get("/healthz")
    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is True
    assert data["status"] == "ok"
    assert data["service"] == "amnezia-api"
    assert data["container"] == "amnezia-awg2"
    assert data["container_running"] is True
    assert data["interface_ready"] is True


def test_auth_fail_closed(mock_awg_env):
    client = TestClient(amnezia_app.app)
    # 1. Invalid or missing key returns 401
    resp1 = client.get("/server")
    assert resp1.status_code == 401

    resp2 = client.get("/server", headers={"x-api-key": "wrong-key"})
    assert resp2.status_code == 401

    # 2. When API_KEY environment variable is empty, it fails closed with 500
    with patch.object(amnezia_app, "API_KEY", ""):
        resp3 = client.get("/server", headers={"x-api-key": "some-key"})
        assert resp3.status_code == 500


def test_server_endpoint(mock_awg_env):
    client = TestClient(amnezia_app.app)
    resp = client.get("/server", headers={"x-api-key": "secret-test-api-key"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["name"] == "amnezia-awg2"
    assert data["protocols"] == ["amneziawg2"]
    assert data["port"] == 44321
    assert data["maxPeers"] > 0
    assert "id" in data
    assert "totalPeers" in data

    # Contract check: services.amnezia_client.AmneziaServerInfo must parse it
    info = AmneziaServerInfo(**data)
    assert info.get_effective_max_peers() == data["maxPeers"]


def test_server_load_endpoint(mock_awg_env):
    client = TestClient(amnezia_app.app)
    resp = client.get("/server/load", headers={"x-api-key": "secret-test-api-key"})
    assert resp.status_code == 200
    data = resp.json()
    assert "cpu_percent" in data
    assert "ram_percent" in data
    assert "disk_percent" in data
    assert "uptime_seconds" in data
    assert "uptimeSec" in data
    assert "memory" in data
    assert "disk" in data
    assert data["total_peers"] == 1
    assert data["active_peers"] == 1


def test_clients_crud_lifecycle(mock_awg_env):
    client = TestClient(amnezia_app.app)
    headers = {"x-api-key": "secret-test-api-key"}

    # 1. GET /clients
    get_resp = client.get("/clients", headers=headers)
    assert get_resp.status_code == 200
    clients_payload = get_resp.json()
    assert clients_payload["total"] == 1
    assert len(clients_payload["items"]) == 1
    assert clients_payload["items"][0]["username"] == "test_peer_1"

    # Contract check: services.amnezia_client.AmneziaClient._parse_clients_page
    parsed_items = AmneziaClient._parse_clients_page(clients_payload)
    assert len(parsed_items) == 1
    assert parsed_items[0].username == "test_peer_1"
    assert parsed_items[0].status == "active"

    # Also check passing raw items list directly
    parsed_items_direct = AmneziaClient._parse_clients_page(clients_payload["items"])
    assert len(parsed_items_direct) == 1
    assert parsed_items_direct[0].username == "test_peer_1"

    # 2. POST /clients (Create new client)
    create_payload = {
        "clientName": "user_42",
        "protocol": "amneziawg2",
    }
    create_resp = client.post("/clients", json=create_payload, headers=headers)
    assert create_resp.status_code == 200
    created = create_resp.json()
    assert "id" in created
    assert "client" in created
    assert created["client"]["id"] == created["id"]
    assert created["config"].startswith("vpn://")
    assert created["protocol"] == "amneziawg2"
    new_client_id = created["id"]

    # Contract check: services.amnezia_client.AmneziaClientCreateResponse
    parsed_create = AmneziaClientCreateResponse(**created)
    assert parsed_create.id == new_client_id
    assert parsed_create.config == created["config"]

    # 3. Verify in clients list
    get_resp2 = client.get("/clients", headers=headers)
    assert get_resp2.json()["total"] == 2
    assert len(get_resp2.json()["items"]) == 2

    # 3b. Verify pagination (skip & limit) for AmneziaClient compatibility
    p1_resp = client.get("/clients?skip=0&limit=1", headers=headers)
    assert p1_resp.status_code == 200
    assert p1_resp.json()["total"] == 2
    assert len(p1_resp.json()["items"]) == 1
    assert p1_resp.json()["items"][0]["username"] == "test_peer_1"

    p2_resp = client.get("/clients?skip=1&limit=1", headers=headers)
    assert p2_resp.status_code == 200
    assert p2_resp.json()["total"] == 2
    assert len(p2_resp.json()["items"]) == 1
    assert p2_resp.json()["items"][0]["username"] == "user_42"

    p3_resp = client.get("/clients?skip=2&limit=1", headers=headers)
    assert p3_resp.status_code == 200
    assert p3_resp.json()["total"] == 2
    assert len(p3_resp.json()["items"]) == 0

    # 4. PATCH /clients (Disable client)
    patch_payload = {
        "clientId": new_client_id,
        "status": "disabled",
    }
    patch_resp = client.patch("/clients", json=patch_payload, headers=headers)
    assert patch_resp.status_code == 200
    assert patch_resp.json()["status"] == "updated"
    assert "message" in patch_resp.json()

    # Verify status changed to disabled
    single_resp = client.get(f"/clients/{new_client_id}", headers=headers)
    assert single_resp.status_code == 200
    assert single_resp.json()["client"]["status"] == "disabled"

    # 5. PATCH /clients (Re-enable client)
    patch_resp2 = client.patch(
        f"/clients/{new_client_id}",
        json={"clientId": new_client_id, "status": "active"},
        headers=headers,
    )
    assert patch_resp2.status_code == 200
    assert client.get(f"/clients/{new_client_id}", headers=headers).json()["client"]["status"] == "active"

    # 6. DELETE /clients (by body and by path)
    del_resp = client.request(
        "DELETE",
        "/clients",
        json={"clientId": new_client_id, "protocol": "amneziawg2"},
        headers=headers,
    )
    assert del_resp.status_code == 200
    assert del_resp.json()["status"] == "ok"

    # Verify deleted
    del_check = client.get(f"/clients/{new_client_id}", headers=headers)
    assert del_check.status_code == 404

    # Existing peer is still present!
    orig_check = client.get("/clients", headers=headers)
    assert orig_check.json()["total"] == 1
    assert len(orig_check.json()["items"]) == 1
    assert orig_check.json()["items"][0]["username"] == "test_peer_1"
