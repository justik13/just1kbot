import json
import os
import re
import unittest
from unittest.mock import patch

from database.models import Server, VPNProfile
from utils.vpn_helpers import build_display_vpn_uri
from utils.vpn_parser import (
    customize_vpn_uri,
    decode_vpn_uri_to_json,
    encode_json_to_vpn_uri,
)


def legacy_show_config_algorithm(raw_config: str, server_name: str, device_name: str) -> str:
    """Frozen reference implementation of the legacy show_config transformation."""
    m = re.search(r'#(\d+)$', device_name or "")
    slot_suffix = f" #{m.group(1)}" if m else ""
    client_description = f"{server_name}{slot_suffix}"
    return customize_vpn_uri(
        raw_config,
        description=client_description,
        dns1="8.8.8.8",
        dns2="8.8.4.4",
        mtu="1280",
    )


class AmneziaBridgeRegressionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.env_patcher = patch.dict(
            os.environ,
            {
                "BOT_TOKEN": "123:test",
                "REDIS_URL": "redis://localhost:6379/1",
                "REDIS_PASSWORD": "test",
                "ADMIN_IDS": "[123456789]",
                "SUPPORT_USERNAME": "test_support",
                "DOMAIN": "test.domain",
                "SSL_EMAIL": "test@domain.com",
                "YOOKASSA_SHOP_ID": "123456",
                "YOOKASSA_SECRET_KEY": "test_secret",
                "YOOKASSA_RETURN_URL": "https://t.me/{bot_username}",
                "YOOKASSA_WEBHOOK_PORT": "8080",
                "DB_ENCRYPTION_KEY": "MDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDA=",
                "AMNEZIA_BRIDGE_HMAC_SECRET": "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
                "DATABASE_URL": "postgresql+asyncpg://user:pass@localhost:5432/db",
            },
        )
        cls.env_patcher.start()
        from config.settings import get_settings
        get_settings.cache_clear()

    @classmethod
    def tearDownClass(cls):
        from config.settings import get_settings
        get_settings.cache_clear()
        cls.env_patcher.stop()

    def _generate_awg2_fixture(self) -> str:
        data = {
            "description": "Original Server Description",
            "dns1": "1.1.1.1",
            "dns2": "1.0.0.1",
            "containers": [
                {
                    "container": "amnezia-awg2",
                    "awg": {
                        "protocol_version": "2",
                        "last_config": json.dumps(
                            {
                                "client_priv_key": "c_priv_12345",
                                "server_pub_key": "s_pub_67890",
                                "hostName": "de-node1.vpn.com",
                                "port": 51820,
                                "client_ip": "10.8.0.2/32",
                                "mtu": 1280,
                                "Jc": 5,
                                "Jmin": 20,
                                "Jmax": 60,
                                "S1": 15,
                                "S2": 25,
                                "S3": 35,
                                "S4": 45,
                                "H1": 111,
                                "H2": 222,
                                "H3": 333,
                                "H4": 444,
                                "I1": "100-200",
                                "I2": "300-400",
                                "I3": "500-600",
                                "I4": "700-800",
                                "I5": "900-1000",
                            }
                        ),
                    },
                }
            ],
        }
        return encode_json_to_vpn_uri(data)

    def test_byte_for_byte_parity_across_diverse_fixtures(self):
        raw_config = self._generate_awg2_fixture()

        scenarios = [
            ("Frankfurt", "iPhone #1", "Frankfurt"),
            ("Amsterdam", "MacBook Pro #12", "Amsterdam"),
            ("  Germany  ", "PC #3", "  Germany  "),
            ("🇩🇪 Berlin Server", "Телефон #1", "🇩🇪 Berlin Server"),
            (None, "Device #5", "server"),
            ("France", "Work-Laptop", "France"),
        ]

        for s_name, dev_name, expected_server in scenarios:
            with self.subTest(server=s_name, device=dev_name):
                server = Server(id=1, name=s_name, protocol="amneziawg2", is_active=True)
                profile = VPNProfile(
                    id=10,
                    user_id=20,
                    server_id=1,
                    server=server,
                    device_name=dev_name,
                    raw_config=raw_config,
                )

                legacy_output = legacy_show_config_algorithm(
                    raw_config=raw_config,
                    server_name=expected_server,
                    device_name=dev_name,
                )

                builder_output = build_display_vpn_uri(profile)

                # Strict byte-for-byte equality of the generated vpn:// keys
                self.assertEqual(
                    legacy_output,
                    builder_output,
                    f"Byte-for-byte mismatch for scenario ({s_name}, {dev_name})",
                )

    def test_preservation_of_all_awg2_parameters(self):
        raw_config = self._generate_awg2_fixture()
        server = Server(id=1, name="Frankfurt", protocol="amneziawg2", is_active=True)
        profile = VPNProfile(
            id=12,
            user_id=34,
            server_id=1,
            server=server,
            device_name="iPhone #2",
            raw_config=raw_config,
        )

        builder_output = build_display_vpn_uri(profile)
        builder_data = decode_vpn_uri_to_json(builder_output)

        self.assertEqual(builder_data["description"], "Frankfurt #2")
        self.assertEqual(builder_data["dns1"], "8.8.8.8")
        self.assertEqual(builder_data["dns2"], "8.8.4.4")

        builder_awg = builder_data["containers"][0]["awg"]
        self.assertEqual(builder_awg["protocol_version"], "2")

        builder_last = json.loads(builder_awg["last_config"])
        for key in (
            "client_priv_key",
            "server_pub_key",
            "hostName",
            "port",
            "client_ip",
            "Jc",
            "Jmin",
            "Jmax",
            "S1",
            "S2",
            "S3",
            "S4",
            "H1",
            "H2",
            "H3",
            "H4",
            "I1",
            "I2",
            "I3",
            "I4",
            "I5",
        ):
            self.assertIn(key, builder_last)


if __name__ == "__main__":
    unittest.main()
