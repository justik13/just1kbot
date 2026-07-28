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
