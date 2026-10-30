"""Unit and integration tests for standard VLESS subscriptions, HWID quotas, and feed endpoint."""

import base64
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
import json
import os
import unittest
import urllib.parse
from unittest.mock import AsyncMock, MagicMock, patch

from aiohttp import web
from aiohttp.test_utils import AioHTTPTestCase

from bot import texts
from bot.handlers.vless_web import setup_vless_web_routes
from config.enums import ServerHealthState
from database.models import Server, User, VlessSubscription
from database.repositories import vless_subscription_repo
from services.vless_subscription_service import VlessSubscriptionService
from utils.datetime_helpers import now_utc


class TestVlessSubscriptionWebFeed(AioHTTPTestCase):
    """Test suite for /sub/vless/{token} HTTP subscription feed."""

    def setUp(self):
        super().setUp()
        from bot.handlers import vless_web
        vless_web._ip_rate_limiter.buckets.clear()
        vless_web._token_rate_limiter.buckets.clear()
        from database.repositories import system_settings_repo
        system_settings_repo._SETTINGS_CACHE.clear()

        now = datetime.now(timezone.utc)
        self.default_user = User(
            id=42,
            telegram_id=999,
            is_banned=False,
            is_deleted=False,
            device_limit=2,
            subscription_end=now + timedelta(days=30),
        )
        self.default_sub = VlessSubscription(
            id=10,
            user_id=42,
            token="test-token-valid-length-12345678",
            uuid="11111111-2222-3333-4444-555555555555",
            is_active=True,
            active_hwids={},
        )
        self.default_server = Server(
            id=1,
            name="NL-Node-1",
            country_flag="🇳🇱",
            is_active=True,
            health_state=ServerHealthState.ONLINE,
            protocol="awg",
            capabilities=["awg", "vless"],
            api_url="https://nl.example.com:8443",
            extra_data={"domain": "nl.example.com", "vless_port": 443},
        )

    def tearDown(self):
        from bot.handlers import vless_web
        vless_web._ip_rate_limiter.buckets.clear()
        vless_web._token_rate_limiter.buckets.clear()
        from database.repositories import system_settings_repo
        system_settings_repo._SETTINGS_CACHE.clear()
        super().tearDown()

    async def get_application(self):
        app = web.Application()
        setup_vless_web_routes(app)
        return app

    async def test_short_token_returns_404(self):
        resp = await self.client.get("/sub/vless/short")
        self.assertEqual(resp.status, 404)

    async def test_nonexistent_token_returns_404(self):
        mock_session = AsyncMock()

        @asynccontextmanager
        async def fake_session_scope():
            yield mock_session

        with (
            patch("bot.handlers.vless_web.session_scope", fake_session_scope),
            patch("database.repositories.vless_subscription_repo.get_subscription_by_token", return_value=None),
        ):
            resp = await self.client.get("/sub/vless/nonexistent-token-12345678")
            self.assertEqual(resp.status, 404)

    async def test_inactive_subscription_returns_notice_200(self):
        mock_session = AsyncMock()

        @asynccontextmanager
        async def fake_session_scope():
            yield mock_session

        inactive_sub = VlessSubscription(
            id=11,
            user_id=42,
            token="inactive-token-12345678",
            uuid="11111111-2222-3333-4444-555555555555",
            is_active=False,
            active_hwids={},
        )
        user = User(
            id=42,
            telegram_id=999,
            is_banned=False,
            is_deleted=False,
            subscription_end=datetime.now(timezone.utc) + timedelta(days=5),
        )

        with (
            patch("bot.handlers.vless_web.session_scope", fake_session_scope),
            patch("database.repositories.vless_subscription_repo.get_subscription_by_token", return_value=inactive_sub),
            patch("database.repositories.users_repo.get_user_by_id", return_value=user),
        ):
            resp = await self.client.get("/sub/vless/inactive-token-12345678")
            self.assertEqual(resp.status, 200)
            body = await resp.text()
            decoded = base64.b64decode(body).decode("utf-8")
            self.assertIn(urllib.parse.quote("Подписка закончилась"), decoded)

    async def test_banned_user_returns_403(self):
        mock_session = AsyncMock()

        @asynccontextmanager
        async def fake_session_scope():
            yield mock_session

        banned_user = User(id=42, telegram_id=999, is_banned=True, is_deleted=False)

        with (
            patch("bot.handlers.vless_web.session_scope", fake_session_scope),
            patch("database.repositories.vless_subscription_repo.get_subscription_by_token", return_value=self.default_sub),
            patch("database.repositories.users_repo.get_user_by_id", return_value=banned_user),
        ):
            resp = await self.client.get("/sub/vless/test-token-valid-length-12345678")
            self.assertEqual(resp.status, 403)

    async def test_expired_subscription_returns_notice_200(self):
        mock_session = AsyncMock()
        mock_session.add = MagicMock()

        @asynccontextmanager
        async def fake_session_scope():
            yield mock_session

        expired_user = User(
            id=42,
            telegram_id=999,
            is_banned=False,
            is_deleted=False,
            subscription_end=datetime.now(timezone.utc) - timedelta(days=5),
        )

        with (
            patch("bot.handlers.vless_web.session_scope", fake_session_scope),
            patch("database.repositories.vless_subscription_repo.get_subscription_by_token", return_value=self.default_sub),
            patch("database.repositories.users_repo.get_user_by_id", return_value=expired_user),
            patch("services.vless_subscription_service.VlessSubscriptionService.ensure_synced_background"),
        ):
            resp = await self.client.get("/sub/vless/test-token-valid-length-12345678")
            self.assertEqual(resp.status, 200)
            body = await resp.text()
            decoded = base64.b64decode(body).decode("utf-8")
            self.assertIn(urllib.parse.quote("Подписка закончилась"), decoded)
            self.assertIn("127.0.0.1:443", decoded)
            title_b64 = resp.headers.get("Profile-Title", "").replace("base64:", "")
            title = base64.b64decode(title_b64).decode("utf-8")
            self.assertIn("Подписка закончилась", title)

    async def test_missing_hwid_returns_403_with_header(self):
        mock_session = AsyncMock()

        @asynccontextmanager
        async def fake_session_scope():
            yield mock_session

        with (
            patch("bot.handlers.vless_web.session_scope", fake_session_scope),
            patch("database.repositories.vless_subscription_repo.get_subscription_by_token", return_value=self.default_sub),
            patch("database.repositories.users_repo.get_user_by_id", return_value=self.default_user),
        ):
            resp = await self.client.get("/sub/vless/test-token-valid-length-12345678")
            self.assertEqual(resp.status, 403)
            self.assertEqual(resp.headers.get("x-hwid-required"), "true")

    async def test_browser_request_without_hwid_returns_html_landing_page(self):
        mock_session = AsyncMock()

        @asynccontextmanager
        async def fake_session_scope():
            yield mock_session

        with (
            patch("bot.handlers.vless_web.session_scope", fake_session_scope),
            patch("database.repositories.vless_subscription_repo.get_subscription_by_token", return_value=self.default_sub),
            patch("database.repositories.users_repo.get_user_by_id", return_value=self.default_user),
        ):
            headers = {
                "Accept": "text/html,application/xhtml+xml",
                "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X)",
            }
            resp = await self.client.get("/sub/vless/test-token-valid-length-12345678", headers=headers)
            self.assertEqual(resp.status, 200)
            self.assertIn("text/html", resp.headers.get("Content-Type", ""))
            body = await resp.text()
            self.assertIn("incy://add/", body)
            self.assertIn(texts.LANDING_VLESS_PAGE_TITLE, body)
            self.assertIn("apps.apple.com/app/incy", body)

    async def test_browser_request_financial_hold_returns_paused_landing(self):
        mock_session = AsyncMock()
        mock_session.add = MagicMock()

        @asynccontextmanager
        async def fake_session_scope():
            yield mock_session

        now = now_utc()
        hold_user = User(
            id=42,
            telegram_id=999,
            is_banned=False,
            is_deleted=False,
            financial_hold=True,
            subscription_end=now + timedelta(days=15),
        )

        with (
            patch("bot.handlers.vless_web.session_scope", fake_session_scope),
            patch("database.repositories.vless_subscription_repo.get_subscription_by_token", return_value=self.default_sub),
            patch("database.repositories.users_repo.get_user_by_id", return_value=hold_user),
            patch("services.vless_subscription_service.VlessSubscriptionService.ensure_synced_background"),
        ):
            headers = {
                "Accept": "text/html,application/xhtml+xml",
                "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X)",
            }
            resp = await self.client.get("/sub/vless/test-token-valid-length-12345678", headers=headers)
            self.assertEqual(resp.status, 200)
            self.assertIn("text/html", resp.headers.get("Content-Type", ""))
            body = await resp.text()
            self.assertIn(texts.LANDING_INACTIVE_BADGE_PAUSED, body)
            self.assertIn(texts.LANDING_INACTIVE_TITLE_DEFAULT, body)
            self.assertNotIn(texts.LANDING_INACTIVE_TITLE_EXPIRED, body)

    async def test_browser_request_expired_returns_expired_landing(self):
        mock_session = AsyncMock()
        mock_session.add = MagicMock()

        @asynccontextmanager
        async def fake_session_scope():
            yield mock_session

        now = now_utc()
        expired_user = User(
            id=42,
            telegram_id=999,
            is_banned=False,
            is_deleted=False,
            financial_hold=False,
            subscription_end=now - timedelta(days=2),
        )

        with (
            patch("bot.handlers.vless_web.session_scope", fake_session_scope),
            patch("database.repositories.vless_subscription_repo.get_subscription_by_token", return_value=self.default_sub),
            patch("database.repositories.users_repo.get_user_by_id", return_value=expired_user),
            patch("services.vless_subscription_service.VlessSubscriptionService.ensure_synced_background"),
        ):
            headers = {
                "Accept": "text/html,application/xhtml+xml",
                "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X)",
            }
            resp = await self.client.get("/sub/vless/test-token-valid-length-12345678", headers=headers)
            self.assertEqual(resp.status, 200)
            self.assertIn("text/html", resp.headers.get("Content-Type", ""))
            body = await resp.text()
            self.assertIn(texts.LANDING_INACTIVE_BADGE_EXPIRED, body)
            self.assertIn(texts.LANDING_INACTIVE_TITLE_EXPIRED, body)
            self.assertNotIn(texts.LANDING_INACTIVE_BADGE_PAUSED, body)

    async def test_resolve_bot_username_retries_on_failure_without_caching_fallback(self):
        from bot.handlers.vless_web import _resolve_bot_username
        import bot.handlers.vless_web as vless_web

        orig_cached = vless_web._cached_bot_username
        try:
            vless_web._cached_bot_username = None

            mock_bot = AsyncMock()
            mock_bot.get_me.side_effect = RuntimeError("Telegram connection failed")

            mock_request = MagicMock()
            mock_request.app = {"bot": mock_bot}

            with patch.dict(os.environ, {"BOT_USERNAME": "env_bot"}):
                resolved = await _resolve_bot_username(mock_request)
                self.assertEqual(resolved, "env_bot")
                self.assertIsNone(vless_web._cached_bot_username)
                self.assertNotIn("bot_username", mock_request.app)

                me_mock = MagicMock()
                me_mock.username = "real_bot"
                mock_bot.get_me.side_effect = None
                mock_bot.get_me.return_value = me_mock

                resolved_success = await _resolve_bot_username(mock_request)
                self.assertEqual(resolved_success, "real_bot")
                self.assertEqual(vless_web._cached_bot_username, "real_bot")
                self.assertEqual(mock_request.app.get("bot_username"), "real_bot")
        finally:
            vless_web._cached_bot_username = orig_cached

    async def test_exceeded_device_limit_returns_notice_200(self):
        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(return_value=MagicMock(scalar_one=MagicMock(return_value=2)))

        @asynccontextmanager
        async def fake_session_scope():
            yield mock_session

        with (
            patch("bot.handlers.vless_web.session_scope", fake_session_scope),
            patch("database.repositories.vless_subscription_repo.get_subscription_by_token", return_value=self.default_sub),
            patch("database.repositories.users_repo.get_user_by_id", return_value=self.default_user),
            patch("services.subscription.SubscriptionService.get_effective_device_limit", return_value=2),
            patch("services.vless_subscription_service.VlessSubscriptionService.get_eligible_vless_servers", return_value=[self.default_server]),
            patch("database.repositories.vless_subscription_repo.register_hwid_atomic", return_value=(False, 1, 0)),
        ):
            headers = {"X-Hwid": "device-overflow-hwid"}
            resp = await self.client.get("/sub/vless/test-token-valid-length-12345678", headers=headers)
            self.assertEqual(resp.status, 200)
            self.assertEqual(resp.headers.get("Device-Limit-Exceeded"), "1")
            self.assertEqual(resp.headers.get("x-hwid-max-devices-reached"), "true")
            self.assertEqual(resp.headers.get("x-hwid-limit"), "true")
            body = await resp.text()
            decoded = base64.b64decode(body).decode("utf-8")
            self.assertIn("127.0.0.1:443", decoded)
            self.assertIn(urllib.parse.quote("Превышен лимит устройств"), decoded)

    async def test_valid_request_returns_200_base64_feed(self):
        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(return_value=MagicMock(scalar_one=MagicMock(return_value=0)))

        @asynccontextmanager
        async def fake_session_scope():
            yield mock_session

        with (
            patch("bot.handlers.vless_web.session_scope", fake_session_scope),
            patch("database.repositories.vless_subscription_repo.get_subscription_by_token", return_value=self.default_sub),
            patch("database.repositories.users_repo.get_user_by_id", return_value=self.default_user),
            patch("services.subscription.SubscriptionService.get_effective_device_limit", return_value=2),
            patch("database.repositories.vless_subscription_repo.register_hwid_atomic", return_value=(True, 1, 2)),
            patch("services.vless_subscription_service.VlessSubscriptionService.ensure_synced_background") as mock_sync,
            patch("services.vless_subscription_service.VlessSubscriptionService.get_eligible_vless_servers", return_value=[self.default_server]),
        ):
            headers = {"X-Hwid": "test-client-hwid-99"}
            resp = await self.client.get("/sub/access/test-token-valid-length-12345678", headers=headers)
            self.assertEqual(resp.status, 200)
            mock_sync.assert_called_once_with(self.default_user.id, session=mock_session)

            body = await resp.text()
            decoded = base64.b64decode(body).decode("utf-8")
            self.assertIn("vless://11111111-2222-3333-4444-555555555555@nl.example.com:443", decoded)
            self.assertIn("xtls-rprx-vision", decoded)
            expected_tag = urllib.parse.quote("🇳🇱 NL-Node-1")
            self.assertIn(f"#{expected_tag}", decoded)
            self.assertNotIn("?serverDescription=", decoded)
            self.assertEqual(resp.headers.get("Content-Disposition"), 'inline; filename="Just1k.txt"; filename*=UTF-8\'\'Just1k.txt')
            self.assertEqual(resp.headers.get("Sort-Order"), "none")
            self.assertEqual(resp.headers.get("x-hwid-active"), "true")
            self.assertEqual(resp.headers.get("Device-Limit"), "2")
            self.assertEqual(resp.headers.get("Device-Active-Count"), "1")
            self.assertEqual(resp.headers.get("Hide-Url"), "1")
            self.assertEqual(resp.headers.get("No-Limit-Enabled"), "1")
            self.assertIn("t.me/", resp.headers.get("Support-Url", ""))
            self.assertIn("upload=0; download=0; total=0;", resp.headers.get("Subscription-Userinfo", ""))

    async def test_feed_subscription_userinfo_reflects_traffic_counters(self):
        """Subscription-Userinfo header reflects accumulated upload and download traffic from VlessSubscription."""
        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(return_value=MagicMock(scalar_one=MagicMock(return_value=0)))

        @asynccontextmanager
        async def fake_session_scope():
            yield mock_session

        traffic_sub = VlessSubscription(
            id=15,
            user_id=42,
            token="traffic-token-valid-length-12345678",
            uuid="11111111-2222-3333-4444-555555555555",
            is_active=True,
            active_hwids={},
            traffic_up=2048,
            traffic_down=8192,
        )

        with (
            patch("bot.handlers.vless_web.session_scope", fake_session_scope),
            patch("database.repositories.vless_subscription_repo.get_subscription_by_token", return_value=traffic_sub),
            patch("database.repositories.users_repo.get_user_by_id", return_value=self.default_user),
            patch("services.subscription.SubscriptionService.get_effective_device_limit", return_value=2),
            patch("database.repositories.vless_subscription_repo.register_hwid_atomic", return_value=(True, 1, 2)),
            patch("services.vless_subscription_service.VlessSubscriptionService.ensure_synced_background"),
            patch("services.vless_subscription_service.VlessSubscriptionService.get_eligible_vless_servers", return_value=[self.default_server]),
        ):
            headers = {"X-Hwid": "test-client-hwid-99"}
            resp = await self.client.get("/sub/access/traffic-token-valid-length-12345678", headers=headers)
            self.assertEqual(resp.status, 200)

            userinfo = resp.headers.get("Subscription-Userinfo", "")
            self.assertIn("upload=2048", userinfo)
            self.assertIn("download=8192", userinfo)
            self.assertIn("total=0", userinfo)
            self.assertIn("expire=", userinfo)

    async def test_empty_servers_returns_503(self):
        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(return_value=MagicMock(scalar_one=MagicMock(return_value=0)))

        @asynccontextmanager
        async def fake_session_scope():
            yield mock_session

        with (
            patch("bot.handlers.vless_web.session_scope", fake_session_scope),
            patch("database.repositories.vless_subscription_repo.get_subscription_by_token", return_value=self.default_sub),
            patch("database.repositories.users_repo.get_user_by_id", return_value=self.default_user),
            patch("services.subscription.SubscriptionService.get_effective_device_limit", return_value=2),
            patch("database.repositories.vless_subscription_repo.register_hwid_atomic", return_value=(True, 1, 2)) as mock_register,
            patch("services.vless_subscription_service.VlessSubscriptionService.ensure_synced_background"),
            patch("services.vless_subscription_service.VlessSubscriptionService.get_eligible_vless_servers", return_value=[]),
        ):
            headers = {"X-Hwid": "test-client-hwid-99"}
            resp = await self.client.get("/sub/access/test-token-valid-length-12345678", headers=headers)
            self.assertEqual(resp.status, 503)
            self.assertEqual(resp.headers.get("Retry-After"), "60")
            mock_register.assert_not_called()
            self.assertEqual(self.default_sub.active_hwids, {})

    async def test_empty_links_generation_returns_503_without_registering_hwid(self):
        """If eligible servers exist but link generation produces empty list, return 503 without consuming quota."""
        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(return_value=MagicMock(scalar_one=MagicMock(return_value=0)))

        @asynccontextmanager
        async def fake_session_scope():
            yield mock_session

        bad_server = Server(
            id=2,
            name="Broken Node",
            is_active=True,
            health_state=ServerHealthState.ONLINE,
            protocol="vless",
            capabilities=["vless"],
            extra_data={},  # No domain!
        )

        with (
            patch("bot.handlers.vless_web.session_scope", fake_session_scope),
            patch("database.repositories.vless_subscription_repo.get_subscription_by_token", return_value=self.default_sub),
            patch("database.repositories.users_repo.get_user_by_id", return_value=self.default_user),
            patch("services.subscription.SubscriptionService.get_effective_device_limit", return_value=2),
            patch("database.repositories.vless_subscription_repo.register_hwid_atomic", return_value=(True, 1, 2)) as mock_register,
            patch("services.vless_subscription_service.VlessSubscriptionService.ensure_synced_background"),
            patch("services.vless_subscription_service.VlessSubscriptionService.get_eligible_vless_servers", return_value=[bad_server]),
        ):
            headers = {"X-Hwid": "test-client-hwid-99"}
            resp = await self.client.get("/sub/access/test-token-valid-length-12345678", headers=headers)
            self.assertEqual(resp.status, 503)
            self.assertEqual(resp.headers.get("Retry-After"), "60")
            mock_register.assert_not_called()
            self.assertEqual(self.default_sub.active_hwids, {})

    async def test_device_metadata_os_version_and_device_hwid_header(self):
        """X-Ver-OS is appended to os metadata and X-Device-Hwid is recognized."""
        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(return_value=MagicMock(scalar_one=MagicMock(return_value=0)))

        @asynccontextmanager
        async def fake_session_scope():
            yield mock_session

        captured_meta = {}

        async def fake_register_hwid(session, sub_id, hwid, effective_limit=None, ttl_hours=48, device_info=None):
            nonlocal captured_meta
            captured_meta = device_info or {}
            return True, 1, 2

        with (
            patch("bot.handlers.vless_web.session_scope", fake_session_scope),
            patch("database.repositories.vless_subscription_repo.get_subscription_by_token", return_value=self.default_sub),
            patch("database.repositories.users_repo.get_user_by_id", return_value=self.default_user),
            patch("services.subscription.SubscriptionService.get_effective_device_limit", return_value=2),
            patch("database.repositories.vless_subscription_repo.register_hwid_atomic", side_effect=fake_register_hwid),
            patch("services.vless_subscription_service.VlessSubscriptionService.ensure_synced_background"),
            patch("services.vless_subscription_service.VlessSubscriptionService.get_eligible_vless_servers", return_value=[self.default_server]),
        ):
            headers = {
                "X-Device-Hwid": "test-alt-hwid-ios-123",
                "X-Device-Os": "iOS",
                "X-Ver-OS": "18.1",
                "X-Device-Model": "iPhone15,2",
            }
            resp = await self.client.get("/sub/access/test-token-valid-length-12345678", headers=headers)
            self.assertEqual(resp.status, 200)
            self.assertEqual(captured_meta.get("os"), "iOS 18.1")
            self.assertEqual(captured_meta.get("model"), "iPhone15,2")

    async def test_financial_hold_user_returns_notice_200(self):
        mock_session = AsyncMock()
        mock_session.add = MagicMock()

        @asynccontextmanager
        async def fake_session_scope():
            yield mock_session

        hold_user = User(
            id=42,
            telegram_id=999,
            is_banned=False,
            is_deleted=False,
            financial_hold=True,
            subscription_end=datetime.now(timezone.utc) + timedelta(days=5),
        )

        with (
            patch("bot.handlers.vless_web.session_scope", fake_session_scope),
            patch("database.repositories.vless_subscription_repo.get_subscription_by_token", return_value=self.default_sub),
            patch("database.repositories.users_repo.get_user_by_id", return_value=hold_user),
            patch("services.vless_subscription_service.VlessSubscriptionService.ensure_synced_background"),
        ):
            headers = {"X-Hwid": "test-client-hwid-99"}
            resp = await self.client.get("/sub/vless/test-token-valid-length-12345678", headers=headers)
            self.assertEqual(resp.status, 200)
            body = await resp.text()
            decoded = base64.b64decode(body).decode("utf-8")
            self.assertIn(urllib.parse.quote("Подписка закончилась"), decoded)
            self.assertIn("127.0.0.1:443", decoded)

    async def test_grace_period_user_returns_200(self):
        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(return_value=MagicMock(scalar_one=MagicMock(return_value=0)))

        @asynccontextmanager
        async def fake_session_scope():
            yield mock_session

        grace_user = User(
            id=42,
            telegram_id=999,
            is_banned=False,
            is_deleted=False,
            financial_hold=False,
            device_limit=2,
            subscription_end=datetime.now(timezone.utc) - timedelta(hours=2),
        )

        with (
            patch("bot.handlers.vless_web.session_scope", fake_session_scope),
            patch("database.repositories.vless_subscription_repo.get_subscription_by_token", return_value=self.default_sub),
            patch("database.repositories.users_repo.get_user_by_id", return_value=grace_user),
            patch("services.subscription.SubscriptionService.get_effective_device_limit", return_value=2),
            patch("database.repositories.vless_subscription_repo.register_hwid_atomic", return_value=(True, 1, 2)),
            patch("services.vless_subscription_service.VlessSubscriptionService.ensure_synced_background"),
            patch("services.vless_subscription_service.VlessSubscriptionService.get_eligible_vless_servers", return_value=[self.default_server]),
        ):
            headers = {"X-Hwid": "test-client-hwid-99"}
            resp = await self.client.get("/sub/vless/test-token-valid-length-12345678", headers=headers)
            self.assertEqual(resp.status, 200)

    async def test_feed_debounces_frequent_requests(self):
        now = now_utc()
        recent_sub = VlessSubscription(
            id=20,
            user_id=42,
            token="test-token-valid-length-12345678",
            uuid="11111111-2222-3333-4444-555555555555",
            is_active=True,
            version=1,
            active_hwids={},
            pending_revoked_uuids=[],
            last_synced_at=now - timedelta(seconds=10),
        )

        mock_session = AsyncMock()
        mock_session.add = MagicMock()
        mock_session.execute = AsyncMock(return_value=MagicMock(scalar_one=MagicMock(return_value=0)))

        @asynccontextmanager
        async def fake_session_scope():
            yield mock_session

        with (
            patch("bot.handlers.vless_web.session_scope", fake_session_scope),
            patch("database.repositories.vless_subscription_repo.get_subscription_by_token", return_value=recent_sub),
            patch("database.repositories.users_repo.get_user_by_id", return_value=self.default_user),
            patch("services.subscription.SubscriptionService.get_effective_device_limit", return_value=2),
            patch("database.repositories.vless_subscription_repo.register_hwid_atomic", return_value=(True, 1, 2)),
            patch("services.vless_subscription_service.VlessSubscriptionService.ensure_synced_background") as mock_sync,
            patch("services.vless_subscription_service.VlessSubscriptionService.get_eligible_vless_servers", return_value=[self.default_server]),
        ):
            headers = {"X-Hwid": "test-client-hwid-99"}
            resp = await self.client.get("/sub/vless/test-token-valid-length-12345678", headers=headers)
            self.assertEqual(resp.status, 200)
            mock_sync.assert_not_called()

    async def test_feed_triggers_deactivation_when_access_expired(self):
        now = now_utc()
        active_sub = VlessSubscription(
            id=30,
            user_id=42,
            token="test-token-valid-length-12345678",
            uuid="11111111-2222-3333-4444-555555555555",
            is_active=True,
            version=1,
            active_hwids={},
            pending_revoked_uuids=[],
        )
        expired_user = User(
            id=42,
            telegram_id=999,
            is_banned=False,
            is_deleted=False,
            device_limit=2,
            subscription_end=now - timedelta(hours=80),
        )

        mock_session = AsyncMock()
        mock_session.add = MagicMock()

        @asynccontextmanager
        async def fake_session_scope():
            yield mock_session

        with (
            patch("bot.handlers.vless_web.session_scope", fake_session_scope),
            patch("database.repositories.vless_subscription_repo.get_subscription_by_token", return_value=active_sub),
            patch("database.repositories.users_repo.get_user_by_id", return_value=expired_user),
            patch("services.subscription.SubscriptionService.check_vpn_access", return_value=False),
            patch("services.vless_subscription_service.VlessSubscriptionService.ensure_synced_background") as mock_sync,
        ):
            resp = await self.client.get("/sub/access/test-token-valid-length-12345678")
            self.assertEqual(resp.status, 200)
            self.assertFalse(active_sub.is_active)
            self.assertEqual(active_sub.version, 2)
            mock_sync.assert_called_once_with(expired_user.id, session=mock_session)

    async def test_vless_mobile_incy_happ_with_hwid_receives_service_notice_when_inactive(self):
        expired_user = User(
            id=42,
            telegram_id=999,
            is_banned=False,
            is_deleted=False,
            financial_hold=False,
            subscription_end=datetime.now(timezone.utc) - timedelta(days=2),
        )
        inactive_sub = VlessSubscription(
            id=1,
            user_id=42,
            token="test-token-valid-length-12345678",
            uuid="11111111-2222-3333-4444-555555555555",
            is_active=False,
            version=1,
        )
        mock_session = AsyncMock()

        @asynccontextmanager
        async def fake_session_scope():
            yield mock_session

        user_agents = [
            "INCY/2.4.1 (Linux; U; Android 14; SM-S918B Build/UP1A.231005.007)",
            "Happ/1.8.0 (iPhone; iOS 17.5.1; Scale/3.00)",
        ]

        for ua in user_agents:
            with (
                patch("bot.handlers.vless_web.session_scope", fake_session_scope),
                patch("database.repositories.vless_subscription_repo.get_subscription_by_token", return_value=inactive_sub),
                patch("database.repositories.users_repo.get_user_by_id", return_value=expired_user),
                patch("services.subscription.SubscriptionService.check_vpn_access", return_value=False),
                patch("services.vless_subscription_service.VlessSubscriptionService.ensure_synced_background"),
            ):
                resp = await self.client.get(
                    "/sub/access/test-token-valid-length-12345678",
                    headers={
                        "User-Agent": ua,
                        "X-Hwid": "test-vless-mobile-hwid",
                        "Accept": "text/html,application/xhtml+xml",
                    },
                )
                self.assertEqual(resp.status, 200, f"Failed for UA: {ua}")
                self.assertIn("text/plain", resp.headers.get("Content-Type", ""))
                self.assertNotIn("text/html", resp.headers.get("Content-Type", ""))
                body = await resp.text()
                decoded = base64.b64decode(body).decode("utf-8")
                self.assertIn("vless://00000000-0000-0000-0000-000000000000", decoded)

    async def test_vless_is_browser_request_identifies_mobile_and_hwid(self):
        from aiohttp.test_utils import make_mocked_request
        from bot.handlers.vless_web import _is_browser_request

        # Mobile clients without HWID are NEVER browsers, even with Accept: text/html
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
                    "/sub/access/test-token",
                    headers={
                        "User-Agent": ua,
                        "Accept": accept_val,
                    },
                )
                self.assertFalse(
                    _is_browser_request(req),
                    f"Expected API client not browser for UA: {ua} with Accept: {accept_val}",
                )

        # HWID presence guarantees not a browser, even with Accept: text/html
        req_with_hwid = make_mocked_request(
            "GET",
            "/sub/access/test-token",
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
                "Accept": "text/html",
                "X-Hwid": "test-device-hwid-123",
            },
        )
        self.assertFalse(_is_browser_request(req_with_hwid))

    async def test_vless_active_sub_mobile_client_without_hwid_gets_403_even_with_html_accept(self):
        active_user = User(
            id=42,
            telegram_id=999,
            is_banned=False,
            is_deleted=False,
            financial_hold=False,
            subscription_end=datetime.now(timezone.utc) + timedelta(days=20),
        )
        active_sub = VlessSubscription(
            id=1,
            user_id=42,
            token="test-token-valid-length-12345678",
            uuid="11111111-2222-3333-4444-555555555555",
            is_active=True,
            version=1,
        )
        mock_session = AsyncMock()

        @asynccontextmanager
        async def fake_session_scope():
            yield mock_session

        mobile_uas = [
            "INCY/2.4.1 (Linux; U; Android 14; SM-S918B Build/UP1A.231005.007)",
            "Happ/1.8.0 (iPhone; iOS 17.5.1; Scale/3.00)",
        ]

        for ua in mobile_uas:
            with (
                patch("bot.handlers.vless_web.session_scope", fake_session_scope),
                patch("database.repositories.vless_subscription_repo.get_subscription_by_token", return_value=active_sub),
                patch("database.repositories.users_repo.get_user_by_id", return_value=active_user),
                patch("services.subscription.SubscriptionService.check_vpn_access", return_value=True),
            ):
                resp = await self.client.get(
                    "/sub/access/test-token-valid-length-12345678",
                    headers={
                        "User-Agent": ua,
                        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                    },
                )
                self.assertEqual(resp.status, 403, f"Expected 403 for {ua}")
                self.assertEqual(resp.headers.get("x-hwid-required"), "true")
                self.assertEqual(await resp.text(), "HWID required")

    async def test_vless_url_header_sanitization_rejects_control_chars_and_non_ascii(self):
        from bot.handlers.vless_web import _sanitize_header_url
        self.assertEqual(_sanitize_header_url("https://example.com/test\r\nInjected: true", fallback="https://default.com"), "https://default.com")
        self.assertEqual(_sanitize_header_url("https://example.com/test\tbad", fallback=""), "")
        self.assertEqual(_sanitize_header_url("  https://example.com/clean  "), "https://example.com/clean")
        self.assertEqual(_sanitize_header_url("https://example.com/test\x00bad", fallback="https://default.com"), "https://default.com")
        self.assertEqual(_sanitize_header_url("https://example.com/test\x0bbad", fallback="https://default.com"), "https://default.com")
        self.assertEqual(_sanitize_header_url("https://example.com/test\x7fbad", fallback="https://default.com"), "https://default.com")
        self.assertEqual(_sanitize_header_url("https://t.me/мой_канал", fallback="https://default.com"), "https://default.com")
        self.assertEqual(_sanitize_header_url("tg://resolve?domain=just1k13bot"), "tg://resolve?domain=just1k13bot")

    async def test_grace_period_access_within_4h_window(self):
        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(return_value=MagicMock(scalar_one=MagicMock(return_value=0)))

        @asynccontextmanager
        async def fake_session_scope():
            yield mock_session

        expired_grace_user = User(
            id=42,
            telegram_id=999,
            is_banned=False,
            is_deleted=False,
            financial_hold=False,
            device_limit=2,
            subscription_end=datetime.now(timezone.utc) - timedelta(hours=2),
        )

        with (
            patch("bot.handlers.vless_web.session_scope", fake_session_scope),
            patch("database.repositories.vless_subscription_repo.get_subscription_by_token", return_value=self.default_sub),
            patch("database.repositories.users_repo.get_user_by_id", return_value=expired_grace_user),
            patch("services.subscription.SubscriptionService.get_effective_device_limit", return_value=2),
            patch("database.repositories.vless_subscription_repo.register_hwid_atomic", return_value=(True, 1, 2)),
            patch("services.vless_subscription_service.VlessSubscriptionService.ensure_synced_background"),
            patch("services.vless_subscription_service.VlessSubscriptionService.get_eligible_vless_servers", return_value=[self.default_server]),
        ):
            headers = {
                "X-Hwid": "test-client-hwid-99",
                "X-Device-Os": "iOS 17.4",
                "X-Device-Model": "iPhone15,2",
            }
            resp = await self.client.get("/sub/access/test-token-valid-length-12345678", headers=headers)
            self.assertEqual(resp.status, 200)

            # Within grace window, user receives standard access feed with routing header
            self.assertIn("Routing", resp.headers)
            self.assertTrue(resp.headers["Routing"].startswith("incy://routing/onadd/"))
            self.assertEqual("168", resp.headers.get("routing-geo-update-interval"))
            self.assertNotIn("Announce", resp.headers)

            # Profile-Title must be single-line strictly <= 25 characters
            self.assertIn("Profile-Title", resp.headers)
            raw_title_hdr = resp.headers["Profile-Title"]
            b64_title_val = raw_title_hdr.removeprefix("base64:") if raw_title_hdr.startswith("base64:") else raw_title_hdr
            decoded_title = base64.b64decode(b64_title_val).decode("utf-8")
            self.assertEqual("✦ Just1k", decoded_title)
            self.assertNotIn("Profile-Description", resp.headers)
            self.assertEqual("1", resp.headers.get("Hide-Url"))
            self.assertIn("Announce-Url", resp.headers)

            # Server link must be present in body
            body = await resp.text()
            decoded_body = base64.b64decode(body).decode("utf-8")
            self.assertIn("vless://11111111-2222-3333-4444-555555555555@nl.example.com:443", decoded_body)

    async def test_feed_happ_client_adaptive_routing_and_in_app_headers(self):
        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(return_value=MagicMock(scalar_one=MagicMock(return_value=0)))

        @asynccontextmanager
        async def fake_session_scope():
            yield mock_session

        with (
            patch("bot.handlers.vless_web.session_scope", fake_session_scope),
            patch("database.repositories.vless_subscription_repo.get_subscription_by_token", return_value=self.default_sub),
            patch("database.repositories.users_repo.get_user_by_id", return_value=self.default_user),
            patch("services.subscription.SubscriptionService.get_effective_device_limit", return_value=2),
            patch("database.repositories.vless_subscription_repo.register_hwid_atomic", return_value=(True, 1, 2)),
            patch("services.vless_subscription_service.VlessSubscriptionService.ensure_synced_background"),
            patch("services.vless_subscription_service.VlessSubscriptionService.get_eligible_vless_servers", return_value=[self.default_server]),
        ):
            headers = {
                "X-Hwid": "test-client-hwid-happ-1",
                "User-Agent": "Happ/4.6.1 (iPhone; iOS 17.4)",
            }
            resp = await self.client.get("/sub/access/test-token-valid-length-12345678", headers=headers)
            self.assertEqual(resp.status, 200)

            # Adaptive routing header for Happ
            self.assertIn("Routing", resp.headers)
            self.assertTrue(resp.headers["Routing"].startswith("happ://routing/onadd/"))
            self.assertEqual(resp.headers.get("Routing-Enable"), "true")

            # Decode routing deep link and verify rules
            routing_uri = resp.headers["Routing"]
            b64_routing = routing_uri.removeprefix("happ://routing/onadd/")
            routing_json = json.loads(base64.b64decode(b64_routing).decode("utf-8"))
            self.assertEqual(routing_json.get("Name"), "✦ Just1k")
            self.assertIs(routing_json.get("useChunkFiles"), True)
            self.assertEqual(routing_json.get("LastUpdated"), "1793318400")
            self.assertIn("geosite:category-ru", routing_json.get("DirectSites", []))
            self.assertIn("geosite:tld-ru", routing_json.get("DirectSites", []))

            # In-App expiration headers for Happ
            self.assertEqual(resp.headers.get("sub-expire"), "1")
            self.assertEqual(resp.headers.get("sub-expire-button-link"), "https://t.me/just1k13bot")

    async def test_feed_happ_client_disabled_routing_headers(self):
        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(return_value=MagicMock(scalar_one=MagicMock(return_value=0)))

        @asynccontextmanager
        async def fake_session_scope():
            yield mock_session

        async def fake_get_setting(session, key, default=None):
            if key == "vless_routing_enabled":
                return False
            return default

        with (
            patch("bot.handlers.vless_web.session_scope", fake_session_scope),
            patch("database.repositories.vless_subscription_repo.get_subscription_by_token", return_value=self.default_sub),
            patch("database.repositories.users_repo.get_user_by_id", return_value=self.default_user),
            patch("services.subscription.SubscriptionService.get_effective_device_limit", return_value=2),
            patch("database.repositories.vless_subscription_repo.register_hwid_atomic", return_value=(True, 1, 2)),
            patch("services.vless_subscription_service.VlessSubscriptionService.ensure_synced_background"),
            patch("services.vless_subscription_service.VlessSubscriptionService.get_eligible_vless_servers", return_value=[self.default_server]),
            patch("bot.handlers.vless_web.get_system_setting", side_effect=fake_get_setting),
        ):
            headers = {
                "X-Hwid": "test-client-hwid-happ-disabled",
                "User-Agent": "Happ/4.6.1 (iPhone; iOS 17.4)",
            }
            resp = await self.client.get("/sub/access/test-token-valid-length-12345678", headers=headers)
            self.assertEqual(resp.status, 200)
            self.assertEqual(resp.headers.get("Routing"), "off")
            self.assertEqual(resp.headers.get("Routing-Enable"), "0")

    async def test_feed_incy_client_standard_routing_without_in_app_headers(self):
        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(return_value=MagicMock(scalar_one=MagicMock(return_value=0)))

        @asynccontextmanager
        async def fake_session_scope():
            yield mock_session

        with (
            patch("bot.handlers.vless_web.session_scope", fake_session_scope),
            patch("database.repositories.vless_subscription_repo.get_subscription_by_token", return_value=self.default_sub),
            patch("database.repositories.users_repo.get_user_by_id", return_value=self.default_user),
            patch("services.subscription.SubscriptionService.get_effective_device_limit", return_value=2),
            patch("database.repositories.vless_subscription_repo.register_hwid_atomic", return_value=(True, 1, 2)),
            patch("services.vless_subscription_service.VlessSubscriptionService.ensure_synced_background"),
            patch("services.vless_subscription_service.VlessSubscriptionService.get_eligible_vless_servers", return_value=[self.default_server]),
        ):
            headers = {
                "X-Hwid": "test-client-hwid-incy-1",
                "User-Agent": "INCY/3.9.0 (iPhone; iOS 17.4)",
            }
            resp = await self.client.get("/sub/access/test-token-valid-length-12345678", headers=headers)
            self.assertEqual(resp.status, 200)

            # Routing header for INCY
            self.assertIn("Routing", resp.headers)
            self.assertTrue(resp.headers["Routing"].startswith("incy://routing/onadd/"))

            # Happ-specific headers should not be present
            self.assertNotIn("sub-expire", resp.headers)
            self.assertNotIn("sub-expire-button-link", resp.headers)
            self.assertNotIn("Routing-Enable", resp.headers)

    async def test_feed_includes_global_system_settings_in_headers(self):
        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(return_value=MagicMock(scalar_one=MagicMock(return_value=0)))

        @asynccontextmanager
        async def fake_session_scope():
            yield mock_session

        settings_dict = {
            "vless_profile_title": "✦ MySpecialVPN",
            "vless_profile_description": "High-Speed Encrypted",
            "vless_announce": "Maintenance scheduled tonight",
            "vless_announce_url": "https://t.me/news_channel",
        }

        async def fake_get_setting(session, key, default=None):
            return settings_dict.get(key, default)

        with (
            patch("bot.handlers.vless_web.session_scope", fake_session_scope),
            patch("database.repositories.vless_subscription_repo.get_subscription_by_token", return_value=self.default_sub),
            patch("database.repositories.users_repo.get_user_by_id", return_value=self.default_user),
            patch("services.subscription.SubscriptionService.get_effective_device_limit", return_value=2),
            patch("database.repositories.vless_subscription_repo.register_hwid_atomic", return_value=(True, 1, 2)),
            patch("services.vless_subscription_service.VlessSubscriptionService.ensure_synced_background"),
            patch("services.vless_subscription_service.VlessSubscriptionService.get_eligible_vless_servers", return_value=[self.default_server]),
            patch("bot.handlers.vless_web.get_system_setting", side_effect=fake_get_setting),
        ):
            headers = {"X-Hwid": "test-client-hwid-99"}
            resp = await self.client.get("/sub/access/test-token-valid-length-12345678", headers=headers)
            self.assertEqual(resp.status, 200)

            # Check Profile-Title (single-line <= 25 characters)
            raw_title = resp.headers.get("Profile-Title", "")
            title_b64 = raw_title.removeprefix("base64:")
            title_val = base64.b64decode(title_b64).decode("utf-8")
            self.assertEqual(title_val, "✦ MySpecialVPN")
            self.assertLessEqual(len(title_val), 25)

            # Profile-Description header must NOT be present
            self.assertNotIn("Profile-Description", resp.headers)

            # Check Announce
            raw_ann = resp.headers.get("Announce", "")
            ann_b64 = raw_ann.removeprefix("base64:")
            ann = base64.b64decode(ann_b64).decode("utf-8")
            self.assertEqual(ann, "Maintenance scheduled tonight")

            # Check Announce-Url
            self.assertEqual(resp.headers.get("Announce-Url"), "https://t.me/news_channel")

    async def test_feed_system_settings_fallbacks_and_url_hygiene(self):
        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(return_value=MagicMock(scalar_one=MagicMock(return_value=0)))

        @asynccontextmanager
        async def fake_session_scope():
            yield mock_session

        settings_dict = {
            "vless_profile_title": "   ",  # empty string -> must fall back to default
            "vless_profile_desc": "Fallback Description Used",  # fallback key
            "vless_announce": "Important News",
            "vless_announce_url": "https://t.me/news channel with spaces",  # invalid url with space -> must be omitted
        }

        async def fake_get_setting(session, key, default=None):
            return settings_dict.get(key, default)

        with (
            patch("bot.handlers.vless_web.session_scope", fake_session_scope),
            patch("database.repositories.vless_subscription_repo.get_subscription_by_token", return_value=self.default_sub),
            patch("database.repositories.users_repo.get_user_by_id", return_value=self.default_user),
            patch("services.subscription.SubscriptionService.get_effective_device_limit", return_value=2),
            patch("database.repositories.vless_subscription_repo.register_hwid_atomic", return_value=(True, 1, 2)),
            patch("services.vless_subscription_service.VlessSubscriptionService.ensure_synced_background"),
            patch("services.vless_subscription_service.VlessSubscriptionService.get_eligible_vless_servers", return_value=[self.default_server]),
            patch("bot.handlers.vless_web.get_system_setting", side_effect=fake_get_setting),
        ):
            headers = {"X-Hwid": "test-client-hwid-100"}
            resp = await self.client.get("/sub/access/test-token-valid-length-12345678", headers=headers)
            self.assertEqual(resp.status, 200)

            # Profile-Title should fall back to default "✦ Just1k"
            raw_title = resp.headers.get("Profile-Title", "")
            title_b64 = raw_title.removeprefix("base64:")
            title_val = base64.b64decode(title_b64).decode("utf-8")
            self.assertEqual(title_val, "✦ Just1k")

            # Profile-Description header must NOT be present
            self.assertNotIn("Profile-Description", resp.headers)

            # Announce-Url with spaces must NOT be injected; should fall back to channel_url
            self.assertNotEqual(resp.headers.get("Announce-Url"), "https://t.me/news channel with spaces")
            self.assertTrue(resp.headers.get("Announce-Url", "").startswith("https://t.me/"))

    async def test_feed_dynamic_tariff_and_devices_formatting(self):
        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(return_value=MagicMock(scalar_one=MagicMock(return_value=0)))

        @asynccontextmanager
        async def fake_session_scope():
            yield mock_session

        from database.models import Tariff
        custom_tariff = Tariff(id=5, name="Супер-Тариф", service_type="standard", device_limit=5)
        user_with_tariff = User(
            id=42,
            telegram_id=999,
            is_banned=False,
            is_deleted=False,
            device_limit=5,
            current_tariff_id=5,
            subscription_end=datetime.now(timezone.utc) + timedelta(days=30),
        )

        settings_dict = {
            "vless_profile_show_devices": True,
            "vless_announce": "📢 {tariff_name} | {active_devices}/{limit}",
        }

        async def fake_get_setting(session, key, default=None):
            return settings_dict.get(key, default)

        async def fake_get_tariff(session, tariff_id):
            if tariff_id == 5:
                return custom_tariff
            return None

        with (
            patch("bot.handlers.vless_web.session_scope", fake_session_scope),
            patch("database.repositories.vless_subscription_repo.get_subscription_by_token", return_value=self.default_sub),
            patch("database.repositories.users_repo.get_user_by_id", return_value=user_with_tariff),
            patch("database.repositories.tariffs_repo.get_tariff_by_id", side_effect=fake_get_tariff),
            patch("services.subscription.SubscriptionService.get_effective_device_limit", return_value=5),
            patch("services.vless_subscription_service.VlessSubscriptionService.get_combined_quota", return_value=(2, 1, 1)),
            patch("database.repositories.vless_subscription_repo.register_hwid_atomic", return_value=(True, 2, 5)),
            patch("services.vless_subscription_service.VlessSubscriptionService.ensure_synced_background"),
            patch("services.vless_subscription_service.VlessSubscriptionService.get_eligible_vless_servers", return_value=[self.default_server]),
            patch("bot.handlers.vless_web.get_system_setting", side_effect=fake_get_setting),
        ):
            headers = {"X-Hwid": "test-client-hwid-dynamic"}
            resp = await self.client.get("/sub/access/test-token-valid-length-12345678", headers=headers)
            self.assertEqual(resp.status, 200)

            # Profile-Title check with show_devices=True
            raw_title = resp.headers.get("Profile-Title", "")
            title_b64 = raw_title.removeprefix("base64:")
            decoded_title = base64.b64decode(title_b64).decode("utf-8")
            self.assertEqual(decoded_title, "✦ Just1k [2/5]")

            # Profile-Description header must NOT be present
            self.assertNotIn("Profile-Description", resp.headers)

            # Announce check
            raw_ann = resp.headers.get("Announce", "")
            ann_b64 = raw_ann.removeprefix("base64:")
            ann = base64.b64decode(ann_b64).decode("utf-8")
            self.assertEqual(ann, "📢 Супер-Тариф | 2/5")

            # Verify Routing profile name is stable and does NOT clone with device count
            routing_uri = resp.headers.get("Routing", "")
            self.assertTrue(routing_uri.startswith("incy://routing/onadd/"))
            b64_routing = routing_uri.removeprefix("incy://routing/onadd/")
            routing_json = json.loads(base64.b64decode(b64_routing).decode("utf-8"))
            self.assertEqual(routing_json.get("Name"), "✦ Just1k")

    async def test_feed_default_description_when_not_set_in_db(self):
        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(return_value=MagicMock(scalar_one=MagicMock(return_value=0)))

        @asynccontextmanager
        async def fake_session_scope():
            yield mock_session

        user_without_tariff = User(
            id=42,
            telegram_id=999,
            is_banned=False,
            is_deleted=False,
            device_limit=2,
            current_tariff_id=None,
            subscription_end=datetime.now(timezone.utc) + timedelta(days=30),
        )

        with (
            patch("bot.handlers.vless_web.session_scope", fake_session_scope),
            patch("database.repositories.vless_subscription_repo.get_subscription_by_token", return_value=self.default_sub),
            patch("database.repositories.users_repo.get_user_by_id", return_value=user_without_tariff),
            patch("services.subscription.SubscriptionService.get_effective_device_limit", return_value=2),
            patch("services.vless_subscription_service.VlessSubscriptionService.get_combined_quota", return_value=(1, 0, 1)),
            patch("database.repositories.vless_subscription_repo.register_hwid_atomic", return_value=(True, 1, 2)),
            patch("services.vless_subscription_service.VlessSubscriptionService.ensure_synced_background"),
            patch("services.vless_subscription_service.VlessSubscriptionService.get_eligible_vless_servers", return_value=[self.default_server]),
            patch("bot.handlers.vless_web.get_system_setting", return_value=None),
        ):
            headers = {"X-Hwid": "test-client-hwid-default"}
            resp = await self.client.get("/sub/access/test-token-valid-length-12345678", headers=headers)
            self.assertEqual(resp.status, 200)

            # Profile-Title should be default single-line "✦ Just1k"
            raw_title = resp.headers.get("Profile-Title", "")
            title_b64 = raw_title.removeprefix("base64:")
            decoded_title = base64.b64decode(title_b64).decode("utf-8")
            self.assertEqual(decoded_title, "✦ Just1k")

            # Profile-Description header must NOT be present
            self.assertNotIn("Profile-Description", resp.headers)

    async def test_feed_white_internet_tariff_isolation(self):
        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(return_value=MagicMock(scalar_one=MagicMock(return_value=0)))

        @asynccontextmanager
        async def fake_session_scope():
            yield mock_session

        from database.models import Tariff
        wi_tariff = Tariff(id=10, name="Белый Интернет 50 ГБ", service_type="white_internet", device_limit=1)
        user_with_wi = User(
            id=42,
            telegram_id=999,
            is_banned=False,
            is_deleted=False,
            device_limit=5,
            current_tariff_id=10,
            subscription_end=datetime.now(timezone.utc) + timedelta(days=30),
        )

        async def fake_get_setting(session, key, default=None):
            if key == "vless_announce":
                return "Тариф: {tariff}"
            return default

        with (
            patch("bot.handlers.vless_web.session_scope", fake_session_scope),
            patch("database.repositories.vless_subscription_repo.get_subscription_by_token", return_value=self.default_sub),
            patch("database.repositories.users_repo.get_user_by_id", return_value=user_with_wi),
            patch("database.repositories.tariffs_repo.get_tariff_by_id", return_value=wi_tariff),
            patch("services.subscription.SubscriptionService.get_effective_device_limit", return_value=5),
            patch("services.vless_subscription_service.VlessSubscriptionService.get_combined_quota", return_value=(3, 1, 2)),
            patch("database.repositories.vless_subscription_repo.register_hwid_atomic", return_value=(True, 2, 4)),
            patch("services.vless_subscription_service.VlessSubscriptionService.ensure_synced_background"),
            patch("services.vless_subscription_service.VlessSubscriptionService.get_eligible_vless_servers", return_value=[self.default_server]),
            patch("bot.handlers.vless_web.get_system_setting", side_effect=fake_get_setting),
        ):
            headers = {"X-Hwid": "test-client-hwid-wi"}
            resp = await self.client.get("/sub/access/test-token-valid-length-12345678", headers=headers)
            self.assertEqual(resp.status, 200)

            raw_title = resp.headers.get("Profile-Title", "")
            title_b64 = raw_title.removeprefix("base64:")
            decoded_title = base64.b64decode(title_b64).decode("utf-8")
            self.assertEqual(decoded_title, "✦ Just1k")
            self.assertNotIn("Profile-Description", resp.headers)

            raw_ann = resp.headers.get("Announce", "")
            ann = base64.b64decode(raw_ann.removeprefix("base64:")).decode("utf-8")
            self.assertEqual(ann, "Тариф: Семейный")

    async def test_feed_long_tariff_name_truncation(self):
        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(return_value=MagicMock(scalar_one=MagicMock(return_value=0)))

        @asynccontextmanager
        async def fake_session_scope():
            yield mock_session

        from database.models import Tariff
        long_name = "Очень Длинный Эксклюзивный Премиум Тариф С Лимитом Больше Сорока Символов"
        long_tariff = Tariff(id=11, name=long_name, service_type="standard", device_limit=5)
        user_with_tariff = User(
            id=42,
            telegram_id=999,
            is_banned=False,
            is_deleted=False,
            device_limit=5,
            current_tariff_id=11,
            subscription_end=datetime.now(timezone.utc) + timedelta(days=30),
        )

        async def fake_get_setting(session, key, default=None):
            if key == "vless_announce":
                return "План: {tariff}"
            return default

        with (
            patch("bot.handlers.vless_web.session_scope", fake_session_scope),
            patch("database.repositories.vless_subscription_repo.get_subscription_by_token", return_value=self.default_sub),
            patch("database.repositories.users_repo.get_user_by_id", return_value=user_with_tariff),
            patch("database.repositories.tariffs_repo.get_tariff_by_id", return_value=long_tariff),
            patch("services.subscription.SubscriptionService.get_effective_device_limit", return_value=5),
            patch("services.vless_subscription_service.VlessSubscriptionService.get_combined_quota", return_value=(4, 2, 2)),
            patch("database.repositories.vless_subscription_repo.register_hwid_atomic", return_value=(True, 2, 3)),
            patch("services.vless_subscription_service.VlessSubscriptionService.ensure_synced_background"),
            patch("services.vless_subscription_service.VlessSubscriptionService.get_eligible_vless_servers", return_value=[self.default_server]),
            patch("bot.handlers.vless_web.get_system_setting", side_effect=fake_get_setting),
        ):
            headers = {"X-Hwid": "test-client-hwid-long"}
            resp = await self.client.get("/sub/access/test-token-valid-length-12345678", headers=headers)
            self.assertEqual(resp.status, 200)

            raw_title = resp.headers.get("Profile-Title", "")
            title_b64 = raw_title.removeprefix("base64:")
            decoded_title = base64.b64decode(title_b64).decode("utf-8")
            self.assertEqual(decoded_title, "✦ Just1k")
            self.assertNotIn("Profile-Description", resp.headers)

            raw_ann = resp.headers.get("Announce", "")
            ann = base64.b64decode(raw_ann.removeprefix("base64:")).decode("utf-8")
            self.assertEqual(ann, f"План: {long_name[:40]}")

    async def test_profile_title_length_limit_with_device_counter_truncation(self):
        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(return_value=MagicMock(scalar_one=MagicMock(return_value=0)))

        @asynccontextmanager
        async def fake_session_scope():
            yield mock_session

        from database.models import Tariff

        tariff_name = "Ультра Безлимит Премиум 2026"
        tariff = Tariff(id=12, name=tariff_name, service_type="standard", device_limit=5)
        user_with_tariff = User(
            id=101,
            telegram_id=12345678,
            device_limit=5,
            current_tariff_id=12,
            subscription_end=datetime.now(timezone.utc) + timedelta(days=30),
        )

        async def fake_get_system_setting(session, key, default=None):
            if key == "vless_profile_title":
                return "Очень Длинное Название Профиля 2026"
            if key == "vless_profile_show_devices":
                return True
            return default

        with (
            patch("bot.handlers.vless_web.session_scope", fake_session_scope),
            patch("database.repositories.vless_subscription_repo.get_subscription_by_token", return_value=self.default_sub),
            patch("database.repositories.users_repo.get_user_by_id", return_value=user_with_tariff),
            patch("database.repositories.tariffs_repo.get_tariff_by_id", return_value=tariff),
            patch("services.subscription.SubscriptionService.get_effective_device_limit", return_value=5),
            patch("services.vless_subscription_service.VlessSubscriptionService.get_combined_quota", return_value=(4, 2, 2)),
            patch("database.repositories.vless_subscription_repo.register_hwid_atomic", return_value=(True, 2, 3)),
            patch("services.vless_subscription_service.VlessSubscriptionService.ensure_synced_background"),
            patch("services.vless_subscription_service.VlessSubscriptionService.get_eligible_vless_servers", return_value=[self.default_server]),
            patch("bot.handlers.vless_web.get_system_setting", side_effect=fake_get_system_setting),
        ):
            headers = {"X-Hwid": "test-client-hwid-template"}
            resp = await self.client.get("/sub/access/test-token-valid-length-12345678", headers=headers)
            self.assertEqual(resp.status, 200)

            raw_title = resp.headers.get("Profile-Title", "")
            title_b64 = raw_title.removeprefix("base64:")
            decoded_title = base64.b64decode(title_b64).decode("utf-8")
            self.assertLessEqual(len(decoded_title), 25)
            self.assertTrue(decoded_title.endswith("[4/5]"))
            self.assertNotIn("Profile-Description", resp.headers)

    async def test_announce_formatting_with_long_template_and_placeholders(self):
        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(return_value=MagicMock(scalar_one=MagicMock(return_value=0)))

        @asynccontextmanager
        async def fake_session_scope():
            yield mock_session

        repeated_template = "{tariff} - {tariff} - {tariff} - {tariff} | {active} из {devices}"
        from database.models import Tariff

        tariff_name = "Ультра Безлимит Премиум 2026"
        tariff = Tariff(id=12, name=tariff_name, service_type="standard", device_limit=5)
        user_with_tariff = User(
            id=101,
            telegram_id=12345678,
            device_limit=5,
            current_tariff_id=12,
            subscription_end=datetime.now(timezone.utc) + timedelta(days=30),
        )

        async def fake_get_system_setting(session, key, default=None):
            if key == "vless_announce":
                return repeated_template
            return default

        with (
            patch("bot.handlers.vless_web.session_scope", fake_session_scope),
            patch("database.repositories.vless_subscription_repo.get_subscription_by_token", return_value=self.default_sub),
            patch("database.repositories.users_repo.get_user_by_id", return_value=user_with_tariff),
            patch("database.repositories.tariffs_repo.get_tariff_by_id", return_value=tariff),
            patch("services.subscription.SubscriptionService.get_effective_device_limit", return_value=5),
            patch("services.vless_subscription_service.VlessSubscriptionService.get_combined_quota", return_value=(4, 2, 2)),
            patch("database.repositories.vless_subscription_repo.register_hwid_atomic", return_value=(True, 2, 3)),
            patch("services.vless_subscription_service.VlessSubscriptionService.ensure_synced_background"),
            patch("services.vless_subscription_service.VlessSubscriptionService.get_eligible_vless_servers", return_value=[self.default_server]),
            patch("bot.handlers.vless_web.get_system_setting", side_effect=fake_get_system_setting),
        ):
            headers = {"X-Hwid": "test-client-hwid-template-repeated"}
            resp = await self.client.get("/sub/access/test-token-valid-length-12345678", headers=headers)
            self.assertEqual(resp.status, 200)

            raw_title = resp.headers.get("Profile-Title", "")
            title_b64 = raw_title.removeprefix("base64:")
            decoded_title = base64.b64decode(title_b64).decode("utf-8")
            self.assertEqual(decoded_title, "✦ Just1k")

            raw_ann = resp.headers.get("Announce", "")
            ann = base64.b64decode(raw_ann.removeprefix("base64:")).decode("utf-8")
            self.assertLessEqual(len(ann), 200)
            self.assertTrue(ann.endswith("4 из 5"))
            self.assertNotIn("Profile-Description", resp.headers)

    async def test_device_metadata_extracted_from_headers(self):
        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(return_value=MagicMock(scalar_one=MagicMock(return_value=0)))

        @asynccontextmanager
        async def fake_session_scope():
            yield mock_session

        with (
            patch("bot.handlers.vless_web.session_scope", fake_session_scope),
            patch("database.repositories.vless_subscription_repo.get_subscription_by_token", return_value=self.default_sub),
            patch("database.repositories.users_repo.get_user_by_id", return_value=self.default_user),
            patch("services.subscription.SubscriptionService.get_effective_device_limit", return_value=2),
            patch("database.repositories.vless_subscription_repo.register_hwid_atomic", return_value=(True, 1, 2)) as mock_register,
            patch("services.vless_subscription_service.VlessSubscriptionService.ensure_synced_background"),
            patch("services.vless_subscription_service.VlessSubscriptionService.get_eligible_vless_servers", return_value=[self.default_server]),
        ):
            headers = {
                "X-Hwid": "test-client-hwid-99",
                "X-Device-Os": "Android 14",
                "X-Device-Model": "Pixel 8 Pro",
                "User-Agent": "IncyClient/2.1.0",
            }
            resp = await self.client.get("/sub/access/test-token-valid-length-12345678", headers=headers)
            self.assertEqual(resp.status, 200)
            mock_register.assert_called_once()
            _, kwargs = mock_register.call_args
            self.assertEqual(
                kwargs.get("device_info"),
                {
                    "client": "IncyClient 2.1.0",
                    "os": "Android 14",
                    "model": "Pixel 8 Pro",
                    "ua": "IncyClient/2.1.0",
                },
            )

    async def test_handle_vless_feed_generates_links_after_hwid_registration(self):
        from database.repositories import vless_subscription_repo
        from services.vless_subscription_service import VlessSubscriptionService

        mock_session = AsyncMock()

        @asynccontextmanager
        async def fake_session_scope():
            yield mock_session

        initial_sub = VlessSubscription(
            id=10,
            user_id=42,
            token="test-token-valid-length-12345678",
            uuid="initial-uuid-1111",
            is_active=True,
            active_hwids={},
        )
        srv = Server(
            id=1,
            name="NL-Node",
            country_flag="🇳🇱",
            is_active=True,
            health_state=ServerHealthState.ONLINE,
            capabilities=["vless"],
            extra_data={"domain": "nl.example.com", "vless_port": 443},
        )

        async def fake_register_hwid_atomic(session, sub_id, hwid, device_info=None):
            initial_sub.uuid = "rotated-new-uuid-2222"
            return True, 1, 3

        async def fake_refresh(instance):
            pass

        mock_session.execute = AsyncMock(return_value=MagicMock(scalar_one=MagicMock(return_value=0)))
        mock_session.refresh = AsyncMock(side_effect=fake_refresh)

        with (
            patch("bot.handlers.vless_web.session_scope", fake_session_scope),
            patch("database.repositories.vless_subscription_repo.get_subscription_by_token", return_value=initial_sub),
            patch("database.repositories.users_repo.get_user_by_id", return_value=self.default_user),
            patch("services.subscription.SubscriptionService.check_vpn_access", return_value=True),
            patch.object(VlessSubscriptionService, "get_eligible_vless_servers", new=AsyncMock(return_value=[srv])),
            patch.object(vless_subscription_repo, "register_hwid_atomic", side_effect=fake_register_hwid_atomic),
            patch.object(VlessSubscriptionService, "ensure_synced_background", return_value=None),
        ):
            resp = await self.client.get(
                "/sub/access/test-token-valid-length-12345678",
                headers={"X-Hwid": "hwid-test-device-123"},
            )
            self.assertEqual(resp.status, 200)
            body = await resp.text()
            decoded = base64.b64decode(body).decode("utf-8")
            self.assertIn("rotated-new-uuid-2222", decoded)
            self.assertNotIn("initial-uuid-1111", decoded)

    async def test_invalid_hwid_format_returns_400(self):
        headers = {"X-Hwid": "bad$$$"}
        resp = await self.client.get("/sub/access/test-token-valid-length-12345678", headers=headers)
        self.assertEqual(resp.status, 400)
        self.assertEqual(resp.headers.get("x-hwid-required"), "true")


