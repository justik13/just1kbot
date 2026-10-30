"""Unit tests for White Internet HTTP subscription feed endpoint (/sub/wl/{token})."""

import base64
import json
import os
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch
from urllib.parse import unquote


from aiohttp import web
from aiohttp.test_utils import AioHTTPTestCase

from bot import texts
from bot.handlers.white_internet_web import setup_white_internet_web_routes
from config.constants import XRAY_PROTOCOL
from config.enums import ServerHealthState, WhiteInternetProvisioningStatus, WhiteInternetStatus
from database.models import Server, User, WhiteInternetSubscription


class TestWhiteInternetWebFeed(AioHTTPTestCase):
    """Test suite for /sub/wl/{token} HTTP subscription feed."""

    def setUp(self):
        super().setUp()
        from bot.handlers import white_internet_web
        white_internet_web._ip_rate_limiter.buckets.clear()
        white_internet_web._token_rate_limiter.buckets.clear()
        self.default_user = User(id=10, telegram_id=777, is_banned=False, is_deleted=False)
        self.user_patcher = patch(
            "database.repositories.users_repo.get_user_by_id",
            new=AsyncMock(return_value=self.default_user),
        )
        self.user_patcher.start()

    def tearDown(self):
        self.user_patcher.stop()
        from bot.handlers import white_internet_web
        white_internet_web._ip_rate_limiter.buckets.clear()
        white_internet_web._token_rate_limiter.buckets.clear()
        super().tearDown()

    async def get_application(self):
        app = web.Application()
        setup_white_internet_web_routes(app)
        return app

    async def test_invalid_token_returns_404(self):
        resp = await self.client.get("/sub/wl/short")
        self.assertEqual(resp.status, 404)

        mock_session = AsyncMock()
        @asynccontextmanager
        async def fake_session_scope():
            yield mock_session

        with patch("bot.handlers.white_internet_web.session_scope", fake_session_scope):
            with patch("database.repositories.white_internet_repo.get_subscription_by_token", return_value=None):
                resp2 = await self.client.get("/sub/wl/nonexistent-token-1234567890abcdef")
                self.assertEqual(resp2.status, 404)

    async def test_pending_status_returns_503(self):
        sub = MagicMock(spec=WhiteInternetSubscription)
        sub.status = WhiteInternetStatus.PENDING

        mock_session = AsyncMock()
        @asynccontextmanager
        async def fake_session_scope():
            yield mock_session

        with patch("bot.handlers.white_internet_web.session_scope", fake_session_scope):
            with patch("database.repositories.white_internet_repo.get_subscription_by_token", return_value=sub):
                resp = await self.client.get("/sub/wl/pending-token-1234567890abcdef")
                self.assertEqual(resp.status, 503)
                self.assertEqual(resp.headers.get("Retry-After"), "5")

    async def test_pending_status_browser_returns_landing_html(self):
        sub = MagicMock(spec=WhiteInternetSubscription)
        sub.status = WhiteInternetStatus.PENDING

        mock_session = AsyncMock()
        @asynccontextmanager
        async def fake_session_scope():
            yield mock_session

        with patch("bot.handlers.white_internet_web.session_scope", fake_session_scope):
            with patch("database.repositories.white_internet_repo.get_subscription_by_token", return_value=sub):
                resp = await self.client.get(
                    "/sub/wl/pending-token-1234567890abcdef",
                    headers={"Accept": "text/html,application/xhtml+xml"},
                )
                self.assertEqual(resp.status, 200)
                self.assertIn("text/html", resp.headers.get("Content-Type", ""))
                html_text = await resp.text()
                self.assertIn("Перейти в бота", html_text)
                self.assertIn("https://t.me/", html_text)

    async def test_exhausted_status_returns_200_with_notice(self):
        sub = MagicMock(spec=WhiteInternetSubscription)
        sub.id = 1
        sub.status = WhiteInternetStatus.EXHAUSTED
        sub.traffic_uplink_bytes = 1000
        sub.traffic_downlink_bytes = 2000
        sub.traffic_limit_bytes = 53687091200
        sub.expires_at = datetime.now(timezone.utc) + timedelta(days=30)
        sub.is_trial = False

        mock_session = AsyncMock()
        @asynccontextmanager
        async def fake_session_scope():
            yield mock_session

        with patch("bot.handlers.white_internet_web.session_scope", fake_session_scope):
            with patch("database.repositories.white_internet_repo.get_subscription_by_token", return_value=sub):
                resp = await self.client.get("/sub/wl/exhausted-token-1234567890abcdef")
                self.assertEqual(resp.status, 200)
                self.assertIn("upload=1000", resp.headers.get("Subscription-Userinfo", ""))
                self.assertIn("download=2000", resp.headers.get("Subscription-Userinfo", ""))
                self.assertIn("Channel-Url", resp.headers)
                self.assertIn("Support-Url", resp.headers)
                self.assertIn("Profile-Title", resp.headers)
                body = await resp.text()
                decoded = base64.b64decode(body).decode("utf-8")
                self.assertTrue(decoded.startswith("vless://00000000-0000-0000-0000-000000000000@127.0.0.1:443"))
                self.assertIn("Трафик исчерпан", unquote(decoded))

    async def test_exhausted_status_browser_returns_landing_html(self):
        sub = MagicMock(spec=WhiteInternetSubscription)
        sub.id = 1
        sub.status = WhiteInternetStatus.EXHAUSTED
        sub.traffic_uplink_bytes = 1000
        sub.traffic_downlink_bytes = 2000
        sub.traffic_limit_bytes = 53687091200
        sub.expires_at = datetime.now(timezone.utc) + timedelta(days=30)

        mock_session = AsyncMock()
        @asynccontextmanager
        async def fake_session_scope():
            yield mock_session

        with patch("bot.handlers.white_internet_web.session_scope", fake_session_scope):
            with patch("database.repositories.white_internet_repo.get_subscription_by_token", return_value=sub):
                resp = await self.client.get(
                    "/sub/wl/exhausted-token-1234567890abcdef",
                    headers={"Accept": "text/html,application/xhtml+xml"},
                )
                self.assertEqual(resp.status, 200)
                self.assertIn("text/html", resp.headers.get("Content-Type", ""))
                html_text = await resp.text()
                self.assertIn("Трафик исчерпан", html_text)
                self.assertIn("Перейти в бота", html_text)
                self.assertIn("https://t.me/", html_text)

    async def test_expired_status_returns_200_with_notice(self):
        now = datetime.now(timezone.utc)
        sub = MagicMock(spec=WhiteInternetSubscription)
        sub.id = 1
        sub.origin_node_id = 1
        sub.status = WhiteInternetStatus.EXPIRED
        sub.traffic_uplink_bytes = 1000
        sub.traffic_downlink_bytes = 2000
        sub.traffic_limit_bytes = 53687091200
        sub.expires_at = now - timedelta(days=1)

        mock_session = AsyncMock()
        @asynccontextmanager
        async def fake_session_scope():
            yield mock_session

        with patch("bot.handlers.white_internet_web.session_scope", fake_session_scope):
            with patch("database.repositories.white_internet_repo.get_subscription_by_token", return_value=sub):
                resp = await self.client.get("/sub/wl/expired-token-1234567890abcdef")
                self.assertEqual(resp.status, 200)
                self.assertIn("Channel-Url", resp.headers)
                self.assertIn("Support-Url", resp.headers)
                self.assertIn("Profile-Title", resp.headers)
                body = await resp.text()
                decoded = base64.b64decode(body).decode("utf-8")
                self.assertTrue(decoded.startswith("vless://00000000-0000-0000-0000-000000000000@127.0.0.1:443"))
                self.assertIn("Подписка закончилась", unquote(decoded))

    async def test_expired_status_browser_returns_landing_html(self):
        now = datetime.now(timezone.utc)
        sub = MagicMock(spec=WhiteInternetSubscription)
        sub.id = 1
        sub.origin_node_id = 1
        sub.status = WhiteInternetStatus.EXPIRED
        sub.traffic_uplink_bytes = 1000
        sub.traffic_downlink_bytes = 2000
        sub.traffic_limit_bytes = 53687091200
        sub.expires_at = now - timedelta(days=1)

        mock_session = AsyncMock()
        @asynccontextmanager
        async def fake_session_scope():
            yield mock_session

        with patch("bot.handlers.white_internet_web.session_scope", fake_session_scope):
            with patch("database.repositories.white_internet_repo.get_subscription_by_token", return_value=sub):
                resp = await self.client.get(
                    "/sub/wl/expired-token-1234567890abcdef",
                    headers={"Accept": "text/html,application/xhtml+xml"},
                )
                self.assertEqual(resp.status, 200)
                self.assertIn("text/html", resp.headers.get("Content-Type", ""))
                html_text = await resp.text()
                self.assertIn("Перейти в бота", html_text)
                self.assertIn("https://t.me/", html_text)

    async def test_disabled_status_returns_200_with_notice(self):
        sub = MagicMock(spec=WhiteInternetSubscription)
        sub.id = 1
        sub.origin_node_id = 1
        sub.status = WhiteInternetStatus.DISABLED
        sub.traffic_uplink_bytes = 1000
        sub.traffic_downlink_bytes = 2000
        sub.traffic_limit_bytes = 53687091200
        sub.expires_at = datetime.now(timezone.utc) + timedelta(days=10)

        mock_session = AsyncMock()
        @asynccontextmanager
        async def fake_session_scope():
            yield mock_session

        with patch("bot.handlers.white_internet_web.session_scope", fake_session_scope):
            with patch("database.repositories.white_internet_repo.get_subscription_by_token", return_value=sub):
                resp = await self.client.get("/sub/wl/disabled-token-1234567890abcdef")
                self.assertEqual(resp.status, 200)
                self.assertIn("Channel-Url", resp.headers)
                self.assertIn("Support-Url", resp.headers)
                self.assertIn("Profile-Title", resp.headers)
                body = await resp.text()
                decoded = base64.b64decode(body).decode("utf-8")
                self.assertTrue(decoded.startswith("vless://00000000-0000-0000-0000-000000000000@127.0.0.1:443"))
                self.assertIn("Доступ отключен", unquote(decoded))

    async def test_disabled_status_browser_returns_landing_html(self):
        sub = MagicMock(spec=WhiteInternetSubscription)
        sub.id = 1
        sub.origin_node_id = 1
        sub.status = WhiteInternetStatus.DISABLED
        sub.traffic_uplink_bytes = 1000
        sub.traffic_downlink_bytes = 2000
        sub.traffic_limit_bytes = 53687091200
        sub.expires_at = datetime.now(timezone.utc) + timedelta(days=10)

        mock_session = AsyncMock()
        @asynccontextmanager
        async def fake_session_scope():
            yield mock_session

        with patch("bot.handlers.white_internet_web.session_scope", fake_session_scope):
            with patch("database.repositories.white_internet_repo.get_subscription_by_token", return_value=sub):
                resp = await self.client.get(
                    "/sub/wl/disabled-token-1234567890abcdef",
                    headers={"Accept": "text/html,application/xhtml+xml"},
                )
                self.assertEqual(resp.status, 200)
                self.assertIn("text/html", resp.headers.get("Content-Type", ""))
                html_text = await resp.text()
                self.assertIn("Доступ отключен", html_text)
                self.assertIn("Перейти в бота", html_text)
                self.assertIn("https://t.me/", html_text)

    async def test_runtime_out_of_sync_returns_503(self):
        now = datetime.now(timezone.utc)
        sub = MagicMock(spec=WhiteInternetSubscription)
        sub.id = 1
        sub.user_id = 10
        sub.origin_node_id = 1
        sub.status = WhiteInternetStatus.ACTIVE
        sub.expires_at = now + timedelta(days=20)
        sub.desired_version = 2
        sub.actual_version = 1  # Not yet synced!

        server = Server(
            id=1,
            name="Origin-Node",
            protocol=XRAY_PROTOCOL,
            api_url="https://cdn.example.com:8444",
            xray_instance_epoch="epoch_xyz",
            capabilities=["xray_origin"],
            is_active=True,
            health_state=ServerHealthState.ONLINE,
        )

        mock_user = MagicMock(is_banned=False, is_deleted=False)
        mock_session = AsyncMock()
        mock_session.scalar.return_value = server

        @asynccontextmanager
        async def fake_session_scope():
            yield mock_session

        with patch("bot.handlers.white_internet_web.session_scope", fake_session_scope):
            with patch("database.repositories.users_repo.get_user_by_id", new_callable=AsyncMock, return_value=mock_user):
                with patch("database.repositories.white_internet_repo.get_subscription_by_token", return_value=sub):
                    resp = await self.client.get(
                        "/sub/wl/unsynced-token-1234567890abcdef",
                        headers={"X-Hwid": "test-device-id"},
                    )
                    self.assertEqual(resp.status, 503)
                    self.assertEqual(resp.headers.get("Retry-After"), "5")

    async def test_browser_returns_active_landing_when_server_unsynced(self):
        now = datetime.now(timezone.utc)
        sub = MagicMock(spec=WhiteInternetSubscription)
        sub.id = 1
        sub.user_id = 10
        sub.origin_node_id = 1
        sub.status = WhiteInternetStatus.ACTIVE
        sub.expires_at = now + timedelta(days=20)
        sub.desired_version = 2
        sub.actual_version = 1  # Not yet synced!

        mock_user = MagicMock(is_banned=False, is_deleted=False)
        mock_session = AsyncMock()

        @asynccontextmanager
        async def fake_session_scope():
            yield mock_session

        with patch("bot.handlers.white_internet_web.session_scope", fake_session_scope):
            with patch("database.repositories.users_repo.get_user_by_id", new_callable=AsyncMock, return_value=mock_user):
                with patch("database.repositories.white_internet_repo.get_subscription_by_token", return_value=sub):
                    resp = await self.client.get(
                        "/sub/wl/unsynced-token-1234567890abcdef",
                        headers={"Accept": "text/html,application/xhtml+xml"},
                    )
                    self.assertEqual(resp.status, 200)
                    self.assertIn("text/html", resp.headers.get("Content-Type", ""))
                    html_text = await resp.text()
                    self.assertIn("incy://add/", html_text)
                    self.assertIn("happ://add/", html_text)
                    self.assertIn("Открыть в приложении INCY", html_text)
                    self.assertIn("Открыть в приложении Happ", html_text)

    async def test_epoch_mismatch_returns_503(self):
        now = datetime.now(timezone.utc)
        sub = MagicMock(spec=WhiteInternetSubscription)
        sub.id = 1
        sub.user_id = 10
        sub.origin_node_id = 1
        sub.status = WhiteInternetStatus.ACTIVE
        sub.expires_at = now + timedelta(days=20)
        sub.desired_version = 1
        sub.actual_version = 1  # Versions match!
        sub.last_reconciled_node_epoch = "epoch-old"

        server = Server(
            id=1,
            name="Origin-Node",
            protocol=XRAY_PROTOCOL,
            api_url="https://cdn.example.com:8444",
            xray_instance_epoch="epoch-new",  # Node rebooted / restarted!
            capabilities=["xray_origin"],
            is_active=True,
            health_state=ServerHealthState.ONLINE,
        )

        mock_user = MagicMock(is_banned=False, is_deleted=False)
        mock_session = AsyncMock()
        mock_session.scalar.return_value = server

        @asynccontextmanager
        async def fake_session_scope():
            yield mock_session

        with patch("bot.handlers.white_internet_web.session_scope", fake_session_scope):
            with patch("database.repositories.users_repo.get_user_by_id", new_callable=AsyncMock, return_value=mock_user):
                with patch("database.repositories.white_internet_repo.get_subscription_by_token", return_value=sub):
                    resp = await self.client.get(
                        "/sub/wl/epoch-mismatch-token-1234567890",
                        headers={"X-Hwid": "test-device-id"},
                    )
                    self.assertEqual(resp.status, 503)
                    self.assertEqual(resp.headers.get("Retry-After"), "5")

    async def test_active_and_synced_returns_base64_vless_feed(self):
        now = datetime.now(timezone.utc)
        sub = WhiteInternetSubscription(
            id=1,
            user_id=10,
            origin_node_id=1,
            token="valid-token-1234567890abcdef",
            uuid="a2b9d4e1-73c5-4812-b964-f3e7b85a1902",
            status=WhiteInternetStatus.ACTIVE,
            started_at=now,
            expires_at=now + timedelta(days=30),
            traffic_limit_bytes=53687091200,
            traffic_used_bytes=1000,
            traffic_uplink_bytes=500,
            traffic_downlink_bytes=500,
            last_uplink_snapshot=500,
            last_downlink_snapshot=500,
            desired_version=1,
            actual_version=1,
            last_reconciled_node_epoch="epoch-xyz",
            device_limit=1,
            active_hwids={},
        )

        server = Server(
            id=1,
            name="Origin-Node",
            protocol=XRAY_PROTOCOL,
            api_url="https://cdn.example.com:8444",
            xray_instance_epoch="epoch-xyz",
            capabilities=["xray_origin"],
            is_active=True,
            health_state=ServerHealthState.ONLINE,
        )

        mock_session = AsyncMock()
        mock_session.scalar.return_value = server
        mock_session.execute.return_value = MagicMock(scalar_one_or_none=lambda: server)
        mock_session.get.return_value = sub

        @asynccontextmanager
        async def fake_session_scope():
            yield mock_session

        with patch.dict(os.environ, {"WHITE_INTERNET_CDN_DOMAIN": "cdn.example.com"}):
            with patch("bot.handlers.white_internet_web.session_scope", fake_session_scope):
                with patch("database.repositories.white_internet_repo.get_subscription_by_token", return_value=sub):
                    resp = await self.client.get(
                        "/sub/wl/valid-token-1234567890abcdef",
                        headers={"X-Hwid": "test-device-hwid"},
                    )

                    self.assertEqual(resp.status, 200)
                    expected_title_b64 = base64.b64encode(texts.WL_PROFILE_NAME.encode("utf-8")).decode("ascii")
                    self.assertEqual(resp.headers.get("Profile-Title"), f"base64:{expected_title_b64}")
                    self.assertIsNone(resp.headers.get("Profile-Description"))
                    self.assertIn("t.me", resp.headers.get("Support-Url", ""))
                    self.assertIn("t.me", resp.headers.get("Profile-Web-Page-Url", ""))
                    self.assertIn("t.me", resp.headers.get("Channel-Url", ""))
                    self.assertEqual(resp.headers.get("Profile-Update-Interval"), "6")
                    self.assertEqual(resp.headers.get("hide-url"), "1")
                    self.assertEqual(resp.headers.get("no-limit-enabled"), "1")
                    self.assertIn("upload=500", resp.headers.get("Subscription-Userinfo", ""))
                    self.assertIn("download=500", resp.headers.get("Subscription-Userinfo", ""))
                    self.assertIn("Routing", resp.headers)
                    self.assertTrue(resp.headers["Routing"].startswith("incy://routing/onadd/"))
                    self.assertEqual(resp.headers.get("routing-geo-update-interval"), "168")
                    self.assertNotIn("Routing-Enable", resp.headers)
                    self.assertNotIn("tun-type", resp.headers)
                    self.assertNotIn("tun-mode", resp.headers)

                    body_b64 = await resp.text()
                    decoded_lines = base64.b64decode(body_b64).decode("utf-8").splitlines()
                    self.assertEqual(len(decoded_lines), 1)

                    wl_url = decoded_lines[0]
                    self.assertTrue(wl_url.startswith("vless://"))
                    self.assertIn("/assets/v1", unquote(wl_url))
                    decoded_url = unquote(wl_url)
                    self.assertIn('"uplinkHTTPMethod":"GET"', decoded_url)
                    self.assertIn('"uplinkDataPlacement":"header"', decoded_url)

    async def test_feed_happ_client_adaptive_routing_and_in_app_headers(self):
        now = datetime.now(timezone.utc)
        sub = WhiteInternetSubscription(
            id=1,
            user_id=10,
            origin_node_id=1,
            token="valid-token-happ-1234567890abcdef",
            uuid="a2b9d4e1-73c5-4812-b964-f3e7b85a1902",
            status=WhiteInternetStatus.ACTIVE,
            started_at=now,
            expires_at=now + timedelta(days=30),
            traffic_limit_bytes=53687091200,
            traffic_used_bytes=1000,
            traffic_uplink_bytes=500,
            traffic_downlink_bytes=500,
            last_uplink_snapshot=500,
            last_downlink_snapshot=500,
            desired_version=1,
            actual_version=1,
            last_reconciled_node_epoch="epoch-xyz",
            device_limit=1,
            active_hwids={},
        )

        server = Server(
            id=1,
            name="Origin-Node",
            protocol=XRAY_PROTOCOL,
            api_url="https://cdn.example.com:8444",
            xray_instance_epoch="epoch-xyz",
            capabilities=["xray_origin"],
            is_active=True,
            health_state=ServerHealthState.ONLINE,
        )

        mock_session = AsyncMock()
        mock_session.scalar.return_value = server
        mock_session.execute.return_value = MagicMock(scalar_one_or_none=lambda: server)
        mock_session.get.return_value = sub

        @asynccontextmanager
        async def fake_session_scope():
            yield mock_session

        with patch.dict(os.environ, {"WHITE_INTERNET_CDN_DOMAIN": "cdn.example.com"}):
            with patch("bot.handlers.white_internet_web.session_scope", fake_session_scope):
                with patch("database.repositories.white_internet_repo.get_subscription_by_token", return_value=sub):
                    resp = await self.client.get(
                        "/sub/wl/valid-token-happ-1234567890abcdef",
                        headers={
                            "X-Hwid": "test-device-hwid-happ",
                            "User-Agent": "Happ/4.6.1 (Android; Pixel 8)",
                        },
                    )

                    self.assertEqual(resp.status, 200)
                    self.assertIn("Routing", resp.headers)
                    self.assertTrue(resp.headers["Routing"].startswith("happ://routing/onadd/"))
                    self.assertEqual(resp.headers.get("Routing-Enable"), "true")

                    # Decode routing deep link and verify White Internet rules
                    routing_uri = resp.headers["Routing"]
                    b64_routing = routing_uri.removeprefix("happ://routing/onadd/")
                    routing_json = json.loads(base64.b64decode(b64_routing).decode("utf-8"))
                    self.assertEqual(routing_json.get("Name"), "✦ Just1k WL")
                    self.assertIs(routing_json.get("useChunkFiles"), True)
                    self.assertEqual(routing_json.get("LastUpdated"), "1793318400")
                    self.assertEqual(routing_json.get("DirectSites"), [])

                    self.assertEqual(resp.headers.get("sub-expire"), "1")
                    self.assertEqual(resp.headers.get("sub-expire-button-link"), "https://t.me/just1k13bot")
                    self.assertEqual(resp.headers.get("tun-type"), "singbox")
                    self.assertEqual(resp.headers.get("tun-mode"), "mixed")

    async def test_feed_happ_client_disabled_routing_headers(self):
        now = datetime.now(timezone.utc)
        sub = WhiteInternetSubscription(
            id=1,
            user_id=10,
            origin_node_id=1,
            token="valid-token-happ-disabled-1234567890",
            uuid="a2b9d4e1-73c5-4812-b964-f3e7b85a1901",
            status=WhiteInternetStatus.ACTIVE,
            started_at=now,
            expires_at=now + timedelta(days=30),
            traffic_limit_bytes=53687091200,
            traffic_used_bytes=1000,
            desired_version=1,
            actual_version=1,
            last_reconciled_node_epoch="epoch-xyz",
            device_limit=1,
            active_hwids={},
        )

        server = Server(
            id=1,
            name="Origin-Node",
            protocol=XRAY_PROTOCOL,
            api_url="https://cdn.example.com:8444",
            xray_instance_epoch="epoch-xyz",
            capabilities=["xray_origin"],
            is_active=True,
            health_state=ServerHealthState.ONLINE,
        )

        mock_session = AsyncMock()
        mock_session.scalar.return_value = server
        mock_session.execute.return_value = MagicMock(scalar_one_or_none=lambda: server)
        mock_session.get.return_value = sub

        @asynccontextmanager
        async def fake_session_scope():
            yield mock_session

        async def fake_get_setting(session, key, default=None):
            if key == "white_internet_routing_enabled":
                return False
            return default

        with patch.dict(os.environ, {"WHITE_INTERNET_CDN_DOMAIN": "cdn.example.com"}):
            with patch("bot.handlers.white_internet_web.session_scope", fake_session_scope):
                with patch("database.repositories.white_internet_repo.get_subscription_by_token", return_value=sub):
                    with patch("bot.handlers.white_internet_web.get_system_setting", side_effect=fake_get_setting):
                        resp = await self.client.get(
                            "/sub/wl/valid-token-happ-disabled-1234567890",
                            headers={
                                "X-Hwid": "test-device-hwid-happ-disabled",
                                "User-Agent": "Happ/4.6.1 (Android; Pixel 8)",
                            },
                        )

                        self.assertEqual(resp.status, 200)
                        self.assertEqual(resp.headers.get("Routing"), "off")
                        self.assertEqual(resp.headers.get("Routing-Enable"), "0")
                        self.assertEqual(resp.headers.get("tun-type"), "singbox")
                        self.assertEqual(resp.headers.get("tun-mode"), "mixed")

    async def test_feed_without_hwid_returns_403_hwid_required(self):
        now = datetime.now(timezone.utc)
        sub = WhiteInternetSubscription(
            id=1,
            user_id=10,
            origin_node_id=1,
            token="valid-token-1234567890abcdef",
            uuid="a2b9d4e1-73c5-4812-b964-f3e7b85a1902",
            status=WhiteInternetStatus.ACTIVE,
            started_at=now,
            expires_at=now + timedelta(days=30),
            traffic_limit_bytes=53687091200,
            traffic_used_bytes=1000,
            desired_version=1,
            actual_version=1,
            last_reconciled_node_epoch="epoch-xyz",
        )

        server = Server(
            id=1,
            name="Origin-Node",
            protocol=XRAY_PROTOCOL,
            api_url="https://cdn.example.com:8444",
            xray_instance_epoch="epoch-xyz",
            capabilities=["xray_origin"],
            is_active=True,
            health_state=ServerHealthState.ONLINE,
        )

        mock_session = AsyncMock()
        mock_session.scalar.return_value = server
        mock_session.execute.return_value = MagicMock(scalar_one_or_none=lambda: server)

        @asynccontextmanager
        async def fake_session_scope():
            yield mock_session

        with patch.dict(os.environ, {"WHITE_INTERNET_CDN_DOMAIN": "cdn.example.com"}):
            with patch("bot.handlers.white_internet_web.session_scope", fake_session_scope):
                with patch("database.repositories.white_internet_repo.get_subscription_by_token", return_value=sub):
                    resp = await self.client.get("/sub/wl/valid-token-1234567890abcdef")
                    self.assertEqual(resp.status, 403)
                    self.assertEqual(resp.headers.get("x-hwid-required"), "true")
                    self.assertEqual(await resp.text(), texts.WL_WEB_HWID_REQUIRED)

    async def test_feed_without_hwid_browser_returns_landing_html(self):
        now = datetime.now(timezone.utc)
        sub = WhiteInternetSubscription(
            id=1,
            user_id=10,
            origin_node_id=1,
            token="valid-token-1234567890abcdef",
            uuid="a2b9d4e1-73c5-4812-b964-f3e7b85a1902",
            status=WhiteInternetStatus.ACTIVE,
            started_at=now,
            expires_at=now + timedelta(days=30),
            traffic_limit_bytes=53687091200,
            traffic_used_bytes=1000,
            desired_version=1,
            actual_version=1,
            last_reconciled_node_epoch="epoch-xyz",
        )

        server = Server(
            id=1,
            name="Origin-Node",
            protocol=XRAY_PROTOCOL,
            api_url="https://cdn.example.com:8444",
            xray_instance_epoch="epoch-xyz",
            capabilities=["xray_origin"],
            is_active=True,
            health_state=ServerHealthState.ONLINE,
        )

        mock_session = AsyncMock()
        mock_session.scalar.return_value = server
        mock_session.execute.return_value = MagicMock(scalar_one_or_none=lambda: server)

        @asynccontextmanager
        async def fake_session_scope():
            yield mock_session

        with patch.dict(os.environ, {"WHITE_INTERNET_CDN_DOMAIN": "cdn.example.com"}):
            with patch("bot.handlers.white_internet_web.session_scope", fake_session_scope):
                with patch("database.repositories.white_internet_repo.get_subscription_by_token", return_value=sub):
                    resp = await self.client.get(
                        "/sub/wl/valid-token-1234567890abcdef",
                        headers={"Accept": "text/html,application/xhtml+xml"},
                    )
                    self.assertEqual(resp.status, 200)
                    self.assertIn("text/html", resp.headers.get("Content-Type", ""))
                    html_text = await resp.text()
                    self.assertIn("incy://add/", html_text)


    async def test_missing_cdn_domain_returns_503_fail_closed(self):
        now = datetime.now(timezone.utc)
        sub = WhiteInternetSubscription(
            id=1,
            user_id=10,
            origin_node_id=1,
            token="valid-token-1234567890abcdef",
            uuid="a2b9d4e1-73c5-4812-b964-f3e7b85a1902",
            status=WhiteInternetStatus.ACTIVE,
            started_at=now,
            expires_at=now + timedelta(days=30),
            traffic_limit_bytes=53687091200,
            traffic_used_bytes=0,
            traffic_uplink_bytes=0,
            traffic_downlink_bytes=0,
            last_uplink_snapshot=0,
            last_downlink_snapshot=0,
            desired_version=1,
            actual_version=1,
            last_reconciled_node_epoch="epoch-xyz",
        )

        server = Server(
            id=1,
            name="Origin-Node",
            protocol=XRAY_PROTOCOL,
            api_url="https://cdn.example.com:8444",
            xray_instance_epoch="epoch-xyz",
            capabilities=["xray_origin"],
            is_active=True,
            health_state=ServerHealthState.ONLINE,
        )

        mock_session = AsyncMock()
        mock_session.scalar.return_value = server
        mock_session.execute.return_value = MagicMock(scalar_one_or_none=lambda: server)

        @asynccontextmanager
        async def fake_session_scope():
            yield mock_session

        with patch.dict(os.environ, {}, clear=True):
            with patch("bot.handlers.white_internet_web.session_scope", fake_session_scope):
                with patch("database.repositories.white_internet_repo.get_subscription_by_token", return_value=sub):
                    resp = await self.client.get(
                        "/sub/wl/valid-token-1234567890abcdef",
                        headers={"X-Hwid": "test-device-id"},
                    )
                    self.assertEqual(resp.status, 503)
                    self.assertEqual(resp.headers.get("Retry-After"), "60")

    async def test_cdn_domain_from_server_extra_data_succeeds_without_env(self):
        now = datetime.now(timezone.utc)
        sub = WhiteInternetSubscription(
            id=1,
            user_id=10,
            origin_node_id=1,
            token="valid-token-1234567890abcdef",
            uuid="a2b9d4e1-73c5-4812-b964-f3e7b85a1902",
            status=WhiteInternetStatus.ACTIVE,
            started_at=now,
            expires_at=now + timedelta(days=30),
            traffic_limit_bytes=53687091200,
            traffic_used_bytes=0,
            traffic_uplink_bytes=0,
            traffic_downlink_bytes=0,
            last_uplink_snapshot=0,
            last_downlink_snapshot=0,
            desired_version=1,
            actual_version=1,
            last_reconciled_node_epoch="epoch-xyz",
            device_limit=1,
            active_hwids={},
        )

        server = Server(
            id=1,
            name="Origin-Node",
            protocol=XRAY_PROTOCOL,
            api_url="https://origin.example.com:8444",
            xray_instance_epoch="epoch-xyz",
            capabilities=["xray_origin"],
            is_active=True,
            health_state=ServerHealthState.ONLINE,
            extra_data={"cdn_domain": "cdn.db-origin.best", "secret_base_path": "/w_custom"},
        )

        mock_session = AsyncMock()
        mock_session.scalar.return_value = server
        mock_session.execute.return_value = MagicMock(scalar_one_or_none=lambda: server)
        mock_session.get.return_value = sub

        @asynccontextmanager
        async def fake_session_scope():
            yield mock_session

        with patch.dict(os.environ, {}, clear=True):
            with patch("bot.handlers.white_internet_web.session_scope", fake_session_scope):
                with patch("database.repositories.white_internet_repo.get_subscription_by_token", return_value=sub):
                    resp = await self.client.get(
                        "/sub/wl/valid-token-1234567890abcdef",
                        headers={"X-Hwid": "test-device-hwid"},
                    )
                    self.assertEqual(resp.status, 200)
                    body_b64 = await resp.text()
                    decoded_lines = base64.b64decode(body_b64).decode("utf-8").splitlines()
                    self.assertEqual(len(decoded_lines), 1)
                    wl_url = decoded_lines[0]
                    self.assertTrue(wl_url.startswith(f"vless://{sub.uuid}@cdn.db-origin.best:443"))
                    self.assertIn("/w_custom/default", unquote(wl_url))

    async def test_offline_server_returns_503_fail_closed(self):
        now = datetime.now(timezone.utc)
        sub = WhiteInternetSubscription(
            id=1,
            user_id=10,
            origin_node_id=1,
            token="valid-token-1234567890abcdef",
            uuid="a2b9d4e1-73c5-4812-b964-f3e7b85a1902",
            status=WhiteInternetStatus.ACTIVE,
            started_at=now,
            expires_at=now + timedelta(days=30),
            traffic_limit_bytes=53687091200,
            desired_version=1,
            actual_version=1,
            last_reconciled_node_epoch="epoch-xyz",
        )

        # Server is marked PROBLEM
        server = Server(
            id=1,
            name="Origin-Node",
            protocol=XRAY_PROTOCOL,
            api_url="https://cdn.example.com:8444",
            xray_instance_epoch="epoch-xyz",
            capabilities=["xray_origin"],
            is_active=True,
            health_state=ServerHealthState.PROBLEM,
        )

        mock_session = AsyncMock()
        mock_session.scalar.return_value = server

        @asynccontextmanager
        async def fake_session_scope():
            yield mock_session

        with patch("bot.handlers.white_internet_web.session_scope", fake_session_scope):
            with patch("database.repositories.white_internet_repo.get_subscription_by_token", return_value=sub):
                resp = await self.client.get(
                    "/sub/wl/valid-token-1234567890abcdef",
                    headers={"X-Hwid": "test-device-id"},
                )
                self.assertEqual(resp.status, 503)
                self.assertEqual(resp.headers.get("Retry-After"), "5")

    async def test_banned_user_returns_403_forbidden(self):
        now = datetime.now(timezone.utc)
        sub = WhiteInternetSubscription(
            id=1,
            user_id=10,
            origin_node_id=1,
            token="valid-token-1234567890abcdef",
            uuid="a2b9d4e1-73c5-4812-b964-f3e7b85a1902",
            status=WhiteInternetStatus.ACTIVE,
            started_at=now,
            expires_at=now + timedelta(days=30),
            traffic_limit_bytes=53687091200,
            desired_version=1,
            actual_version=1,
            last_reconciled_node_epoch="epoch-xyz",
        )
        banned_user = User(id=10, telegram_id=777, is_banned=True, is_deleted=False)

        mock_session = AsyncMock()

        @asynccontextmanager
        async def fake_session_scope():
            yield mock_session

        with patch("bot.handlers.white_internet_web.session_scope", fake_session_scope):
            with patch("database.repositories.white_internet_repo.get_subscription_by_token", return_value=sub):
                with patch("database.repositories.users_repo.get_user_by_id", new=AsyncMock(return_value=banned_user)):
                    resp = await self.client.get("/sub/wl/valid-token-1234567890abcdef")
                    self.assertEqual(resp.status, 403)
                    self.assertEqual(await resp.text(), "Forbidden")

    async def test_deleted_user_returns_403_forbidden(self):
        now = datetime.now(timezone.utc)
        sub = WhiteInternetSubscription(
            id=1,
            user_id=10,
            origin_node_id=1,
            token="valid-token-1234567890abcdef",
            uuid="a2b9d4e1-73c5-4812-b964-f3e7b85a1902",
            status=WhiteInternetStatus.ACTIVE,
            started_at=now,
            expires_at=now + timedelta(days=30),
            traffic_limit_bytes=53687091200,
            desired_version=1,
            actual_version=1,
            last_reconciled_node_epoch="epoch-xyz",
        )
        deleted_user = User(id=10, telegram_id=777, is_banned=False, is_deleted=True)

        mock_session = AsyncMock()

        @asynccontextmanager
        async def fake_session_scope():
            yield mock_session

        with patch("bot.handlers.white_internet_web.session_scope", fake_session_scope):
            with patch("database.repositories.white_internet_repo.get_subscription_by_token", return_value=sub):
                with patch("database.repositories.users_repo.get_user_by_id", new=AsyncMock(return_value=deleted_user)):
                    resp = await self.client.get("/sub/wl/valid-token-1234567890abcdef")
                    self.assertEqual(resp.status, 403)
                    self.assertEqual(await resp.text(), "Forbidden")

    async def test_awg_server_with_xray_origin_capability_returns_503_and_blocks_vless_feed(self):
        """Server with protocol='amneziawg2' and capabilities=['xray_origin'] must return 503 and block Xray VLESS generation."""
        now = datetime.now(timezone.utc)
        sub = WhiteInternetSubscription(
            id=1,
            user_id=10,
            origin_node_id=1,
            token="valid-token-1234567890abcdef",
            uuid="a2b9d4e1-73c5-4812-b964-f3e7b85a1902",
            status=WhiteInternetStatus.ACTIVE,
            started_at=now,
            expires_at=now + timedelta(days=30),
            traffic_limit_bytes=53687091200,
            desired_version=1,
            actual_version=1,
            last_reconciled_node_epoch="epoch-xyz",
        )
        awg_server = Server(
            id=1,
            name="AWG-Node",
            protocol="amneziawg2",
            capabilities=["xray_origin"],  # Stale capability on non-xray server
            api_url="https://awg.example.com:8443",
            api_key="secret",
            is_active=True,
            health_state=ServerHealthState.ONLINE,
            extra_data={"cdn_domain": "cdn.example.com"},
            xray_instance_epoch="epoch-xyz",
        )

        mock_session = AsyncMock()
        mock_session.scalar.return_value = awg_server

        @asynccontextmanager
        async def fake_session_scope():
            yield mock_session

        with (
            patch("bot.handlers.white_internet_web.session_scope", fake_session_scope),
            patch("database.repositories.white_internet_repo.get_subscription_by_token", return_value=sub),
            patch("services.white_internet_service.WhiteInternetService.generate_vless_links") as mock_generate,
        ):
            resp = await self.client.get(
                "/sub/wl/valid-token-1234567890abcdef",
                headers={"X-Hwid": "test-device-id"},
            )
            self.assertEqual(resp.status, 503)
            self.assertIn("Retry-After", resp.headers)
            # VLESS link generation must never be invoked for an AWG server!
            mock_generate.assert_not_called()

    async def test_feed_handles_malformed_relays_and_hidden_gracefully(self):
        now = datetime.now(timezone.utc)
        sub = WhiteInternetSubscription(
            id=1,
            user_id=10,
            origin_node_id=1,
            token="token-malformed-relays-test-12345",
            uuid="11111111-2222-3333-4444-555555555555",
            status=WhiteInternetStatus.ACTIVE,
            started_at=now,
            expires_at=now + timedelta(days=30),
            traffic_limit_bytes=53687091200,
            traffic_used_bytes=1000,
            traffic_uplink_bytes=500,
            traffic_downlink_bytes=500,
            last_uplink_snapshot=500,
            last_downlink_snapshot=500,
            desired_version=1,
            actual_version=1,
            last_reconciled_node_epoch="epoch-xyz",
            device_limit=1,
            active_hwids={},
        )
        server = Server(
            id=1,
            name="Origin-Node",
            protocol=XRAY_PROTOCOL,
            api_url="https://cdn.example.com:8444",
            xray_instance_epoch="epoch-xyz",
            capabilities=["xray_origin"],
            is_active=True,
            health_state=ServerHealthState.ONLINE,
            extra_data={
                "relays": "malformed_string_not_list",
                "relay_hidden": "de,nl",
            },
        )
        mock_session = AsyncMock()
        mock_session.scalar.return_value = server
        mock_session.execute.return_value = MagicMock(scalar_one_or_none=lambda: server)
        mock_session.get.return_value = sub

        @asynccontextmanager
        async def fake_session_scope():
            yield mock_session

        with patch.dict(os.environ, {"WHITE_INTERNET_CDN_DOMAIN": "cdn.example.com"}):
            with patch("bot.handlers.white_internet_web.session_scope", fake_session_scope):
                with patch("database.repositories.white_internet_repo.get_subscription_by_token", return_value=sub):
                    resp = await self.client.get(
                        "/sub/wl/token-malformed-relays-test-12345",
                        headers={"X-Hwid": "test-device-hwid"},
                    )
                    self.assertEqual(resp.status, 200)
                    body = await resp.text()
                    self.assertTrue(len(body) > 0)

    async def test_active_sub_graceful_degradation_during_server_maintenance(self):
        """Verify active subscription receives config and maintenance banner when server is inactive."""
        now = datetime.now(timezone.utc)
        sub = WhiteInternetSubscription(
            id=42,
            user_id=10,
            origin_node_id=1,
            token="active-sub-token-maintenance-12345",
            status=WhiteInternetStatus.ACTIVE,
            provisioning_status=WhiteInternetProvisioningStatus.ACTIVE,
            expires_at=now + timedelta(days=15),
            base_traffic_bytes=50 * 1024 * 1024 * 1024,
            traffic_used_bytes=1024,
            traffic_uplink_bytes=500,
            traffic_downlink_bytes=500,
            desired_version=1,
            actual_version=1,
            last_reconciled_node_epoch="epoch-xyz",
            device_limit=1,
            active_hwids={},
        )
        server = Server(
            id=1,
            name="Origin-Node",
            protocol=XRAY_PROTOCOL,
            api_url="https://cdn.example.com:8444",
            xray_instance_epoch="epoch-xyz",
            capabilities=["xray_origin"],
            is_active=False,  # Server disabled for maintenance!
            health_state=ServerHealthState.MANUAL_DISABLED,
            extra_data={"cdn_domain": "cdn.example.com"},
        )
        mock_session = AsyncMock()
        mock_session.scalar.return_value = server
        mock_session.execute.return_value = MagicMock(scalar_one_or_none=lambda: server)
        mock_session.get.return_value = sub

        @asynccontextmanager
        async def fake_session_scope():
            yield mock_session

        with patch("bot.handlers.white_internet_web.session_scope", fake_session_scope):
            with patch("database.repositories.white_internet_repo.get_subscription_by_token", return_value=sub):
                resp = await self.client.get(
                    "/sub/wl/active-sub-token-maintenance-12345",
                    headers={"X-Hwid": "test-device-hwid"},
                )
                self.assertEqual(resp.status, 200)
                body = await resp.text()
                self.assertTrue(len(body) > 0)
                announce_header = resp.headers.get("Announce", "")
                self.assertTrue(announce_header.startswith("base64:"))
                decoded_announce = base64.b64decode(announce_header.replace("base64:", "")).decode("utf-8")
                self.assertIn("техническом обслуживании", decoded_announce)

    async def test_active_sub_with_wrong_protocol_strictly_fails_503(self):
        """Verify active subscription fails closed with 503 if origin server is not XRAY."""
        now = datetime.now(timezone.utc)
        sub = WhiteInternetSubscription(
            id=43,
            user_id=10,
            origin_node_id=1,
            token="active-sub-wrong-proto-12345",
            status=WhiteInternetStatus.ACTIVE,
            provisioning_status=WhiteInternetProvisioningStatus.ACTIVE,
            expires_at=now + timedelta(days=15),
            base_traffic_bytes=50 * 1024 * 1024 * 1024,
            desired_version=1,
            actual_version=1,
            last_reconciled_node_epoch="epoch-xyz",
            device_limit=1,
            active_hwids={},
        )
        server = Server(
            id=1,
            name="AWG-Node",
            protocol="amneziawg2",  # Wrong protocol!
            api_url="https://awg.example.com:8444",
            xray_instance_epoch="epoch-xyz",
            capabilities=["xray_origin"],
            is_active=True,
            health_state=ServerHealthState.ONLINE,
            extra_data={"cdn_domain": "cdn.example.com"},
        )
        mock_session = AsyncMock()
        mock_session.scalar.return_value = server

        @asynccontextmanager
        async def fake_session_scope():
            yield mock_session

        with patch("bot.handlers.white_internet_web.session_scope", fake_session_scope):
            with patch("database.repositories.white_internet_repo.get_subscription_by_token", return_value=sub):
                resp = await self.client.get(
                    "/sub/wl/active-sub-wrong-proto-12345",
                    headers={"X-Hwid": "test-device-id"},
                )
                self.assertEqual(resp.status, 503)
                self.assertEqual(resp.headers.get("Retry-After"), "5")

    async def test_active_sub_with_epoch_mismatch_strictly_fails_503(self):
        """Verify active subscription fails closed with 503 if node epoch drifted."""
        now = datetime.now(timezone.utc)
        sub = WhiteInternetSubscription(
            id=44,
            user_id=10,
            origin_node_id=1,
            token="active-sub-epoch-mismatch-12345",
            status=WhiteInternetStatus.ACTIVE,
            provisioning_status=WhiteInternetProvisioningStatus.ACTIVE,
            expires_at=now + timedelta(days=15),
            base_traffic_bytes=50 * 1024 * 1024 * 1024,
            desired_version=1,
            actual_version=1,
            last_reconciled_node_epoch="old-epoch",  # Node was reset!
            device_limit=1,
            active_hwids={},
        )
        server = Server(
            id=1,
            name="Origin-Node",
            protocol=XRAY_PROTOCOL,
            api_url="https://cdn.example.com:8444",
            xray_instance_epoch="new-epoch-restarted",
            capabilities=["xray_origin"],
            is_active=True,
            health_state=ServerHealthState.ONLINE,
            extra_data={"cdn_domain": "cdn.example.com"},
        )
        mock_session = AsyncMock()
        mock_session.scalar.return_value = server

        @asynccontextmanager
        async def fake_session_scope():
            yield mock_session

        with patch("bot.handlers.white_internet_web.session_scope", fake_session_scope):
            with patch("database.repositories.white_internet_repo.get_subscription_by_token", return_value=sub):
                resp = await self.client.get(
                    "/sub/wl/active-sub-epoch-mismatch-12345",
                    headers={"X-Hwid": "test-device-id"},
                )
                self.assertEqual(resp.status, 503)
                self.assertEqual(resp.headers.get("Retry-After"), "5")

    async def test_active_sub_with_version_mismatch_strictly_fails_503(self):
        """Verify active subscription fails closed with 503 if versions have not converged."""
        now = datetime.now(timezone.utc)
        sub = WhiteInternetSubscription(
            id=45,
            user_id=10,
            origin_node_id=1,
            token="active-sub-version-mismatch-12345",
            status=WhiteInternetStatus.ACTIVE,
            provisioning_status=WhiteInternetProvisioningStatus.ACTIVE,
            expires_at=now + timedelta(days=15),
            base_traffic_bytes=50 * 1024 * 1024 * 1024,
            desired_version=2,  # Drift / incomplete reconciliation
            actual_version=1,
            last_reconciled_node_epoch="epoch-xyz",
            device_limit=1,
            active_hwids={},
        )
        server = Server(
            id=1,
            name="Origin-Node",
            protocol=XRAY_PROTOCOL,
            api_url="https://cdn.example.com:8444",
            xray_instance_epoch="epoch-xyz",
            capabilities=["xray_origin"],
            is_active=True,
            health_state=ServerHealthState.ONLINE,
            extra_data={"cdn_domain": "cdn.example.com"},
        )
        mock_session = AsyncMock()
        mock_session.scalar.return_value = server

        @asynccontextmanager
        async def fake_session_scope():
            yield mock_session

        with patch("bot.handlers.white_internet_web.session_scope", fake_session_scope):
            with patch("database.repositories.white_internet_repo.get_subscription_by_token", return_value=sub):
                resp = await self.client.get(
                    "/sub/wl/active-sub-version-mismatch-12345",
                    headers={"X-Hwid": "test-device-id"},
                )
                self.assertEqual(resp.status, 503)
                self.assertEqual(resp.headers.get("Retry-After"), "5")

    async def test_active_sub_with_problem_health_state_receives_maintenance_announce(self):
        """Verify active subscription gets 200 and maintenance banner when server is in PROBLEM state."""
        now = datetime.now(timezone.utc)
        sub = WhiteInternetSubscription(
            id=46,
            user_id=10,
            origin_node_id=1,
            token="active-sub-problem-state-12345",
            status=WhiteInternetStatus.ACTIVE,
            provisioning_status=WhiteInternetProvisioningStatus.ACTIVE,
            expires_at=now + timedelta(days=15),
            base_traffic_bytes=50 * 1024 * 1024 * 1024,
            desired_version=1,
            actual_version=1,
            last_reconciled_node_epoch="epoch-xyz",
            device_limit=1,
            active_hwids={},
        )
        server = Server(
            id=1,
            name="Origin-Node",
            protocol=XRAY_PROTOCOL,
            api_url="https://cdn.example.com:8444",
            xray_instance_epoch="epoch-xyz",
            capabilities=["xray_origin"],
            is_active=True,
            health_state=ServerHealthState.PROBLEM,  # Monitoring detected node outage!
            extra_data={"cdn_domain": "cdn.example.com"},
        )
        mock_session = AsyncMock()
        mock_session.scalar.return_value = server
        mock_session.execute.return_value = MagicMock(scalar_one_or_none=lambda: server)
        mock_session.get.return_value = sub

        @asynccontextmanager
        async def fake_session_scope():
            yield mock_session

        with patch("bot.handlers.white_internet_web.session_scope", fake_session_scope):
            with patch("database.repositories.white_internet_repo.get_subscription_by_token", return_value=sub):
                resp = await self.client.get(
                    "/sub/wl/active-sub-problem-state-12345",
                    headers={"X-Hwid": "test-device-hwid"},
                )
                self.assertEqual(resp.status, 200)
                announce_header = resp.headers.get("Announce", "")
                self.assertTrue(announce_header.startswith("base64:"))
                decoded_announce = base64.b64decode(announce_header.replace("base64:", "")).decode("utf-8")
                self.assertIn("техническом обслуживании", decoded_announce)

    async def test_active_sub_with_auto_disabled_health_state_receives_maintenance_announce(self):
        """Verify active subscription gets 200 and maintenance banner when server is in AUTO_DISABLED state."""
        now = datetime.now(timezone.utc)
        sub = WhiteInternetSubscription(
            id=47,
            user_id=10,
            origin_node_id=1,
            token="active-sub-auto-disabled-12345",
            status=WhiteInternetStatus.ACTIVE,
            provisioning_status=WhiteInternetProvisioningStatus.ACTIVE,
            expires_at=now + timedelta(days=15),
            base_traffic_bytes=50 * 1024 * 1024 * 1024,
            desired_version=1,
            actual_version=1,
            last_reconciled_node_epoch="epoch-xyz",
            device_limit=1,
            active_hwids={},
        )
        server = Server(
            id=1,
            name="Origin-Node",
            protocol=XRAY_PROTOCOL,
            api_url="https://cdn.example.com:8444",
            xray_instance_epoch="epoch-xyz",
            capabilities=["xray_origin"],
            is_active=False,
            health_state=ServerHealthState.AUTO_DISABLED,
            extra_data={"cdn_domain": "cdn.example.com"},
        )
        mock_session = AsyncMock()
        mock_session.scalar.return_value = server
        mock_session.execute.return_value = MagicMock(scalar_one_or_none=lambda: server)
        mock_session.get.return_value = sub

        @asynccontextmanager
        async def fake_session_scope():
            yield mock_session

        with patch("bot.handlers.white_internet_web.session_scope", fake_session_scope):
            with patch("database.repositories.white_internet_repo.get_subscription_by_token", return_value=sub):
                resp = await self.client.get(
                    "/sub/wl/active-sub-auto-disabled-12345",
                    headers={"X-Hwid": "test-device-hwid"},
                )
                self.assertEqual(resp.status, 200)
                announce_header = resp.headers.get("Announce", "")
                self.assertTrue(announce_header.startswith("base64:"))
                decoded_announce = base64.b64decode(announce_header.replace("base64:", "")).decode("utf-8")
                self.assertIn("техническом обслуживании", decoded_announce)

    async def test_mobile_incy_and_happ_with_hwid_receive_service_notice_on_expired(self):
        now = datetime.now(timezone.utc)
        sub = MagicMock(spec=WhiteInternetSubscription)
        sub.id = 50
        sub.user_id = 10
        sub.origin_node_id = 1
        sub.token = "expired-mobile-1234567890abcdef"
        sub.status = WhiteInternetStatus.EXPIRED
        sub.expires_at = now - timedelta(days=1)
        sub.traffic_limit_bytes = 53687091200
        sub.traffic_used_bytes = 0
        sub.traffic_overage_bytes = 0
        sub.traffic_uplink_bytes = 0
        sub.traffic_downlink_bytes = 0

        mock_session = AsyncMock()
        mock_user = MagicMock(is_banned=False, is_deleted=False)

        @asynccontextmanager
        async def fake_session_scope():
            yield mock_session

        # INCY on Android includes "Android" in UA; Happ on iOS includes "iPhone" in UA
        user_agents = [
            "INCY/2.4.1 (Linux; U; Android 14; SM-S918B Build/UP1A.231005.007)",
            "Happ/1.8.0 (iPhone; iOS 17.5.1; Scale/3.00)",
        ]

        for ua in user_agents:
            with patch("bot.handlers.white_internet_web.session_scope", fake_session_scope):
                with patch("database.repositories.users_repo.get_user_by_id", new_callable=AsyncMock, return_value=mock_user):
                    with patch("database.repositories.white_internet_repo.get_subscription_by_token", return_value=sub):
                        resp = await self.client.get(
                            f"/sub/wl/{sub.token}",
                            headers={
                                "User-Agent": ua,
                                "X-Hwid": "mobile-hwid-device-123",
                                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                            },
                        )
                        self.assertEqual(resp.status, 200, f"Failed for UA: {ua}")
                        self.assertIn("text/plain", resp.headers.get("Content-Type", ""))
                        self.assertNotIn("text/html", resp.headers.get("Content-Type", ""))
                        text_payload = await resp.text()
                        decoded_links = base64.b64decode(text_payload).decode("utf-8")
                        self.assertIn("vless://00000000-0000-0000-0000-000000000000", decoded_links)
                        self.assertIn("Profile-Title", resp.headers)

    async def test_mobile_happ_with_hwid_receives_service_notice_on_disabled(self):
        now = datetime.now(timezone.utc)
        sub = MagicMock(spec=WhiteInternetSubscription)
        sub.id = 51
        sub.user_id = 10
        sub.origin_node_id = 1
        sub.token = "disabled-mobile-1234567890abcdef"
        sub.status = WhiteInternetStatus.DISABLED
        sub.expires_at = now + timedelta(days=10)
        sub.traffic_limit_bytes = 53687091200
        sub.traffic_used_bytes = 0
        sub.traffic_overage_bytes = 0
        sub.traffic_uplink_bytes = 0
        sub.traffic_downlink_bytes = 0

        mock_session = AsyncMock()
        mock_user = MagicMock(is_banned=False, is_deleted=False)

        @asynccontextmanager
        async def fake_session_scope():
            yield mock_session

        with patch("bot.handlers.white_internet_web.session_scope", fake_session_scope):
            with patch("database.repositories.users_repo.get_user_by_id", new_callable=AsyncMock, return_value=mock_user):
                with patch("database.repositories.white_internet_repo.get_subscription_by_token", return_value=sub):
                    resp = await self.client.get(
                        f"/sub/wl/{sub.token}",
                        headers={
                            "User-Agent": "Happ/1.8.0 (iPhone; iOS 17.5.1; Scale/3.00)",
                            "X-Device-Hwid": "mobile-happ-hwid-456",
                        },
                    )
                    self.assertEqual(resp.status, 200)
                    self.assertIn("text/plain", resp.headers.get("Content-Type", ""))
                    self.assertNotIn("text/html", resp.headers.get("Content-Type", ""))
                    text_payload = await resp.text()
                    decoded_links = base64.b64decode(text_payload).decode("utf-8")
                    self.assertIn("vless://00000000-0000-0000-0000-000000000000", decoded_links)

    async def test_mobile_incy_with_hwid_receives_service_notice_on_exhausted(self):
        now = datetime.now(timezone.utc)
        sub = MagicMock(spec=WhiteInternetSubscription)
        sub.id = 52
        sub.user_id = 10
        sub.origin_node_id = 1
        sub.token = "exhausted-mobile-123456789abcdef"
        sub.status = WhiteInternetStatus.EXHAUSTED
        sub.expires_at = now + timedelta(days=10)
        sub.traffic_limit_bytes = 53687091200
        sub.traffic_used_bytes = 53687091200
        sub.traffic_overage_bytes = 0
        sub.traffic_uplink_bytes = 0
        sub.traffic_downlink_bytes = 0

        mock_session = AsyncMock()
        mock_user = MagicMock(is_banned=False, is_deleted=False)

        @asynccontextmanager
        async def fake_session_scope():
            yield mock_session

        with patch("bot.handlers.white_internet_web.session_scope", fake_session_scope):
            with patch("database.repositories.users_repo.get_user_by_id", new_callable=AsyncMock, return_value=mock_user):
                with patch("database.repositories.white_internet_repo.get_subscription_by_token", return_value=sub):
                    resp = await self.client.get(
                        f"/sub/wl/{sub.token}",
                        headers={
                            "User-Agent": "INCY/2.4.1 (Linux; U; Android 14; Pixel 8)",
                            "X-Device-Id": "mobile-pixel-hwid-789",
                        },
                    )
                    self.assertEqual(resp.status, 200)
                    self.assertIn("text/plain", resp.headers.get("Content-Type", ""))
                    text_payload = await resp.text()
                    decoded_links = base64.b64decode(text_payload).decode("utf-8")
                    self.assertIn("vless://00000000-0000-0000-0000-000000000000", decoded_links)

    async def test_mobile_incy_with_hwid_receives_503_on_pending(self):
        sub = MagicMock(spec=WhiteInternetSubscription)
        sub.id = 53
        sub.user_id = 10
        sub.origin_node_id = 1
        sub.token = "pending-mobile-1234567890abcdef"
        sub.status = WhiteInternetStatus.PENDING

        mock_session = AsyncMock()
        mock_user = MagicMock(is_banned=False, is_deleted=False)

        @asynccontextmanager
        async def fake_session_scope():
            yield mock_session

        with patch("bot.handlers.white_internet_web.session_scope", fake_session_scope):
            with patch("database.repositories.users_repo.get_user_by_id", new_callable=AsyncMock, return_value=mock_user):
                with patch("database.repositories.white_internet_repo.get_subscription_by_token", return_value=sub):
                    resp = await self.client.get(
                        f"/sub/wl/{sub.token}",
                        headers={
                            "User-Agent": "INCY/2.4.1 (Linux; U; Android 14; SM-S918B)",
                            "X-Hwid": "mobile-hwid-pending-123",
                        },
                    )
                    self.assertEqual(resp.status, 503)
                    self.assertEqual(resp.headers.get("Retry-After"), "5")
                    self.assertNotIn("text/html", resp.headers.get("Content-Type", ""))

    async def test_pending_status_browser_receives_auto_refresh_html(self):
        sub = MagicMock(spec=WhiteInternetSubscription)
        sub.status = WhiteInternetStatus.PENDING

        mock_session = AsyncMock()
        @asynccontextmanager
        async def fake_session_scope():
            yield mock_session

        with patch("bot.handlers.white_internet_web.session_scope", fake_session_scope):
            with patch("database.repositories.white_internet_repo.get_subscription_by_token", return_value=sub):
                resp = await self.client.get(
                    "/sub/wl/pending-token-1234567890abcdef",
                    headers={
                        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                        "Accept": "text/html,application/xhtml+xml",
                    },
                )
                self.assertEqual(resp.status, 200)
                self.assertIn("text/html", resp.headers.get("Content-Type", ""))
                html_text = await resp.text()
                self.assertIn('<meta http-equiv="refresh" content="5">', html_text)
                self.assertIn("Настройка шлюза", html_text)
                self.assertIn("Перейти в бота", html_text)

    async def test_is_browser_request_identifies_mobile_and_hwid(self):
        from aiohttp.test_utils import make_mocked_request
        from bot.handlers.white_internet_web import _is_browser_request

        # Mobile and API clients without HWID are NEVER browsers, even with Accept: text/html
        api_uas = [
            "INCY/2.4.1 (Linux; U; Android 14; SM-S918B Build/UP1A.231005.007)",
            "Happ/1.8.0 (iPhone; iOS 17.5.1; Scale/3.00)",
            "curl/8.4.0",
            "xray/1.8.4",
            "sing-box 1.8.11",
            "clash-meta/v1.18.0",
        ]
        for ua in api_uas:
            for accept_val in ("*/*", "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"):
                req = make_mocked_request(
                    "GET",
                    "/sub/wl/test-token",
                    headers={
                        "User-Agent": ua,
                        "Accept": accept_val,
                    },
                )
                self.assertFalse(
                    _is_browser_request(req),
                    f"Expected API client not browser for UA: {ua} with Accept: {accept_val}",
                )

        # True browsers without HWID are browsers
        browser_uas = [
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
            "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5_1 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1",
        ]
        for bua in browser_uas:
            req = make_mocked_request(
                "GET",
                "/sub/wl/test-token",
                headers={
                    "User-Agent": bua,
                    "Accept": "text/html,application/xhtml+xml",
                },
            )
            self.assertTrue(_is_browser_request(req), f"Expected browser for UA: {bua}")

        # HWID presence overrides everything, even for browser UA
        req_with_hwid = make_mocked_request(
            "GET",
            "/sub/wl/test-token",
            headers={
                "User-Agent": browser_uas[0],
                "Accept": "text/html",
                "X-Hwid": "test-device-hwid-123",
            },
        )
        self.assertFalse(_is_browser_request(req_with_hwid))

    async def test_active_sub_mobile_client_without_hwid_gets_403_even_with_html_accept(self):
        now = datetime.now(timezone.utc)
        sub = WhiteInternetSubscription(
            id=1,
            user_id=10,
            origin_node_id=1,
            token="valid-token-active-no-hwid-test",
            uuid="a2b9d4e1-73c5-4812-b964-f3e7b85a1902",
            status=WhiteInternetStatus.ACTIVE,
            started_at=now,
            expires_at=now + timedelta(days=30),
            traffic_limit_bytes=53687091200,
            desired_version=1,
            actual_version=1,
            last_reconciled_node_epoch="epoch-xyz",
        )
        server = Server(
            id=1,
            name="Origin-Node",
            protocol=XRAY_PROTOCOL,
            api_url="https://cdn.example.com:8444",
            xray_instance_epoch="epoch-xyz",
            capabilities=["xray_origin"],
            is_active=True,
            health_state=ServerHealthState.ONLINE,
        )
        mock_session = AsyncMock()
        mock_session.scalar.return_value = server
        mock_session.execute.return_value = MagicMock(scalar_one_or_none=lambda: server)
        mock_user = MagicMock(is_banned=False, is_deleted=False)

        @asynccontextmanager
        async def fake_session_scope():
            yield mock_session

        mobile_uas = [
            "INCY/2.4.1 (Linux; U; Android 14; SM-S918B Build/UP1A.231005.007)",
            "Happ/1.8.0 (iPhone; iOS 17.5.1; Scale/3.00)",
        ]

        for ua in mobile_uas:
            with patch.dict(os.environ, {"WHITE_INTERNET_CDN_DOMAIN": "cdn.example.com"}):
                with patch("bot.handlers.white_internet_web.session_scope", fake_session_scope):
                    with patch("database.repositories.users_repo.get_user_by_id", new_callable=AsyncMock, return_value=mock_user):
                        with patch("database.repositories.white_internet_repo.get_subscription_by_token", return_value=sub):
                            resp = await self.client.get(
                                f"/sub/wl/{sub.token}",
                                headers={
                                    "User-Agent": ua,
                                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                                },
                            )
                            self.assertEqual(resp.status, 403, f"Expected 403 for {ua}")
                            self.assertEqual(resp.headers.get("x-hwid-required"), "true")
                            self.assertEqual(await resp.text(), texts.WL_WEB_HWID_REQUIRED)

    async def test_exhausted_sub_trial_notice_vs_regular(self):
        now = datetime.now(timezone.utc)
        for is_trial, expected_phrase in (
            (True, "Пробный трафик исчерпан"),
            (False, "Трафик исчерпан. Докупите пакет"),
        ):
            sub = MagicMock(spec=WhiteInternetSubscription)
            sub.id = 1
            sub.user_id = 10
            sub.origin_node_id = 1
            sub.status = WhiteInternetStatus.EXHAUSTED
            sub.traffic_uplink_bytes = 1000
            sub.traffic_downlink_bytes = 2000
            sub.traffic_limit_bytes = 53687091200
            sub.expires_at = now + timedelta(days=10)
            sub.is_trial = is_trial

            mock_session = AsyncMock()

            @asynccontextmanager
            async def fake_session_scope(ms=mock_session):
                yield ms

            with patch("bot.handlers.white_internet_web.session_scope", fake_session_scope):
                with patch("database.repositories.white_internet_repo.get_subscription_by_token", return_value=sub):
                    resp = await self.client.get(
                        "/sub/wl/exhausted-token-sample-123",
                        headers={"X-Hwid": "test-device-123"},
                    )
                    self.assertEqual(resp.status, 200)
                    body = await resp.text()
                    decoded = base64.b64decode(body).decode("utf-8")
                    self.assertIn(expected_phrase, unquote(decoded))

    async def test_url_header_sanitization_removes_newlines_and_spaces(self):
        from bot.handlers.white_internet_web import _sanitize_header_url
        self.assertEqual(_sanitize_header_url("https://example.com/test\r\nInjected: true", fallback="https://default.com"), "https://default.com")
        self.assertEqual(_sanitize_header_url("https://example.com/test\tbad", fallback=""), "")
        self.assertEqual(_sanitize_header_url("  https://example.com/clean  "), "https://example.com/clean")
        self.assertEqual(_sanitize_header_url(None, fallback="https://default.com"), "https://default.com")
        self.assertEqual(_sanitize_header_url("", fallback="https://default.com"), "https://default.com")
        # Control characters and non-ASCII must be rejected
        self.assertEqual(_sanitize_header_url("https://example.com/test\x00bad", fallback="https://default.com"), "https://default.com")
        self.assertEqual(_sanitize_header_url("https://example.com/test\x0bbad", fallback="https://default.com"), "https://default.com")
        self.assertEqual(_sanitize_header_url("https://example.com/test\x1fbad", fallback="https://default.com"), "https://default.com")
        self.assertEqual(_sanitize_header_url("https://example.com/test\x7fbad", fallback="https://default.com"), "https://default.com")
        self.assertEqual(_sanitize_header_url("https://t.me/мой_канал", fallback="https://default.com"), "https://default.com")
        self.assertEqual(_sanitize_header_url("tg://resolve?domain=just1k13bot"), "tg://resolve?domain=just1k13bot")

    async def test_resolve_bot_username_retries_on_failure_without_caching_fallback(self):
        from bot.handlers.white_internet_web import _resolve_bot_username
        import bot.handlers.white_internet_web as wi_web

        orig_cached = wi_web._cached_bot_username
        try:
            wi_web._cached_bot_username = None

            mock_bot = AsyncMock()
            mock_bot.get_me.side_effect = RuntimeError("Telegram connection failed")

            mock_request = MagicMock()
            mock_request.app = {"bot": mock_bot}

            with patch.dict(os.environ, {"BOT_USERNAME": "env_bot"}):
                resolved = await _resolve_bot_username(mock_request)
                self.assertEqual(resolved, "env_bot")
                self.assertIsNone(wi_web._cached_bot_username)
                self.assertNotIn("bot_username", mock_request.app)

                me_mock = MagicMock()
                me_mock.username = "real_bot"
                mock_bot.get_me.side_effect = None
                mock_bot.get_me.return_value = me_mock

                resolved_success = await _resolve_bot_username(mock_request)
                self.assertEqual(resolved_success, "real_bot")
                self.assertEqual(wi_web._cached_bot_username, "real_bot")
                self.assertEqual(mock_request.app.get("bot_username"), "real_bot")
        finally:
            wi_web._cached_bot_username = orig_cached

