"""Unit tests for White Internet HTTP subscription feed endpoint (/sub/wl/{token})."""

import base64
import os
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch
from urllib.parse import unquote


from aiohttp import web
from aiohttp.test_utils import AioHTTPTestCase

from bot import texts
from bot.handlers.white_internet_web import setup_white_internet_web_routes
from config.enums import ServerHealthState, WhiteInternetStatus
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

    async def test_exhausted_status_returns_403(self):
        sub = MagicMock(spec=WhiteInternetSubscription)
        sub.id = 1
        sub.status = WhiteInternetStatus.EXHAUSTED
        sub.traffic_uplink_bytes = 1000
        sub.traffic_downlink_bytes = 2000
        sub.traffic_limit_bytes = 53687091200
        sub.expires_at = datetime(2026, 9, 30, 0, 0, tzinfo=timezone.utc)

        mock_session = AsyncMock()
        @asynccontextmanager
        async def fake_session_scope():
            yield mock_session

        with patch("bot.handlers.white_internet_web.session_scope", fake_session_scope):
            with patch("database.repositories.white_internet_repo.get_subscription_by_token", return_value=sub):
                resp = await self.client.get("/sub/wl/exhausted-token-1234567890abcdef")
                self.assertEqual(resp.status, 403)
                self.assertIn("upload=1000", resp.headers.get("Subscription-Userinfo", ""))
                self.assertIn("download=2000", resp.headers.get("Subscription-Userinfo", ""))

    async def test_expired_status_returns_403(self):
        now = datetime(2026, 8, 30, 12, 0, tzinfo=timezone.utc)
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
                self.assertEqual(resp.status, 403)
                text = await resp.text()
                self.assertEqual(text, texts.WL_WEB_EXPIRED)

    async def test_runtime_out_of_sync_returns_503(self):
        now = datetime(2026, 8, 30, 12, 0, tzinfo=timezone.utc)
        sub = MagicMock(spec=WhiteInternetSubscription)
        sub.id = 1
        sub.origin_node_id = 1
        sub.status = WhiteInternetStatus.ACTIVE
        sub.expires_at = now + timedelta(days=20)
        sub.desired_version = 2
        sub.actual_version = 1  # Not yet synced!

        server = Server(
            id=1,
            name="Origin-Node",
            api_url="https://cdn.just1k.online:8444",
            xray_instance_epoch="epoch_xyz",
            capabilities=["xray_origin"],
            is_active=True,
            health_state=ServerHealthState.ONLINE,
        )

        mock_session = AsyncMock()
        mock_session.scalar.return_value = server

        @asynccontextmanager
        async def fake_session_scope():
            yield mock_session

        with patch("bot.handlers.white_internet_web.session_scope", fake_session_scope):
            with patch("database.repositories.white_internet_repo.get_subscription_by_token", return_value=sub):
                resp = await self.client.get("/sub/wl/unsynced-token-1234567890abcdef")
                self.assertEqual(resp.status, 503)
                self.assertEqual(resp.headers.get("Retry-After"), "5")

    async def test_epoch_mismatch_returns_503(self):
        now = datetime(2026, 8, 30, 12, 0, tzinfo=timezone.utc)
        sub = MagicMock(spec=WhiteInternetSubscription)
        sub.id = 1
        sub.origin_node_id = 1
        sub.status = WhiteInternetStatus.ACTIVE
        sub.expires_at = now + timedelta(days=20)
        sub.desired_version = 1
        sub.actual_version = 1  # Versions match!
        sub.last_reconciled_node_epoch = "epoch-old"

        server = Server(
            id=1,
            name="Origin-Node",
            api_url="https://cdn.just1k.online:8444",
            xray_instance_epoch="epoch-new",  # Node rebooted / restarted!
            capabilities=["xray_origin"],
            is_active=True,
            health_state=ServerHealthState.ONLINE,
        )

        mock_session = AsyncMock()
        mock_session.scalar.return_value = server

        @asynccontextmanager
        async def fake_session_scope():
            yield mock_session

        with patch("bot.handlers.white_internet_web.session_scope", fake_session_scope):
            with patch("database.repositories.white_internet_repo.get_subscription_by_token", return_value=sub):
                resp = await self.client.get("/sub/wl/epoch-mismatch-token-1234567890")
                self.assertEqual(resp.status, 503)
                self.assertEqual(resp.headers.get("Retry-After"), "5")

    async def test_active_and_synced_returns_base64_vless_feed(self):
        now = datetime(2026, 8, 30, 12, 0, tzinfo=timezone.utc)
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
        )

        server = Server(
            id=1,
            name="Origin-Node",
            api_url="https://cdn.just1k.online:8444",
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

        with patch.dict(os.environ, {"WHITE_INTERNET_CDN_DOMAIN": "cdn.just1k.online"}):
            with patch("bot.handlers.white_internet_web.session_scope", fake_session_scope):
                with patch("database.repositories.white_internet_repo.get_subscription_by_token", return_value=sub):
                    resp = await self.client.get("/sub/wl/valid-token-1234567890abcdef")

                    self.assertEqual(resp.status, 200)
                    self.assertEqual(resp.headers.get("Content-Type"), "text/plain; charset=utf-8")
                    self.assertEqual(resp.headers.get("Profile-Title"), "base64:SnVzdDFrINCR0LXQu9GL0Lkg0JjQvdGC0LXRgNC90LXRgg==")
                    self.assertEqual(resp.headers.get("Profile-Update-Interval"), "6")
                    self.assertEqual(resp.headers.get("hide-url"), "1")
                    self.assertEqual(resp.headers.get("no-limit-enabled"), "1")
                    self.assertIn("upload=500", resp.headers.get("Subscription-Userinfo", ""))
                    self.assertIn("download=500", resp.headers.get("Subscription-Userinfo", ""))

                    body_b64 = await resp.text()
                    decoded_lines = base64.b64decode(body_b64).decode("utf-8").splitlines()
                    self.assertEqual(len(decoded_lines), 1)

                    wl_url = decoded_lines[0]
                    self.assertTrue(wl_url.startswith("vless://"))
                    self.assertIn("/stream/v1", unquote(wl_url))
                    self.assertIn("OPTIONS", unquote(wl_url))


    async def test_missing_cdn_domain_returns_503_fail_closed(self):
        now = datetime(2026, 8, 30, 12, 0, tzinfo=timezone.utc)
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
            api_url="https://cdn.just1k.online:8444",
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
                    resp = await self.client.get("/sub/wl/valid-token-1234567890abcdef")
                    self.assertEqual(resp.status, 503)
                    self.assertEqual(resp.headers.get("Retry-After"), "60")

    async def test_cdn_domain_from_server_extra_data_succeeds_without_env(self):
        now = datetime(2026, 8, 30, 12, 0, tzinfo=timezone.utc)
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
            api_url="https://origin.just1k.best:8444",
            xray_instance_epoch="epoch-xyz",
            capabilities=["xray_origin"],
            is_active=True,
            health_state=ServerHealthState.ONLINE,
            extra_data={"cdn_domain": "cdn.db-origin.best", "secret_base_path": "/w_custom"},
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
                    resp = await self.client.get("/sub/wl/valid-token-1234567890abcdef")
                    self.assertEqual(resp.status, 200)
                    body_b64 = await resp.text()
                    decoded_lines = base64.b64decode(body_b64).decode("utf-8").splitlines()
                    self.assertEqual(len(decoded_lines), 1)
                    wl_url = decoded_lines[0]
                    self.assertTrue(wl_url.startswith(f"vless://{sub.uuid}@cdn.db-origin.best:443"))
                    self.assertIn("/w_custom/default", unquote(wl_url))

    async def test_offline_server_returns_503_fail_closed(self):
        now = datetime(2026, 8, 30, 12, 0, tzinfo=timezone.utc)
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
            api_url="https://cdn.just1k.online:8444",
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
                resp = await self.client.get("/sub/wl/valid-token-1234567890abcdef")
                self.assertEqual(resp.status, 503)
                self.assertEqual(resp.headers.get("Retry-After"), "5")

    async def test_banned_user_returns_403_forbidden(self):
        now = datetime(2026, 8, 30, 12, 0, tzinfo=timezone.utc)
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
        now = datetime(2026, 8, 30, 12, 0, tzinfo=timezone.utc)
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
