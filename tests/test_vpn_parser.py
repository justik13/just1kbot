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

    def test_build_conf_file_rejects_raw_wireguard_conf(self):
        from utils.vpn_parser import build_conf_file
        raw_key = "[Interface]\nPrivateKey = abc\n[Peer]\nPublicKey = def"
        res = build_conf_file(raw_key)
        self.assertIsNone(res)

    def test_detect_awg_version_all_generations(self):
        from utils.vpn_parser import detect_awg_version

        # AWG 3.1
        self.assertEqual(detect_awg_version({"RandomTrailers": "1"}), "3.1")
        self.assertEqual(detect_awg_version({"DisableCookies": "1"}), "3.1")

        # AWG 3.0
        self.assertEqual(detect_awg_version({"HeaderProtectionKey": "secret="}), "3.0")
        self.assertEqual(detect_awg_version({"ContentPaddingAddition": "10-20"}), "3.0")
        self.assertEqual(detect_awg_version({"RekeyAfterTime": "120"}), "3.0")

        # AWG 2.0 (S3/S4 present)
        self.assertEqual(detect_awg_version({"S3": "49", "S4": "1"}), "2.0")
        # AWG 2.0 (Ranged H1..H4)
        self.assertEqual(detect_awg_version({"H1": "100-200", "Jc": "4"}), "2.0")
        # AWG 2.0 (protocol_version == "2")
        self.assertEqual(detect_awg_version({"protocol_version": "2"}), "2.0")

        # Toggle semantics: 0 or false means disabled
        self.assertEqual(detect_awg_version({"RandomTrailers": "0"}), "2.0")
        self.assertEqual(detect_awg_version({"DisableCookies": "0"}), "2.0")
        self.assertEqual(detect_awg_version({"RandomTrailers": "false"}), "2.0")
        self.assertEqual(detect_awg_version({"RandomTrailers": ""}), "2.0")

        # Explicit protocol_version
        self.assertEqual(detect_awg_version({"protocol_version": "3.1"}), "3.1")
        self.assertEqual(detect_awg_version({"protocol_version": "3.0"}), "3.0")
        self.assertEqual(detect_awg_version({"protocol_version": "3"}), "3.0")

        # Toggle semantics: 0 or false means disabled
        self.assertEqual(detect_awg_version({"RandomTrailers": "0"}), "2.0")
        self.assertEqual(detect_awg_version({"DisableCookies": "0"}), "2.0")
        self.assertEqual(detect_awg_version({"RandomTrailers": "false"}), "2.0")
        self.assertEqual(detect_awg_version({"RandomTrailers": ""}), "2.0")

    def test_is_valid_vpn_uri_awg2_and_awg3(self):
        from utils.vpn_parser import encode_json_to_vpn_uri, is_valid_vpn_uri

        base_last_cfg = {
            "client_priv_key": "c_priv_key=",
            "client_pub_key": "c_pub_key=",
            "server_pub_key": "s_pub_key=",
            "client_ip": "10.8.1.25",
            "hostName": "vpn.node.com",
            "port": 51820,
            "mtu": "1280",
            "persistent_keep_alive": "25",
            "Jc": "4",
            "Jmin": "10",
            "Jmax": "50",
            "S1": "87",
            "S2": "61",
            "S3": "49",
            "S4": "1",
            "H1": "100-200",
            "H2": "300-400",
            "H3": "500-600",
            "H4": "700-800",
        }

        # 1. Valid AWG 2.0 URI
        awg2_data = {
            "defaultContainer": "amnezia-awg2",
            "containers": [{
                "container": "amnezia-awg2",
                "awg": {
                    "protocol_version": "2",
                    "port": "51820",
                    "Jc": "4",
                    "Jmin": "10",
                    "Jmax": "50",
                    "S1": "87",
                    "S2": "61",
                    "S3": "49",
                    "S4": "1",
                    "H1": "100-200",
                    "H2": "300-400",
                    "H3": "500-600",
                    "H4": "700-800",
                    "last_config": json.dumps(base_last_cfg),
                },
            }]
        }
        self.assertTrue(is_valid_vpn_uri(encode_json_to_vpn_uri(awg2_data)))

        # 2. Valid AWG 3.0 URI (with HeaderProtectionKey)
        awg3_last_cfg = dict(base_last_cfg, HeaderProtectionKey="secret_key=")
        awg3_data = {
            "defaultContainer": "amnezia-awg2",
            "containers": [{
                "container": "amnezia-awg2",
                "awg": {
                    "protocol_version": "3.0",
                    "port": "51820",
                    "Jc": "4",
                    "Jmin": "10",
                    "Jmax": "50",
                    "S1": "87",
                    "S2": "61",
                    "S3": "49",
                    "S4": "1",
                    "H1": "100-200",
                    "H2": "300-400",
                    "H3": "500-600",
                    "H4": "700-800",
                    "HeaderProtectionKey": "secret_key=",
                    "last_config": json.dumps(awg3_last_cfg),
                },
            }]
        }
        self.assertTrue(is_valid_vpn_uri(encode_json_to_vpn_uri(awg3_data)))

        # 3. Valid AWG 3.1 URI (with RandomTrailers)
        awg31_last_cfg = dict(awg3_last_cfg, RandomTrailers="1")
        awg31_data = {
            "defaultContainer": "amnezia-awg2",
            "containers": [{
                "container": "amnezia-awg2",
                "awg": {
                    "protocol_version": "3.1",
                    "port": "51820",
                    "Jc": "4",
                    "Jmin": "10",
                    "Jmax": "50",
                    "S1": "87",
                    "S2": "61",
                    "S3": "49",
                    "S4": "1",
                    "H1": "100-200",
                    "H2": "300-400",
                    "H3": "500-600",
                    "H4": "700-800",
                    "HeaderProtectionKey": "secret_key=",
                    "RandomTrailers": "1",
                    "last_config": json.dumps(awg31_last_cfg),
                },
            }]
        }
        self.assertTrue(is_valid_vpn_uri(encode_json_to_vpn_uri(awg31_data)))

        # 4. Reject structural stubs: only [Interface]\n[Peer] in config
        stub_data = {
            "containers": [{
                "container": "amnezia-awg2",
                "awg": {
                    "protocol_version": "2",
                    "last_config": json.dumps({"config": "[Interface]\n[Peer]\n"}),
                },
            }]
        }
        self.assertFalse(is_valid_vpn_uri(encode_json_to_vpn_uri(stub_data)))

        # 5. Reject empty last_config
        empty_data = {
            "containers": [{
                "container": "amnezia-awg2",
                "awg": {"protocol_version": "2", "last_config": "{}"},
            }]
        }
        self.assertFalse(is_valid_vpn_uri(encode_json_to_vpn_uri(empty_data)))

        # 6. Reject unsupported container (e.g. unknown-container) and accept amnezia-awg3
        bad_container_data = {
            "containers": [{
                "container": "unknown-container",
                "awg": {
                    "protocol_version": "3.1",
                    "last_config": json.dumps(awg31_last_cfg),
                },
            }]
        }
        self.assertFalse(is_valid_vpn_uri(encode_json_to_vpn_uri(bad_container_data)))

        # 6b. Accept valid amnezia-awg3 container
        awg3_container_data = {
            "containers": [{
                "container": "amnezia-awg3",
                "awg": dict(awg31_data["containers"][0]["awg"]),
            }]
        }
        self.assertTrue(is_valid_vpn_uri(encode_json_to_vpn_uri(awg3_container_data)))

        # 7. Reject missing S3/S4 for AWG 2.0
        missing_s3_cfg = dict(base_last_cfg)
        del missing_s3_cfg["S3"]
        missing_s3_data = {
            "containers": [{
                "container": "amnezia-awg2",
                "awg": {"protocol_version": "2", "last_config": json.dumps(missing_s3_cfg)},
            }]
        }
        self.assertFalse(is_valid_vpn_uri(encode_json_to_vpn_uri(missing_s3_data)))

        # 8. Reject mismatched parameters between awg dict and last_config
        mismatched_data = {
            "containers": [{
                "container": "amnezia-awg2",
                "awg": {
                    "protocol_version": "2",
                    "port": "51820",
                    "Jc": "99",  # Mismatch!
                    "Jmin": "10",
                    "Jmax": "50",
                    "S1": "87",
                    "S2": "61",
                    "S3": "49",
                    "S4": "1",
                    "H1": "100-200",
                    "H2": "300-400",
                    "H3": "500-600",
                    "H4": "700-800",
                    "last_config": json.dumps(base_last_cfg),
                },
            }]
        }
        self.assertFalse(is_valid_vpn_uri(encode_json_to_vpn_uri(mismatched_data)))

        # 9. Reject when awg dict is missing mandatory base keys (even if last_config has them)
        missing_awg_keys_data = {
            "containers": [{
                "container": "amnezia-awg2",
                "awg": {
                    "protocol_version": "2",
                    "port": "51820",
                    # Missing Jc..H4 in top-level awg dict
                    "last_config": json.dumps(base_last_cfg),
                },
            }]
        }
        self.assertFalse(is_valid_vpn_uri(encode_json_to_vpn_uri(missing_awg_keys_data)))

        # 10. Reject legacy AWG 1.0 protocol_version
        legacy_awg1_data = {
            "containers": [{
                "container": "amnezia-awg",
                "awg": {
                    "protocol_version": "1.0",
                    "port": "51820",
                    "Jc": "4",
                    "Jmin": "10",
                    "Jmax": "50",
                    "S1": "87",
                    "S2": "61",
                    "S3": "49",
                    "S4": "1",
                    "H1": "100-200",
                    "H2": "300-400",
                    "H3": "500-600",
                    "H4": "700-800",
                    "last_config": json.dumps(base_last_cfg),
                },
            }]
        }
        self.assertFalse(is_valid_vpn_uri(encode_json_to_vpn_uri(legacy_awg1_data)))

        # 11. Reject AWG 3.x if HeaderProtectionKey is missing in awg dict
        awg3_missing_hpk_awg = {
            "containers": [{
                "container": "amnezia-awg2",
                "awg": {
                    "protocol_version": "3.0",
                    "port": "51820",
                    "Jc": "4",
                    "Jmin": "10",
                    "Jmax": "50",
                    "S1": "87",
                    "S2": "61",
                    "S3": "49",
                    "S4": "1",
                    "H1": "100-200",
                    "H2": "300-400",
                    "H3": "500-600",
                    "H4": "700-800",
                    # HeaderProtectionKey missing in awg dict
                    "last_config": json.dumps(awg3_last_cfg),
                },
            }]
        }
        self.assertFalse(is_valid_vpn_uri(encode_json_to_vpn_uri(awg3_missing_hpk_awg)))

    def test_k1_reference_decoding_and_customization(self):
        """Verify real-world working AmneziaWG 2.0 reference key decodes and customizes correctly."""
        from utils.vpn_parser import (
            build_conf_file,
            customize_vpn_uri,
            decode_vpn_uri_to_json,
        )

        k1 = (
            "vpn://AAAI6HictVbdbts2FH4VQ7tM4pAiJUtBU8BInNhJ7blz0jaOCkOR6ESNLKsSFTsOAvS-z7B32EUHDBj2"
            "Du4b7RySdpvNu8iAWjD4nfOdH4qHh9SDFU0zGSaZKEprr3b5YIWzawAP1kkEg8Wt7Zp1MkkyFCjRUjhHyVHSgCL"
            "2GgrbiF2qMFPuvsJceSNsK3PqMeZymzlsxyaUUo_btorQVhFs0vBd4vm0sWNTQjhzbNtRtApqU06J4zseAZozgD"
            "ZRedrc0ODsOI6iG5xzr6Hojsr9oqjZL19c1cjcgwjwo-avf67ru8yN3HHDcTlhgMduTNZ2ESHR9z4wkRAGzmPHY"
            "w3WeKkSqbdQiK0RXyNnhfJiKqfRNB3dweonU7XGNhJpWMoRFGacYC2shwCqEQAKLB4AH6iKaAUlK0041xrHaAZU"
            "y17DyLaWXWpkZkL6RuYmohbbxn1jrYyJibixXsbEJNlYM2PC1yb_rps26Zi5_ODamWTmrYzEnkj8ieR8L4VpOp2J"
            "eJTkJaovA4vU1bNrCrK3h-g9wihNRCY7sfYfHudifNh5O8t_Kd2tORdvBqzyF9nrakKbi61jFg5a-cI9e5U293U"
            "o7Q-pVpug7tVpfbXohs2L5G50K-61DTkivl0l92-O79_1Jwf5kX9-ezBrpR_brYW_-Lnfe51eDN9NovZt9DRJXl"
            "19i_I_pqq2sfa-7GRSFOMwEu-DIDvsDWr7Na-unm0FeJ0D0T07B4LaHgGhGceFKEtUrN5yl9lA9OH1QilOxT1wz"
            "3q7IDuJwAdTYSOp0AqHc8AO4gHFqTUQ2YCgZwAxdPIRcfQB0EazzR0CJHpu7g0gMdjmrgCSa3JDPwRZB3P-6E6A"
            "NDh7HJkZuRkdPV72hSiwjP3qKk0iXYZha-idXTeHsdy9ceezathcdJ0uP-mOC9F25am8kmNytiDePd9XJRTlTVi"
            "IWHvfvT350D78OEwPeq-2LmYt_6h7VhTp4OLm1DufnVfluWRxT3RLdo3eTd1wnT5ujnWvbddUnwVZK4vzaZJJIL"
            "O0_qEqJb2tX4lS7jHq-7iSfTx2Swmb_FSIPEyTO4HrjuXRe_dmWspeOBF69z6Noi0msjItiLtVqfJ1VGgbkY9UX"
            "HPEmQbNp4VEjZqIUpS333rsWcug4pWigCvkH536nFJYj-o-gmnhfaOmhQpZhFmJ2pG6qpCr4tx6BGr92YDKcJKJ"
            "RRLuwLeDbT3CCWfFYhxWqTz4TytlU0ZFkktz8y1_Xf6x_LL8_eun5Z_L35Z_Lb98_Vz7Sd37cVbqzwx9VhiVvVL"
            "BqYGqVbVQ_bRW1uPfGcBsxw"
        )
        decoded = decode_vpn_uri_to_json(k1)
        self.assertIsNotNone(decoded)
        self.assertEqual(decoded["defaultContainer"], "amnezia-awg2")
        awg = decoded["containers"][0]["awg"]
        self.assertEqual(str(awg["protocol_version"]), "2")
        last_cfg = json.loads(awg["last_config"])
        self.assertEqual(last_cfg["client_ip"], "10.8.1.25")
        self.assertIn("H1", last_cfg)
        self.assertIn("I1", last_cfg)

        conf = build_conf_file(k1)
        self.assertIn("[Interface]", conf)
        self.assertIn("Address = 10.8.1.25/32", conf)
        self.assertIn("Jc = 4", conf)

        customized = customize_vpn_uri(k1, description="Netherlands #1", dns1="1.1.1.1", dns2="1.0.0.1")
        custom_decoded = decode_vpn_uri_to_json(customized)
        self.assertEqual(custom_decoded["description"], "Netherlands #1")
        self.assertEqual(custom_decoded["dns1"], "1.1.1.1")

    def test_build_display_vpn_uri_awg2_and_awg3(self):
        from unittest.mock import MagicMock
        from utils.vpn_helpers import build_display_vpn_uri, InvalidAmneziaConfigError
        from utils.vpn_parser import encode_json_to_vpn_uri

        profile = MagicMock()
        profile.server = MagicMock()
        profile.server.protocol = "amneziawg"
        profile.server.name = "Frankfurt"
        profile.device_name = "Frankfurt #3"

        # AWG2 profile
        awg2_key = encode_json_to_vpn_uri({
            "containers": [{
                "container": "amnezia-awg2",
                "awg": {
                    "protocol_version": "2",
                    "last_config": json.dumps({"config": "[Interface]\nDNS = 1.1.1.1\nMTU = 1376\n[Peer]"}),
                },
            }],
            "description": "old",
        })
        profile.raw_config = awg2_key
        display_key = build_display_vpn_uri(profile)
        self.assertTrue(display_key.startswith("vpn://"))

        # AWG3 profile (container is amnezia-awg2 with protocol_version 3.1)
        awg3_key = encode_json_to_vpn_uri({
            "containers": [{
                "container": "amnezia-awg2",
                "awg": {
                    "protocol_version": "3.1",
                    "last_config": json.dumps({"config": "[Interface]\nDNS = 1.1.1.1\nMTU = 1376\n[Peer]"}),
                },
            }],
            "description": "old",
        })
        profile.raw_config = awg3_key
        display_key_3 = build_display_vpn_uri(profile)
        self.assertTrue(display_key_3.startswith("vpn://"))

        # Unsupported container name (e.g. invalid "unknown-container")
        bad_container_key = encode_json_to_vpn_uri({
            "containers": [{
                "container": "unknown-container",
                "awg": {"protocol_version": "3.1"},
            }],
        })
        profile.raw_config = bad_container_key
        with self.assertRaises(InvalidAmneziaConfigError):
            build_display_vpn_uri(profile)

        # Unsupported protocol version (e.g. unknown "99")
        bad_key = encode_json_to_vpn_uri({
            "containers": [{
                "container": "amnezia-awg2",
                "awg": {"protocol_version": "99"},
            }],
        })
        profile.raw_config = bad_key
        with self.assertRaises(InvalidAmneziaConfigError):
            build_display_vpn_uri(profile)


if __name__ == "__main__":
    unittest.main()
