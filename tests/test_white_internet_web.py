"""Unit tests for White Internet HTTP subscription feed endpoint (/sub/wl/{token})."""

import base64
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch
from urllib.parse import unquote

from aiohttp import web
from aiohttp.test_utils import AioHTTPTestCase, unittest_run_loop

from bot.handlers.white_internet_web import setup_white_internet_web_routes
from config.enums import WhiteInternetStatus
from database.models import Server, WhiteInternetSubscription


class TestWhiteInternetWebFeed(AioHTTPTestCase):
    """Test suite for /sub/wl/{token} HTTP subscription feed."""

    async def get_application(self):
        app = web.Application()
        setup_white_internet_web_routes(app)
        return app

    @unittest_run_loop
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

    @unittest_run_loop
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

    @unittest_run_loop
    async def test_exhausted_status_returns_403(self):
        sub = MagicMock(spec=WhiteInternetSubscription)
        sub.status = WhiteInternetStatus.EXHAUSTED
        sub.last_uplink_snapshot = 1000
        sub.last_downlink_snapshot = 2000
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

    @unittest_run_loop
    async def test_runtime_out_of_sync_returns_503(self):
        now = datetime(2026, 8, 30, 12, 0, tzinfo=timezone.utc)
        sub = MagicMock(spec=WhiteInternetSubscription)
        sub.id = 1
        sub.status = WhiteInternetStatus.ACTIVE
        sub.expires_at = now + timedelta(days=20)
        sub.desired_version = 2
        sub.actual_version = 1  # Not yet synced!

        mock_session = AsyncMock()
        @asynccontextmanager
        async def fake_session_scope():
            yield mock_session

        with patch("bot.handlers.white_internet_web.session_scope", fake_session_scope):
            with patch("database.repositories.white_internet_repo.get_subscription_by_token", return_value=sub):
                with patch("database.repositories.white_internet_repo.get_available_quota_bytes", return_value=10 * 1024**3):
                    resp = await self.client.get("/sub/wl/unsynced-token-1234567890abcdef")
                    self.assertEqual(resp.status, 503)
                    self.assertEqual(resp.headers.get("Retry-After"), "5")

    @unittest_run_loop
    async def test_epoch_mismatch_returns_503(self):
        now = datetime(2026, 8, 30, 12, 0, tzinfo=timezone.utc)
        sub = MagicMock(spec=WhiteInternetSubscription)
        sub.id = 1
        sub.origin_node_id = 1
        sub.status = WhiteInternetStatus.ACTIVE
        sub.expires_at = now + timedelta(days=20)
        sub.desired_version = 2
        sub.actual_version = 2  # Versions match...
        sub.last_reconciled_node_epoch = "epoch_old"  # ...but epoch is old!

        server = Server(
            id=1,
            name="Origin-Node",
            api_url="https://cdn.just1k.online:8444",
            xray_instance_epoch="epoch_new",  # Node restarted with new epoch!
        )

        mock_session = AsyncMock()
        mock_session.execute.return_value = MagicMock(scalar_one_or_none=lambda: server)

        @asynccontextmanager
        async def fake_session_scope():
            yield mock_session

        with patch("bot.handlers.white_internet_web.session_scope", fake_session_scope):
            with patch("database.repositories.white_internet_repo.get_subscription_by_token", return_value=sub):
                with patch("database.repositories.white_internet_repo.get_available_quota_bytes", return_value=10 * 1024**3):
                    resp = await self.client.get("/sub/wl/epoch-mismatch-token-1234567890")
                    self.assertEqual(resp.status, 503)
                    self.assertEqual(resp.headers.get("Retry-After"), "5")


    @unittest_run_loop
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
            last_uplink_snapshot=500,
            last_downlink_snapshot=500,
            desired_version=1,
            actual_version=1,
        )

        server = Server(
            id=1,
            name="Origin-Node",
            api_url="https://cdn.just1k.online:8444",
        )

        mock_session = AsyncMock()
        mock_session.execute.return_value = MagicMock(scalar_one_or_none=lambda: server)

        @asynccontextmanager
        async def fake_session_scope():
            yield mock_session

        with patch("bot.handlers.white_internet_web.session_scope", fake_session_scope):
            with patch("database.repositories.white_internet_repo.get_subscription_by_token", return_value=sub):
                with patch("database.repositories.white_internet_repo.get_available_quota_bytes", return_value=40 * 1024**3):
                    resp = await self.client.get("/sub/wl/valid-token-1234567890abcdef")

                    self.assertEqual(resp.status, 200)
                    self.assertEqual(resp.headers.get("Content-Type"), "text/plain; charset=utf-8")
                    self.assertEqual(resp.headers.get("Profile-Update-Interval"), "6")
                    self.assertEqual(resp.headers.get("Hide-Url"), "1")
                    self.assertEqual(resp.headers.get("No-Limit-Enabled"), "1")

                    body_b64 = await resp.text()
                    decoded_lines = base64.b64decode(body_b64).decode("utf-8").splitlines()
                    self.assertEqual(len(decoded_lines), 2)

                    de_url = decoded_lines[0]
                    nl_url = decoded_lines[1]
                    self.assertTrue(de_url.startswith("vless://"))
                    self.assertTrue(nl_url.startswith("vless://"))
                    self.assertIn("/api/v3/de", unquote(de_url))
                    self.assertIn("/api/v3/nl", unquote(nl_url))
                    self.assertIn("OPTIONS", unquote(de_url))