class TestVlessSubscriptionRepoLogic(unittest.IsolatedAsyncioTestCase):
    """Test suite for atomic HWID registration and pruning in repository."""


    def test_prune_stale_hwids(self):
        now = datetime.now(timezone.utc)
        recent_ts = (now - timedelta(hours=5)).isoformat()
        stale_ts = (now - timedelta(hours=50)).isoformat()

        hwids = {
            "hwid-fresh": recent_ts,
            "hwid-old": stale_ts,
        }

        pruned = vless_subscription_repo.prune_stale_hwids(hwids, ttl_hours=48)
        self.assertIn("hwid-fresh", pruned)
        self.assertNotIn("hwid-old", pruned)

    def test_prune_stale_hwids_backward_compatibility(self):
        now = datetime.now(timezone.utc)
        recent_ts = (now - timedelta(hours=5)).isoformat()
        stale_ts = (now - timedelta(hours=50)).isoformat()

        hwids = {
            "legacy-fresh": recent_ts,
            "legacy-old": stale_ts,
            "meta-fresh": {"last_seen": recent_ts, "os": "Android", "model": "Pixel 8"},
            "meta-old": {"last_seen": stale_ts, "os": "iOS", "model": "iPhone 14"},
        }

        pruned = vless_subscription_repo.prune_stale_hwids(hwids, ttl_hours=48)
        self.assertIn("legacy-fresh", pruned)
        self.assertNotIn("legacy-old", pruned)
        self.assertIn("meta-fresh", pruned)
        self.assertNotIn("meta-old", pruned)
        self.assertIsInstance(pruned["meta-fresh"], dict)
        self.assertEqual(pruned["meta-fresh"]["model"], "Pixel 8")

    async def test_get_total_active_hwid_count_empty(self):
        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = []
        mock_session.execute.return_value = mock_result

        users, devices = await vless_subscription_repo.get_active_vless_counts(mock_session)
        self.assertEqual(users, 0)
        self.assertEqual(devices, 0)

        total = await vless_subscription_repo.get_total_active_hwid_count(mock_session)
        self.assertEqual(total, 0)

    async def test_get_total_active_hwid_count_mixed(self):
        now = datetime.now(timezone.utc)
        recent_ts = (now - timedelta(hours=2)).isoformat()
        stale_ts = (now - timedelta(hours=60)).isoformat()

        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [
            {"hw1": recent_ts, "hw2": stale_ts},
            {},
            None,
            {"hw3": {"last_seen": recent_ts, "model": "Pixel"}, "hw4": {"last_seen": recent_ts, "model": "iPad"}},
        ]
        mock_session.execute.return_value = mock_result

        users, devices = await vless_subscription_repo.get_active_vless_counts(mock_session)
        self.assertEqual(users, 2)
        self.assertEqual(devices, 3)

        total = await vless_subscription_repo.get_total_active_hwid_count(mock_session)
        self.assertEqual(total, 3)

    async def test_register_hwid_atomic_with_device_metadata(self):
        sub = VlessSubscription(
            id=1,
            user_id=100,
            token="tok1234567890123456",
            uuid="abc-uuid",
            is_active=True,
            active_hwids={},
        )

        mock_session = AsyncMock()
        mock_session.scalar = AsyncMock(return_value=sub)
        mock_session.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=sub)))

        device_info = {"os": "iOS 17.5", "model": "iPhone15,2", "ua": "IncyClient/1.2"}
        allowed, count, limit = await vless_subscription_repo.register_hwid_atomic(
            mock_session,
            subscription_id=sub.id,
            hwid="hwid-ios-1",
            effective_limit=2,
            device_info=device_info,
        )
        self.assertTrue(allowed)
        self.assertEqual(count, 1)
        self.assertIn("hwid-ios-1", sub.active_hwids)
        entry = sub.active_hwids["hwid-ios-1"]
        self.assertIsInstance(entry, dict)
        self.assertEqual(entry["os"], "iOS 17.5")
        self.assertEqual(entry["model"], "iPhone15,2")
        self.assertEqual(entry["ua"], "IncyClient/1.2")
        self.assertIn("last_seen", entry)

        # Refresh with updated model
        updated_info = {"os": "iOS 17.5", "model": "iPhone15,2-Updated"}
        allowed, count, limit = await vless_subscription_repo.register_hwid_atomic(
            mock_session,
            subscription_id=sub.id,
            hwid="hwid-ios-1",
            effective_limit=2,
            device_info=updated_info,
        )
        self.assertTrue(allowed)
        entry2 = sub.active_hwids["hwid-ios-1"]
        self.assertEqual(entry2["model"], "iPhone15,2-Updated")
        self.assertEqual(entry2["ua"], "IncyClient/1.2")

    async def test_register_hwid_atomic_throttles_last_seen_for_unchanged_device(self):
        recent_dt = datetime.now(timezone.utc) - timedelta(minutes=5)
        sub = VlessSubscription(
            id=1,
            user_id=100,
            token="tok1234567890123456",
            uuid="abc-uuid",
            is_active=True,
            active_hwids={
                "hwid-ios-1": {
                    "last_seen": recent_dt.isoformat(),
                    "os": "iOS 17.5",
                    "model": "iPhone15,2",
                }
            },
        )

        mock_session = AsyncMock()
        mock_session.flush = AsyncMock()
        mock_session.scalar = AsyncMock(return_value=sub)
        mock_session.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=sub)))

        device_info = {"os": "iOS 17.5", "model": "iPhone15,2"}
        allowed, count, limit = await vless_subscription_repo.register_hwid_atomic(
            mock_session,
            subscription_id=sub.id,
            hwid="hwid-ios-1",
            effective_limit=2,
            device_info=device_info,
        )
        self.assertTrue(allowed)
        self.assertEqual(count, 1)
        mock_session.flush.assert_not_called()
        self.assertEqual(sub.active_hwids["hwid-ios-1"]["last_seen"], recent_dt.isoformat())

    async def test_register_hwid_atomic_quota_enforcement(self):
        now = datetime.now(timezone.utc)
        sub = VlessSubscription(
            id=1,
            user_id=100,
            token="tok1234567890123456",
            uuid="abc-uuid",
            is_active=True,
            active_hwids={
                "hwid-1": now.isoformat(),
            },
        )

        mock_session = AsyncMock()
        mock_session.scalar = AsyncMock(return_value=sub)
        mock_session.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=sub)))

        # Effective limit is 1, so registering a new hwid-2 must be rejected
        allowed, count, limit = await vless_subscription_repo.register_hwid_atomic(
            mock_session,
            subscription_id=sub.id,
            hwid="hwid-2",
            effective_limit=1,
        )
        self.assertFalse(allowed)
        self.assertEqual(count, 1)

        # Effective limit is 0 (all slots taken by AWG) must reject even existing HWID without wiping active_hwids
        allowed, count, limit = await vless_subscription_repo.register_hwid_atomic(
            mock_session,
            subscription_id=sub.id,
            hwid="hwid-1",
            effective_limit=0,
        )
        self.assertFalse(allowed)
        self.assertEqual(count, 1)

        # Existing device hwid-1 must be allowed and timestamp updated when quota available
        allowed, count, limit = await vless_subscription_repo.register_hwid_atomic(
            mock_session,
            subscription_id=sub.id,
            hwid="hwid-1",
            effective_limit=1,
        )
        self.assertTrue(allowed)
        self.assertEqual(count, 1)

    async def test_register_hwid_atomic_network_grace_window(self):
        now = datetime.now(timezone.utc)
        sub = VlessSubscription(
            id=1,
            user_id=100,
            token="tok1234567890123456",
            uuid="abc-uuid",
            is_active=True,
            active_hwids={},
        )
        user_2h_expired = User(
            id=100,
            telegram_id=888,
            is_banned=False,
            is_deleted=False,
            financial_hold=False,
            subscription_end=now - timedelta(hours=2),
        )

        mock_session = AsyncMock()
        mock_session.scalar = AsyncMock(side_effect=[100, user_2h_expired, sub])
        mock_session.execute = AsyncMock(side_effect=[
            MagicMock(scalar_one_or_none=MagicMock(return_value=100)),
            MagicMock(scalar_one_or_none=MagicMock(return_value=user_2h_expired)),
            MagicMock(scalar_one_or_none=MagicMock(return_value=sub)),
        ])

        # User expired by 2 hours (< 4h grace window) must be allowed
        allowed, count, limit = await vless_subscription_repo.register_hwid_atomic(
            mock_session,
            subscription_id=sub.id,
            hwid="grace-hwid",
            effective_limit=2,
        )
        self.assertTrue(allowed)
        self.assertIn("grace-hwid", sub.active_hwids)

        # User expired by 10 hours (> 4h grace window) must be rejected
        user_10h_expired = User(
            id=100,
            telegram_id=888,
            is_banned=False,
            is_deleted=False,
            financial_hold=False,
            subscription_end=now - timedelta(hours=10),
        )
        mock_session.execute = AsyncMock(side_effect=[
            MagicMock(scalar_one_or_none=MagicMock(return_value=100)),
            MagicMock(scalar_one_or_none=MagicMock(return_value=user_10h_expired)),
            MagicMock(scalar_one_or_none=MagicMock(return_value=sub)),
        ])
        mock_session.scalar = AsyncMock(side_effect=[100, user_10h_expired, sub])
        allowed, count, limit = await vless_subscription_repo.register_hwid_atomic(
            mock_session,
            subscription_id=sub.id,
            hwid="too-late-hwid",
            effective_limit=2,
        )
        self.assertFalse(allowed)
        self.assertNotIn("too-late-hwid", sub.active_hwids)

    async def test_reset_hwids(self):
        now = datetime.now(timezone.utc)
        sub = VlessSubscription(
            id=1,
            user_id=100,
            token="tok1234567890123456",
            uuid="abc-uuid",
            is_active=True,
            active_hwids={"dev1": now.isoformat()},
            node_traffic_snapshots={"1": {"epoch": "e1", "up": 100, "down": 200}},
        )
        mock_session = AsyncMock()
        mock_session.scalar = AsyncMock(return_value=sub)
        mock_session.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=sub)))

        old_uuid, new_uuid = await vless_subscription_repo.reset_hwids(mock_session, sub.id)
        self.assertEqual(sub.active_hwids, {})
        self.assertEqual(sub.node_traffic_snapshots, {"_fresh": True})
        self.assertEqual(old_uuid, "abc-uuid")
        self.assertNotEqual(new_uuid, "abc-uuid")
        self.assertEqual(sub.uuid, new_uuid)
        self.assertEqual(sub.version, 2)
        self.assertEqual(sub.pending_revoked_uuids, ["abc-uuid"])

    async def test_reset_hwids_empty_noop(self):
        sub = VlessSubscription(
            id=1,
            user_id=100,
            token="tok1234567890123456",
            uuid="abc-uuid",
            is_active=True,
            version=1,
            active_hwids={},
        )
        mock_session = AsyncMock()
        mock_session.scalar = AsyncMock(return_value=sub)
        mock_session.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=sub)))

        old_uuid, new_uuid = await vless_subscription_repo.reset_hwids(mock_session, sub.id)
        self.assertIsNone(old_uuid)
        self.assertIsNone(new_uuid)
        self.assertEqual(sub.uuid, "abc-uuid")
        self.assertEqual(sub.version, 1)

    async def test_get_or_create_subscription_race_condition(self):
        from sqlalchemy.exc import IntegrityError
        from database.models import VlessSubscription
        from database.repositories import vless_subscription_repo

        existing_sub = VlessSubscription(
            id=5,
            user_id=42,
            token="winning-token-12345678",
            uuid="winning-uuid-1234",
            is_active=True,
            active_hwids={},
        )

        @asynccontextmanager
        async def fake_nested():
            yield

        mock_session = AsyncMock()
        mock_session.add = MagicMock()
        mock_session.begin_nested = fake_nested
        mock_session.scalar = AsyncMock(side_effect=[None, existing_sub])
        mock_session.execute = AsyncMock(side_effect=[
            MagicMock(scalar_one_or_none=MagicMock(return_value=None)),
            MagicMock(scalar_one_or_none=MagicMock(return_value=existing_sub)),
        ])
        mock_session.flush = AsyncMock(side_effect=IntegrityError("duplicate key", params=None, orig=Exception()))

        sub = await vless_subscription_repo.get_or_create_subscription(mock_session, 42)
        self.assertEqual(sub.id, 5)
        self.assertEqual(sub.token, "winning-token-12345678")

    async def test_register_hwid_atomic_rejects_banned_user_under_lock(self):
        from database.models import User, VlessSubscription
        from database.repositories import vless_subscription_repo

        user = User(id=1, telegram_id=123, is_banned=True, subscription_end=None)
        sub = VlessSubscription(id=1, user_id=1, token="tok1234567890123456", uuid="abc-uuid", is_active=True, active_hwids={})

        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(side_effect=[
            MagicMock(scalar_one_or_none=MagicMock(return_value=1)),
            MagicMock(scalar_one_or_none=MagicMock(return_value=user)),
            MagicMock(scalar_one_or_none=MagicMock(return_value=sub)),
        ])

        allowed, count, limit = await vless_subscription_repo.register_hwid_atomic(
            mock_session,
            subscription_id=1,
            hwid="hwid-test",
            effective_limit=2,
        )
        self.assertFalse(allowed)
        self.assertEqual(limit, 0)

    async def test_bump_subscription_version_under_row_lock(self):
        from database.models import VlessSubscription
        from database.repositories import vless_subscription_repo

        sub = VlessSubscription(id=99, user_id=1, uuid="test-uuid", version=3)
        session = AsyncMock()
        session.scalar = AsyncMock(return_value=sub)
        mock_res = MagicMock()
        mock_res.scalar_one_or_none = MagicMock(return_value=sub)
        session.execute = AsyncMock(return_value=mock_res)

        updated = await vless_subscription_repo.bump_subscription_version(session, 99)
        self.assertIsNotNone(updated)
        self.assertEqual(updated.version, 4)
        session.flush.assert_called_once()


