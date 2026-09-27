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
S3 = 45
S4 = 30
H1 = 169154911-1234371153
H2 = 2057051984-2121122945
H3 = 2132872968-2133668229
H4 = 2136455412-2141801388
# I1 = 1234
HeaderProtectionKey = v1c2X3y4Z5a6B7c8D9e0F1g2H3i4J5k6L7m8N9o0P1Q=
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
    assert iface["S4"] == "30"
    assert iface["H1"] == "169154911-1234371153"
    assert iface["I1"] == "1234"
    assert iface["HeaderProtectionKey"] == "v1c2X3y4Z5a6B7c8D9e0F1g2H3i4J5k6L7m8N9o0P1Q="
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
def test_build_client_configs_and_vpn_uri_awg2():
    """Verify AWG 2.0 key generation strictly mirrors all 28 parameters matching k1 reference."""
    client = {
        "clientIp": "10.8.1.25",
        "clientPrivKey": "privkey25=",
        "clientPubKey": "pubkey25=",
        "psk": "psk25=",
    }
    interface_params = {
        "ListenPort": "31999",
        "MTU": "1280",
        "Jc": "4",
        "Jmin": "10",
        "Jmax": "50",
        "S1": "87",
        "S2": "61",
        "S3": "49",
        "S4": "1",
        "H1": "1833642353-2011184227",
        "H2": "2079608917-2100435225",
        "H3": "2141059580-2143059209",
        "H4": "2146085550-2147444879",
        "I1": "<r 2><b 0x858000010001000000000669636c6f756403636f6d0000010001c00c000100010000105a00044d583737>",
    }
    raw_conf, vpn_uri = amnezia_app.build_client_configs(
        client,
        interface_params,
        server_pubkey="srvpub=",
        host_name="nl.just1k.best",
        dns1="8.8.8.8",
        dns2="8.8.4.4",
        container_name="amnezia-awg2",
    )

    # Check raw .conf
    assert "[Interface]" in raw_conf
    assert "DNS = 8.8.8.8, 8.8.4.4" in raw_conf
    assert "MTU = 1280" in raw_conf
    assert "Address = 10.8.1.25/32" in raw_conf
    assert "PrivateKey = privkey25=" in raw_conf
    assert "Jc = 4" in raw_conf
    assert "S3 = 49" in raw_conf
    assert "H1 = 1833642353-2011184227" in raw_conf
    assert "I1 = <r 2><b 0x85800001" in raw_conf
    # Empty I2..I5 must not be emitted as broken empty lines in .conf
    assert "I2 =" not in raw_conf
    assert "[Peer]" in raw_conf
    assert "PublicKey = srvpub=" in raw_conf
    assert "PresharedKey = psk25=" in raw_conf
    assert "AllowedIPs = 0.0.0.0/0, ::/0" in raw_conf
    assert "Endpoint = nl.just1k.best:31999" in raw_conf
    assert "PersistentKeepalive = 25" in raw_conf

    # Check vpn:// URI
    assert vpn_uri.startswith("vpn://")
    decoded = decode_vpn_uri(vpn_uri)
    assert decoded["defaultContainer"] == "amnezia-awg2"
    assert decoded["hostName"] == "nl.just1k.best"
    assert decoded["dns1"] == "8.8.8.8"
    assert decoded["dns2"] == "8.8.4.4"

    awg = decoded["containers"][0]["awg"]
    assert awg["protocol_version"] == "2"
    assert awg["port"] == "31999"
    assert awg["transport_proto"] == "udp"
    assert awg["Jc"] == "4"
    assert awg["S3"] == "49"
    assert awg["I1"].startswith("<r 2><b")
    # I2..I5 must be present as empty strings in awg dict
    assert awg["I2"] == ""
    assert awg["I3"] == ""
    assert awg["I4"] == ""
    assert awg["I5"] == ""

    # Check last_config has all 28 mirrored fields
    last_cfg = json.loads(awg["last_config"])
    expected_fields = [
        "H1", "H2", "H3", "H4",
        "I1", "I2", "I3", "I4", "I5",
        "Jc", "Jmax", "Jmin",
        "S1", "S2", "S3", "S4",
        "allowed_ips", "clientId", "client_ip", "client_priv_key", "client_pub_key",
        "config", "hostName", "mtu", "persistent_keep_alive", "port", "psk_key", "server_pub_key",
    ]
    for field in expected_fields:
        assert field in last_cfg, f"Field {field} missing from last_config"
    assert last_cfg["client_ip"] == "10.8.1.25"
    assert last_cfg["client_pub_key"] == "pubkey25="
    assert last_cfg["clientId"] == "pubkey25="
    assert last_cfg["allowed_ips"] == ["0.0.0.0/0", "::/0"]
    assert last_cfg["mtu"] == "1280"
    assert last_cfg["persistent_keep_alive"] == "25"
    assert last_cfg["port"] == 31999
    assert last_cfg["Jc"] == "4"
    assert last_cfg["S3"] == "49"
    assert last_cfg["I2"] == ""

    # 3-way consistency check across raw conf, awg dict, and last_config
    for k in ("Jc", "Jmin", "Jmax", "S1", "S2", "S3", "S4", "H1", "H2", "H3", "H4"):
        assert awg[k] == str(interface_params[k])
        assert last_cfg[k] == str(interface_params[k])
        assert f"{k} = {interface_params[k]}" in raw_conf


