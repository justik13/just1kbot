import base64
import os
import unittest
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch
from urllib.parse import unquote

from aiohttp import web
from aiohttp.test_utils import AioHTTPTestCase

from bot.handlers.white_internet_web import setup_white_internet_web_routes
from config.constants import XRAY_PROTOCOL
from config.enums import ServerHealthState, WhiteInternetStatus
from database.models import Server, User, WhiteInternetSubscription
from database.repositories import white_internet_repo


class TestWhiteInternetHwidRepo(unittest.IsolatedAsyncioTestCase):
    """Test suite for atomic HWID registration and device limit logic in repository."""

    async def test_register_hwid_first_device(self):
        """Verify registering a new HWID succeeds and initializes active_hwids."""
        sub = MagicMock(spec=WhiteInternetSubscription)
        sub.id = 1
        sub.active_hwids = {}

        mock_session = AsyncMock()
        mock_session.get.return_value = sub

        allowed, count, max_devs = await white_internet_repo.register_hwid_atomic(
            mock_session, subscription_id=1, hwid="device-aaa", max_devices=2
        )

        self.assertTrue(allowed)
        self.assertEqual(count, 1)
        self.assertEqual(max_devs, 2)
        self.assertIn("device-aaa", sub.active_hwids)
        mock_session.flush.assert_awaited_once()

    async def test_register_hwid_idempotent_existing_device(self):
        """Verify updating existing HWID refreshes timestamp without incrementing count."""
        sub = MagicMock(spec=WhiteInternetSubscription)
        sub.id = 1
        sub.active_hwids = {"device-aaa": "2026-09-01T10:00:00+00:00"}

        mock_session = AsyncMock()
        mock_session.get.return_value = sub

        allowed, count, max_devs = await white_internet_repo.register_hwid_atomic(
            mock_session, subscription_id=1, hwid="device-aaa", max_devices=2
        )

        self.assertTrue(allowed)
        self.assertEqual(count, 1)
        self.assertEqual(max_devs, 2)
        mock_session.flush.assert_awaited_once()

    async def test_register_hwid_empty_rejected(self):
        """Verify registering an empty HWID string is fail-closed and rejected."""
        sub = MagicMock(spec=WhiteInternetSubscription)
        sub.id = 1
        now = datetime.now(timezone.utc)
        sub.active_hwids = {"device-aaa": now.isoformat()}

        mock_session = AsyncMock()
        mock_session.get.return_value = sub

        allowed, count, max_devs = await white_internet_repo.register_hwid_atomic(
            mock_session, subscription_id=1, hwid="   ", max_devices=2
        )

        self.assertFalse(allowed)
        self.assertEqual(count, 1)
        self.assertEqual(max_devs, 2)
        mock_session.flush.assert_not_called()

    async def test_register_hwid_exceeds_limit(self):
        """Verify registering a new HWID when limit reached is rejected."""
        sub = MagicMock(spec=WhiteInternetSubscription)
        sub.id = 1
        sub.active_hwids = {
            "device-1": datetime.now(timezone.utc).isoformat(),
            "device-2": datetime.now(timezone.utc).isoformat(),
        }

        mock_session = AsyncMock()
        mock_session.get.return_value = sub

        allowed, count, max_devs = await white_internet_repo.register_hwid_atomic(
            mock_session, subscription_id=1, hwid="device-3", max_devices=2
        )

        self.assertFalse(allowed)
        self.assertEqual(count, 2)
        self.assertEqual(max_devs, 2)
        self.assertNotIn("device-3", sub.active_hwids)

    async def test_register_hwid_prunes_expired(self):
        """Verify devices inactive for > ttl_hours are pruned, freeing slots."""
        old_time = (datetime.now(timezone.utc) - timedelta(hours=50)).isoformat()
        sub = MagicMock(spec=WhiteInternetSubscription)
        sub.id = 1
        sub.active_hwids = {
            "stale-device": old_time,
            "active-device": datetime.now(timezone.utc).isoformat(),
        }

        mock_session = AsyncMock()
        mock_session.get.return_value = sub

        allowed, count, max_devs = await white_internet_repo.register_hwid_atomic(
            mock_session, subscription_id=1, hwid="new-device", max_devices=2, ttl_hours=48
        )

        self.assertTrue(allowed)
        self.assertEqual(count, 2)
        self.assertNotIn("stale-device", sub.active_hwids)
        self.assertIn("active-device", sub.active_hwids)
        self.assertIn("new-device", sub.active_hwids)

    async def test_register_hwid_empty_or_none(self):
        """Verify empty HWID is rejected fail-closed."""
        sub = MagicMock(spec=WhiteInternetSubscription)
        sub.id = 1
        sub.active_hwids = {}

        mock_session = AsyncMock()
        mock_session.get.return_value = sub

        allowed, count, max_devs = await white_internet_repo.register_hwid_atomic(
            mock_session, subscription_id=1, hwid="", max_devices=2
        )
        self.assertFalse(allowed)
        self.assertEqual(count, 0)
        self.assertEqual(max_devs, 2)

    async def test_reset_active_hwids_atomic(self):
        """Verify resetting HWIDs clears dictionary and flushes."""
        sub = MagicMock(spec=WhiteInternetSubscription)
        sub.id = 1
        sub.active_hwids = {"device-1": "2026-09-01T10:00:00+00:00"}

        mock_session = AsyncMock()
        mock_session.get.return_value = sub

        success = await white_internet_repo.reset_active_hwids_atomic(
            mock_session, subscription_id=1
        )
        self.assertTrue(success)
        self.assertEqual(sub.active_hwids, {})
        mock_session.flush.assert_awaited_once()

    async def test_reset_active_hwids_not_found(self):
        """Verify resetting HWIDs returns False when subscription does not exist."""
        mock_session = AsyncMock()
        mock_session.get.return_value = None

        success = await white_internet_repo.reset_active_hwids_atomic(
            mock_session, subscription_id=999
        )
        self.assertFalse(success)

    async def test_register_hwid_downgrade_lru_prune(self):
        """Verify that when effective limit drops below active count, oldest is pruned."""
        sub = MagicMock(spec=WhiteInternetSubscription)
        sub.id = 1
        now = datetime.now(timezone.utc)
        sub.active_hwids = {
            "dev-old": (now - timedelta(minutes=10)).isoformat(),
            "dev-new": (now - timedelta(minutes=1)).isoformat(),
        }

        mock_session = AsyncMock()
        mock_session.get.return_value = sub

        # Downgrade to max_devices=1 and attempt registering 3rd device
        allowed, count, max_devs = await white_internet_repo.register_hwid_atomic(
            mock_session, subscription_id=1, hwid="dev-3", max_devices=1
        )
        self.assertFalse(allowed)
        self.assertEqual(max_devs, 1)
        self.assertEqual(count, 1)
        self.assertNotIn("dev-old", sub.active_hwids)
        self.assertIn("dev-new", sub.active_hwids)

    async def test_add_extra_traffic_exceeding_max_quota_raises_error(self):
        """Adding extra traffic that exceeds 150 GiB cap must raise WhiteInternetQuotaCapExceededError."""
        sub = MagicMock(spec=WhiteInternetSubscription)
        sub.id = 1
        sub.device_limit = 1
        sub.status = WhiteInternetStatus.ACTIVE
        sub.base_traffic_bytes = 100 * 1024**3
        sub.extra_traffic_bytes = 120 * 1024**3

        mock_session = AsyncMock()

        with patch("database.repositories.white_internet_repo.get_subscription_with_lock", return_value=sub):
            with self.assertRaises(white_internet_repo.WhiteInternetQuotaCapExceededError):
                await white_internet_repo.add_extra_traffic_atomic(
                    mock_session, subscription_id=1, extra_bytes=51 * 1024**3
                )

    async def test_set_base_traffic_quota_exceeding_max_quota_raises_error(self):
        """Setting base traffic quota that exceeds 150 GiB cap must raise WhiteInternetQuotaCapExceededError."""
        sub = MagicMock(spec=WhiteInternetSubscription)
        sub.id = 1
        sub.device_limit = 1
        sub.status = WhiteInternetStatus.ACTIVE
        sub.base_traffic_bytes = 50 * 1024**3
        sub.extra_traffic_bytes = 25 * 1024**3

        mock_session = AsyncMock()

        with patch("database.repositories.white_internet_repo.get_subscription_with_lock", return_value=sub):
            with self.assertRaises(white_internet_repo.WhiteInternetQuotaCapExceededError):
                await white_internet_repo.set_base_traffic_quota_atomic(
                    mock_session, subscription_id=1, base_bytes=160 * 1024**3
                )


