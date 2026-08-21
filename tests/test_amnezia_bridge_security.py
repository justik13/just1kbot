import json
import os
import unittest
from unittest.mock import AsyncMock, patch

from aiohttp import web
from aiohttp.test_utils import make_mocked_request

from bot.handlers.amnezia_bridge import amnezia_bridge_handler
from database.models import Server, User, VPNProfile
from services.amnezia_bridge_token_service import AmneziaBridgeTokenService
from utils.datetime_helpers import now_utc
from utils.http_rate_limiter import amnezia_bridge_rate_limiter
from utils.vpn_helpers import (
    InvalidAmneziaConfigError,
    InvalidAmneziaProfileError,
    build_display_vpn_uri,
)
from utils.vpn_parser import encode_json_to_vpn_uri

TEST_SECRET = "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"


def make_valid_awg2_vpn_uri(
    server_host: str = "vpn.node.com",
    port: int = 54321,
    protocol_version: str = "2",
) -> str:
    config_obj = {
        "description": "Initial Server",
        "dns1": "1.1.1.1",
        "dns2": "1.0.0.1",
        "containers": [
            {
                "container": "amnezia-awg2",
                "awg": {
                    "protocol_version": protocol_version,
                    "last_config": json.dumps(
                        {
                            "client_priv_key": "c_priv_key",
                            "server_pub_key": "s_pub_key",
                            "hostName": server_host,
                            "port": port,
                            "client_ip": "10.0.0.5/32",
                            "mtu": 1280,
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
        ],
    }
    return encode_json_to_vpn_uri(config_obj)


class AmneziaBridgeSecurityTests(unittest.IsolatedAsyncioTestCase):
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

    def setUp(self):
        amnezia_bridge_rate_limiter.reset()

    # ─────────────────────────────────────────────────────────────
    # Token Service Unit Tests
    # ─────────────────────────────────────────────────────────────
    def test_token_service_canonical_and_signature(self):
        canonical = AmneziaBridgeTokenService.get_canonical_string(10, 20, 1700000000)
        self.assertEqual(canonical, "amnezia:v1:10:20:1700000000")

        sig = AmneziaBridgeTokenService.sign(10, 20, 1700000000, secret=TEST_SECRET)
        self.assertEqual(len(sig), 64)
        self.assertTrue(sig.islower())

        self.assertTrue(AmneziaBridgeTokenService.verify(10, 20, 1700000000, sig, secret=TEST_SECRET))
        self.assertFalse(AmneziaBridgeTokenService.verify(11, 20, 1700000000, sig, secret=TEST_SECRET))
        self.assertFalse(AmneziaBridgeTokenService.verify(10, 21, 1700000000, sig, secret=TEST_SECRET))
        self.assertFalse(AmneziaBridgeTokenService.verify(10, 20, 1700000001, sig, secret=TEST_SECRET))
        self.assertFalse(AmneziaBridgeTokenService.verify(10, 20, 1700000000, sig.upper(), secret=TEST_SECRET))
        self.assertFalse(AmneziaBridgeTokenService.verify(10, 20, 1700000000, "invalid_sig", secret=TEST_SECRET))

    def test_token_service_ttl_validation(self):
        now_ts = 100000

        # Expired
        valid, reason = AmneziaBridgeTokenService.is_ttl_valid(now_ts - 1, now_ts=now_ts)
        self.assertFalse(valid)
        self.assertEqual(reason, "expired")

        # Just expired
        valid, reason = AmneziaBridgeTokenService.is_ttl_valid(now_ts, now_ts=now_ts)
        self.assertFalse(valid)
        self.assertEqual(reason, "expired")

        # Valid (+600s)
        valid, reason = AmneziaBridgeTokenService.is_ttl_valid(now_ts + 600, now_ts=now_ts)
        self.assertTrue(valid)
        self.assertEqual(reason, "valid")

        # Max allowed (+930s)
        valid, reason = AmneziaBridgeTokenService.is_ttl_valid(now_ts + 930, now_ts=now_ts)
        self.assertTrue(valid)
        self.assertEqual(reason, "valid")

        # Future skew exceeded (+931s)
        valid, reason = AmneziaBridgeTokenService.is_ttl_valid(now_ts + 931, now_ts=now_ts)
        self.assertFalse(valid)
        self.assertEqual(reason, "future_skew_exceeded")

    def test_token_service_build_bridge_url(self):
        url = AmneziaBridgeTokenService.build_bridge_url("vpn.example.com", 42, 100)
        self.assertTrue(url.startswith("https://vpn.example.com/amnezia/open/42?"))
        self.assertIn("uid=100", url)
        self.assertIn("exp=", url)
        self.assertIn("sig=", url)

    # ─────────────────────────────────────────────────────────────
    # VPN Helpers & Protocol Guard Unit Tests
    # ─────────────────────────────────────────────────────────────
    def test_build_display_vpn_uri_success(self):
        server = Server(id=1, name="Germany", protocol="amneziawg2", country_flag="🇩🇪", is_active=True)
        raw_uri = make_valid_awg2_vpn_uri(server_host="de.vpn.com", port=1234)
        profile = VPNProfile(id=5, user_id=10, server_id=1, server=server, device_name="Phone #1", raw_config=raw_uri)

        display_uri = build_display_vpn_uri(profile)
        self.assertTrue(display_uri.startswith("vpn://"))

    def test_build_display_vpn_uri_rejects_unsupported_protocol(self):
        server = Server(id=1, name="Server", protocol="wireguard", is_active=True)
        raw_uri = make_valid_awg2_vpn_uri()
        profile = VPNProfile(id=5, user_id=10, server_id=1, server=server, device_name="Device", raw_config=raw_uri)

        with self.assertRaises(InvalidAmneziaProfileError):
            build_display_vpn_uri(profile)

    def test_build_display_vpn_uri_rejects_awg3(self):
        # AWG3 is strictly outside current runtime scope
        server = Server(id=1, name="Server", protocol="amneziawg3", is_active=True)
        raw_uri = make_valid_awg2_vpn_uri(protocol_version="3")
        profile = VPNProfile(id=5, user_id=10, server_id=1, server=server, device_name="Device", raw_config=raw_uri)

        with self.assertRaises(InvalidAmneziaProfileError):
            build_display_vpn_uri(profile)

    def test_build_display_vpn_uri_rejects_non_awg2_container(self):
        server = Server(id=1, name="Server", protocol="amneziawg2", is_active=True)
        # AWG3 payload on AWG2 server must fail closed
        raw_uri = make_valid_awg2_vpn_uri(protocol_version="3")
        profile = VPNProfile(id=5, user_id=10, server_id=1, server=server, device_name="Device", raw_config=raw_uri)

        with self.assertRaises(InvalidAmneziaConfigError):
            build_display_vpn_uri(profile)

    # ─────────────────────────────────────────────────────────────
    # Web Bridge Endpoint Tests
    # ─────────────────────────────────────────────────────────────
    def _create_request(self, profile_id: str, query: dict, raw_path: str | None = None) -> web.Request:
        path = f"/amnezia/open/{profile_id}"
        req = make_mocked_request(
            "GET",
            raw_path or path,
            match_info={"profile_id": profile_id},
            headers={"Host": "test.domain"},
            client_max_size=2048,
        )
        # Mock request query
        req._query = query
        req._rel_url = req._rel_url.with_query(query)
        return req

    async def test_endpoint_input_format_errors_return_400(self):
        # Non-numeric profile_id
        req = self._create_request("abc", {"uid": "1", "exp": "1700000000", "sig": "a" * 64})
        resp = await amnezia_bridge_handler(req)
        self.assertEqual(resp.status, 400)

        # Non-numeric uid
        req = self._create_request("1", {"uid": "xyz", "exp": "1700000000", "sig": "a" * 64})
        resp = await amnezia_bridge_handler(req)
        self.assertEqual(resp.status, 400)

        # Uppercase sig
        req = self._create_request("1", {"uid": "1", "exp": "1700000000", "sig": "A" * 64})
        resp = await amnezia_bridge_handler(req)
        self.assertEqual(resp.status, 400)

        # Short sig
        req = self._create_request("1", {"uid": "1", "exp": "1700000000", "sig": "a" * 63})
        resp = await amnezia_bridge_handler(req)
        self.assertEqual(resp.status, 400)

    async def test_endpoint_expired_token_returns_410(self):
        exp = int(now_utc().timestamp()) - 10
        sig = AmneziaBridgeTokenService.sign(1, 1, exp, secret=TEST_SECRET)
        req = self._create_request("1", {"uid": "1", "exp": str(exp), "sig": sig})

        resp = await amnezia_bridge_handler(req)
        self.assertEqual(resp.status, 410)
        self.assertIn("Ссылка устарела", resp.text)

    async def test_endpoint_future_skew_exceeded_returns_403(self):
        exp = int(now_utc().timestamp()) + 1000  # > 930s
        sig = AmneziaBridgeTokenService.sign(1, 1, exp, secret=TEST_SECRET)
        req = self._create_request("1", {"uid": "1", "exp": str(exp), "sig": sig})

        resp = await amnezia_bridge_handler(req)
        self.assertEqual(resp.status, 403)

    async def test_endpoint_invalid_signature_returns_403(self):
        exp = int(now_utc().timestamp()) + 300
        bad_sig = "0" * 64
        req = self._create_request("1", {"uid": "1", "exp": str(exp), "sig": bad_sig})

        resp = await amnezia_bridge_handler(req)
        self.assertEqual(resp.status, 403)
        self.assertIn("Недействительная подпись", resp.text)

    @patch("bot.handlers.amnezia_bridge.SubscriptionService.check_vpn_access", return_value=True)
    @patch("bot.handlers.amnezia_bridge.get_user_by_id")
    @patch("bot.handlers.amnezia_bridge.get_profile_by_id")
    @patch("bot.handlers.amnezia_bridge.session_scope")
    async def test_endpoint_successful_delivery_200(
        self,
        mock_session_scope,
        mock_get_profile,
        mock_get_user,
        mock_check_access,
    ):
        mock_session = AsyncMock()
        mock_session_scope.return_value.__aenter__.return_value = mock_session

        server = Server(
            id=10,
            name="Netherlands #1",
            protocol="amneziawg2",
            country_flag="🇳🇱",
            is_active=True,
        )
        raw_uri = make_valid_awg2_vpn_uri(server_host="nl.vpn.com", port=5555)
        profile = VPNProfile(
            id=77,
            user_id=88,
            server_id=10,
            server=server,
            device_name="MacBook #1",
            raw_config=raw_uri,
            peer_id="peer_abc",
            is_active=True,
            provisioning_status="active",
        )
        user = User(
            id=88,
            telegram_id=99999,
            is_deleted=False,
            is_banned=False,
            financial_hold=False,
        )

        mock_get_profile.return_value = profile
        mock_get_user.return_value = user

        exp = int(now_utc().timestamp()) + 300
        sig = AmneziaBridgeTokenService.sign(77, 88, exp, secret=TEST_SECRET)
        req = self._create_request("77", {"uid": "88", "exp": str(exp), "sig": sig})

        resp = await amnezia_bridge_handler(req)
        self.assertEqual(resp.status, 200)
        self.assertEqual(resp.headers["Content-Type"], "text/html; charset=utf-8")
        self.assertIn("🇳🇱 Netherlands #1 — MacBook #1", resp.text)
        self.assertIn('<textarea id="vpn-key"', resp.text)
        self.assertIn("vpn://", resp.text)
        self.assertIn("Автоматическое открытие", resp.text)
        self.assertIn("Скопировать полный ключ", resp.text)

    @patch("bot.handlers.amnezia_bridge.get_user_by_id")
    @patch("bot.handlers.amnezia_bridge.get_profile_by_id")
    @patch("bot.handlers.amnezia_bridge.session_scope")
    async def test_endpoint_profile_not_found_returns_404(
        self,
        mock_session_scope,
        mock_get_profile,
        mock_get_user,
    ):
        mock_session = AsyncMock()
        mock_session_scope.return_value.__aenter__.return_value = mock_session
        mock_get_profile.return_value = None

        exp = int(now_utc().timestamp()) + 300
        sig = AmneziaBridgeTokenService.sign(99, 88, exp, secret=TEST_SECRET)
        req = self._create_request("99", {"uid": "88", "exp": str(exp), "sig": sig})

        resp = await amnezia_bridge_handler(req)
        self.assertEqual(resp.status, 404)

    @patch("bot.handlers.amnezia_bridge.SubscriptionService.check_vpn_access", return_value=True)
    @patch("bot.handlers.amnezia_bridge.get_user_by_id")
    @patch("bot.handlers.amnezia_bridge.get_profile_by_id")
    @patch("bot.handlers.amnezia_bridge.session_scope")
    async def test_endpoint_ownership_mismatch_returns_403(
        self,
        mock_session_scope,
        mock_get_profile,
        mock_get_user,
        mock_check_access,
    ):
        mock_session = AsyncMock()
        mock_session_scope.return_value.__aenter__.return_value = mock_session

        server = Server(id=10, name="Server", protocol="amneziawg2", is_active=True)
        profile = VPNProfile(id=77, user_id=111, server=server, is_active=True, provisioning_status="active", peer_id="p")
        user = User(id=88, is_deleted=False, is_banned=False, financial_hold=False)

        mock_get_profile.return_value = profile
        mock_get_user.return_value = user

        exp = int(now_utc().timestamp()) + 300
        sig = AmneziaBridgeTokenService.sign(77, 88, exp, secret=TEST_SECRET)
        req = self._create_request("77", {"uid": "88", "exp": str(exp), "sig": sig})

        resp = await amnezia_bridge_handler(req)
        self.assertEqual(resp.status, 403)

    @patch("bot.handlers.amnezia_bridge.session_scope", side_effect=RuntimeError("Forced unexpected crash"))
    async def test_endpoint_unexpected_exception_returns_controlled_500(self, mock_session_scope):
        exp = int(now_utc().timestamp()) + 300
        sig = AmneziaBridgeTokenService.sign(77, 88, exp, secret=TEST_SECRET)
        req = self._create_request("77", {"uid": "88", "exp": str(exp), "sig": sig})

        resp = await amnezia_bridge_handler(req)
        self.assertEqual(resp.status, 500)
        self.assertEqual(resp.headers["Content-Type"], "text/html; charset=utf-8")
        self.assertIn("Временная ошибка сервера", resp.text)
        self.assertNotIn("Forced unexpected crash", resp.text)
        self.assertNotIn(TEST_SECRET, resp.text)


    @patch("bot.handlers.amnezia_bridge.SubscriptionService.check_vpn_access", return_value=True)
    @patch("bot.handlers.amnezia_bridge.get_user_by_id")
    @patch("bot.handlers.amnezia_bridge.get_profile_by_id")
    @patch("bot.handlers.amnezia_bridge.session_scope")
    async def test_endpoint_acl_user_flags_return_403(
        self,
        mock_session_scope,
        mock_get_profile,
        mock_get_user,
        mock_check_access,
    ):
        mock_session = AsyncMock()
        mock_session_scope.return_value.__aenter__.return_value = mock_session

        server = Server(id=10, name="Server", protocol="amneziawg2", is_active=True)
        raw_uri = make_valid_awg2_vpn_uri()
        profile = VPNProfile(
            id=77,
            user_id=88,
            server=server,
            raw_config=raw_uri,
            peer_id="peer_1",
            is_active=True,
            provisioning_status="active",
        )
        mock_get_profile.return_value = profile

        for flag_name in ("is_deleted", "is_banned", "financial_hold"):
            with self.subTest(flag=flag_name):
                user_kwargs = {
                    "id": 88,
                    "is_deleted": False,
                    "is_banned": False,
                    "financial_hold": False,
                }
                user_kwargs[flag_name] = True
                user = User(**user_kwargs)
                mock_get_user.return_value = user

                exp = int(now_utc().timestamp()) + 300
                sig = AmneziaBridgeTokenService.sign(77, 88, exp, secret=TEST_SECRET)
                req = self._create_request("77", {"uid": "88", "exp": str(exp), "sig": sig})

                resp = await amnezia_bridge_handler(req)
                self.assertEqual(resp.status, 403)

    @patch("bot.handlers.amnezia_bridge.SubscriptionService.check_vpn_access", return_value=False)
    @patch("bot.handlers.amnezia_bridge.get_user_by_id")
    @patch("bot.handlers.amnezia_bridge.get_profile_by_id")
    @patch("bot.handlers.amnezia_bridge.session_scope")
    async def test_endpoint_acl_subscription_access_denied_returns_403(
        self,
        mock_session_scope,
        mock_get_profile,
        mock_get_user,
        mock_check_access,
    ):
        mock_session = AsyncMock()
        mock_session_scope.return_value.__aenter__.return_value = mock_session

        server = Server(id=10, name="Server", protocol="amneziawg2", is_active=True)
        profile = VPNProfile(
            id=77,
            user_id=88,
            server=server,
            raw_config=make_valid_awg2_vpn_uri(),
            peer_id="peer_1",
            is_active=True,
            provisioning_status="active",
        )
        user = User(id=88, is_deleted=False, is_banned=False, financial_hold=False)
        mock_get_profile.return_value = profile
        mock_get_user.return_value = user

        exp = int(now_utc().timestamp()) + 300
        sig = AmneziaBridgeTokenService.sign(77, 88, exp, secret=TEST_SECRET)
        req = self._create_request("77", {"uid": "88", "exp": str(exp), "sig": sig})

        resp = await amnezia_bridge_handler(req)
        self.assertEqual(resp.status, 403)

    @patch("bot.handlers.amnezia_bridge.SubscriptionService.check_vpn_access", return_value=True)
    @patch("bot.handlers.amnezia_bridge.get_user_by_id")
    @patch("bot.handlers.amnezia_bridge.get_profile_by_id")
    @patch("bot.handlers.amnezia_bridge.session_scope")
    async def test_endpoint_acl_profile_and_server_states_return_403(
        self,
        mock_session_scope,
        mock_get_profile,
        mock_get_user,
        mock_check_access,
    ):
        mock_session = AsyncMock()
        mock_session_scope.return_value.__aenter__.return_value = mock_session
        user = User(id=88, is_deleted=False, is_banned=False, financial_hold=False)
        mock_get_user.return_value = user
        raw_uri = make_valid_awg2_vpn_uri()

        invalid_combinations = [
            ("server_none", None, True, "active", "peer_1", raw_uri),
            ("server_inactive", Server(id=10, protocol="amneziawg2", is_active=False), True, "active", "peer_1", raw_uri),
            ("server_wireguard", Server(id=10, protocol="wireguard", is_active=True), True, "active", "peer_1", raw_uri),
            ("server_awg3", Server(id=10, protocol="amneziawg3", is_active=True), True, "active", "peer_1", raw_uri),
            ("profile_inactive", Server(id=10, protocol="amneziawg2", is_active=True), False, "active", "peer_1", raw_uri),
            ("provisioning_pending", Server(id=10, protocol="amneziawg2", is_active=True), True, "pending_create", "peer_1", raw_uri),
            ("peer_id_missing", Server(id=10, protocol="amneziawg2", is_active=True), True, "active", None, raw_uri),
            ("raw_config_empty", Server(id=10, protocol="amneziawg2", is_active=True), True, "active", "peer_1", ""),
            ("raw_config_not_vpn", Server(id=10, protocol="amneziawg2", is_active=True), True, "active", "peer_1", "https://not.vpn"),
        ]

        for desc, server, p_active, prov_stat, peer, config in invalid_combinations:
            with self.subTest(case=desc):
                profile = VPNProfile(
                    id=77,
                    user_id=88,
                    server=server,
                    is_active=p_active,
                    provisioning_status=prov_stat,
                    peer_id=peer,
                    raw_config=config,
                )
                mock_get_profile.return_value = profile

                exp = int(now_utc().timestamp()) + 300
                sig = AmneziaBridgeTokenService.sign(77, 88, exp, secret=TEST_SECRET)
                req = self._create_request("77", {"uid": "88", "exp": str(exp), "sig": sig})

                resp = await amnezia_bridge_handler(req)
                self.assertEqual(resp.status, 403)

    @patch("bot.handlers.amnezia_bridge.session_scope")
    async def test_unexpected_exception_logs_only_exception_type_without_traceback(self, mock_session_scope):
        mock_session_scope.side_effect = RuntimeError("Sensitive internal secret error details")
        exp = int(now_utc().timestamp()) + 300
        sig = AmneziaBridgeTokenService.sign(1, 1, exp, secret=TEST_SECRET)
        req = self._create_request("1", {"uid": "1", "exp": str(exp), "sig": sig})

        with self.assertLogs("bot.handlers.amnezia_bridge", level="ERROR") as cm:
            resp = await amnezia_bridge_handler(req)

        self.assertEqual(resp.status, 500)
        self.assertEqual(len(cm.output), 1)
        self.assertIn("Unexpected error in Amnezia bridge handler: RuntimeError", cm.output[0])
        self.assertNotIn("Sensitive internal secret error details", cm.output[0])
        self.assertNotIn("Traceback", cm.output[0])


if __name__ == "__main__":
    unittest.main()