def test_build_client_configs_and_vpn_uri_awg3():
    """Verify AWG 3.x key generation preserves container amnezia-awg2 and detects 3.0 vs 3.1."""
    client = {
        "clientIp": "10.8.1.5",
        "clientPrivKey": "privkey5=",
        "clientPubKey": "pubkey5=",
        "psk": "psk5=",
    }
    # AWG 3.0 (with HeaderProtectionKey)
    hpk_valid = "v1c2X3y4Z5a6B7c8D9e0F1g2H3i4J5k6L7m8N9o0P1Q="
    interface_params_30 = {
        "ListenPort": "44321",
        "Jc": "4",
        "Jmin": "10",
        "Jmax": "50",
        "S1": "79",
        "S2": "115",
        "S3": "45",
        "S4": "30",
        "H1": "100-200",
        "H2": "300-400",
        "H3": "500-600",
        "H4": "700-800",
        "I1": "9999",
        "HeaderProtectionKey": hpk_valid,
    }
    raw_conf, vpn_uri = amnezia_app.build_client_configs(
        client,
        interface_params_30,
        server_pubkey="srvpub=",
        host_name="vpn.example.com",
        dns1="1.1.1.1",
        dns2="1.0.0.1",
        container_name="amnezia-awg2",
    )

    assert "[Interface]" in raw_conf
    assert f"HeaderProtectionKey = {hpk_valid}" in raw_conf
    assert vpn_uri.startswith("vpn://")
    decoded = decode_vpn_uri(vpn_uri)
    # Container MUST remain amnezia-awg2 for native Amnezia client compatibility
    assert decoded["defaultContainer"] == "amnezia-awg2"
    awg = decoded["containers"][0]["awg"]
    assert awg["protocol_version"] == "3.0"
    assert awg["HeaderProtectionKey"] == hpk_valid
    last_cfg = json.loads(awg["last_config"])
    assert last_cfg["HeaderProtectionKey"] == hpk_valid
    assert last_cfg["Jc"] == "4"
    assert last_cfg["port"] == 44321

    # AWG 3.1 (with RandomTrailers)
    interface_params_31 = dict(interface_params_30)
    interface_params_31["RandomTrailers"] = "1"
    _, vpn_uri_31 = amnezia_app.build_client_configs(
        client,
        interface_params_31,
        server_pubkey="srvpub=",
        host_name="vpn.example.com",
        dns1="1.1.1.1",
        dns2="1.0.0.1",
        container_name="amnezia-awg2",
    )
    decoded_31 = decode_vpn_uri(vpn_uri_31)
    assert decoded_31["defaultContainer"] == "amnezia-awg2"
    assert decoded_31["containers"][0]["awg"]["protocol_version"] == "3.1"
    assert decoded_31["containers"][0]["awg"]["RandomTrailers"] == "1"

    # Rejection of incomplete interface missing mandatory AWG 2.0+ parameters
    incomplete_iface = {
        "ListenPort": "44321",
        "Jc": "4",
        "Jmin": "10",
        "Jmax": "50",
        "S1": "79",
        "S2": "115",
        # Missing S3, S4, H1..H4
    }
    with pytest.raises(HTTPException) as exc_info:
        amnezia_app.build_client_configs(
            client,
            incomplete_iface,
            server_pubkey="srvpub=",
            host_name="vpn.example.com",
            dns1="1.1.1.1",
            dns2="1.0.0.1",
            container_name="amnezia-awg2",
        )
    assert exc_info.value.status_code == 422
    assert "missing mandatory AWG 2.0+ parameters" in exc_info.value.detail

    # Rejection of AWG 3.x interface missing HeaderProtectionKey
    incomplete_30_iface = dict(interface_params_30)
    del incomplete_30_iface["HeaderProtectionKey"]
    incomplete_30_iface["RandomTrailers"] = "1"  # triggers AWG 3.x detection
    with pytest.raises(HTTPException) as exc_info_hpk:
        amnezia_app.build_client_configs(
            client,
            incomplete_30_iface,
            server_pubkey="srvpub=",
            host_name="vpn.example.com",
            dns1="1.1.1.1",
            dns2="1.0.0.1",
            container_name="amnezia-awg2",
        )
    assert exc_info_hpk.value.status_code == 422
    assert "HeaderProtectionKey" in exc_info_hpk.value.detail

    # Rejection of invalid HeaderProtectionKey (not 32-byte base64)
    invalid_hpk_iface = dict(interface_params_30)
    invalid_hpk_iface["HeaderProtectionKey"] = "hpk_test="
    with pytest.raises(HTTPException) as exc_invalid_hpk:
        amnezia_app.build_client_configs(
            client,
            invalid_hpk_iface,
            server_pubkey="srvpub=",
            host_name="vpn.example.com",
            dns1="1.1.1.1",
            dns2="1.0.0.1",
            container_name="amnezia-awg2",
        )
    assert exc_invalid_hpk.value.status_code == 422
    assert "HeaderProtectionKey" in exc_invalid_hpk.value.detail

    # Rejection of S3 < 12 under Header Protection
    invalid_s3_iface = dict(interface_params_30)
    invalid_s3_iface["S3"] = "5"
    with pytest.raises(HTTPException) as exc_invalid_s3:
        amnezia_app.build_client_configs(
            client,
            invalid_s3_iface,
            server_pubkey="srvpub=",
            host_name="vpn.example.com",
            dns1="1.1.1.1",
            dns2="1.0.0.1",
            container_name="amnezia-awg2",
        )
    assert exc_invalid_s3.value.status_code == 422
    assert "S3" in exc_invalid_s3.value.detail

    # Rejection of Jc out of range
    invalid_jc_iface = dict(interface_params_30)
    invalid_jc_iface["Jc"] = "200"
    with pytest.raises(HTTPException) as exc_invalid_jc:
        amnezia_app.build_client_configs(
            client,
            invalid_jc_iface,
            server_pubkey="srvpub=",
            host_name="vpn.example.com",
            dns1="1.1.1.1",
            dns2="1.0.0.1",
            container_name="amnezia-awg2",
        )
    assert exc_invalid_jc.value.status_code == 422
    assert "Jc" in exc_invalid_jc.value.detail


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
    assert data["protocols"] == ["amneziawg2", "amneziawg3"]
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
    assert created["client"]["id"] == created["id"]
    assert created["config"].startswith("vpn://")
    assert created["protocol"] == "amneziawg3"
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


