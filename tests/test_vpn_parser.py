import base64
import json
import struct
import unittest
import zlib

from utils.vpn_parser import (
    VPNConfigParseError,
    _decompress_amnezia_format,
    decode_vpn_uri_to_json,
)


def _payload(content: bytes, *, declared_length: int | None = None) -> bytes:
    length = len(content) if declared_length is None else declared_length
    return struct.pack(">I", length) + zlib.compress(content)


class VPNParserTests(unittest.TestCase):
    def test_decompresses_valid_amnezia_payload(self):
        content = json.dumps({"containers": []}).encode()
        self.assertEqual(
            _decompress_amnezia_format(_payload(content)), content.decode()
        )

    def test_rejects_payload_with_mismatched_declared_length(self):
        content = b"{}"
        with self.assertRaises(VPNConfigParseError):
            _decompress_amnezia_format(_payload(content, declared_length=100))

    def test_rejects_truncated_header(self):
        with self.assertRaises(VPNConfigParseError):
            _decompress_amnezia_format(b"123")

    def test_rejects_declared_length_exceeding_limit(self):
        content = b"{}"
        with self.assertRaises(VPNConfigParseError) as ctx:
            _decompress_amnezia_format(_payload(content, declared_length=2 * 1024 * 1024))
        self.assertIn("exceeds limit", str(ctx.exception))

    def test_rejects_decompression_bomb_with_small_declared_length(self):
        # 2 MB of zeroes compresses into ~2 KB, but declared length is forged to 50 bytes
        bomb_data = b"0" * (2 * 1024 * 1024)
        forged_payload = struct.pack(">I", 50) + zlib.compress(bomb_data)
        with self.assertRaises(VPNConfigParseError) as ctx:
            _decompress_amnezia_format(forged_payload)
        self.assertIn("exceeds maximum size limit", str(ctx.exception))

    def test_full_vpn_uri_roundtrip(self):
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
        self.assertEqual(decode_vpn_uri_to_json(uri), config)

    def test_customize_vpn_config_dict_updates_description_dns_and_mtu(self):
        from utils.vpn_parser import (
            build_conf_file_from_dict,
            customize_vpn_config_dict,
        )

        config = {
            "containers": [
                {
                    "container": "amnesia-awg2",
                    "awg": {
                        "protocol_version": "2",
                        "last_config": json.dumps(
                            {
                                "client_priv_key": "privkey=",
                                "server_pub_key": "pubkey=",
                                "hostName": "server.com",
                                "port": 1234,
                                "client_ip": "10.8.1.2/32",
                                "Jc": "4",
                                "Jmin": "10",
                                "Jmax": "50",
                                "S1": "1",
                                "S2": "2",
                                "S3": "3",
                                "S4": "4",
                                "H1": "1-2",
                                "H2": "3-4",
                                "H3": "5-6",
                                "H4": "7-8",
                                "config": (
                                    "[Interface]\nAddress = 10.8.1.2/32\n"
                                    "DNS = 1.1.1.1, 1.0.0.1\nMTU = 1376\n"
                                    "PrivateKey = privkey=\n\n"
                                    "[Peer]\nPublicKey = pubkey=\n"
                                    "Endpoint = server.com:1234\n"
                                ),
                            }
                        ),
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

        self.assertEqual(customized["description"], "Estonia #1")
        self.assertEqual(customized["dns1"], "8.8.8.8")
        self.assertEqual(customized["dns2"], "8.8.4.4")

        conf = build_conf_file_from_dict(customized)
        self.assertIn("DNS = 8.8.8.8, 8.8.4.4", conf)
        self.assertIn("MTU = 1280", conf)

    def test_customize_vpn_uri_roundtrip(self):
        from utils.vpn_parser import (
            customize_vpn_uri,
            decode_vpn_uri_to_json,
            encode_json_to_vpn_uri,
        )

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
        self.assertEqual(result["description"], "Germany #2")
        self.assertEqual(result["dns1"], "8.8.8.8")
        self.assertEqual(result["dns2"], "8.8.4.4")

    def test_customize_vpn_uri_safe_with_raw_text(self):
        from utils.vpn_parser import customize_vpn_uri
        raw_key = "[Interface]\nPrivateKey = abc\n[Peer]\nPublicKey = def"
        res = customize_vpn_uri(raw_key, description="Test")
        self.assertEqual(res, raw_key)

    def test_build_conf_file_returns_raw_wireguard_conf(self):
        from utils.vpn_parser import build_conf_file
        raw_key = "[Interface]\nPrivateKey = abc\n[Peer]\nPublicKey = def"
        res = build_conf_file(raw_key)
        self.assertEqual(res, raw_key)


if __name__ == "__main__":
    unittest.main()
