import io
import json
import logging
import os
import unittest
from unittest.mock import AsyncMock, patch

from aiohttp.test_utils import make_mocked_request

from bot.handlers.amnezia_bridge import amnezia_bridge_handler
from database.models import Server, User, VPNProfile
from services.amnezia_bridge_token_service import AmneziaBridgeTokenService
from utils.datetime_helpers import now_utc
from utils.vpn_parser import encode_json_to_vpn_uri

TEST_SECRET = "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"


class AmneziaBridgeLeakageTests(unittest.IsolatedAsyncioTestCase):
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
                "AMNEZIA_BRIDGE_HMAC_SECRET": TEST_SECRET,
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

    def _create_request(self, profile_id: str, query: dict) -> make_mocked_request:
        path = f"/amnezia/open/{profile_id}"
        req = make_mocked_request(
            "GET",
            path,
            match_info={"profile_id": profile_id},
            headers={"Host": "test.domain"},
        )
        req._query = query
        req._rel_url = req._rel_url.with_query(query)
        return req

    @patch("bot.handlers.amnezia_bridge.SubscriptionService.check_vpn_access", return_value=True)
    @patch("bot.handlers.amnezia_bridge.get_user_by_id")
    @patch("bot.handlers.amnezia_bridge.get_profile_by_id")
    @patch("bot.handlers.amnezia_bridge.session_scope")
    async def test_zero_leakage_in_logs_and_errors(
        self,
        mock_session_scope,
        mock_get_profile,
        mock_get_user,
        mock_check_access,
    ):
        mock_session = AsyncMock()
        mock_session_scope.return_value.__aenter__.return_value = mock_session

        raw_config = encode_json_to_vpn_uri(
            {
                "containers": [
                    {
                        "container": "amnezia-awg2",
                        "awg": {
                            "protocol_version": "2",
                            "last_config": json.dumps(
                                {
                                    "client_priv_key": "SECRET_CLIENT_PRIV_KEY",
                                    "server_pub_key": "PUB_KEY",
                                    "hostName": "secret.vpn.com",
                                    "port": 1234,
                                    "client_ip": "10.0.0.2/32",
                                    "mtu": 1280,
                                }
                            ),
                        },
                    }
                ]
            }
        )
        server = Server(id=1, name="SecretNode", protocol="amneziawg2", is_active=True)
        profile = VPNProfile(
            id=5,
            user_id=10,
            server_id=1,
            server=server,
            device_name="MyDevice",
            raw_config=raw_config,
            peer_id="peer_123",
            is_active=True,
            provisioning_status="active",
        )
        user = User(id=10, is_deleted=False, is_banned=False, financial_hold=False)

        mock_get_profile.return_value = profile
        mock_get_user.return_value = user

        log_stream = io.StringIO()
        handler = logging.StreamHandler(log_stream)
        bridge_logger = logging.getLogger("bot.handlers.amnezia_bridge")
        bridge_logger.addHandler(handler)
        bridge_logger.setLevel(logging.INFO)

        try:
            exp = int(now_utc().timestamp()) + 300
            sig = AmneziaBridgeTokenService.sign(5, 10, exp, secret=TEST_SECRET)
            req = self._create_request("5", {"uid": "10", "exp": str(exp), "sig": sig})

            resp = await amnezia_bridge_handler(req)
            self.assertEqual(resp.status, 200)

            # Response body has vpn:// inside textarea
            self.assertIn("vpn://", resp.text)
            self.assertIn('<textarea id="vpn-key"', resp.text)

            # Headers do not leak key or secrets
            for h_val in resp.headers.values():
                self.assertNotIn("SECRET_CLIENT_PRIV_KEY", h_val)
                self.assertNotIn(TEST_SECRET, h_val)

            # Logs do NOT contain raw config or secret key
            log_output = log_stream.getvalue()
            self.assertNotIn("SECRET_CLIENT_PRIV_KEY", log_output)
            self.assertNotIn(TEST_SECRET, log_output)
            self.assertNotIn(sig, log_output)
            self.assertIn("Amnezia bridge access granted: profile_id=5 user_id=10", log_output)

        finally:
            bridge_logger.removeHandler(handler)


if __name__ == "__main__":
    unittest.main()
