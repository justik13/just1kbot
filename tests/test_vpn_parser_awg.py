import json
import os
import unittest
from unittest.mock import patch

from utils.vpn_parser import (
    _build_conf_fallback,
    build_conf_file,
    encode_json_to_vpn_uri,
)


class AWGParserTests(unittest.TestCase):
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
                "DATABASE_URL": "postgresql+asyncpg://user:pass@localhost:5432/db",
            },
        )
        cls.env_patcher.start()

    @classmethod
    def tearDownClass(cls):
        cls.env_patcher.stop()

    def test_fallback_generates_correct_I1_to_I5_parameters(self):
        data = {
            "dns1": "1.1.1.1",
            "dns2": "1.0.0.1",
        }
        last_config = {
            "client_priv_key": "test_priv_key",
            "server_pub_key": "test_pub_key",
            "hostName": "1.2.3.4",
            "port": 51820,
            "client_ip": "10.8.0.2/32",
            "Jc": 4,
            "Jmin": 10,
            "Jmax": 50,
            "S1": 15,
            "S2": 20,
            "S3": 25,
            "S4": 30,
            "H1": 100,
            "H2": 200,
            "H3": 300,
            "H4": 400,
            "I1": "10-20",
            "I2": "30-40",
            "I3": "50-60",
            "I4": "70-80",
            "I5": "90-100",
        }
        conf = _build_conf_fallback(data, last_config)

        self.assertIn("Jc = 4", conf)
        self.assertIn("Jmin = 10", conf)
        self.assertIn("Jmax = 50", conf)
        self.assertIn("S1 = 15", conf)
        self.assertIn("S2 = 20", conf)
        self.assertIn("S3 = 25", conf)
        self.assertIn("S4 = 30", conf)
        self.assertIn("H1 = 100", conf)
        self.assertIn("H2 = 200", conf)
        self.assertIn("H3 = 300", conf)
        self.assertIn("H4 = 400", conf)
        self.assertIn("I1 = 10-20", conf)
        self.assertIn("I2 = 30-40", conf)
        self.assertIn("I3 = 50-60", conf)
        self.assertIn("I4 = 70-80", conf)
        self.assertIn("I5 = 90-100", conf)

        # Must not contain legacy/erroneous h1..h5
        self.assertNotIn("h1 =", conf)
        self.assertNotIn("h2 =", conf)
        self.assertNotIn("h3 =", conf)
        self.assertNotIn("h4 =", conf)
        self.assertNotIn("h5 =", conf)

    def test_build_conf_file_from_vpn_uri(self):
        config_obj = {
            "dns1": "1.1.1.1",
            "dns2": "1.0.0.1",
            "containers": [
                {
                    "container": "amnezia-awg2",
                    "awg": {
                        "protocol_version": "2",
                        "last_config": json.dumps(
                            {
                                "client_priv_key": "c_priv_key",
                                "server_pub_key": "s_pub_key",
                                "hostName": "vpn.node.com",
                                "port": 54321,
                                "client_ip": "10.0.0.5/32",
                                "Jc": 3,
                                "Jmin": 10,
                                "Jmax": 50,
                                "S1": 10,
                                "S2": 20,
                                "S3": 30,
                                "S4": 40,
                                "H1": 1,
                                "H2": 2,
                                "H3": 3,
                                "H4": 4,
                                "I1": "11",
                                "I2": "22",
                                "I3": "33",
                                "I4": "44",
                                "I5": "55",
                            }
                        ),
                    },
                }
            ]
        }
        uri = encode_json_to_vpn_uri(config_obj)
        conf = build_conf_file(uri)

        self.assertIsNotNone(conf)
        self.assertIn("[Interface]", conf)
        self.assertIn("PrivateKey = c_priv_key", conf)
        self.assertIn("I1 = 11", conf)
        self.assertIn("I5 = 55", conf)
        self.assertIn("[Peer]", conf)
        self.assertIn("PublicKey = s_pub_key", conf)
        self.assertIn("Endpoint = vpn.node.com:54321", conf)


if __name__ == "__main__":
    unittest.main()
