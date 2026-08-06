import base64
import json
import struct
import zlib

from utils.vpn_parser import (
    _decompress_amnezia_format,
    build_conf_file_from_dict,
    build_vpn_file_from_dict,
    customize_vpn_data,
    decode_vpn_uri_to_json,
)


def _payload(content: bytes, *, declared_length: int | None = None) -> bytes:
    length = len(content) if declared_length is None else declared_length
    return struct.pack(">I", length) + zlib.compress(content)


def test_decompresses_valid_amnezia_payload():
    content = json.dumps({"containers": []}).encode()

    assert _decompress_amnezia_format(_payload(content)) == content.decode()


def test_rejects_payload_with_mismatched_declared_length():
    content = b'{}'

    assert _decompress_amnezia_format(_payload(content, declared_length=100)) is None


def test_rejects_truncated_header():
    assert _decompress_amnezia_format(b"123") is None


def test_full_vpn_uri_roundtrip():
    config = {
        "containers": [
            {
                "container": "amnezia-awg",
                "awg": {
                    "client_priv_key": "private-key",
                    "hostName": "vpn.example.com",
                    "port": 51820,
                },
            }
        ]
    }
    content = json.dumps(config).encode()
    payload = struct.pack(">I", len(content)) + zlib.compress(content)
    encoded_payload = base64.urlsafe_b64encode(payload).decode().rstrip("=")
    uri = f"vpn://{encoded_payload}"

    assert decode_vpn_uri_to_json(uri) == config


def test_customize_vpn_data_enforces_dns_mtu_and_description():
    data = {
        "containers": [
            {
                "container": "amnesia-awg2",
                "awg": {
                    "last_config": json.dumps({
                        "config": "[Interface]\nAddress = 10.8.1.2/32\nDNS = 1.1.1.1\nMTU = 1420\n[Peer]\nPublicKey = pubkey",
                        "client_priv_key": "privkey",
                        "server_pub_key": "pubkey",
                        "hostName": "example.com",
                        "port": 1234,
                        "client_ip": "10.8.1.2/32",
                        "Jc": "4", "Jmin": "10", "Jmax": "50",
                        "S1": "79", "S2": "115", "S3": "5", "S4": "1",
                        "H1": "1-2", "H2": "3-4", "H3": "5-6", "H4": "7-8"
                    })
                }
            }
        ]
    }

    customized = customize_vpn_data(data, server_name="Estonia")
    assert customized["dns1"] == "8.8.8.8"
    assert customized["dns2"] == "8.8.4.4"
    assert customized["description"] == "Estonia"

    conf = build_conf_file_from_dict(data, server_name="Estonia")
    assert "DNS = 8.8.8.8, 8.8.4.4" in conf
    assert "MTU = 1280" in conf
