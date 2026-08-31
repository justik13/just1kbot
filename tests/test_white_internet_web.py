"""Unit tests for White Internet HTTP subscription feed endpoint (/sub/wl/{token})."""

import base64
import os
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch
from urllib.parse import unquote


from aiohttp import web
from aiohttp.test_utils import AioHTTPTestCase, unittest_run_loop

from bot import texts
from bot.handlers.white_internet_web import setup_white_internet_web_routes
from config.enums import ServerHealthState, WhiteInternetStatus
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
        sub.id = 1
        sub.status = WhiteInternetStatus.EXHAUSTED
        sub.traffic_uplink_bytes = 1000
        sub.traffic_downlink_bytes = 2000
        sub.traffic_limit_bytes = 53687091200
        sub.expires_at = datetime(2026, 9, 30, 0, 0, tzinfo=timezone.utc)

        mock_grant = MagicMock()
        mock_grant.bytes_granted = 53687091200
        mock_grant.bytes_remaining = 0

        mock_session = AsyncMock()
        @asynccontextmanager
        async def fake_session_scope():
            yield mock_session

        with patch("bot.handlers.white_internet_web.session_scope", fake_session_scope):
            with patch("database.repositories.white_internet_repo.get_subscription_by_token", return_value=sub):
                with patch("database.repositories.white_internet_repo.get_period_grants", return_value=[mock_grant]):
                    resp = await self.client.get("/sub/wl/exhausted-token-1234567890abcdef")
                    self.assertEqual(resp.status, 403)
                    self.assertIn("upload=1000", resp.headers.get("Subscription-Userinfo", ""))
                    self.assertIn("download=2000", resp.headers.get("Subscription-Userinfo", ""))

    @unittest_run_loop
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

    @unittest_run_loop
    async def test_runtime_out_of_sync_returns_503(self):
        now = datetime(2026, 8, 30, 12, 0, tzinfo=timezone.utc)
        sub = MagicMock(spec=WhiteInternetSubscription)
        sub.id = 1
        sub.origin_node_id = 1
        sub.status = WhiteInternetStatus.ACTIVE
        sub.expires_at = now + timedelta(days=20)
        sub.desired_version = 2
        sub.actual_version = 1  # Not yet synced!

        mock_grant = MagicMock()
        mock_grant.bytes_granted = 53687091200
        mock_grant.bytes_remaining = 10 * 1024**3

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
                with patch("database.repositories.white_internet_repo.get_period_grants", return_value=[mock_grant]):
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

        mock_grant = MagicMock()
        mock_grant.bytes_granted = 53687091200
        mock_grant.bytes_remaining = 10 * 1024**3

        server = Server(
            id=1,
            name="Origin-Node",
            api_url="https://cdn.just1k.online:8444",
            xray_instance_epoch="epoch_new",  # Node restarted with new epoch!
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
                with patch("database.repositories.white_internet_repo.get_period_grants", return_value=[mock_grant]):
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
            traffic_uplink_bytes=500,
            traffic_downlink_bytes=500,
            last_uplink_snapshot=500,
            last_downlink_snapshot=500,
            desired_version=1,
            actual_version=1,
            last_reconciled_node_epoch="epoch-xyz",
        )

        grant = MagicMock()
        grant.bytes_granted = 53687091200
        grant.bytes_remaining = 40 * 1024**3

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
                    with patch("database.repositories.white_internet_repo.get_period_grants", return_value=[grant]):
                        resp = await self.client.get("/sub/wl/valid-token-1234567890abcdef")

                        self.assertEqual(resp.status, 200)
                        self.assertEqual(resp.headers.get("Content-Type"), "text/plain; charset=utf-8")
                        self.assertEqual(resp.headers.get("Profile-Title"), "base64:SnVzdDFrINCR0LXQu9GL0Lkg0JjQvdGC0LXRgNC90LXRgg==")
                        self.assertEqual(resp.headers.get("Profile-Update-Interval"), "12")
                        self.assertEqual(resp.headers.get("hide-url"), "1")
                        self.assertEqual(resp.headers.get("no-limit-enabled"), "1")
                        self.assertIn("upload=500", resp.headers.get("Subscription-Userinfo", ""))
                        self.assertIn("download=500", resp.headers.get("Subscription-Userinfo", ""))

                        body_b64 = await resp.text()
                        decoded_lines = base64.b64decode(body_b64).decode("utf-8").splitlines()
                        self.assertEqual(len(decoded_lines), 2)

                        de_url = decoded_lines[0]
                        nl_url = decoded_lines[1]
                        self.assertTrue(de_url.startswith("vless://"))
                        self.assertTrue(nl_url.startswith("vless://"))
                        self.assertIn("/stream/v1/de", unquote(de_url))
                        self.assertIn("/stream/v1/nl", unquote(nl_url))
                        self.assertIn("OPTIONS", unquote(de_url))


    @unittest_run_loop
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

        grant = MagicMock()
        grant.bytes_granted = 53687091200
        grant.bytes_remaining = 40 * 1024**3

        with patch.dict(os.environ, {}, clear=True):
            with patch("bot.handlers.white_internet_web.session_scope", fake_session_scope):
                with patch("database.repositories.white_internet_repo.get_subscription_by_token", return_value=sub):
                    with patch("database.repositories.white_internet_repo.get_period_grants", return_value=[grant]):
                        resp = await self.client.get("/sub/wl/valid-token-1234567890abcdef")
                        self.assertEqual(resp.status, 503)
                        self.assertEqual(resp.headers.get("Retry-After"), "60")

    @unittest_run_loop
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

        grant = MagicMock()
        grant.bytes_granted = 53687091200
        grant.bytes_remaining = 40 * 1024**3

        with patch("bot.handlers.white_internet_web.session_scope", fake_session_scope):
            with patch("database.repositories.white_internet_repo.get_subscription_by_token", return_value=sub):
                with patch("database.repositories.white_internet_repo.get_period_grants", return_value=[grant]):
                    resp = await self.client.get("/sub/wl/valid-token-1234567890abcdef")
                    self.assertEqual(resp.status, 503)
                    self.assertEqual(resp.headers.get("Retry-After"), "5")