class TestVlessSubscriptionService(unittest.IsolatedAsyncioTestCase):
    """Test suite for VlessSubscriptionService methods."""

    def test_build_subscription_url(self):
        url = VlessSubscriptionService.build_subscription_url("my-token-123", domain="bot.example.com")
        self.assertEqual(url, "https://bot.example.com/sub/access/my-token-123")

    def test_build_subscription_url_no_domain_raises(self):
        with patch.dict(os.environ, {"DOMAIN": "", "BOT_DOMAIN": ""}, clear=True):
            with patch("config.settings.get_settings", side_effect=Exception("No settings")):
                with self.assertRaises(ValueError):
                    VlessSubscriptionService.build_subscription_url("my-token-123")

    def test_generate_vless_links(self):
        sub = VlessSubscription(
            id=1,
            user_id=100,
            token="tok",
            uuid="12345678-1234-5678-1234-567812345678",
            is_active=True,
        )
        srv = Server(
            id=1,
            name="DE Server",
            country_flag="🇩🇪",
            is_active=True,
            health_state=ServerHealthState.ONLINE,
            protocol="vless",
            capabilities=["vless"],
            api_url="https://de.example.com:8443",
            extra_data={"domain": "de.example.com", "vless_port": 443},
        )
        links = VlessSubscriptionService.generate_vless_links(sub, [srv])
        self.assertEqual(len(links), 1)
        self.assertTrue(links[0].startswith("vless://12345678-1234-5678-1234-567812345678@de.example.com:443"))
        self.assertIn("flow=xtls-rprx-vision", links[0])
        self.assertIn("security=tls", links[0])
        self.assertIn("alpn=http/1.1", links[0])
        self.assertIn("fp=firefox", links[0])
        expected_tag = urllib.parse.quote("🇩🇪 DE Server")
        self.assertIn(f"#{expected_tag}", links[0])
        self.assertNotIn("?serverDescription=", links[0])

        # Test per-server fp override via extra_data
        srv_custom_fp = Server(
            id=2,
            name="NL Server",
            country_flag="🇳🇱",
            is_active=True,
            health_state=ServerHealthState.ONLINE,
            protocol="vless",
            capabilities=["vless"],
            api_url="https://nl.example.com:8443",
            extra_data={"domain": "nl.example.com", "vless_port": 443, "fp": "chrome"},
        )
        custom_links = VlessSubscriptionService.generate_vless_links(sub, [srv_custom_fp])
        self.assertEqual(len(custom_links), 1)
        self.assertIn("fp=chrome", custom_links[0])

    def test_generate_incy_routing_header_invariants_and_fallbacks(self):
        # 1. Standard defaults
        std_header = VlessSubscriptionService.generate_incy_routing_header()
        self.assertTrue(std_header.startswith("incy://routing/onadd/"))
        std_json = json.loads(base64.b64decode(std_header.removeprefix("incy://routing/onadd/")).decode("utf-8"))
        self.assertEqual(std_json.get("Name"), "✦ Just1k")
        self.assertIs(std_json.get("useChunkFiles"), True)
        self.assertEqual(std_json.get("LastUpdated"), "1793318400")
        self.assertIn("geosite:category-ru", std_json.get("DirectSites", []))
        self.assertIn("geosite:tld-ru", std_json.get("DirectSites", []))

        # 2. White Internet defaults (direct_ru=False)
        wl_header = VlessSubscriptionService.generate_incy_routing_header(direct_ru=False, scheme="happ")
        self.assertTrue(wl_header.startswith("happ://routing/onadd/"))
        wl_json = json.loads(base64.b64decode(wl_header.removeprefix("happ://routing/onadd/")).decode("utf-8"))
        self.assertEqual(wl_json.get("Name"), "✦ Just1k WL")
        self.assertIs(wl_json.get("useChunkFiles"), True)
        self.assertEqual(wl_json.get("LastUpdated"), "1793318400")
        self.assertEqual(wl_json.get("DirectSites"), [])

        # 3. None and edge-case fallbacks
        edge_header = VlessSubscriptionService.generate_incy_routing_header(
            profile_name=None,
            direct_ru=False,
            last_updated=None,
            scheme=None,
        )
        self.assertTrue(edge_header.startswith("incy://routing/onadd/"))
        edge_json = json.loads(base64.b64decode(edge_header.removeprefix("incy://routing/onadd/")).decode("utf-8"))
        self.assertEqual(edge_json.get("Name"), "✦ Just1k WL")
        self.assertEqual(edge_json.get("LastUpdated"), "1793318400")

    def test_generate_vless_links_forces_client_port_443_when_relay_port_configured(self):
        sub = VlessSubscription(
            id=1,
            user_id=100,
            token="tok",
            uuid="12345678-1234-5678-1234-567812345678",
            is_active=True,
        )
        srv = Server(
            id=1,
            name="Relay Node",
            country_flag="🇩🇪",
            is_active=True,
            health_state=ServerHealthState.ONLINE,
            protocol="vless",
            capabilities=["vless"],
            api_url="https://relay.example.com:8443",
            extra_data={"domain": "relay.example.com", "vless_port": 10443},
        )
        links = VlessSubscriptionService.generate_vless_links(sub, [srv])
        self.assertEqual(len(links), 1)
        self.assertTrue(links[0].startswith("vless://12345678-1234-5678-1234-567812345678@relay.example.com:443"))

    async def test_sync_all_active_to_server_concurrency_guard(self):
        """sync_all_active_to_server must reject concurrent execution for the same server_id."""
        VlessSubscriptionService._ACTIVE_SERVER_SYNCS.add(99)
        try:
            res = await VlessSubscriptionService.sync_all_active_to_server(99)
            self.assertEqual(res, 0)
        finally:
            VlessSubscriptionService._ACTIVE_SERVER_SYNCS.discard(99)

    async def test_create_device_enforces_combined_quota(self):
        from database.models import Server, User
        from services.device_service import DeviceLimitExceeded, DeviceService
        from services.slots_cache import ServerPeerSnapshot

        user = User(
            id=1,
            telegram_id=123,
            device_limit=2,
            is_deleted=False,
            is_banned=False,
            financial_hold=False,
            subscription_end=datetime.now(timezone.utc) + timedelta(days=10),
            device_creations_today=0,
            last_creation_date=None,
        )
        server = Server(id=1, name="Test", is_active=True, max_clients=100)
        snapshot = ServerPeerSnapshot(server_id=1, peer_ids=frozenset(), captured_at=datetime.now(timezone.utc))

        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(side_effect=[
            # 1. select User FOR UPDATE
            MagicMock(scalar_one=MagicMock(return_value=user)),
            # 2. select Server FOR UPDATE
            MagicMock(scalar_one_or_none=MagicMock(return_value=server)),
            # 3. select user profiles -> []
            MagicMock(scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[])))),
            # 4. duplicate -> None
            MagicMock(scalar_one_or_none=MagicMock(return_value=None)),
            # 5. user_count -> 1 (with vless_count=1 -> 1 + 1 = 2 >= device_limit 2 -> DeviceLimitExceeded)
            MagicMock(scalar_one=MagicMock(return_value=1)),
        ])

        with (
            patch("services.device_service.is_admin", return_value=False),
            patch("database.repositories.vless_subscription_repo.get_active_hwid_count", new=AsyncMock(return_value=1)),
        ):
            with self.assertRaises(DeviceLimitExceeded):
                await DeviceService.create_device(
                    mock_session,
                    user_id=1,
                    server_id=1,
                    snapshot=snapshot,
                    vless_count=1,
                )

    async def test_sync_user_to_nodes_handles_version_conflict_by_bumping(self):
        from database.models import User, Server, VlessSubscription
        from services.vless_subscription_service import VlessSubscriptionService

        session = AsyncMock()
        user = User(
            id=10,
            telegram_id=1010,
            subscription_end=now_utc() + timedelta(days=5),
            is_banned=False,
            is_deleted=False,
            financial_hold=False,
        )
        sub = VlessSubscription(id=20, user_id=10, uuid="test-uuid", is_active=True, version=1)
        srv = Server(id=1, api_url="https://node.com:8443", api_key="secret", capabilities=["vless"])

        session.get = AsyncMock(return_value=user)

        def do_bump(s, sid):
            sub.version = sub.version + 1
            return sub

        with (
            patch("database.repositories.vless_subscription_repo.get_subscription_by_user_id", new=AsyncMock(return_value=sub)),
            patch("services.vless_subscription_service.VlessSubscriptionService.get_configured_vless_servers", new=AsyncMock(return_value=[srv])),
            patch("database.repositories.vless_subscription_repo.bump_subscription_version", new=AsyncMock(side_effect=do_bump)) as mock_bump,
            patch.object(VlessSubscriptionService, "_execute_sync_to_nodes_with_status") as mock_sync_status,
        ):
            # First attempt triggers version bump; second attempt succeeds
            mock_sync_status.side_effect = [
                ({1: False}, True),
                ({1: True}, False),
            ]
            res = await VlessSubscriptionService.sync_user_to_nodes(session, 10)
            assert res == {1: True}
            mock_bump.assert_called_once_with(session, 20)
            assert sub.version == 2
            assert mock_sync_status.call_count == 2
            assert sub.last_synced_version == 2

    async def test_sweep_converges_unsynced_server_epochs(self):
        from database.models import Server
        from services.workers.cleanup import _sweep_vless_pending_revocations

        srv = Server(
            id=42,
            is_active=True,
            capabilities=["vless"],
            xray_instance_epoch="new_epoch_999",
            extra_data={"vless_synced_epoch": "old_epoch_111"},
        )

        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(return_value=MagicMock(scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[])))))

        @asynccontextmanager
        async def fake_session_scope():
            yield mock_session

        with (
            patch("services.workers.cleanup.session_scope", fake_session_scope),
            patch("database.repositories.vless_subscription_repo.get_subscriptions_with_pending_revocations", new=AsyncMock(return_value=[])),
            patch("services.vless_subscription_service.VlessSubscriptionService.get_configured_vless_servers", new=AsyncMock(return_value=[srv])),
            patch("services.vless_subscription_service.VlessSubscriptionService.sync_all_active_to_server", new=AsyncMock(return_value=5)) as mock_sync_server,
        ):
            await _sweep_vless_pending_revocations()
            mock_sync_server.assert_called_once_with(42)

    async def test_ensure_synced_background_revocation_failure_retains_pending_uuid(self):
        from config.enums import ServerLifecycleStatus
        from database.models import Server, User, VlessSubscription
        from services.vless_subscription_service import VlessSubscriptionService

        user = User(id=10, is_deleted=False, is_banned=False, financial_hold=False, subscription_end=None)
        sub = VlessSubscription(
            id=1,
            user_id=10,
            uuid="curr-uuid",
            version=1,
            is_active=True,
            pending_revoked_uuids=["pending-old-uuid"],
        )
        srv1 = Server(id=1, is_active=True, lifecycle_status=ServerLifecycleStatus.ACTIVE, capabilities=["vless"], api_url="https://s1.example.com:8443", api_key="k1")
        srv2 = Server(id=2, is_active=False, lifecycle_status=ServerLifecycleStatus.ACTIVE, capabilities=["vless"], api_url="https://s2.example.com:8443", api_key="k2")

        mock_session = AsyncMock()
        mock_session.get.return_value = user

        @asynccontextmanager
        async def fake_session_scope():
            yield mock_session

        with (
            patch("database.connection.session_scope", fake_session_scope),
            patch("database.repositories.vless_subscription_repo.get_subscription_by_user_id", new=AsyncMock(return_value=sub)),
            patch.object(VlessSubscriptionService, "get_configured_vless_servers", new=AsyncMock(return_value=[srv1])),
            patch.object(VlessSubscriptionService, "get_revocation_target_servers", new=AsyncMock(return_value=[srv1, srv2])),
            patch.object(VlessSubscriptionService, "_execute_sync_to_nodes_with_status", new=AsyncMock(return_value=({1: True}, False))),
            patch.object(VlessSubscriptionService, "_execute_deprovision_on_nodes", new=AsyncMock(return_value={1: True, 2: False})) as mock_deprov,
            patch("database.repositories.vless_subscription_repo.pop_pending_revoked_uuid", new=AsyncMock()) as mock_pop,
        ):
            task = VlessSubscriptionService.ensure_synced_background(10, is_active=True)
            self.assertIsNotNone(task)
            await task
            mock_deprov.assert_called_once()
            mock_pop.assert_not_called()

    async def test_execute_sync_and_deprovision_strict_all_inbounds_verified(self):
        from services.vless_subscription_service import VlessSubscriptionService
        from services.xray_node_client import SyncResponse, SyncResult

        targets = [(10, "https://node1.example.com:8443", "key1")]

        # Case 1: result == "applied" but all_inbounds_verified is False -> results[10] must be False
        partial_resp = SyncResponse(
            result=SyncResult.APPLIED,
            verified_inbounds=["just1k-vless-direct"],
            all_inbounds_verified=False,
            raw_data={"result": "applied"},
        )
        with patch("services.vless_subscription_service.XrayNodeClient.sync_client", new=AsyncMock(return_value=partial_resp)):
            sync_res, _ = await VlessSubscriptionService._execute_sync_to_nodes_with_status(
                targets, "uuid-123", True, version=1
            )
            self.assertEqual(sync_res, {10: False})

            deprov_res = await VlessSubscriptionService._execute_deprovision_on_nodes(
                targets, "uuid-123", version=1
            )
            self.assertEqual(deprov_res, {10: False})

        # Case 2: result == "applied" and all_inbounds_verified is True -> results[10] must be True
        full_resp = SyncResponse(
            result=SyncResult.APPLIED,
            verified_inbounds=["just1k-vless-direct"],
            all_inbounds_verified=True,
            raw_data={"result": "applied"},
        )
        with patch("services.vless_subscription_service.XrayNodeClient.sync_client", new=AsyncMock(return_value=full_resp)):
            sync_res, _ = await VlessSubscriptionService._execute_sync_to_nodes_with_status(
                targets, "uuid-123", True, version=1
            )
            self.assertEqual(sync_res, {10: True})

            deprov_res = await VlessSubscriptionService._execute_deprovision_on_nodes(
                targets, "uuid-123", version=1
            )
            self.assertEqual(deprov_res, {10: True})

    async def test_get_revocation_target_servers_includes_inactive_servers(self):
        from config.enums import ServerLifecycleStatus
        from database.models import Server
        from services.vless_subscription_service import VlessSubscriptionService

        active_srv = Server(
            id=1,
            is_active=True,
            lifecycle_status=ServerLifecycleStatus.ACTIVE,
            capabilities=["vless"],
            api_url="https://s1.example.com:8443",
            api_key="k1",
        )
        inactive_but_alive_srv = Server(
            id=2,
            is_active=False,
            lifecycle_status=ServerLifecycleStatus.ACTIVE,
            capabilities=["vless"],
            api_url="https://s2.example.com:8443",
            api_key="k2",
        )
        non_vless_srv = Server(
            id=3,
            is_active=True,
            lifecycle_status=ServerLifecycleStatus.ACTIVE,
            capabilities=["awg"],
            api_url="https://s3.example.com:8443",
            api_key="k3",
        )
        _decommissioned_srv = Server(
            id=4,
            is_active=False,
            lifecycle_status=ServerLifecycleStatus.DECOMMISSIONED,
            capabilities=["vless"],
            api_url="https://s4.example.com:8443",
            api_key="k4",
        )

        session = AsyncMock()
        mock_res = MagicMock()
        mock_res.scalars = MagicMock(return_value=MagicMock(all=MagicMock(return_value=[active_srv, inactive_but_alive_srv, non_vless_srv])))
        session.execute = AsyncMock(return_value=mock_res)

        targets = await VlessSubscriptionService.get_revocation_target_servers(session)
        target_ids = [s.id for s in targets]
        self.assertIn(1, target_ids)
        self.assertIn(2, target_ids)
        self.assertNotIn(3, target_ids)
        self.assertNotIn(4, target_ids)

    def test_generate_vless_links_skips_invalid_domain_or_missing_fqdn(self):
        from config.enums import ServerHealthState
        from database.models import Server, VlessSubscription
        from services.vless_subscription_service import VlessSubscriptionService

        sub = VlessSubscription(id=1, uuid="11111111-2222-3333-4444-555555555555", is_active=True)

        srv_no_domain = Server(
            id=1,
            name="No Domain",
            is_active=True,
            health_state=ServerHealthState.ONLINE,
            capabilities=["vless"],
            api_url="https://192.168.1.1:8443",
            extra_data={},
        )
        srv_invalid_domain = Server(
            id=2,
            name="Invalid Domain",
            is_active=True,
            health_state=ServerHealthState.ONLINE,
            capabilities=["vless"],
            api_url="https://node.example.com:8443",
            extra_data={"domain": "localhost:8443"},
        )
        srv_no_dot = Server(
            id=3,
            name="No Dot Domain",
            is_active=True,
            health_state=ServerHealthState.ONLINE,
            capabilities=["vless"],
            api_url="https://node.example.com:8443",
            extra_data={"domain": "nodomain"},
        )
        srv_valid = Server(
            id=4,
            name="Valid Domain",
            is_active=True,
            health_state=ServerHealthState.ONLINE,
            capabilities=["vless"],
            api_url="https://node.example.com:8443",
            extra_data={"domain": "valid.example.com"},
        )

        links = VlessSubscriptionService.generate_vless_links(
            sub, [srv_no_domain, srv_invalid_domain, srv_no_dot, srv_valid]
        )
        self.assertEqual(len(links), 1)
        self.assertIn("@valid.example.com:443", links[0])


