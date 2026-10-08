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
            name="NL-Dual-1",
            country_flag="🇳🇱",
            is_active=True,
            health_state=ServerHealthState.ONLINE,
            protocol="dual",
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

    async def test_inactive_subscription_returns_404(self):
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

        with (
            patch("bot.handlers.vless_web.session_scope", fake_session_scope),
            patch("database.repositories.vless_subscription_repo.get_subscription_by_token", return_value=inactive_sub),
        ):
            resp = await self.client.get("/sub/vless/inactive-token-12345678")
            self.assertEqual(resp.status, 404)

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

    async def test_expired_subscription_returns_403(self):
        mock_session = AsyncMock()

        @asynccontextmanager
        async def fake_session_scope():
            yield mock_session

        expired_user = User(
            id=42,
            telegram_id=999,
            is_banned=False,
            is_deleted=False,
            subscription_end=datetime.now(timezone.utc) - timedelta(days=1),
        )

        with (
            patch("bot.handlers.vless_web.session_scope", fake_session_scope),
            patch("database.repositories.vless_subscription_repo.get_subscription_by_token", return_value=self.default_sub),
            patch("database.repositories.users_repo.get_user_by_id", return_value=expired_user),
        ):
            resp = await self.client.get("/sub/vless/test-token-valid-length-12345678")
            self.assertEqual(resp.status, 403)

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

    async def test_exceeded_device_limit_returns_403(self):
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
            patch("database.repositories.vless_subscription_repo.register_hwid_atomic", return_value=(False, 1, 0)),
        ):
            headers = {"X-Hwid": "device-overflow-hwid"}
            resp = await self.client.get("/sub/vless/test-token-valid-length-12345678", headers=headers)
            self.assertEqual(resp.status, 403)
            self.assertEqual(resp.headers.get("Device-Limit-Exceeded"), "1")
            self.assertEqual(resp.headers.get("x-hwid-max-devices-reached"), "true")

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
            resp = await self.client.get("/sub/vless/test-token-valid-length-12345678", headers=headers)
            self.assertEqual(resp.status, 200)
            mock_sync.assert_called_once_with(self.default_user.id, is_active=True)

            body = await resp.text()
            decoded = base64.b64decode(body).decode("utf-8")
            self.assertIn("vless://11111111-2222-3333-4444-555555555555@nl.example.com:443", decoded)
            self.assertIn("xtls-rprx-vision", decoded)
            self.assertIn("#🇳🇱 NL-Dual-1?serverDescription=", decoded)
            self.assertNotIn("%F0%9F", decoded)
            self.assertEqual(resp.headers.get("Device-Limit"), "3")
            self.assertEqual(resp.headers.get("Device-Active-Count"), "1")
            self.assertEqual(resp.headers.get("Hide-Url"), "1")
            self.assertEqual(resp.headers.get("No-Limit-Enabled"), "1")
            self.assertIn("t.me/", resp.headers.get("Support-Url", ""))


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

        # Existing device hwid-1 must be allowed and timestamp updated
        allowed, count, limit = await vless_subscription_repo.register_hwid_atomic(
            mock_session,
            subscription_id=sub.id,
            hwid="hwid-1",
            effective_limit=1,
        )
        self.assertTrue(allowed)
        self.assertEqual(count, 1)

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

        await vless_subscription_repo.reset_hwids(mock_session, sub.id)
        self.assertEqual(sub.active_hwids, {})


class TestVlessSubscriptionService(unittest.IsolatedAsyncioTestCase):
    """Test suite for VlessSubscriptionService methods."""

    def test_build_subscription_url(self):
        url = VlessSubscriptionService.build_subscription_url("my-token-123", domain="bot.example.com")
        self.assertEqual(url, "https://bot.example.com/sub/vless/my-token-123")

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
            protocol="dual",
            api_url="https://de.example.com:8443",
            extra_data={"domain": "de.example.com", "vless_port": 443},
        )
        links = VlessSubscriptionService.generate_vless_links(sub, [srv])
        self.assertEqual(len(links), 1)
        self.assertTrue(links[0].startswith("vless://12345678-1234-5678-1234-567812345678@de.example.com:443"))
        self.assertIn("flow=xtls-rprx-vision", links[0])
        self.assertIn("security=tls", links[0])
        self.assertIn("#🇩🇪 DE Server?serverDescription=", links[0])
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

        new_token = await vless_subscription_repo.rotate_token(mock_session, 1)

        self.assertNotEqual(new_token, "old-token-1234567890")
        self.assertEqual(sub.token, new_token)
        self.assertEqual(sub.active_hwids, {})

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
            vless_sub_url="https://just1k.pro/sub/vless/mytoken",
            has_vless_hwids=True,
        )

        buttons = [btn for row in kb.inline_keyboard for btn in row]
        copy_btn = next((b for b in buttons if b.copy_text and b.copy_text.text == "https://just1k.pro/sub/vless/mytoken"), None)
        self.assertIsNotNone(copy_btn)

        hwid_reset_btn = next((b for b in buttons if b.callback_data == "admin_vless_hwid_reset:123456"), None)
        self.assertIsNotNone(hwid_reset_btn)

        token_rotate_btn = next((b for b in buttons if b.callback_data == "admin_vless_token_rotate:123456"), None)
        self.assertIsNotNone(token_rotate_btn)


