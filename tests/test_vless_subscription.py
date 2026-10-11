"""Unit and integration tests for standard VLESS subscriptions, HWID quotas, and feed endpoint."""

import base64
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from aiohttp import web
from aiohttp.test_utils import AioHTTPTestCase

from bot.handlers.vless_web import setup_vless_web_routes
from config.enums import ServerHealthState
from database.models import Server, User, VlessSubscription
from database.repositories import vless_subscription_repo
from services.vless_subscription_service import VlessSubscriptionService
from utils.datetime_helpers import now_msk, now_utc


class TestVlessSubscriptionWebFeed(AioHTTPTestCase):
    """Test suite for /sub/vless/{token} HTTP subscription feed."""

    def setUp(self):
        super().setUp()
        from bot.handlers import vless_web
        vless_web._ip_rate_limiter.buckets.clear()
        vless_web._token_rate_limiter.buckets.clear()

        now = datetime.now(timezone.utc)
        self.default_user = User(
            id=42,
            telegram_id=999,
            is_banned=False,
            is_deleted=False,
            device_limit=3,
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
            self.assertIn("Подписка закончилась", decoded)

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
        ):
            resp = await self.client.get("/sub/vless/test-token-valid-length-12345678")
            self.assertEqual(resp.status, 200)
            body = await resp.text()
            decoded = base64.b64decode(body).decode("utf-8")
            self.assertIn("Подписка закончилась", decoded)
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
            body = await resp.text()
            decoded = base64.b64decode(body).decode("utf-8")
            self.assertIn("127.0.0.1:443", decoded)
            self.assertIn("Превышен лимит устройств", decoded)

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
            patch("services.subscription.SubscriptionService.get_effective_device_limit", return_value=3),
            patch("database.repositories.vless_subscription_repo.register_hwid_atomic", return_value=(True, 1, 3)),
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
            self.assertIn("#🇳🇱 NL-Node-1", decoded)
            self.assertNotIn("?serverDescription=", decoded)
            self.assertNotIn("%F0%9F", decoded)
            self.assertEqual(resp.headers.get("Device-Limit"), "3")
            self.assertEqual(resp.headers.get("Device-Active-Count"), "1")
            self.assertEqual(resp.headers.get("Hide-Url"), "1")
            self.assertEqual(resp.headers.get("No-Limit-Enabled"), "1")
            self.assertIn("t.me/", resp.headers.get("Support-Url", ""))

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
            patch("services.subscription.SubscriptionService.get_effective_device_limit", return_value=3),
            patch("database.repositories.vless_subscription_repo.register_hwid_atomic", return_value=(True, 1, 3)) as mock_register,
            patch("services.vless_subscription_service.VlessSubscriptionService.ensure_synced_background"),
            patch("services.vless_subscription_service.VlessSubscriptionService.get_eligible_vless_servers", return_value=[]),
        ):
            headers = {"X-Hwid": "test-client-hwid-99"}
            resp = await self.client.get("/sub/access/test-token-valid-length-12345678", headers=headers)
            self.assertEqual(resp.status, 503)
            self.assertEqual(resp.headers.get("Retry-After"), "60")
            mock_register.assert_not_called()
            self.assertEqual(self.default_sub.active_hwids, {})

    async def test_financial_hold_user_returns_notice_200(self):
        mock_session = AsyncMock()

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
        ):
            headers = {"X-Hwid": "test-client-hwid-99"}
            resp = await self.client.get("/sub/vless/test-token-valid-length-12345678", headers=headers)
            self.assertEqual(resp.status, 200)
            body = await resp.text()
            decoded = base64.b64decode(body).decode("utf-8")
            self.assertIn("Подписка закончилась", decoded)
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
            device_limit=3,
            subscription_end=datetime.now(timezone.utc) - timedelta(hours=2),
        )

        with (
            patch("bot.handlers.vless_web.session_scope", fake_session_scope),
            patch("database.repositories.vless_subscription_repo.get_subscription_by_token", return_value=self.default_sub),
            patch("database.repositories.users_repo.get_user_by_id", return_value=grace_user),
            patch("services.subscription.SubscriptionService.get_effective_device_limit", return_value=3),
            patch("database.repositories.vless_subscription_repo.register_hwid_atomic", return_value=(True, 1, 3)),
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
            patch("services.subscription.SubscriptionService.get_effective_device_limit", return_value=3),
            patch("database.repositories.vless_subscription_repo.register_hwid_atomic", return_value=(True, 1, 3)),
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
            device_limit=3,
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
            device_limit=3,
            subscription_end=datetime.now(timezone.utc) - timedelta(hours=2),
        )

        with (
            patch("bot.handlers.vless_web.session_scope", fake_session_scope),
            patch("database.repositories.vless_subscription_repo.get_subscription_by_token", return_value=self.default_sub),
            patch("database.repositories.users_repo.get_user_by_id", return_value=expired_grace_user),
            patch("services.subscription.SubscriptionService.get_effective_device_limit", return_value=3),
            patch("database.repositories.vless_subscription_repo.register_hwid_atomic", return_value=(True, 1, 3)),
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

            # Within grace window, user receives standard access feed without emergency headers
            self.assertNotIn("Routing", resp.headers)
            self.assertNotIn("Announce", resp.headers)

            # Profile-Title must indicate standard profile title
            self.assertIn("Profile-Title", resp.headers)
            raw_title_hdr = resp.headers["Profile-Title"]
            b64_title_val = raw_title_hdr.removeprefix("base64:") if raw_title_hdr.startswith("base64:") else raw_title_hdr
            profile_title = base64.b64decode(b64_title_val).decode("utf-8")
            self.assertEqual("Just1k Access", profile_title)

            # Server link must be present in body
            body = await resp.text()
            decoded_body = base64.b64decode(body).decode("utf-8")
            self.assertIn("vless://11111111-2222-3333-4444-555555555555@nl.example.com:443", decoded_body)

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
            patch("services.subscription.SubscriptionService.get_effective_device_limit", return_value=3),
            patch("database.repositories.vless_subscription_repo.register_hwid_atomic", return_value=(True, 1, 3)) as mock_register,
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
                {"os": "Android 14", "model": "Pixel 8 Pro", "ua": "IncyClient/2.1.0"},
            )

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
        )
        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=sub)))

        old_uuid, new_uuid = await vless_subscription_repo.reset_hwids(mock_session, sub.id)
        self.assertEqual(sub.active_hwids, {})
        self.assertEqual(old_uuid, "abc-uuid")
        self.assertNotEqual(new_uuid, "abc-uuid")
        self.assertEqual(sub.uuid, new_uuid)
        self.assertEqual(sub.version, 2)
        self.assertEqual(sub.pending_revoked_uuids, ["abc-uuid"])