class TestWhiteInternetWebHwidEnforcement(AioHTTPTestCase):
    """Test suite for HTTP feed device limiting based on X-Hwid / X-HWID headers."""

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

    async def test_request_with_hwid_limit_exceeded(self):
        """Verify HTTP 200 with service notice and limit headers when HWID registration indicates limit exceeded."""
        sub = MagicMock(spec=WhiteInternetSubscription)
        sub.id = 42
        sub.status = WhiteInternetStatus.ACTIVE
        sub.user_id = 10
        sub.device_limit = 2
        sub.uuid = "12345678-1234-1234-1234-123456789abc"
        sub.expires_at = datetime.now(timezone.utc) + timedelta(days=30)
        sub.traffic_limit_bytes = 100 * 1024 * 1024 * 1024
        sub.traffic_used_bytes = 0
        sub.traffic_uplink_bytes = 0
        sub.traffic_downlink_bytes = 0
        sub.last_uplink_snapshot = 0
        sub.last_downlink_snapshot = 0
        sub.origin_node_id = 1
        sub.desired_version = 1
        sub.actual_version = 1
        sub.last_reconciled_node_epoch = "epoch-xyz"

        server = MagicMock(spec=Server)
        server.id = 1
        server.ip = "192.0.2.1"
        server.port = 443
        server.protocol = XRAY_PROTOCOL
        server.health_state = ServerHealthState.ONLINE
        server.is_active = True
        server.capabilities = ["xray_origin"]
        server.xray_instance_epoch = "epoch-xyz"
        server.extra_data = {"cdn_domain": "cdn.example.com"}

        mock_session = AsyncMock()
        mock_session.scalar.return_value = server
        mock_session.execute.return_value = MagicMock(scalar_one_or_none=lambda: server)

        @asynccontextmanager
        async def fake_session_scope():
            yield mock_session

        with patch.dict(os.environ, {"WHITE_INTERNET_CDN_DOMAIN": "cdn.example.com"}):
            with patch("bot.handlers.white_internet_web.session_scope", fake_session_scope):
                with patch("database.repositories.white_internet_repo.get_subscription_by_token", return_value=sub):
                    with patch(
                        "services.subscription.SubscriptionService.get_effective_device_limit",
                        new=AsyncMock(return_value=2),
                    ):
                        with patch(
                            "database.repositories.white_internet_repo.register_hwid_atomic",
                            new=AsyncMock(return_value=(False, 2, 2)),
                        ):
                            resp = await self.client.get(
                                "/sub/wl/test-token-valid-1234567890abcdef",
                                headers={"X-Hwid": "device-overflow-3"},
                            )
                            self.assertEqual(resp.status, 200)
                            self.assertEqual(resp.headers.get("Device-Limit-Exceeded"), "1")
                            self.assertEqual(resp.headers.get("Device-Limit"), "2")
                            self.assertEqual(resp.headers.get("Device-Active-Count"), "2")
                            self.assertEqual(resp.headers.get("x-hwid-max-devices-reached"), "true")
                            self.assertEqual(resp.headers.get("x-hwid-limit"), "true")
                            self.assertEqual(resp.headers.get("x-hwid-active"), "2")
                            self.assertIn("Channel-Url", resp.headers)
                            self.assertIn("Support-Url", resp.headers)
                            self.assertIn("Profile-Title", resp.headers)
                            body = await resp.text()
                            decoded = base64.b64decode(body).decode("utf-8")
                            self.assertTrue(decoded.startswith("vless://00000000-0000-0000-0000-000000000000@127.0.0.1:443"))
                            self.assertIn("Превышен лимит устройств (2/2)", unquote(decoded))

    async def test_request_exhausted_quota_does_not_register_hwid(self):
        """Verify that when quota is exhausted, 200 with notice is returned and HWID is NOT registered."""
        sub = MagicMock(spec=WhiteInternetSubscription)
        sub.id = 42
        sub.status = WhiteInternetStatus.EXHAUSTED
        sub.user_id = 10
        sub.expires_at = datetime.now(timezone.utc) + timedelta(days=30)
        sub.traffic_limit_bytes = 100
        sub.traffic_used_bytes = 100
        sub.traffic_uplink_bytes = 50
        sub.traffic_downlink_bytes = 50

        mock_session = AsyncMock()

        @asynccontextmanager
        async def fake_session_scope():
            yield mock_session

        with patch("bot.handlers.white_internet_web.session_scope", fake_session_scope):
            with patch("database.repositories.white_internet_repo.get_subscription_by_token", return_value=sub):
                with patch("database.repositories.white_internet_repo.register_hwid_atomic") as mock_register:
                    resp = await self.client.get(
                        "/sub/wl/test-token-valid-1234567890abcdef",
                        headers={"X-Hwid": "device-new"},
                    )
                    self.assertEqual(resp.status, 200)
                    mock_register.assert_not_called()

    async def test_request_unhealthy_server_does_not_register_hwid(self):
        """Verify that when server is unhealthy, 503 is returned and HWID is NOT registered."""
        sub = MagicMock(spec=WhiteInternetSubscription)
        sub.id = 42
        sub.status = WhiteInternetStatus.ACTIVE
        sub.user_id = 10
        sub.expires_at = datetime.now(timezone.utc) + timedelta(days=30)
        sub.traffic_limit_bytes = 100 * 1024 * 1024
        sub.traffic_used_bytes = 0
        sub.origin_node_id = 1

        server = MagicMock(spec=Server)
        server.id = 1
        server.health_state = ServerHealthState.PROBLEM
        server.is_active = True
        server.protocol = XRAY_PROTOCOL

        mock_session = AsyncMock()
        mock_session.scalar.return_value = server

        @asynccontextmanager
        async def fake_session_scope():
            yield mock_session

        with patch("bot.handlers.white_internet_web.session_scope", fake_session_scope):
            with patch("database.repositories.white_internet_repo.get_subscription_by_token", return_value=sub):
                with patch("database.repositories.white_internet_repo.register_hwid_atomic") as mock_register:
                    resp = await self.client.get(
                        "/sub/wl/test-token-valid-1234567890abcdef",
                        headers={"X-Hwid": "device-new"},
                    )
                    self.assertEqual(resp.status, 503)
                    mock_register.assert_not_called()

    async def test_request_with_hwid_allowed(self):
        """Verify HTTP 200 and device registration when under limit."""
        sub = MagicMock(spec=WhiteInternetSubscription)
        sub.id = 42
        sub.status = WhiteInternetStatus.ACTIVE
        sub.user_id = 10
        sub.device_limit = 2
        sub.uuid = "12345678-1234-1234-1234-123456789abc"
        sub.expires_at = datetime.now(timezone.utc) + timedelta(days=30)
        sub.traffic_limit_bytes = 100 * 1024 * 1024 * 1024
        sub.traffic_used_bytes = 0
        sub.traffic_uplink_bytes = 0
        sub.traffic_downlink_bytes = 0
        sub.last_uplink_snapshot = 0
        sub.last_downlink_snapshot = 0
        sub.origin_node_id = 1
        sub.desired_version = 1
        sub.actual_version = 1
        sub.last_reconciled_node_epoch = "epoch-xyz"

        server = MagicMock(spec=Server)
        server.id = 1
        server.ip = "192.0.2.1"
        server.port = 443
        server.protocol = XRAY_PROTOCOL
        server.health_state = ServerHealthState.ONLINE
        server.is_active = True
        server.capabilities = ["xray_origin"]
        server.xray_instance_epoch = "epoch-xyz"
        server.extra_data = {"cdn_domain": "cdn.example.com"}

        mock_session = AsyncMock()
        mock_session.scalar.return_value = server
        mock_session.execute.return_value = MagicMock(scalar_one_or_none=lambda: server)

        @asynccontextmanager
        async def fake_session_scope():
            yield mock_session

        with patch.dict(os.environ, {"WHITE_INTERNET_CDN_DOMAIN": "cdn.example.com"}):
            with patch("bot.handlers.white_internet_web.session_scope", fake_session_scope):
                with patch("database.repositories.white_internet_repo.get_subscription_by_token", return_value=sub):
                    with patch(
                        "services.subscription.SubscriptionService.get_effective_device_limit",
                        new=AsyncMock(return_value=2),
                    ):
                        with patch(
                            "database.repositories.white_internet_repo.register_hwid_atomic",
                            new=AsyncMock(return_value=(True, 1, 2)),
                        ) as mock_register:
                            resp = await self.client.get(
                                "/sub/wl/test-token-valid-1234567890abcdef",
                                headers={"X-Hwid": "device-valid-1"},
                            )
                            self.assertEqual(resp.status, 200)
                            mock_register.assert_awaited_once()

    async def test_sub_feed_extracts_device_info_headers_and_client(self):
        """Verify headers (X-Device-Model, X-Device-Os, X-Ver-Os, User-Agent) extract device_info."""
        sub = MagicMock(spec=WhiteInternetSubscription)
        sub.id = 42
        sub.status = WhiteInternetStatus.ACTIVE
        sub.user_id = 10
        sub.expires_at = datetime.now(timezone.utc) + timedelta(days=30)
        sub.traffic_limit_bytes = 100 * 1024 * 1024
        sub.traffic_used_bytes = 0
        sub.traffic_uplink_bytes = 0
        sub.traffic_downlink_bytes = 0
        sub.device_limit = 1
        sub.last_uplink_snapshot = 0
        sub.last_downlink_snapshot = 0
        sub.origin_node_id = 1
        sub.desired_version = 1
        sub.actual_version = 1
        sub.last_reconciled_node_epoch = "epoch-xyz"

        server = MagicMock(spec=Server)
        server.id = 1
        server.ip = "192.0.2.1"
        server.port = 443
        server.protocol = XRAY_PROTOCOL
        server.health_state = ServerHealthState.ONLINE
        server.is_active = True
        server.capabilities = ["xray_origin"]
        server.xray_instance_epoch = "epoch-xyz"
        server.extra_data = {"cdn_domain": "cdn.example.com"}

        mock_session = AsyncMock()
        mock_session.scalar.return_value = server
        mock_session.execute.return_value = MagicMock(scalar_one_or_none=lambda: server)

        @asynccontextmanager
        async def fake_session_scope():
            yield mock_session

        with patch.dict(os.environ, {"WHITE_INTERNET_CDN_DOMAIN": "cdn.example.com"}):
            with patch("bot.handlers.white_internet_web.session_scope", fake_session_scope):
                with patch("database.repositories.white_internet_repo.get_subscription_by_token", return_value=sub):
                    with patch(
                        "database.repositories.white_internet_repo.register_hwid_atomic",
                        new=AsyncMock(return_value=(True, 1, 2)),
                    ) as mock_register:
                        resp = await self.client.get(
                            "/sub/wl/test-token-valid-1234567890abcdef",
                            headers={
                                "X-Hwid": "device-valid-1",
                                "X-Device-Os": "iOS",
                                "X-Ver-Os": "17.5.1",
                                "X-Device-Model": "iPhone 15 Pro",
                                "User-Agent": "INCY/3.7.0",
                            },
                        )
                        self.assertEqual(resp.status, 200)
                        mock_register.assert_awaited_once()
                        _, kwargs = mock_register.call_args
                        self.assertEqual(
                            kwargs.get("device_info"),
                            {
                                "client": "INCY 3.7.0",
                                "os": "iOS 17.5.1",
                                "model": "iPhone 15 Pro",
                                "ua": "INCY/3.7.0",
                            },
                        )