@pytest.mark.asyncio
async def test_server_backup_export_and_import(monkeypatch):
    """Test full server backup export and import endpoints."""
    monkeypatch.setattr(amnezia_app, "API_KEY", "secret-test-key")
    headers = {"X-API-Key": "secret-test-key"}

    current_conf = SAMPLE_AWG0_CONF
    current_table = [{"clientId": "peer1", "clientPubKey": "peer1", "clientIp": "10.8.1.2"}]
    current_psk = "vI9V78j2eX4uGv7l0i1XN9b9yP4oR2tQ8uY4wI7qB3o="

    async def mock_read(path):
        return current_conf

    async def mock_load_table():
        return list(current_table)

    async def mock_get_psk(*args, **kwargs):
        return current_psk

    saved_files = {}

    async def mock_write(path, content):
        saved_files[path] = content
        return True

    saved_tables = []

    async def mock_save_table(table):
        saved_tables.append(table)
        return True

    mock_syncconf = AsyncMock(return_value=True)

    monkeypatch.setattr(amnezia_app, "read_container_file_async", mock_read)
    monkeypatch.setattr(amnezia_app, "load_clients_table_async", mock_load_table)
    monkeypatch.setattr(amnezia_app, "get_server_psk_async", mock_get_psk)
    monkeypatch.setattr(amnezia_app, "write_container_file_async", mock_write)
    monkeypatch.setattr(amnezia_app, "save_clients_table_async", mock_save_table)
    monkeypatch.setattr(amnezia_app, "syncconf_container", mock_syncconf)

    client = TestClient(amnezia_app.app)

    # 1. GET /server/backup
    resp = client.get("/server/backup", headers=headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["version"] == 1
    assert "generatedAt" in data
    assert data["conf_content"] == SAMPLE_AWG0_CONF
    assert len(data["clients_table"]) == 1
    assert data["server_psk"] == current_psk
    assert "amnezia" in data

    # 2. POST /server/backup with valid direct payload
    import_payload = {
        "conf_content": SAMPLE_AWG0_CONF,
        "clients_table": [{"clientId": "restored_peer", "clientPubKey": "restored_peer"}],
        "server_psk": "new_psk==",
    }
    import_resp = client.post("/server/backup", json=import_payload, headers=headers)
    assert import_resp.status_code == 200
    assert import_resp.json()["status"] == "ok"
    assert import_resp.json()["peers_count"] == 1
    assert import_resp.json()["kernel_synced"] is True
    assert mock_syncconf.called

    # 3. POST /server/backup with upstream kyoresuas nested payload
    upstream_payload = {
        "amnezia": {
            "config": SAMPLE_AWG0_CONF,
            "clientsTable": [{"clientId": "upstream_peer"}],
        }
    }
    upstream_resp = client.post("/server/backup", json=upstream_payload, headers=headers)
    assert upstream_resp.status_code == 200
    assert upstream_resp.json()["status"] == "ok"

    # 4. POST /server/backup with invalid config (missing [Interface])
    bad_resp = client.post("/server/backup", json={"conf_content": "invalid data"}, headers=headers)
    assert bad_resp.status_code == 400
    assert "Interface" in bad_resp.json()["detail"]


@pytest.mark.asyncio
async def test_server_reboot_endpoint(monkeypatch):
    """Test /server/reboot endpoint returns 200 OK and starts background reboot."""
    monkeypatch.setattr(amnezia_app, "API_KEY", "secret-test-key")
    headers = {"X-API-Key": "secret-test-key"}

    client = TestClient(amnezia_app.app)
    resp = client.post("/server/reboot", headers=headers)
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"
    assert "Сервер перезагружается" in resp.json()["message"]


def test_awg2_vs_awg3_exclusive_detection():
    """Verify that I1..I5 are treated as AWG 2.0 and only exclusive keys trigger AWG 3.x."""
    # AWG 2.0 with I1..I5 parameters
    awg2_params = {
        "Jc": "4",
        "S1": "79",
        "H1": "169154911-1234371153",
        "I1": "1234",
        "I2": "5678",
    }
    assert amnezia_app.is_awg3_detected(awg2_params) is False

    # AWG 3.x with HeaderProtectionKey
    awg3_params = dict(awg2_params)
    awg3_params["HeaderProtectionKey"] = "secret_key=="
    assert amnezia_app.is_awg3_detected(awg3_params) is True

    # AWG 3.x with RekeyAfterTime
    awg3_params_2 = dict(awg2_params)
    awg3_params_2["RekeyAfterTime"] = "120"
    assert amnezia_app.is_awg3_detected(awg3_params_2) is True


def test_client_without_privkey_safe_response(mock_awg_env):
    """Verify that desktop app clients without private key don't get broken configs."""
    client = TestClient(amnezia_app.app)
    headers = {"x-api-key": "secret-test-api-key"}

    # Existing peer in mock_awg_env has no clientPrivKey in clientsTable
    peer_pub = "bRqF9LY7lnONibMDWH3u0QbeC7QbrLYPufdO4QMm53o="
    resp = client.get(f"/clients/{peer_pub}", headers=headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["id"] == peer_pub
    assert data["config"] is None
    assert data["raw_config"] is None
    assert data["client"]["clientIp"] == "10.8.1.2"
    assert data["client"]["psk"] == "PGh2rNsBmWVJC7qpa3fZ1dwB6tLjBUVKsxSZK6pMQRY="


def test_patch_client_disable_removes_from_conf_and_active_readds(mock_awg_env):
    """Verify disabling peer removes [Peer] from conf text and enabling re-appends it."""
    client = TestClient(amnezia_app.app)
    headers = {"x-api-key": "secret-test-api-key"}
    peer_pub = "bRqF9LY7lnONibMDWH3u0QbeC7QbrLYPufdO4QMm53o="
    conf_file = mock_awg_env["conf_file"]

    # Initial state: peer is in awg0.conf
    assert peer_pub in conf_file.read_text(encoding="utf-8")

    # 1. Disable peer
    patch_resp = client.patch(
        f"/clients/{peer_pub}",
        json={"clientId": peer_pub, "status": "disabled"},
        headers=headers,
    )
    assert patch_resp.status_code == 200
    # [Peer] block removed from file on disk
    conf_after_disable = conf_file.read_text(encoding="utf-8")
    assert peer_pub not in conf_after_disable

    # 2. Re-enable peer
    patch_resp2 = client.patch(
        f"/clients/{peer_pub}",
        json={"clientId": peer_pub, "status": "active"},
        headers=headers,
    )
    assert patch_resp2.status_code == 200
    # [Peer] block re-appended to file on disk
    conf_after_enable = conf_file.read_text(encoding="utf-8")
    assert peer_pub in conf_after_enable
    assert "AllowedIPs = 10.8.1.2/32" in conf_after_enable


def test_patch_client_clear_expires_at(mock_awg_env):
    """Verify explicit null/None clears expiresAt."""
    client = TestClient(amnezia_app.app)
    headers = {"x-api-key": "secret-test-api-key"}
    peer_pub = "bRqF9LY7lnONibMDWH3u0QbeC7QbrLYPufdO4QMm53o="

    # 1. Set an expiration timestamp
    client.patch(
        f"/clients/{peer_pub}",
        json={"clientId": peer_pub, "expiresAt": 1893456000},
        headers=headers,
    )
    resp = client.get(f"/clients/{peer_pub}", headers=headers)
    assert resp.json()["client"]["expiresAt"] == 1893456000

    # 2. Clear expiration by sending null
    client.patch(
        f"/clients/{peer_pub}",
        json={"clientId": peer_pub, "expiresAt": None},
        headers=headers,
    )
    resp = client.get(f"/clients/{peer_pub}", headers=headers)
    assert resp.json()["client"]["expiresAt"] is None


@pytest.mark.asyncio
async def test_persistent_psk_generation(tmp_path, monkeypatch):
    """Verify generated PSK is persisted to wireguard_psk.key."""
    psk_file = tmp_path / "wireguard_psk.key"
    monkeypatch.setattr(amnezia_app, "SERVER_PSK_PATH", str(psk_file))
    monkeypatch.setattr(amnezia_app, "AWG_DIR", str(tmp_path))

    # First call generates and persists PSK
    psk1 = await amnezia_app.get_server_psk_async()
    assert isinstance(psk1, str) and len(psk1) == 44
    assert psk_file.exists()
    assert psk_file.read_text(encoding="utf-8").strip() == psk1

    # Second call returns the persisted PSK
    psk2 = await amnezia_app.get_server_psk_async()
    assert psk1 == psk2


def test_max_peers_off_by_one_fixed(mock_awg_env):
    """Verify /24 subnet has maxPeers=253 (256 - 3)."""
    client = TestClient(amnezia_app.app)
    headers = {"x-api-key": "secret-test-api-key"}
    resp = client.get("/server", headers=headers)
    assert resp.status_code == 200
    assert resp.json()["maxPeers"] == 253


def test_import_backup_fail_closed_on_syncconf_error(mock_awg_env, monkeypatch):
    """Verify POST /server/backup raises HTTP 500 if syncconf fails."""
    monkeypatch.setattr(amnezia_app, "syncconf_container", AsyncMock(return_value=False))
    client = TestClient(amnezia_app.app)
    headers = {"x-api-key": "secret-test-api-key"}

    import_payload = {
        "conf_content": SAMPLE_AWG0_CONF,
        "clients_table": [],
    }
    resp = client.post("/server/backup", json=import_payload, headers=headers)
    assert resp.status_code == 500
    assert "sync" in resp.json()["detail"].lower()


def test_allocate_next_ip_prevents_collision_with_disabled_clients():
    """Verify allocate_next_ip considers both active conf peers and disabled clients_table peers."""
    active_peers = [{"AllowedIPs": "10.8.1.2/32"}]
    disabled_clients = [{"clientIp": "10.8.1.3", "status": "disabled"}]

    next_ip = amnezia_app.allocate_next_ip(
        "10.8.1.1/24",
        existing_peers=active_peers,
        clients_table=disabled_clients,
    )
    assert next_ip == "10.8.1.4"


def test_allocate_next_ip_comma_separated_interface_addr():
    """Verify allocate_next_ip correctly parses multi-address interface strings (e.g. IPv4 + IPv6)."""
    next_ip = amnezia_app.allocate_next_ip(
        "10.8.1.1/24, fd00:abcd::1/64",
        existing_peers=[],
    )
    assert next_ip == "10.8.1.2"


def test_get_clients_includes_disabled_clients_from_table(mock_awg_env):
    """Verify GET /clients returns disabled clients stored in clientsTable even if removed from conf."""
    client = TestClient(amnezia_app.app)
    headers = {"x-api-key": "secret-test-api-key"}
    peer_pub = "bRqF9LY7lnONibMDWH3u0QbeC7QbrLYPufdO4QMm53o="

    # 1. Disable the peer (removes from conf, marks status=disabled in clientsTable)
    patch_resp = client.patch(
        f"/clients/{peer_pub}",
        json={"clientId": peer_pub, "status": "disabled"},
        headers=headers,
    )
    assert patch_resp.status_code == 200

    # 2. Query /clients
    get_resp = client.get("/clients", headers=headers)
    assert get_resp.status_code == 200
    data = get_resp.json()
    assert data["total"] == 1
    item = data["items"][0]
    assert item["clientId"] == peer_pub
    assert item["status"] == "disabled"
    assert item["traffic"]["received"] == 0


def test_get_server_total_peers_includes_disabled_clients(mock_awg_env):
    """Verify GET /server calculates totalPeers from unique union of conf peers and clientsTable."""
    client = TestClient(amnezia_app.app)
    headers = {"x-api-key": "secret-test-api-key"}
    peer_pub = "bRqF9LY7lnONibMDWH3u0QbeC7QbrLYPufdO4QMm53o="

    # Disable peer (removes from awg0.conf)
    client.patch(
        f"/clients/{peer_pub}",
        json={"clientId": peer_pub, "status": "disabled"},
        headers=headers,
    )

    # Server should still report totalPeers == 1
    resp = client.get("/server", headers=headers)
    assert resp.status_code == 200
    assert resp.json()["totalPeers"] == 1


@pytest.mark.asyncio
async def test_sync_kernel_peer_add_without_psk(monkeypatch):
    """Verify sync_kernel_peer_add does not pass preshared-key when psk is empty."""
    captured_commands = []

    async def fake_docker_exec(cmd):
        captured_commands.append(cmd)
        return 0, "", ""

    monkeypatch.setattr(amnezia_app, "run_docker_exec_async", fake_docker_exec)

    success = await amnezia_app.sync_kernel_peer_add(
        pubkey="testpubkey123=",
        ip="10.8.1.5",
        psk="",
        container="amnezia-awg",
    )
    assert success is True
    assert len(captured_commands) == 1
    sh_cmd = captured_commands[0][2]
    assert "preshared-key" not in sh_cmd
    assert "allowed-ips '10.8.1.5/32'" in sh_cmd


def test_import_backup_upstream_kyoresuas_v1_format(mock_awg_env):
    """Verify POST /server/backup correctly restores upstream kyoresuas wire format (wgConfig, clients, presharedKey)."""
    client = TestClient(amnezia_app.app)
    headers = {"x-api-key": "secret-test-api-key"}

    upstream_payload = {
        "generatedAt": "2026-09-26T12:00:00Z",
        "serverId": "upstream-node",
        "protocols": ["amneziawg2"],
        "amneziaWg2": {
            "wgConfig": SAMPLE_AWG0_CONF,
            "clients": [
                {
                    "clientId": "bRqF9LY7lnONibMDWH3u0QbeC7QbrLYPufdO4QMm53o=",
                    "clientName": "upstream_peer",
                    "status": "active",
                }
            ],
            "presharedKey": "PGh2rNsBmWVJC7qpa3fZ1dwB6tLjBUVKsxSZK6pMQRY=",
        },
    }

    resp = client.post("/server/backup", json=upstream_payload, headers=headers)
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"
    assert resp.json()["kernel_synced"] is True

    # Verify GET /server/backup provides upstream compatible fields
    get_resp = client.get("/server/backup", headers=headers)
    assert get_resp.status_code == 200
    b_data = get_resp.json()
    assert "amneziaWg2" in b_data
    assert b_data["amneziaWg2"]["wgConfig"] == SAMPLE_AWG0_CONF
    assert len(b_data["amneziaWg2"]["clients"]) == 1


def test_import_backup_rolls_back_original_files_on_syncconf_failure(mock_awg_env, monkeypatch):
    """Verify POST /server/backup rolls back old configuration if syncconf fails."""
    # Ensure original config has a known marker
    conf_file = mock_awg_env["conf_file"]
    original_text = conf_file.read_text(encoding="utf-8")
    assert "10.8.1.1/24" in original_text

    monkeypatch.setattr(amnezia_app, "syncconf_container", AsyncMock(return_value=False))
    client = TestClient(amnezia_app.app)
    headers = {"x-api-key": "secret-test-api-key"}

    new_conf = SAMPLE_AWG0_CONF.replace("10.8.1.1/24", "10.9.9.1/24")
    import_payload = {
        "conf_content": new_conf,
        "clients_table": [],
    }

    resp = client.post("/server/backup", json=import_payload, headers=headers)
    assert resp.status_code == 500
    assert "rolled back" in resp.json()["detail"].lower()

    # Conf file on disk must be restored to original_text
    current_text = conf_file.read_text(encoding="utf-8")
    assert "10.8.1.1/24" in current_text
    assert "10.9.9.1/24" not in current_text


def test_get_server_backup_is_read_only_does_not_create_psk(tmp_path, monkeypatch):
    """Verify GET /server/backup does not generate or persist PSK if none existed."""
    awg_dir = tmp_path / "awg_no_psk"
    awg_dir.mkdir()
    conf_file = awg_dir / "awg0.conf"
    conf_file.write_text(SAMPLE_AWG0_CONF, encoding="utf-8")
    clients_file = awg_dir / "clientsTable"
    clients_file.write_text("[]", encoding="utf-8")

    psk_file = awg_dir / "wireguard_psk.key"
    assert not psk_file.exists()

    monkeypatch.setattr(amnezia_app, "API_KEY", "secret-test-api-key")
    monkeypatch.setattr(amnezia_app, "AWG_DIR", str(awg_dir))
    monkeypatch.setattr(amnezia_app, "SERVER_PSK_PATH", str(psk_file))

    client = TestClient(amnezia_app.app)
    headers = {"x-api-key": "secret-test-api-key"}

    resp = client.get("/server/backup", headers=headers)
    assert resp.status_code == 200
    assert resp.json()["server_psk"] == ""
    # PSK file must NOT be created on disk
    assert not psk_file.exists()


def test_create_and_patch_client_unsupported_protocol_rejects_422(mock_awg_env):
    """Verify unsupported protocols return HTTP 422 Unprocessable Entity."""
    client = TestClient(amnezia_app.app)
    headers = {"x-api-key": "secret-test-api-key"}

    # 1. create_client with unsupported protocol
    resp1 = client.post(
        "/clients",
        json={"clientName": "bad_proto", "protocol": "xray"},
        headers=headers,
    )
    assert resp1.status_code == 422
    assert "unsupported protocol" in resp1.json()["detail"].lower()

    # 2. patch_client with unsupported protocol
    peer_pub = "bRqF9LY7lnONibMDWH3u0QbeC7QbrLYPufdO4QMm53o="
    resp2 = client.patch(
        f"/clients/{peer_pub}",
        json={"clientId": peer_pub, "protocol": "openvpn"},
        headers=headers,
    )
    assert resp2.status_code == 422
    assert "unsupported protocol" in resp2.json()["detail"].lower()

    # 3. amneziawg3.1 is explicitly supported and not rejected with 422
    resp3 = client.post(
        "/clients",
        json={"clientName": "awg31_user", "protocol": "amneziawg3.1"},
        headers=headers,
    )
    assert resp3.status_code != 422


def test_delete_client_fails_closed_and_rolls_back_on_kernel_sync_failure(mock_awg_env, monkeypatch):
    """Verify DELETE /clients rolls back awg0.conf and clientsTable if kernel sync fails."""
    monkeypatch.setattr(amnezia_app, "sync_kernel_peer_remove", AsyncMock(return_value=False))
    monkeypatch.setattr(amnezia_app, "syncconf_container", AsyncMock(return_value=False))

    client = TestClient(amnezia_app.app)
    headers = {"x-api-key": "secret-test-api-key"}
    peer_pub = "bRqF9LY7lnONibMDWH3u0QbeC7QbrLYPufdO4QMm53o="

    conf_file = mock_awg_env["conf_file"]
    original_conf = conf_file.read_text(encoding="utf-8")

    resp = client.delete(f"/clients/{peer_pub}", headers=headers)
    assert resp.status_code == 500

    # Peer must still exist on disk due to rollback
    current_conf = conf_file.read_text(encoding="utf-8")
    assert peer_pub in current_conf
    assert current_conf == original_conf


def test_healthcheck_status_codes(mock_awg_env, monkeypatch):
    """Verify /healthz returns 200 OK when healthy and 503 Service Unavailable when degraded."""
    client = TestClient(amnezia_app.app)

    # 1. Healthy -> 200 OK
    monkeypatch.setattr(amnezia_app, "is_container_running", AsyncMock(return_value=True))
    monkeypatch.setattr(amnezia_app, "run_docker_exec_async", AsyncMock(return_value=(0, "interface info", "")))
    resp = client.get("/healthz")
    assert resp.status_code == 200
    assert resp.json()["ok"] is True
    assert resp.json()["status"] == "ok"

    # 2. Degraded (container down) -> 503 Service Unavailable
    monkeypatch.setattr(amnezia_app, "is_container_running", AsyncMock(return_value=False))
    resp_down = client.get("/healthz")
    assert resp_down.status_code == 503
    assert resp_down.json()["ok"] is False
    assert resp_down.json()["status"] == "degraded"

    # 3. Degraded (interface not ready) -> 503 Service Unavailable
    monkeypatch.setattr(amnezia_app, "is_container_running", AsyncMock(return_value=True))
    monkeypatch.setattr(amnezia_app, "run_docker_exec_async", AsyncMock(return_value=(1, "", "interface not ready")))
    resp_iface = client.get("/healthz")
    assert resp_iface.status_code == 503
    assert resp_iface.json()["ok"] is False
    assert resp_iface.json()["status"] == "degraded"


@pytest.mark.asyncio
async def test_get_server_psk_failure_raises_500(monkeypatch):
    """Verify get_server_psk_async raises 500 when saving generated PSK fails."""
    monkeypatch.setattr(amnezia_app, "read_container_file_async", AsyncMock(return_value=""))
    monkeypatch.setattr(amnezia_app, "write_container_file_async", AsyncMock(return_value=False))

    with pytest.raises(HTTPException) as exc_info:
        await amnezia_app.get_server_psk_async(create_if_missing=True)
    assert exc_info.value.status_code == 500
    assert "Failed to persist server preshared key" in exc_info.value.detail


def test_delete_client_rolls_back_conf_when_table_save_fails(mock_awg_env, monkeypatch):
    """Verify DELETE /clients restores conf_file if save_clients_table_async fails."""
    monkeypatch.setattr(amnezia_app, "save_clients_table_async", AsyncMock(return_value=False))

    client = TestClient(amnezia_app.app)
    headers = {"x-api-key": "secret-test-api-key"}
    peer_pub = "bRqF9LY7lnONibMDWH3u0QbeC7QbrLYPufdO4QMm53o="

    conf_file = mock_awg_env["conf_file"]
    original_conf = conf_file.read_text(encoding="utf-8")

    resp = client.delete(f"/clients/{peer_pub}", headers=headers)
    assert resp.status_code == 500

    current_conf = conf_file.read_text(encoding="utf-8")
    assert peer_pub in current_conf
    assert current_conf == original_conf


def test_patch_client_reallocate_ip_on_collision(mock_awg_env):
    """Verify re-enabling a peer whose IP was re-assigned to another peer allocates a new IP."""
    client = TestClient(amnezia_app.app)
    headers = {"x-api-key": "secret-test-api-key"}
    peer_pub = "bRqF9LY7lnONibMDWH3u0QbeC7QbrLYPufdO4QMm53o="

    # 1. Disable the peer (removes from conf, leaves in clientsTable with 10.8.1.2)
    resp = client.patch(
        f"/clients/{peer_pub}",
        json={"clientId": peer_pub, "status": "disabled"},
        headers=headers,
    )
    assert resp.status_code == 200

    # 2. Add another peer to conf manually that takes 10.8.1.2
    conf_file = mock_awg_env["conf_file"]
    conf_text = conf_file.read_text(encoding="utf-8")
    conf_text += "\n[Peer]\nPublicKey = occupied_pub_key==\nAllowedIPs = 10.8.1.2/32\n"
    conf_file.write_text(conf_text, encoding="utf-8")

    # 3. Re-enable the peer -> must detect that 10.8.1.2 is occupied and allocate next IP (10.8.1.3)
    resp_enable = client.patch(
        f"/clients/{peer_pub}",
        json={"clientId": peer_pub, "status": "active"},
        headers=headers,
    )
    assert resp_enable.status_code == 200
    data = resp_enable.json()
    new_ip = data["client"]["clientIp"]
    assert new_ip != "10.8.1.2"
    assert new_ip.startswith("10.8.1.")


def test_amnezia_tool_resolution_and_key_validation():
    """Verify awg0, awg binary and awg0.conf are resolved for all AmneziaWG containers."""
    # 1. Key validation
    valid_key = "v1c2X3y4Z5a6B7c8D9e0F1g2H3i4J5k6L7m8N9o0P1Q="
    assert amnezia_app.is_valid_awg_key(valid_key) is True
    assert amnezia_app.is_valid_wg_key(valid_key) is True
    assert amnezia_app._is_valid_wg_key(valid_key) is True

    assert amnezia_app.is_valid_awg_key("short_key==") is False
    assert amnezia_app.is_valid_awg_key("not-valid-base64???") is False
    assert amnezia_app.is_valid_awg_key("") is False
    assert amnezia_app.is_valid_awg_key(None) is False

    # 2. Tool and interface resolution
    # Standard AmneziaVPN self-hosted container (AWG 2.0 and AWG 3.x)
    assert amnezia_app.get_interface_name("amnezia-awg2") == "awg0"
    assert amnezia_app.get_tool_binary("amnezia-awg2") == "awg"
    assert amnezia_app.get_config_path("amnezia-awg2").endswith("awg0.conf")

    # Legacy or custom AWG containers
    assert amnezia_app.get_interface_name("amnezia-awg") == "awg0"
    assert amnezia_app.get_tool_binary("amnezia-awg") == "awg"
    assert amnezia_app.get_config_path("amnezia-awg").endswith("awg0.conf")

    assert amnezia_app.get_interface_name("custom-awg-node") == "awg0"
    assert amnezia_app.get_tool_binary("custom-awg-node") == "awg"
    assert amnezia_app.get_config_path("custom-awg-node").endswith("awg0.conf")

    # Non-AWG fallback
    assert amnezia_app.get_interface_name("plain-wireguard") == "wg0"
    assert amnezia_app.get_tool_binary("plain-wireguard") == "wg"
    assert amnezia_app.get_config_path("plain-wireguard").endswith("wg0.conf")