class TestVlessSubscriptionDomainAndBotResolution(unittest.TestCase):
    """Verify dynamic bot username discovery, support URLs, and SSOT domain extraction."""

    def test_vless_links_use_api_url_hostname_as_ssot(self):
        sub = VlessSubscription(
            id=1,
            user_id=10,
            token="token1234567890123456",
            uuid="11111111-2222-3333-4444-555555555555",
            is_active=True,
        )
        # Server has api_url with 'nl.just1k.pro' and legacy extra_data domain with 'nl.just1k.best'
        server = Server(
            id=12,
            name="Нидерланды",
            country_flag="🇳🇱",
            is_active=True,
            health_state=ServerHealthState.ONLINE,
            protocol="vless",
            api_url="https://nl.just1k.pro:8444",
            extra_data={"vless_port": 443},
        )
        links = VlessSubscriptionService.generate_vless_links(sub, [server])
        self.assertEqual(len(links), 1)
        self.assertIn("@nl.just1k.pro:443", links[0])

    def test_extract_node_targets_prefers_server_api_url_for_vless(self):
        server = Server(
            id=12,
            name="Нидерланды",
            country_flag="🇳🇱",
            is_active=True,
            protocol="vless",
            api_key="secret-key-123",
            api_url="https://nl.just1k.pro:8444",
            extra_data={"xray_api_url": "https://nl.just1k.best:8444"},
        )
        targets = VlessSubscriptionService._extract_node_targets([server])
        self.assertEqual(len(targets), 1)
        srv_id, api_url, api_key = targets[0]
        self.assertEqual(srv_id, 12)
        self.assertEqual(api_url, "https://nl.just1k.pro:8444")
        self.assertEqual(api_key, "secret-key-123")

    def test_extract_node_targets_uses_xray_api_url_for_mixed_awg_server(self):
        server = Server(
            id=6,
            name="Польша",
            country_flag="🇵🇱",
            is_active=True,
            protocol="amneziawg3.1",
            capabilities=["awg", "vless"],
            api_key="secret-key-456",
            api_url="https://pl.just1k.pro:8443",
            extra_data={"xray_api_url": "https://pl.just1k.pro:8444"},
        )
        targets = VlessSubscriptionService._extract_node_targets([server])
        self.assertEqual(len(targets), 1)
        srv_id, api_url, api_key = targets[0]
        self.assertEqual(srv_id, 6)
        self.assertEqual(api_url, "https://pl.just1k.pro:8444")
        self.assertEqual(api_key, "secret-key-456")

    def test_extract_node_targets_skips_mixed_server_without_xray_api_url(self):
        server = Server(
            id=6,
            name="Польша",
            country_flag="🇵🇱",
            is_active=True,
            protocol="amneziawg3.1",
            capabilities=["awg", "vless"],
            api_key="secret-key-456",
            api_url="https://pl.just1k.pro:8443",
            extra_data={},
        )
        targets = VlessSubscriptionService._extract_node_targets([server])
        self.assertEqual(len(targets), 0)

    def test_extract_node_targets_skips_when_api_key_missing(self):
        server = Server(
            id=12,
            name="Нидерланды",
            country_flag="🇳🇱",
            is_active=True,
            protocol="vless",
            api_key="",
            api_url="https://nl.just1k.pro:8444",
        )
        targets = VlessSubscriptionService._extract_node_targets([server])
        self.assertEqual(len(targets), 0)