class TestVlessSubscriptionService(unittest.IsolatedAsyncioTestCase):
    """Test suite for VlessSubscriptionService methods."""

    def test_build_subscription_url(self):
        url = VlessSubscriptionService.build_subscription_url("my-token-123", domain="bot.example.com")
        self.assertEqual(url, "https://bot.example.com/sub/access/my-token-123")

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
        self.assertIn("alpn=http%2F1.1", links[0])
        self.assertIn("#🇩🇪 DE Server", links[0])
        self.assertNotIn("?serverDescription=", links[0])
        self.assertNotIn("%F0%9F", links[0])


class TestConnectionsScreenLayout(unittest.IsolatedAsyncioTestCase):
    """Test suite for connections screen prioritizing VLESS subscription."""

    async def test_build_connections_screen_prioritizes_vless_sub(self):
        from types import SimpleNamespace
        from bot.handlers.connection.common import _build_amnezia_screen, _build_connections_screen

        user = SimpleNamespace(id=1, telegram_id=123, subscription_end=None)
        session = AsyncMock()

        mock_server = SimpleNamespace(country_flag="🇳🇱", name="Netherlands")
        profile = SimpleNamespace(
            id=10,
            user_id=1,
            server_id=1,
            device_name="Keenetic",
            provisioning_status="active",
            server=mock_server,
            last_connected=None,
            traffic_down=0,
            traffic_up=0,
        )
        mock_sub = SimpleNamespace(token="sim_token_12345678", uuid="some-uuid")

        with (
            patch("database.repositories.vless_subscription_repo.get_or_create_subscription", new=AsyncMock(return_value=mock_sub)),
            patch("database.repositories.vless_subscription_repo.get_active_hwid_count", new=AsyncMock(return_value=1)),
            patch("bot.handlers.connection.common._get_effective_device_limit", new=AsyncMock(return_value=5)),
        ):
            # 1. Main connection hub screen
            rendered, builder = await _build_connections_screen(user, session, [profile])

            self.assertIn("2 из 5", rendered)
            self.assertIn("sim_token_12345678", rendered)
            self.assertIn("по ссылке: 1", rendered)
            self.assertIn("Amnezia: 1", rendered)

            markup = builder.as_markup()
            buttons = [btn for row in markup.inline_keyboard for btn in row]
            self.assertTrue(any(btn.callback_data == "amnezia_devices" for btn in buttons))
            self.assertTrue(any(btn.callback_data == "vless_sub_feed_info" for btn in buttons))
            self.assertTrue(any(btn.callback_data == "vless_sub_reset" for btn in buttons))
            self.assertTrue(any(btn.callback_data == "manage_device:10" for btn in buttons))

            # 2. Amnezia devices screen
            amnezia_rendered, amnezia_builder = await _build_amnezia_screen(user, session, [profile])
            self.assertIn("Keenetic", amnezia_rendered)
            amnezia_markup = amnezia_builder.as_markup()
            amnezia_buttons = [btn for row in amnezia_markup.inline_keyboard for btn in row]
            self.assertTrue(any(btn.callback_data == "manage_device:10" for btn in amnezia_buttons))
            self.assertTrue(any(btn.callback_data == "add_device" for btn in amnezia_buttons))
            self.assertTrue(any(btn.callback_data == "back_to_connections" for btn in amnezia_buttons))