class TestWhiteInternetHwidBackwardCompatibility(unittest.IsolatedAsyncioTestCase):
    def test_count_active_hwids_with_mixed_str_and_dict_and_timezones(self):
        from database.repositories.white_internet_repo import (
            count_active_hwids,
            extract_hwid_last_seen,
            parse_hwid_last_seen_dt,
        )

        now = datetime(2026, 10, 24, 12, 0, tzinfo=timezone.utc)
        active_hwids = {
            "dev_str_active": (now - timedelta(hours=1)).isoformat(),
            "dev_str_stale": (now - timedelta(hours=50)).isoformat(),
            "dev_dict_active": {
                "last_seen": (now - timedelta(hours=2)).isoformat(),
                "client": "INCY 3.7.0",
                "model": "iPhone 17 Pro",
            },
            "dev_dict_stale": {
                "last_seen": (now - timedelta(hours=60)).isoformat(),
                "client": "Happ 3.6.5",
            },
            "dev_tz_offset_active": (now - timedelta(hours=3)).astimezone(timezone(timedelta(hours=3))).isoformat(),
        }

        count = count_active_hwids(active_hwids, now=now, ttl_hours=48)
        self.assertEqual(count, 3)

        self.assertEqual(
            extract_hwid_last_seen("2026-10-24T12:00:00+00:00"),
            "2026-10-24T12:00:00+00:00",
        )
        self.assertEqual(
            extract_hwid_last_seen({"last_seen": "2026-10-24T12:00:00+00:00"}),
            "2026-10-24T12:00:00+00:00",
        )
        self.assertIsNone(extract_hwid_last_seen({}))
        self.assertIsNone(extract_hwid_last_seen(None))

        # Test aware datetime parsing and comparison across timezone offsets
        dt_utc = parse_hwid_last_seen_dt("2026-10-24T13:00:00+00:00")
        dt_msk = parse_hwid_last_seen_dt("2026-10-24T15:00:00+03:00")  # 12:00:00 UTC
        self.assertGreater(dt_utc, dt_msk)

    async def test_set_device_limit_atomic_lru_prunes_correctly_with_mixed_formats(self):
        from database.repositories.white_internet_repo import set_device_limit_atomic, update_device_limit_atomic

        # update_device_limit_atomic must be an alias for set_device_limit_atomic
        self.assertIs(update_device_limit_atomic, set_device_limit_atomic)

        sub = MagicMock(spec=WhiteInternetSubscription)
        sub.id = 99
        sub.device_limit = 3
        sub.extra_traffic_bytes = 0
        sub.is_trial = False
        sub.active_hwids = {
            "hwid_oldest": "2026-10-24T10:00:00+00:00",
            "hwid_middle": {
                "last_seen": "2026-10-24T11:00:00+00:00",
                "client": "Happ 3.6.5",
            },
            "hwid_newest": {
                "last_seen": "2026-10-24T12:00:00+00:00",
                "client": "INCY 3.7.0",
            },
        }

        mock_session = AsyncMock()
        mock_session.get.return_value = sub

        res = await set_device_limit_atomic(mock_session, 99, limit=2)
        self.assertEqual(res.device_limit, 2)
        # Should keep newest 2 and prune oldest
        self.assertIn("hwid_newest", res.active_hwids)
        self.assertIn("hwid_middle", res.active_hwids)
        self.assertNotIn("hwid_oldest", res.active_hwids)

