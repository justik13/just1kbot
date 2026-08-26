import base64
import json
import os
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

from database.models import Server, User, VPNProfile
from integrations.incy.feed_service import (
    SubscriptionFeedService,
)
from utils.vpn_parser import encode_json_to_vpn_uri


def _make_dummy_awg2_uri(device_num: int) -> str:
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
                            "client_priv_key": f"privkey_{device_num}",
                            "server_pub_key": f"pubkey_{device_num}",
                            "hostName": "node.vpn.test",
                            "port": 50000 + device_num,
                            "client_ip": f"10.0.0.{device_num}/32",
                            "Jc": 4,
                            "Jmin": 10,
                            "Jmax": 50,
                            "S1": 15,
                            "S2": 20,
                            "S3": 25,
                            "S4": 30,
                            "H1": 1,
                            "H2": 2,
                            "H3": 3,
                            "H4": 4,
                            "I1": "10",
                            "I2": "20",
                            "I3": "30",
                            "I4": "40",
                            "I5": "50",
                        }
                    ),
                },
            }
        ]
    }
    return encode_json_to_vpn_uri(config_obj)


class SubscriptionFeedServiceTests(unittest.IsolatedAsyncioTestCase):
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

    @patch("integrations.incy.feed_service.get_user_profiles")
    async def test_get_exportable_configs_filters_properly(self, mock_get_profiles):
        server_awg2 = Server(id=1, name="Poland #1", country_flag="🇵🇱", protocol="amneziawg2", is_active=True)
        server_awg3 = Server(id=2, name="Germany #1", country_flag="🇩🇪", protocol="amneziawg3", is_active=True)
        server_inactive = Server(id=3, name="Estonia #1", country_flag="🇪🇪", protocol="amneziawg2", is_active=False)

        valid_uri = _make_dummy_awg2_uri(1)

        p1_valid = VPNProfile(
            id=1, user_id=10, server_id=1, device_name="Device #1",
            is_active=True, desired_is_active=True, raw_config=valid_uri,
            provisioning_status="active", server=server_awg2
        )
        p2_awg3 = VPNProfile(
            id=2, user_id=10, server_id=2, device_name="Device #2",
            is_active=True, desired_is_active=True, raw_config=valid_uri,
            provisioning_status="active", server=server_awg3
        )
        p3_inactive_server = VPNProfile(
            id=3, user_id=10, server_id=3, device_name="Device #3",
            is_active=True, desired_is_active=True, raw_config=valid_uri,
            provisioning_status="active", server=server_inactive
        )
        p4_inactive_profile = VPNProfile(
            id=4, user_id=10, server_id=1, device_name="Device #4",
            is_active=False, desired_is_active=False, raw_config=valid_uri,
            provisioning_status="active", server=server_awg2
        )
        p5_broken_config = VPNProfile(
            id=5, user_id=10, server_id=1, device_name="Device #5",
            is_active=True, desired_is_active=True, raw_config="not_a_vpn_uri",
            provisioning_status="active", server=server_awg2
        )

        mock_get_profiles.return_value = [
            p1_valid, p2_awg3, p3_inactive_server, p4_inactive_profile, p5_broken_config
        ]

        session = AsyncMock()
        configs = await SubscriptionFeedService.get_exportable_configs(session, 10)

        # Only p1_valid must be exported
        self.assertEqual(len(configs), 1)
        profile, conf_text = configs[0]
        self.assertEqual(profile.id, 1)
        self.assertIn("[Interface]", conf_text)
        self.assertIn("PrivateKey = privkey_1", conf_text)

    @patch("integrations.incy.feed_service.SubscriptionFeedService.get_exportable_configs")
    @patch("integrations.incy.feed_service.SubscriptionFeedService.get_user_traffic")
    async def test_build_feed_active_user_with_profiles(
        self, mock_get_traffic, mock_get_exportable
    ):
        mock_get_traffic.return_value = (1000, 2000)
        server = Server(id=1, name="Warsaw", country_flag="🇵🇱", protocol="amneziawg2", is_active=True)
        profile = VPNProfile(
            id=1, user_id=100, server_id=1, device_name="iPhone",
            is_active=True, desired_is_active=True, raw_config=_make_dummy_awg2_uri(1),
            provisioning_status="active", server=server
        )
        sample_config = "[Interface]\nPrivateKey = >>>>????\n"
        mock_get_exportable.return_value = [(profile, sample_config)]

        now = datetime.now(timezone.utc)
        sub_end = now + timedelta(days=10)
        user = User(
            id=100, telegram_id=1001, subscription_end=sub_end,
            is_banned=False, financial_hold=False, is_deleted=False
        )

        session = AsyncMock()
        status, headers, body = await SubscriptionFeedService.build_feed(session, user)

        self.assertEqual(status, 200)
        self.assertEqual(headers["Content-Type"], "text/plain; charset=utf-8")
        self.assertEqual(headers["Cache-Control"], "no-store")
        self.assertEqual(headers["profile-title"], "JUST1K VPN")
        self.assertTrue(headers["profile-description"].startswith("base64:"))
        self.assertTrue(headers["announce"].startswith("base64:"))
        self.assertIn(f"expire={int(sub_end.timestamp())}", headers["subscription-userinfo"])
        self.assertIn("upload=1000", headers["subscription-userinfo"])
        self.assertIn("download=2000", headers["subscription-userinfo"])

        # Decode body and verify amneziawg line
        decoded_feed = base64.b64decode(body).decode("utf-8")
        self.assertTrue(decoded_feed.startswith("amneziawg://"))
        self.assertIn("#🇵🇱 Warsaw — iPhone", decoded_feed)
        inner_b64 = decoded_feed.removeprefix("amneziawg://").split("#", 1)[0]

        # Strict URL-safe base64 proof: standard base64 would produce '+' and '/',
        # while urlsafe base64 produces '-' and '_' and must not contain '+' or '/'
        self.assertIn("-", inner_b64)
        self.assertIn("_", inner_b64)
        self.assertNotIn("+", inner_b64)
        self.assertNotIn("/", inner_b64)
        self.assertEqual(
            base64.urlsafe_b64decode(inner_b64.encode("ascii")).decode("utf-8"),
            sample_config,
        )

    @patch("integrations.incy.feed_service.SubscriptionFeedService.get_exportable_configs")
    @patch("integrations.incy.feed_service.SubscriptionFeedService.get_user_traffic")
    async def test_build_feed_grace_period_extends_expire_header(
        self, mock_get_traffic, mock_get_exportable
    ):
        mock_get_traffic.return_value = (100, 200)
        server = Server(id=1, name="Warsaw", country_flag="🇵🇱", protocol="amneziawg2", is_active=True)
        profile = VPNProfile(
            id=1, user_id=100, server_id=1, device_name="iPhone",
            is_active=True, desired_is_active=True, raw_config=_make_dummy_awg2_uri(1),
            provisioning_status="active", server=server
        )
        mock_get_exportable.return_value = [(profile, "[Interface]\nPrivateKey = privkey_1\n")]

        # subscription_end is 1 hour ago (within 4 hours grace period)
        now = datetime.now(timezone.utc)
        sub_end = now - timedelta(hours=1)
        expected_grace_expire = sub_end + timedelta(hours=4)

        user = User(
            id=100, telegram_id=1001, subscription_end=sub_end,
            is_banned=False, financial_hold=False, is_deleted=False
        )

        session = AsyncMock()
        status, headers, body = await SubscriptionFeedService.build_feed(session, user)

        self.assertEqual(status, 200)
        # Should return configs because grace period is active
        self.assertNotEqual(body, "")
        # expire in header must be future-aligned to the grace period (+4h)
        self.assertIn(
            f"expire={int(expected_grace_expire.timestamp())}",
            headers["subscription-userinfo"],
        )

    @patch("integrations.incy.feed_service.SubscriptionFeedService.get_exportable_configs")
    @patch("integrations.incy.feed_service.SubscriptionFeedService.get_user_traffic")
    async def test_build_feed_active_user_zero_devices(
        self, mock_get_traffic, mock_get_exportable
    ):
        mock_get_traffic.return_value = (0, 0)
        mock_get_exportable.return_value = []

        sub_end = datetime.now(timezone.utc) + timedelta(days=15)
        user = User(
            id=100, telegram_id=1001, subscription_end=sub_end,
            is_banned=False, financial_hold=False, is_deleted=False
        )

        session = AsyncMock()
        status, headers, body = await SubscriptionFeedService.build_feed(session, user)

        self.assertEqual(status, 200)
        self.assertEqual(body, "")  # Empty body for 0 devices
        # Real future expiration must be preserved in headers
        self.assertIn(f"expire={int(sub_end.timestamp())}", headers["subscription-userinfo"])

    @patch("integrations.incy.feed_service.SubscriptionFeedService.get_user_traffic")
    async def test_build_feed_expired_or_banned_user(self, mock_get_traffic):
        mock_get_traffic.return_value = (500, 500)
        past_end = datetime.now(timezone.utc) - timedelta(days=5)
        user_expired = User(
            id=100, telegram_id=1001, subscription_end=past_end,
            is_banned=False, financial_hold=False, is_deleted=False
        )

        session = AsyncMock()
        status, headers, body = await SubscriptionFeedService.build_feed(session, user_expired)

        self.assertEqual(status, 200)
        self.assertEqual(body, "")  # Clean empty feed to wipe servers in client
        self.assertIn(f"expire={int(past_end.timestamp())}", headers["subscription-userinfo"])

        # Banned user test
        user_banned = User(
            id=101, telegram_id=1002, subscription_end=datetime.now(timezone.utc) + timedelta(days=5),
            is_banned=True, financial_hold=False, is_deleted=False
        )
        status_banned, _headers_banned, body_banned = await SubscriptionFeedService.build_feed(session, user_banned)
        self.assertEqual(status_banned, 200)
        self.assertEqual(body_banned, "")

    @patch("integrations.incy.feed_service.SubscriptionFeedService.get_exportable_configs")
    @patch("integrations.incy.feed_service.SubscriptionFeedService.get_user_traffic")
    async def test_build_feed_multiple_devices_multi_server(
        self, mock_get_traffic, mock_get_exportable
    ):
        mock_get_traffic.return_value = (5000, 15000)

        server_pl = Server(id=1, name="Warsaw", country_flag="🇵🇱", protocol="amneziawg2", is_active=True)
        server_de = Server(id=2, name="Frankfurt", country_flag="🇩🇪", protocol="amneziawg2", is_active=True)
        server_nl = Server(id=3, name="Amsterdam", country_flag="🇳🇱", protocol="amneziawg2", is_active=True)

        p1 = VPNProfile(id=1, user_id=100, server_id=1, device_name="iPhone", is_active=True, desired_is_active=True, raw_config=_make_dummy_awg2_uri(1), provisioning_status="active", server=server_pl)
        p2 = VPNProfile(id=2, user_id=100, server_id=2, device_name="MacBook", is_active=True, desired_is_active=True, raw_config=_make_dummy_awg2_uri(2), provisioning_status="active", server=server_de)
        p3 = VPNProfile(id=3, user_id=100, server_id=3, device_name="PC", is_active=True, desired_is_active=True, raw_config=_make_dummy_awg2_uri(3), provisioning_status="active", server=server_nl)

        conf1 = "[Interface]\nPrivateKey = >>>>????\n"
        conf2 = "[Interface]\nPrivateKey = ????>>>>\n"
        conf3 = "[Interface]\nPrivateKey = privkey_normal\n"

        mock_get_exportable.return_value = [
            (p1, conf1),
            (p2, conf2),
            (p3, conf3),
        ]

        sub_end = datetime.now(timezone.utc) + timedelta(days=20)
        user = User(
            id=100, telegram_id=1001, subscription_end=sub_end,
            is_banned=False, financial_hold=False, is_deleted=False
        )

        session = AsyncMock()
        status, headers, body = await SubscriptionFeedService.build_feed(session, user)

        self.assertEqual(status, 200)
        self.assertEqual(headers["Content-Type"], "text/plain; charset=utf-8")

        # Decode multi-line subscription body
        decoded_feed = base64.b64decode(body).decode("utf-8")
        lines = decoded_feed.strip().split("\n")
        self.assertEqual(len(lines), 3)

        expected_fragments = [
            "🇵🇱 Warsaw — iPhone",
            "🇩🇪 Frankfurt — MacBook",
            "🇳🇱 Amsterdam — PC",
        ]
        expected_confs = [conf1, conf2, conf3]

        for i, line in enumerate(lines):
            self.assertTrue(line.startswith("amneziawg://"))
            self.assertIn(f"#{expected_fragments[i]}", line)

            inner_b64 = line.removeprefix("amneziawg://").split("#", 1)[0]
            # Ensure URL-safe encoding (no '+' or '/')
            self.assertNotIn("+", inner_b64)
            self.assertNotIn("/", inner_b64)

            # Ensure payload decodes to exact original configuration
            decoded_conf = base64.urlsafe_b64decode(inner_b64.encode("ascii")).decode("utf-8")
            self.assertEqual(decoded_conf, expected_confs[i])


if __name__ == "__main__":
    unittest.main()