class TestAdminVlessManagement(unittest.IsolatedAsyncioTestCase):
    """Test suite for Admin Panel VLESS management."""

    async def test_rotate_token(self):
        from database.models import VlessSubscription
        from database.repositories import vless_subscription_repo

        sub = VlessSubscription(
            id=1,
            user_id=10,
            token="old-token-1234567890",
            uuid="11111111-2222-3333-4444-555555555555",
            is_active=True,
            active_hwids={"hwid1": "2026-10-08T12:00:00"},
        )

        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = sub
        mock_session.execute.return_value = mock_result

        new_token, old_uuid, new_uuid = await vless_subscription_repo.rotate_token(mock_session, 1)

        self.assertNotEqual(new_token, "old-token-1234567890")
        self.assertEqual(sub.token, new_token)
        self.assertEqual(old_uuid, "11111111-2222-3333-4444-555555555555")
        self.assertNotEqual(new_uuid, "11111111-2222-3333-4444-555555555555")
        self.assertEqual(sub.uuid, new_uuid)
        self.assertEqual(sub.active_hwids, {})
        self.assertEqual(sub.version, 2)
        self.assertEqual(sub.pending_revoked_uuids, ["11111111-2222-3333-4444-555555555555"])

    def test_user_card_vless_breakdown(self):
        from bot.handlers.admin.users.common import format_user_card_text
        from database.models import User

        now = datetime(2026, 10, 8, 12, 0, tzinfo=timezone.utc)
        user = User(
            id=1,
            telegram_id=123456,
            username="vless_user",
            first_name="Vless",
            device_limit=5,
            subscription_end=now + timedelta(days=10),
        )

        card_text = format_user_card_text(
            user=user,
            profiles=["profile1"],
            referrals=[],
            now=now,
            vless_hwid_count=2,
        )

        self.assertIn("<b>Устройств:</b> 3 (VLESS: 2, Amnezia: 1)/5", card_text)

    def test_admin_user_devices_keyboard_with_vless(self):
        from bot.keyboards.admin.users import get_admin_user_devices_keyboard

        kb = get_admin_user_devices_keyboard(
            telegram_id=123456,
            profiles=[],
            vless_sub_url="https://just1k.pro/sub/access/mytoken",
            has_vless_hwids=True,
        )

        buttons = [btn for row in kb.inline_keyboard for btn in row]
        copy_btn = next((b for b in buttons if b.copy_text and b.copy_text.text == "https://just1k.pro/sub/access/mytoken"), None)
        self.assertIsNotNone(copy_btn)

        hwid_reset_btn = next((b for b in buttons if b.callback_data == "admin_vless_hwid_reset:123456"), None)
        self.assertIsNotNone(hwid_reset_btn)

        token_rotate_btn = next((b for b in buttons if b.callback_data == "admin_vless_token_rotate:123456"), None)
        self.assertIsNotNone(token_rotate_btn)

        back_btn = next((b for b in buttons if b.callback_data == "admin_sub_awg_menu:123456"), None)
        self.assertIsNotNone(back_btn)

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
        mock_session.execute = AsyncMock(side_effect=[
            MagicMock(scalar_one_or_none=MagicMock(return_value=None)),
            MagicMock(scalar_one_or_none=MagicMock(return_value=existing_sub)),
        ])
        mock_session.flush = AsyncMock(side_effect=IntegrityError("duplicate key", params=None, orig=Exception()))

        sub = await vless_subscription_repo.get_or_create_subscription(mock_session, 42)
        self.assertEqual(sub.id, 5)
        self.assertEqual(sub.token, "winning-token-12345678")

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

    async def test_eligible_servers_excludes_relay(self):
        from database.models import Server
        from services.vless_subscription_service import VlessSubscriptionService
        from database.repositories.servers_repo import is_server_allocatable
        from config.enums import ServerHealthState
        from config.constants import AMNEZIA_PROTOCOL

        srv_relay = Server(
            id=1, name="WI Relay", is_active=True, health_state=ServerHealthState.ONLINE, protocol="xray", capabilities=["relay"]
        )
        srv_awg = Server(
            id=2, name="Solo AWG", is_active=True, health_state=ServerHealthState.ONLINE, protocol="amneziawg2", capabilities=["awg"]
        )
        srv_vless = Server(
            id=3, name="Solo VLESS", is_active=True, health_state=ServerHealthState.ONLINE, protocol="vless", capabilities=["vless"]
        )
        srv_both = Server(
            id=4, name="Both AWG & VLESS", is_active=True, health_state=ServerHealthState.ONLINE, protocol="amneziawg2", capabilities=["awg", "vless"]
        )
        srv_origin = Server(
            id=5, name="RF Origin", is_active=True, health_state=ServerHealthState.ONLINE, protocol="xray", capabilities=["origin"]
        )

        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(
            return_value=MagicMock(scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[
                srv_relay, srv_awg, srv_vless, srv_both, srv_origin
            ]))))
        )

        # 1. VLESS sub eligibility: only srv_vless and srv_both
        eligible = await VlessSubscriptionService.get_eligible_vless_servers(mock_session)
        eligible_names = [s.name for s in eligible]
        self.assertNotIn("WI Relay", eligible_names)
        self.assertNotIn("Solo AWG", eligible_names)
        self.assertNotIn("RF Origin", eligible_names)
        self.assertIn("Solo VLESS", eligible_names)
        self.assertIn("Both AWG & VLESS", eligible_names)

        # 2. AWG allocatability: only srv_awg and srv_both
        self.assertTrue(is_server_allocatable(srv_awg, AMNEZIA_PROTOCOL))
        self.assertTrue(is_server_allocatable(srv_both, AMNEZIA_PROTOCOL))
        self.assertFalse(is_server_allocatable(srv_vless, AMNEZIA_PROTOCOL))
        self.assertFalse(is_server_allocatable(srv_relay, AMNEZIA_PROTOCOL))
        self.assertFalse(is_server_allocatable(srv_origin, AMNEZIA_PROTOCOL))

    async def test_tariff_downgrade_checks_combined_quota(self):
        from database.models import User
        from services.subscription import SubscriptionService

        user = User(id=1, telegram_id=123, device_limit=3, subscription_end=datetime.now(timezone.utc) + timedelta(days=10))
        mock_session = AsyncMock()
        mock_session.scalar.return_value = user

        with (
            patch("services.subscription.get_user_profiles_count", new=AsyncMock(return_value=1)),
            patch("database.repositories.vless_subscription_repo.get_active_hwid_count", new=AsyncMock(return_value=2)),
        ):
            # 1 AWG + 2 VLESS = 3 devices. Downgrading to 2 devices must raise ValueError
            with self.assertRaises(ValueError) as ctx:
                await SubscriptionService.extend_subscription(
                    mock_session,
                    telegram_id=123,
                    days=0,
                    new_device_limit=2,
                )
            self.assertIn("Cannot downgrade: 3 devices > 2 limit", str(ctx.exception))

    async def test_ban_user_deactivates_vless(self):
        from database.models import User, VlessSubscription
        from services.ban_service import BanService

        user = User(id=1, telegram_id=123, is_banned=False, is_deleted=False)
        sub = VlessSubscription(id=1, user_id=1, uuid="test-uuid", is_active=True)
        mock_session = AsyncMock()
        mock_session.scalar.return_value = user
        mock_session.scalars.return_value = MagicMock(all=MagicMock(return_value=[]))

        with (
            patch("services.ban_service.update_user", new=AsyncMock()),
            patch("services.profile_deletion_service.ProfileDeletionService.delete_profiles_for_user", new=AsyncMock(return_value=0)),
            patch("services.white_internet_service.WhiteInternetService.deactivate_user_subscriptions", new=AsyncMock(return_value=[])),
            patch("services.audit_service.AuditService.log_action", new=AsyncMock()),
            patch("database.repositories.vless_subscription_repo.get_subscription_by_user_id", new=AsyncMock(return_value=sub)),
            patch("services.vless_subscription_service.VlessSubscriptionService.deprovision_background") as mock_deprov,
        ):
            success, status = await BanService._ban_user(mock_session, admin_id=999, user=user, telegram_id=123)
            self.assertTrue(success)
            self.assertFalse(sub.is_active)
            mock_deprov.assert_called_once_with("test-uuid", version=2, sub_id=1, session=mock_session)

    async def test_vless_sub_reset_handler_imports_maintenance_properly(self):
        from bot.handlers.connection.device_view_routes import vless_sub_reset
        from database.models import User

        callback = MagicMock()
        callback.from_user.id = 123
        callback.answer = AsyncMock()
        state = AsyncMock()
        session = AsyncMock()
        db_user = User(id=1, telegram_id=123, subscription_end=None)

        with (
            patch("services.maintenance_service.MaintenanceService.can_user_perform_action", new=AsyncMock(return_value=False)),
        ):
            await vless_sub_reset(callback, state, session, db_user=db_user)
            callback.answer.assert_called_once()

    async def test_sync_user_to_nodes_overrides_stale_active_if_banned(self):
        from database.models import Server, User, VlessSubscription
        from config.enums import ServerHealthState
        from services.vless_subscription_service import VlessSubscriptionService

        user = User(id=1, telegram_id=123, is_banned=True, subscription_end=None)
        sub = VlessSubscription(id=1, user_id=1, uuid="test-uuid", is_active=True, version=1)
        srv = Server(id=1, name="Exit", is_active=True, health_state=ServerHealthState.ONLINE, capabilities=["vless"], api_url="https://exit.com:8443", api_key="secret")

        mock_session = AsyncMock()
        mock_session.get.return_value = user

        with (
            patch("database.repositories.vless_subscription_repo.get_subscription_by_user_id", new=AsyncMock(return_value=sub)),
            patch("services.vless_subscription_service.VlessSubscriptionService.get_configured_vless_servers", new=AsyncMock(return_value=[srv])),
            patch("services.vless_subscription_service.XrayNodeClient") as mock_client_cls,
        ):
            mock_client = AsyncMock()
            mock_resp = MagicMock(result="applied", verified_inbounds=["vless"])
            mock_client.sync_client = AsyncMock(return_value=mock_resp)
            mock_client.__aenter__.return_value = mock_client
            mock_client.__aexit__.return_value = None
            mock_client_cls.return_value = mock_client

            results = await VlessSubscriptionService.sync_user_to_nodes(mock_session, user_id=1, is_active=True)
            self.assertTrue(results.get(1))
            # Must send is_active=False because user.is_banned is True!
            mock_client.sync_client.assert_called_once_with(
                api_url="https://exit.com:8443",
                api_key="secret",
                client_uuid="test-uuid",
                is_active=False,
                version=1,
                idempotency_key="vless:1:1:False",
                service="vless",
            )

    async def test_register_hwid_atomic_rejects_banned_user_under_lock(self):
        from database.models import User, VlessSubscription
        from database.repositories import vless_subscription_repo

        user = User(id=1, telegram_id=123, is_banned=True, subscription_end=None)
        sub = VlessSubscription(id=1, user_id=1, token="tok1234567890123456", uuid="abc-uuid", is_active=True, active_hwids={})

        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(side_effect=[
            # 1. user_id
            MagicMock(scalar_one_or_none=MagicMock(return_value=1)),
            # 2. User FOR UPDATE
            MagicMock(scalar_one_or_none=MagicMock(return_value=user)),
            # 3. VlessSubscription FOR UPDATE
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

    async def test_post_commit_dispatch_enqueued_when_session_passed(self):
        from services.vless_subscription_service import VlessSubscriptionService

        mock_session = MagicMock()
        mock_session.info = {}

        with patch("database.connection.queue_post_commit_task") as mock_queue:
            task = VlessSubscriptionService.ensure_synced_background(10, is_active=True, session=mock_session)
            self.assertIsNone(task)
            mock_queue.assert_called_once()

        with patch("database.connection.queue_post_commit_task") as mock_queue:
            task = VlessSubscriptionService.deprovision_background("old-uuid", session=mock_session)
            self.assertIsNone(task)
            mock_queue.assert_called_once()

    async def test_ensure_synced_background_respects_is_active_false(self):
        from services.vless_subscription_service import VlessSubscriptionService
        from database.models import User, VlessSubscription

        user = User(id=10, is_deleted=False, is_banned=False, financial_hold=False, subscription_end=None)
        sub = VlessSubscription(id=1, user_id=10, uuid="u-1", version=1, is_active=True)

        mock_session = AsyncMock()
        mock_session.get = AsyncMock(return_value=user)
        with (
            patch("database.connection.session_scope") as mock_scope,
            patch("database.repositories.vless_subscription_repo.get_subscription_by_user_id", new=AsyncMock(return_value=sub)),
            patch.object(VlessSubscriptionService, "get_configured_vless_servers", new=AsyncMock(return_value=[])),
            patch.object(VlessSubscriptionService, "_extract_node_targets", return_value=[{"url": "http://node", "key": "k"}]),
            patch.object(VlessSubscriptionService, "_execute_sync_to_nodes", new=AsyncMock(return_value={"node": True})) as mock_exec,
        ):
            mock_scope.return_value.__aenter__.return_value = mock_session
            task = VlessSubscriptionService.ensure_synced_background(10, is_active=False)
            self.assertIsNotNone(task)
            await task
            mock_exec.assert_called_once()
            self.assertFalse(mock_exec.call_args[0][2])

    async def test_payment_hub_includes_vless_hwid_count(self):
        from bot.handlers.payment.common import _show_hub
        from database.models import User

        callback = MagicMock()
        callback.message.chat.id = 123
        callback.bot = MagicMock()
        user = User(id=1, telegram_id=123, device_limit=5, subscription_end=None)
        session = AsyncMock()

        with (
            patch("bot.handlers.payment.common.get_user_profiles", new=AsyncMock(return_value=[])),
            patch("bot.handlers.payment.common._get_effective_device_limit", new=AsyncMock(return_value=5)),
            patch("bot.handlers.payment.common.get_tariff_display_name", return_value="5 устройств"),
            patch("database.repositories.vless_subscription_repo.get_active_hwid_count", new=AsyncMock(return_value=2)),
            patch("bot.handlers.payment.common.render_hub", new=AsyncMock()) as mock_render,
        ):
            await _show_hub(callback, user, session)
            mock_render.assert_called_once()
            call_text = mock_render.call_args[0][2]
            # Must show devices_count as 2 (0 Amnezia + 2 VLESS)
            self.assertIn("2 / 5", call_text)

    async def test_admin_sub_menu_includes_vless_hwid_count(self):
        from bot.handlers.admin.users.subscription_menu_routes import admin_subscription_menu
        from database.models import User

        callback = MagicMock()
        callback.data = "admin_subscription:123"
        callback.from_user.id = 123
        callback.answer = AsyncMock()
        callback.message.edit_text = AsyncMock()
        user = User(
            id=1,
            telegram_id=123,
            device_limit=5,
            subscription_end=now_utc() + timedelta(days=10),
            is_deleted=False,
            is_banned=False,
            current_tariff_id=None,
        )
        session = AsyncMock()

        with (
            patch("bot.handlers.admin.users.subscription_menu_routes.is_admin", return_value=True),
            patch("bot.handlers.admin.users.subscription_menu_routes.get_user_by_telegram_id", new=AsyncMock(return_value=user)),
            patch("bot.handlers.admin.users.subscription_menu_routes.get_user_profiles_count", new=AsyncMock(return_value=1)),
            patch("database.repositories.vless_subscription_repo.get_active_hwid_count", new=AsyncMock(return_value=2)),
            patch("bot.handlers.admin.users.subscription_menu_routes.get_admin_awg_subscription_keyboard", return_value=MagicMock()),
        ):
            await admin_subscription_menu(callback, session)
            callback.message.edit_text.assert_called_once()
            call_text = callback.message.edit_text.call_args[0][0]
            # Must show 3 devices (1 AWG + 2 VLESS)
            self.assertIn("3/5", call_text)

    async def test_create_device_calculates_vless_count_under_lock(self):
        from services.device_service import DeviceService
        from database.models import User, Server
        from services.slots_cache import ServerPeerSnapshot
        from datetime import timezone

        user = User(
            id=1,
            telegram_id=123,
            device_limit=2,
            subscription_end=datetime.now(timezone.utc) + timedelta(days=10),
            is_banned=False,
            device_creations_today=0,
            last_creation_date=now_msk().date(),
        )
        server = Server(
            id=1,
            protocol="awg",
            capabilities=["awg"],
            is_active=True,
            health_state="healthy",
            lifecycle_status="active",
            max_clients=100,
        )
        snapshot = ServerPeerSnapshot(
            server_id=1,
            peer_ids=frozenset(),
            captured_at=datetime.now(timezone.utc),
        )

        mock_session = AsyncMock()
        # Mock returns for user, server, duplicate check, user_count, server_count, bot_peer_ids
        user_res = MagicMock(scalar_one=MagicMock(return_value=user))
        server_res = MagicMock(scalar_one_or_none=MagicMock(return_value=server))
        duplicate_res = MagicMock(scalar_one_or_none=MagicMock(return_value=None))
        user_count_res = MagicMock(scalar_one=MagicMock(return_value=1))
        server_count_res = MagicMock(scalar_one=MagicMock(return_value=5))
        bot_peers_res = MagicMock(all=MagicMock(return_value=[]))

        mock_session.execute.side_effect = [
            user_res,
            server_res,
            duplicate_res,
            user_count_res,
            server_count_res,
            bot_peers_res,
        ]

        with (
            patch("database.repositories.vless_subscription_repo.get_active_hwid_count", new=AsyncMock(return_value=1)) as mock_hwid,
            patch("services.subscription.SubscriptionService.get_effective_device_limit", new=AsyncMock(return_value=2)),
            patch("services.device_service.is_server_allocatable", return_value=True),
            patch("services.device_service.is_admin", return_value=False),
        ):
            from services.device_service import DeviceLimitExceeded
            # user_count (1) + vless_count (1) == limit (2) -> must raise DeviceLimitExceeded
            with self.assertRaises(DeviceLimitExceeded):
                await DeviceService.create_device(
                    mock_session,
                    user_id=1,
                    server_id=1,
                    device_name="Test",
                    snapshot=snapshot,
                    vless_count=None,
                )
            mock_hwid.assert_called_once_with(mock_session, 1)

    async def test_vless_cleanup_4h_grace_and_pending_revocation_sweep(self):
        from database.models import Server
        from config.enums import ServerHealthState
        from services.workers.cleanup import _cleanup_expired_vless_network_grace, _sweep_vless_pending_revocations

        user = User(id=77, telegram_id=888, subscription_end=now_utc() - timedelta(hours=80))
        sub = VlessSubscription(id=77, user_id=77, is_active=True, version=1, pending_revoked_uuids=["old-rev-uuid"])
        srv = Server(
            id=1,
            name="Exit",
            is_active=True,
            health_state=ServerHealthState.ONLINE,
            capabilities=["vless"],
            api_url="https://exit.com:8443",
            api_key="secret",
        )

        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(return_value=MagicMock(all=MagicMock(return_value=[(77,)])))
        mock_session.get = AsyncMock(return_value=user)

        @asynccontextmanager
        async def fake_session_scope():
            yield mock_session

        with (
            patch("services.workers.cleanup.session_scope", fake_session_scope),
            patch("services.subscription.SubscriptionService.sync_access_state", new=AsyncMock()) as mock_sync_access,
        ):
            await _cleanup_expired_vless_network_grace()
            mock_sync_access.assert_called_once_with(mock_session, user)

        with (
            patch("services.workers.cleanup.session_scope", fake_session_scope),
            patch("database.repositories.vless_subscription_repo.get_subscriptions_with_pending_revocations", new=AsyncMock(return_value=[sub])),
            patch("services.vless_subscription_service.VlessSubscriptionService.get_configured_vless_servers", new=AsyncMock(return_value=[srv])),
            patch("services.vless_subscription_service.VlessSubscriptionService._execute_deprovision_on_nodes", new=AsyncMock(return_value={1: True})),
            patch("database.repositories.vless_subscription_repo.pop_pending_revoked_uuid", new=AsyncMock()) as mock_pop,
            patch("services.vless_subscription_service.VlessSubscriptionService.sync_user_to_nodes", new=AsyncMock()),
        ):
            await _sweep_vless_pending_revocations()
            mock_pop.assert_called_once_with(mock_session, 77, "old-rev-uuid")

    async def test_sweep_does_not_pop_uuid_when_node_offline(self):
        from database.models import Server
        from services.workers.cleanup import _sweep_vless_pending_revocations

        sub = VlessSubscription(id=77, user_id=77, is_active=True, version=1, pending_revoked_uuids=["old-rev-uuid"])
        srv1 = Server(id=1, name="Node-1", is_active=True, capabilities=["vless"], api_url="https://n1.com:8443", api_key="k1")
        srv2 = Server(id=2, name="Node-2", is_active=True, capabilities=["vless"], api_url="https://n2.com:8443", api_key="k2")

        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(return_value=MagicMock(scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[])))))

        @asynccontextmanager
        async def fake_session_scope():
            yield mock_session

        with (
            patch("services.workers.cleanup.session_scope", fake_session_scope),
            patch("database.repositories.vless_subscription_repo.get_subscriptions_with_pending_revocations", new=AsyncMock(return_value=[sub])),
            patch("services.vless_subscription_service.VlessSubscriptionService.get_configured_vless_servers", new=AsyncMock(return_value=[srv1, srv2])),
            # Node 2 is offline/fails
            patch("services.vless_subscription_service.VlessSubscriptionService._execute_deprovision_on_nodes", new=AsyncMock(return_value={1: True, 2: False})),
            patch("database.repositories.vless_subscription_repo.pop_pending_revoked_uuid", new=AsyncMock()) as mock_pop,
        ):
            await _sweep_vless_pending_revocations()
            # Must NOT pop when any configured node fails!
            mock_pop.assert_not_called()

    async def test_sync_validates_actual_state_on_already_newer_response(self):
        from services.vless_subscription_service import VlessSubscriptionService
        from services.xray_node_client import SyncResponse, SyncResult

        # 1. When desired_active=False and node returns already_newer with state='active', must NOT consider it success!
        resp_mismatch = SyncResponse(
            result=SyncResult.ALREADY_NEWER,
            error="state=active",
            verified_inbounds=["just1k-vless-direct"],
            raw_data={"result": "already_newer", "state": "active"},
        )
        with patch("services.xray_node_client.XrayNodeClient.sync_client", new=AsyncMock(return_value=resp_mismatch)):
            res = await VlessSubscriptionService._execute_sync_to_nodes(
                [(1, "https://node.com:8443", "key")],
                client_uuid="uuid-1",
                desired_active=False,
                version=1,
            )
            self.assertFalse(res[1])

        # 2. When desired_active=False and node returns already_newer with state='disabled' and all_inbounds_verified=True, must consider it success!
        resp_match = SyncResponse(
            result=SyncResult.ALREADY_NEWER,
            error="state=disabled",
            verified_inbounds=["just1k-vless-direct"],
            all_inbounds_verified=True,
            raw_data={"result": "already_newer", "state": "disabled"},
        )
        with patch("services.xray_node_client.XrayNodeClient.sync_client", new=AsyncMock(return_value=resp_match)):
            res = await VlessSubscriptionService._execute_sync_to_nodes(
                [(1, "https://node.com:8443", "key")],
                client_uuid="uuid-1",
                desired_active=False,
                version=1,
            )
            self.assertTrue(res[1])

        # 3. When desired_active=False and node returns already_newer with state='disabled' but all_inbounds_verified=False, must NOT consider it success!
        resp_unverified = SyncResponse(
            result=SyncResult.ALREADY_NEWER,
            error="state=disabled",
            verified_inbounds=[],
            all_inbounds_verified=False,
            raw_data={"result": "already_newer", "state": "disabled"},
        )
        with patch("services.xray_node_client.XrayNodeClient.sync_client", new=AsyncMock(return_value=resp_unverified)):
            res = await VlessSubscriptionService._execute_sync_to_nodes(
                [(1, "https://node.com:8443", "key")],
                client_uuid="uuid-1",
                desired_active=False,
                version=1,
            )
            self.assertFalse(res[1])

    async def test_sync_user_to_nodes_empty_targets_does_not_mark_synced(self):
        from database.models import User, VlessSubscription
        from services.vless_subscription_service import VlessSubscriptionService

        session = AsyncMock()
        user = User(id=10, telegram_id=1010, subscription_end=now_utc() + timedelta(days=5))
        session.get.return_value = user
        sub = VlessSubscription(id=1, user_id=10, uuid="u-1", last_synced_at=None, is_active=True)

        with (
            patch("database.repositories.vless_subscription_repo.get_subscription_by_user_id", new=AsyncMock(return_value=sub)),
            patch("services.vless_subscription_service.VlessSubscriptionService.get_configured_vless_servers", new=AsyncMock(return_value=[])),
        ):
            res = await VlessSubscriptionService.sync_user_to_nodes(session, user_id=10, is_active=True)
            self.assertEqual(res, {})
            self.assertIsNone(sub.last_synced_at)
            session.flush.assert_not_called()

    async def test_deprovision_validates_vless_inbound(self):
        from services.vless_subscription_service import VlessSubscriptionService
        from services.xray_node_client import SyncResponse, SyncResult

        # 1. Missing vless in verified_inbounds -> fail-closed (False)
        resp_no_vless = SyncResponse(
            result=SyncResult.APPLIED,
            verified_inbounds=["white_internet"],
            raw_data={"result": "applied"},
        )
        with patch("services.xray_node_client.XrayNodeClient.sync_client", new=AsyncMock(return_value=resp_no_vless)):
            res = await VlessSubscriptionService._execute_deprovision_on_nodes(
                [(1, "https://node.com:8443", "key")],
                client_uuid="uuid-1",
                version=1,
            )
            self.assertFalse(res[1])

        # 2. Presence of vless in verified_inbounds -> True
        resp_with_vless = SyncResponse(
            result=SyncResult.APPLIED,
            verified_inbounds=["just1k-vless-direct"],
            raw_data={"result": "applied"},
        )
        with patch("services.xray_node_client.XrayNodeClient.sync_client", new=AsyncMock(return_value=resp_with_vless)):
            res = await VlessSubscriptionService._execute_deprovision_on_nodes(
                [(1, "https://node.com:8443", "key")],
                client_uuid="uuid-1",
                version=1,
            )
            self.assertTrue(res[1])

        # 3. already_newer with disabled and all_inbounds_verified=True -> True
        resp_deprov_match = SyncResponse(
            result=SyncResult.ALREADY_NEWER,
            verified_inbounds=["just1k-vless-direct"],
            all_inbounds_verified=True,
            raw_data={"result": "already_newer", "state": "disabled"},
        )
        with patch("services.xray_node_client.XrayNodeClient.sync_client", new=AsyncMock(return_value=resp_deprov_match)):
            res = await VlessSubscriptionService._execute_deprovision_on_nodes(
                [(1, "https://node.com:8443", "key")],
                client_uuid="uuid-1",
                version=1,
            )
            self.assertTrue(res[1])

        # 4. already_newer with disabled but all_inbounds_verified=False -> False
        resp_deprov_unverified = SyncResponse(
            result=SyncResult.ALREADY_NEWER,
            verified_inbounds=[],
            all_inbounds_verified=False,
            raw_data={"result": "already_newer", "state": "disabled"},
        )
        with patch("services.xray_node_client.XrayNodeClient.sync_client", new=AsyncMock(return_value=resp_deprov_unverified)):
            res = await VlessSubscriptionService._execute_deprovision_on_nodes(
                [(1, "https://node.com:8443", "key")],
                client_uuid="uuid-1",
                version=1,
            )
            self.assertFalse(res[1])

    async def test_tariff_downgrade_checks_total_devices_including_vless(self):
        from bot.handlers.payment.common import _check_tariff_change_allowed
        from database.models import Tariff, User

        db_user = User(
            id=5,
            telegram_id=555,
            device_limit=3,
            current_tariff_id=1,
            subscription_end=now_utc() + timedelta(days=10),
        )
        target_tariff = Tariff(id=1, name="1 dev", device_limit=1, price_rub=100)
        session = AsyncMock()

        with (
            patch("bot.handlers.payment.common._get_effective_device_limit", new=AsyncMock(return_value=1)),
            patch("bot.handlers.payment.common.get_user_profiles_count", new=AsyncMock(return_value=1)),
            patch("database.repositories.vless_subscription_repo.get_active_hwid_count", new=AsyncMock(return_value=1)),
        ):
            # profiles_count (1) + vless_count (1) = 2 > new_limit (1) -> blocked
            res = await _check_tariff_change_allowed(session, db_user, target_tariff)
            self.assertIsNotNone(res)
            self.assertIn("2", res)
            self.assertIn("1", res)

    async def test_admin_user_card_keyboard_has_standard_button(self):
        from bot.keyboards.admin.users import get_admin_user_card_keyboard
        from bot import texts

        kb = get_admin_user_card_keyboard(user_id=123, is_banned=False)
        buttons = [btn.text for row in kb.inline_keyboard for btn in row]
        self.assertIn(texts.ADMIN_BTN_SUB_STANDARD, buttons)
        self.assertNotIn(texts.ADMIN_SERVER_BTN_PROTO_AWG, buttons)

    async def test_admin_awg_subscription_keyboard_includes_vless_buttons(self):
        from bot.keyboards.admin.users import get_admin_awg_subscription_keyboard, get_admin_user_devices_keyboard
        from bot import texts

        kb = get_admin_awg_subscription_keyboard(
            telegram_id=123,
            has_active_sub=True,
            vless_sub_url="https://just1k.pro/sub/access/tok123",
            has_vless_hwids=True,
        )
        buttons = [btn for row in kb.inline_keyboard for btn in row]
        button_texts = [b.text for b in buttons]
        self.assertIn(texts.ADMIN_BTN_VLESS_COPY_LINK, button_texts)
        self.assertIn(texts.ADMIN_BTN_USER_DEVICES, button_texts)
        # Action buttons are consolidated under user devices screen
        self.assertNotIn(texts.ADMIN_BTN_VLESS_HWID_RESET, button_texts)
        self.assertNotIn(texts.ADMIN_BTN_VLESS_TOKEN_ROTATE, button_texts)

        # Verify device management screen contains the HWID reset and token rotate actions
        dev_kb = get_admin_user_devices_keyboard(
            telegram_id=123,
            profiles=[],
            vless_sub_url="https://just1k.pro/sub/access/tok123",
            has_vless_hwids=True,
        )
        dev_buttons = [btn for row in dev_kb.inline_keyboard for btn in row]
        hwid_btn = next(b for b in dev_buttons if b.text == texts.ADMIN_BTN_VLESS_HWID_RESET)
        self.assertEqual(hwid_btn.callback_data, "admin_vless_hwid_reset:123")
        rotate_btn = next(b for b in dev_buttons if b.text == texts.ADMIN_BTN_VLESS_TOKEN_ROTATE)
        self.assertEqual(rotate_btn.callback_data, "admin_vless_token_rotate:123")

    async def test_vless_sub_reset_renders_connections_hub(self):
        from bot.handlers.connection.device_view_routes import vless_sub_reset
        from database.models import User, VlessSubscription

        callback = MagicMock()
        callback.from_user.id = 123
        callback.message = MagicMock()
        callback.answer = AsyncMock()
        state = AsyncMock()
        session = AsyncMock()
        db_user = User(id=1, telegram_id=123, subscription_end=now_utc() + timedelta(days=5))
        sub = VlessSubscription(id=10, user_id=1, uuid="test-uuid", token="tok", active_hwids={"hw1": "ts"})

        with (
            patch("services.maintenance_service.MaintenanceService.can_user_perform_action", new=AsyncMock(return_value=True)),
            patch("services.subscription.SubscriptionService.check_vpn_access", return_value=True),
            patch("database.repositories.vless_subscription_repo.get_subscription_by_user_id", new=AsyncMock(return_value=sub)),
            patch("database.repositories.vless_subscription_repo.reset_hwids", new=AsyncMock(return_value=("old-uuid", "new-uuid"))),
            patch("services.vless_subscription_service.VlessSubscriptionService.deprovision_background"),
            patch("services.vless_subscription_service.VlessSubscriptionService.ensure_synced_background"),
            patch("bot.handlers.connection.device_view_routes._render_connections", new=AsyncMock()) as mock_render,
        ):
            await vless_sub_reset(callback, state, session, db_user=db_user)
            callback.answer.assert_called_once()
            mock_render.assert_called_once_with(callback.message, db_user, session)

    async def test_vless_links_custom_name_and_badge(self):
        from database.models import Server, VlessSubscription
        from config.enums import ServerHealthState
        from services.vless_subscription_service import VlessSubscriptionService

        sub = VlessSubscription(id=1, user_id=1, uuid="11111111-2222-3333-4444-555555555555", token="tok", is_active=True)
        srv = Server(
            id=1,
            name="DE Server",
            country_flag="🇩🇪",
            is_active=True,
            health_state=ServerHealthState.ONLINE,
            protocol="awg",
            capabilities=["awg", "vless"],
            api_url="https://de.example.com:8443",
            extra_data={"domain": "de.example.com", "vless_port": 443, "origin_tag": "Frankfurt Fast", "origin_badge": "Ultra"},
        )
        links = VlessSubscriptionService.generate_vless_links(sub, [srv])
        self.assertEqual(len(links), 1)
        self.assertIn("#🇩🇪 Frankfurt Fast?serverDescription=", links[0])
        # b64 of 'Ultra'
        b64_ultra = base64.b64encode("Ultra".encode("utf-8")).decode("utf-8")
        self.assertIn(f"?serverDescription={b64_ultra}", links[0])

    async def test_vless_links_badge_none_omits_badge(self):
        from database.models import Server, VlessSubscription
        from config.enums import ServerHealthState
        from services.vless_subscription_service import VlessSubscriptionService

        sub = VlessSubscription(id=1, user_id=1, uuid="11111111-2222-3333-4444-555555555555", token="tok", is_active=True)
        srv = Server(
            id=1,
            name="DE Server",
            country_flag="🇩🇪",
            is_active=True,
            health_state=ServerHealthState.ONLINE,
            protocol="awg",
            capabilities=["awg", "vless"],
            api_url="https://de.example.com:8443",
            extra_data={"domain": "de.example.com", "vless_port": 443, "origin_badge": "none"},
        )
        links = VlessSubscriptionService.generate_vless_links(sub, [srv])
        self.assertEqual(len(links), 1)
        self.assertIn("#🇩🇪 DE Server", links[0])
        self.assertNotIn("?serverDescription=", links[0])

    async def test_vless_links_origin_hidden_skips_server(self):
        from database.models import Server, VlessSubscription
        from config.enums import ServerHealthState
        from services.vless_subscription_service import VlessSubscriptionService

        sub = VlessSubscription(id=1, user_id=1, uuid="11111111-2222-3333-4444-555555555555", token="tok", is_active=True)
        srv = Server(
            id=1,
            name="DE Server",
            is_active=True,
            health_state=ServerHealthState.ONLINE,
            capabilities=["vless"],
            extra_data={"domain": "de.example.com", "vless_port": 443, "origin_hidden": True},
        )
        links = VlessSubscriptionService.generate_vless_links(sub, [srv])
        self.assertEqual(len(links), 0)

    async def test_server_card_and_incy_keyboards_for_vless(self):
        from bot.keyboards.admin.servers import get_admin_server_card_keyboard, get_admin_server_incy_keyboard
        from bot import texts

        card_kb = get_admin_server_card_keyboard(
            server_id=1, is_active=True, is_xray=False, has_vless=True
        )
        card_btns = [b.text for row in card_kb.inline_keyboard for b in row]
        self.assertIn(texts.ADMIN_SERVER_BTN_INCY, card_btns)

        incy_kb = get_admin_server_incy_keyboard(server_id=1, is_xray=False)
        incy_btns = [b.text for row in incy_kb.inline_keyboard for b in row]
        self.assertIn(texts.ADMIN_SERVER_INCY_BTN_ORIGIN_NAME, incy_btns)
        self.assertIn(texts.ADMIN_SERVER_INCY_BTN_ORIGIN_BADGE, incy_btns)

    async def test_amnezia_screen_reflects_combined_quota_and_hides_add_btn(self):
        from bot.handlers.connection.common import _build_amnezia_screen
        from database.models import User
        from bot import texts

        user = User(id=1, telegram_id=123, subscription_end=now_utc() + timedelta(days=5), device_limit=3)
        session = AsyncMock()

        # 3 VLESS devices active, 0 Amnezia devices -> total 3 / 3 limit reached
        with (
            patch("database.repositories.vless_subscription_repo.get_active_hwid_count", new=AsyncMock(return_value=3)),
            patch("bot.handlers.connection.common._get_effective_device_limit", new=AsyncMock(return_value=3)),
        ):
            rendered, builder = await _build_amnezia_screen(user, session, profiles=[])
            self.assertIn("3 из 3", rendered)
            self.assertIn("по ссылке: 3", rendered)
            # Add device button must NOT be present since limit is reached
            button_texts = [b.text for row in builder.export() for b in row]
            self.assertNotIn(texts.BTN_ADD_DEVICE, button_texts)
            self.assertIn(texts.BTN_BACK_TO_CONNECTION, button_texts)

    async def test_device_service_create_device_default_vless_count_resolves_from_repo(self):
        from database.models import User, Server
        from services.device_service import DeviceService, DeviceLimitExceeded, ServerPeerSnapshot

        session = AsyncMock()
        user = User(
            id=1,
            telegram_id=123,
            device_limit=2,
            subscription_end=now_utc() + timedelta(days=5),
            is_banned=False,
        )
        server = Server(id=5, name="NL", is_active=True, capabilities=["awg"])

        snapshot = ServerPeerSnapshot(
            server_id=5,
            peer_ids=frozenset(["peer-1"]),
            captured_at=datetime.now(timezone.utc),
        )

        # Mock scalar_one for user, scalar_one_or_none for server, dup check, and AWG profile count
        user_res = MagicMock(scalar_one=MagicMock(return_value=user))
        server_res = MagicMock(scalar_one_or_none=MagicMock(return_value=server))
        dup_res = MagicMock(scalar_one_or_none=MagicMock(return_value=None))
        count_res = MagicMock(scalar_one=MagicMock(return_value=1))  # 1 AWG profile
        session.execute = AsyncMock(side_effect=[user_res, server_res, dup_res, count_res])

        # 1 active VLESS device + 1 active AWG device = 2 >= limit 2 -> DeviceLimitExceeded
        with (
            patch("database.repositories.vless_subscription_repo.get_active_hwid_count", new=AsyncMock(return_value=1)) as mock_hwid_cnt,
            patch("services.subscription.SubscriptionService.get_effective_device_limit", new=AsyncMock(return_value=2)),
        ):
            with self.assertRaises(DeviceLimitExceeded):
                # Call passing vless_count=None to verify dynamic resolution from repo
                await DeviceService.create_device(
                    session,
                    user_id=1,
                    server_id=5,
                    device_name="Phone",
                    snapshot=snapshot,
                    vless_count=None,
                )
            mock_hwid_cnt.assert_called_once_with(session, 1)
