import base64
import json
import struct
import zlib

from utils.vpn_parser import _decompress_amnezia_format, decode_vpn_uri_to_json


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


def test_customize_vpn_config_dict_updates_description_dns_and_mtu():
    from utils.vpn_parser import customize_vpn_config_dict, build_conf_file_from_dict

    config = {
        "containers": [
            {
                "container": "amnesia-awg2",
                "awg": {
                    "protocol_version": "2",
                    "last_config": json.dumps({
                        "client_priv_key": "privkey=",
                        "server_pub_key": "pubkey=",
                        "hostName": "server.com",
                        "port": 1234,
                        "client_ip": "10.8.1.2/32",
                        "Jc": "4", "Jmin": "10", "Jmax": "50",
                        "S1": "1", "S2": "2", "S3": "3", "S4": "4",
                        "H1": "1-2", "H2": "3-4", "H3": "5-6", "H4": "7-8",
                        "config": "[Interface]\nAddress = 10.8.1.2/32\nDNS = 1.1.1.1, 1.0.0.1\nMTU = 1376\nPrivateKey = privkey=\n\n[Peer]\nPublicKey = pubkey=\nEndpoint = server.com:1234\n"
                    }),
                },
            }
        ],
        "description": "OldName",
        "dns1": "1.1.1.1",
        "dns2": "1.0.0.1",
    }

    customized = customize_vpn_config_dict(
        config,
        description="Estonia #1",
        dns1="8.8.8.8",
        dns2="8.8.4.4",
        mtu="1280",
    )

    assert customized["description"] == "Estonia #1"
    assert customized["dns1"] == "8.8.8.8"
    assert customized["dns2"] == "8.8.4.4"

    conf = build_conf_file_from_dict(customized)
    assert "DNS = 8.8.8.8, 8.8.4.4" in conf
    assert "MTU = 1280" in conf


def test_customize_vpn_uri_roundtrip():
    from utils.vpn_parser import customize_vpn_uri, decode_vpn_uri_to_json, encode_json_to_vpn_uri

    config = {
        "containers": [
            {
                "container": "amnesia-awg2",
                "awg": {
                    "protocol_version": "2",
                    "last_config": "{}",
                },
            }
        ],
        "description": "OldName",
    }
    uri = encode_json_to_vpn_uri(config)
    customized_uri = customize_vpn_uri(
        uri,
        description="Germany #2",
        dns1="8.8.8.8",
        dns2="8.8.4.4",
        mtu="1280",
    )
    result = decode_vpn_uri_to_json(customized_uri)
    assert result["description"] == "Germany #2"
    assert result["dns1"] == "8.8.8.8"
    assert result["dns2"] == "8.8.4.4"