class TestVlessFeedDynamicBotUrls(AioHTTPTestCase):
    """Test dynamic bot username and support URLs in feed responses."""

    def setUp(self):
        super().setUp()
        from bot.handlers import vless_web
        vless_web._ip_rate_limiter.buckets.clear()
        vless_web._token_rate_limiter.buckets.clear()
        from database.repositories import system_settings_repo
        system_settings_repo._SETTINGS_CACHE.clear()

        now = datetime.now(timezone.utc)
        self.default_user = User(
            id=42,
            telegram_id=999,
            is_banned=False,
            is_deleted=False,
            device_limit=2,
            subscription_end=now + timedelta(days=30),
        )
        self.default_sub = VlessSubscription(
            id=10,
            user_id=42,
            token="test-token-valid-length-12345678",
            uuid="11111111-2222-3333-4444-555555555555",
            is_active=True,
            active_hwids={},
        )
        self.default_server = Server(
            id=1,
            name="NL-Node-1",
            country_flag="🇳🇱",
            is_active=True,
            health_state=ServerHealthState.ONLINE,
            protocol="vless",
            api_url="https://nl.just1k.pro:8444",
            extra_data={"domain": "nl.just1k.pro", "vless_port": 443},
        )

    def tearDown(self):
        from bot.handlers import vless_web
        vless_web._ip_rate_limiter.buckets.clear()
        vless_web._token_rate_limiter.buckets.clear()
        super().tearDown()

    async def get_application(self):
        app = web.Application()
        mock_bot = AsyncMock()
        mock_me = MagicMock()
        mock_me.username = "just1k13bot"
        mock_bot.get_me.return_value = mock_me
        app["bot"] = mock_bot
        setup_vless_web_routes(app)
        return app

    async def test_feed_resolves_bot_username_and_support_urls(self):
        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(return_value=MagicMock(scalar_one=MagicMock(return_value=0)))

        @asynccontextmanager
        async def fake_session_scope():
            yield mock_session

        with (
            patch("bot.handlers.vless_web.session_scope", fake_session_scope),
            patch("database.repositories.vless_subscription_repo.get_subscription_by_token", return_value=self.default_sub),
            patch("database.repositories.users_repo.get_user_by_id", return_value=self.default_user),
            patch("services.subscription.SubscriptionService.check_vpn_access", return_value=True),
            patch("services.subscription.SubscriptionService.get_effective_device_limit", return_value=2),
            patch("database.repositories.vless_subscription_repo.register_hwid_atomic", return_value=(True, 1, 2)),
            patch("services.vless_subscription_service.VlessSubscriptionService.ensure_synced_background"),
            patch("services.vless_subscription_service.VlessSubscriptionService.get_eligible_vless_servers", return_value=[self.default_server]),
            patch("config.settings.get_settings") as mock_settings,
        ):
            mock_s = MagicMock()
            mock_s.SUPPORT_USERNAME = "my_support_contact"
            mock_settings.return_value = mock_s

            headers = {"X-Hwid": "test-client-hwid-88"}
            resp = await self.client.get("/sub/vless/test-token-valid-length-12345678", headers=headers)
            self.assertEqual(resp.status, 200)

            # Support-Url must point to project support, not just1kbot
            self.assertEqual(resp.headers.get("Support-Url"), "https://t.me/my_support_contact")
            # Profile-Web-Page-Url must point to the dynamically resolved bot username
            self.assertEqual(resp.headers.get("Profile-Web-Page-Url"), "https://t.me/just1k13bot")
            self.assertNotIn("just1kbot", resp.headers.get("Support-Url", ""))
            self.assertNotIn("just1kbot", resp.headers.get("Profile-Web-Page-Url", ""))




