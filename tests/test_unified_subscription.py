"""Unit tests for Unified Subscription Service and /sub/{token} endpoint."""

from __future__ import annotations

import base64
import urllib.parse
from contextlib import asynccontextmanager
from datetime import timedelta
from unittest.mock import AsyncMock, MagicMock, patch

from aiohttp import web
from aiohttp.test_utils import AioHTTPTestCase

from bot.handlers.unified_subscription_web import setup_unified_web_routes
from config.constants import (
    DEFAULT_WHITE_INTERNET_PATH,
)
from config.enums import ServerHealthState, WhiteInternetStatus
from database.models import Server, User, VlessSubscription, WhiteInternetSubscription
from utils.datetime_helpers import now_utc


class TestUnifiedSubscriptionService(AioHTTPTestCase):
    """Test suite for unified v2 subscription generation and edge cases."""

    async def get_application(self) -> web.Application:
        app = web.Application()
        setup_unified_web_routes(app)
        return app

    def _create_mock_user(
        self,
        user_id: int = 1,
        telegram_id: int = 123456789,
        is_active: bool = True,
        device_limit: int = 2,
    ) -> User:
        user = MagicMock(spec=User)
        user.id = user_id
        user.telegram_id = telegram_id
        user.is_deleted = False
        user.is_banned = False
        user.financial_hold = False
        user.subscription_end = now_utc() + timedelta(days=30) if is_active else None
        user.device_limit = device_limit
        user.current_tariff_id = None
        return user

    def _create_mock_vless_sub(
        self,
        user_id: int = 1,
        token: str = "test-token-standard-12345678",
        uuid_str: str = "11111111-1111-1111-1111-111111111111",
        is_active: bool = True,
    ) -> VlessSubscription:
        sub = MagicMock(spec=VlessSubscription)
        sub.id = 10
        sub.user_id = user_id
        sub.token = token
        sub.uuid = uuid_str
        sub.is_active = is_active
        sub.active_hwids = {}
        sub.traffic_up = 1024
        sub.traffic_down = 2048
        return sub

    def _create_mock_wi_sub(
        self,
        user_id: int = 1,
        token: str = "test-token-white-12345678",
        uuid_str: str = "22222222-2222-2222-2222-222222222222",
        status: str = WhiteInternetStatus.ACTIVE,
        is_exhausted: bool = False,
        device_limit: int = 1,
    ) -> WhiteInternetSubscription:
        sub = MagicMock(spec=WhiteInternetSubscription)
        sub.id = 20
        sub.user_id = user_id
        sub.origin_node_id = 99
        sub.token = token
        sub.uuid = uuid_str
        sub.status = status
        sub.expires_at = now_utc() + timedelta(days=20)
        sub.base_traffic_bytes = 53687091200  # 50 GB
        sub.extra_traffic_bytes = 0
        sub.traffic_limit_bytes = 53687091200
        sub.traffic_used_bytes = 53687091200 if is_exhausted else 1073741824  # 1 GB
        sub.traffic_uplink_bytes = 500000
        sub.traffic_downlink_bytes = 573741824
        sub.traffic_overage_bytes = 0
        sub.device_limit = device_limit
        sub.active_hwids = {}
        return sub

    def _create_mock_origin_server(self, server_id: int = 99) -> Server:
        server = MagicMock(spec=Server)
        server.id = server_id
        server.name = "RU-HUB-MSK"
        server.is_active = True
        server.health_state = ServerHealthState.ONLINE
        server.extra_data = {
            "cdn_domain": "ya.just1k.pro",
            "turboflare_domain": "turbo.just1k.pro",
            "secret_base_path": DEFAULT_WHITE_INTERNET_PATH,
            "transports": ["cdn_yandex", "cdn_turboflare"],
        }
        return server

    def setUp(self):
        super().setUp()
        from services import unified_subscription_service
        unified_subscription_service._ip_rate_limiter.buckets.clear()
        unified_subscription_service._token_rate_limiter.buckets.clear()

    async def test_unified_feed_standard_only(self):
        """User with Standard only receives direct VLESS TLS links."""
        user = self._create_mock_user()
        vless_sub = self._create_mock_vless_sub()

        mock_server = MagicMock(spec=Server)
        mock_server.id = 1
        mock_server.name = "NL-DIRECT"
        mock_server.is_active = True
        mock_server.health_state = ServerHealthState.ONLINE
        mock_server.lifecycle_status = "ACTIVE"
        mock_server.country_flag = "🇳🇱"
        mock_server.extra_data = {"domain": "nl.node.just1k.pro", "vless_port": 443}

        @asynccontextmanager
        async def mock_session_scope():
            mock_sess = AsyncMock()
            yield mock_sess

        with (
            patch(
                "bot.handlers.unified_subscription_web.session_scope",
                mock_session_scope,
            ),
            patch(
                "services.unified_subscription_service.UnifiedSubscriptionService.get_user_and_subscriptions",
                AsyncMock(return_value=(user, vless_sub, None)),
            ),
            patch(
                "services.unified_subscription_service.SubscriptionService.check_vpn_access",
                return_value=True,
            ),
            patch(
                "services.unified_subscription_service.SubscriptionService.get_effective_device_limit",
                AsyncMock(return_value=2),
            ),
            patch(
                "services.unified_subscription_service.vless_subscription_repo.register_hwid_atomic",
                AsyncMock(return_value=(True, 1, 2)),
            ),
            patch(
                "services.unified_subscription_service.VlessSubscriptionService.get_configured_vless_servers",
                AsyncMock(return_value=[mock_server]),
            ),
        ):
            resp = await self.client.get(
                f"/sub/{vless_sub.token}",
                headers={"X-Hwid": "test-device-uuid-1", "User-Agent": "Happ/3.9.8"},
            )
            self.assertEqual(resp.status, 200)
            self.assertEqual(resp.headers.get("Device-Limit"), "2")
            self.assertEqual(resp.headers.get("Device-Active-Count"), "1")
            self.assertIn("Routing", resp.headers)

            body_text = await resp.text()
            decoded = base64.b64decode(body_text).decode("utf-8")
            self.assertIn("nl.node.just1k.pro", decoded)
            self.assertIn("vless://", decoded)

    async def test_unified_feed_dual_active(self):
        """User with both Standard and White Internet receives both sets of nodes."""
        user = self._create_mock_user()
        vless_sub = self._create_mock_vless_sub()
        wi_sub = self._create_mock_wi_sub()
        origin_server = self._create_mock_origin_server()

        mock_server = MagicMock(spec=Server)
        mock_server.id = 1
        mock_server.name = "NL-DIRECT"
        mock_server.is_active = True
        mock_server.health_state = ServerHealthState.ONLINE
        mock_server.lifecycle_status = "ACTIVE"
        mock_server.country_flag = "🇳🇱"
        mock_server.extra_data = {"domain": "nl.node.just1k.pro", "vless_port": 443}

        @asynccontextmanager
        async def mock_session_scope():
            mock_sess = AsyncMock()
            mock_sess.scalar = AsyncMock(return_value=origin_server)
            yield mock_sess

        with (
            patch(
                "bot.handlers.unified_subscription_web.session_scope",
                mock_session_scope,
            ),
            patch(
                "services.unified_subscription_service.UnifiedSubscriptionService.get_user_and_subscriptions",
                AsyncMock(return_value=(user, vless_sub, wi_sub)),
            ),
            patch(
                "services.unified_subscription_service.SubscriptionService.check_vpn_access",
                return_value=True,
            ),
            patch(
                "services.unified_subscription_service.SubscriptionService.get_effective_device_limit",
                AsyncMock(return_value=2),
            ),
            patch(
                "services.unified_subscription_service.vless_subscription_repo.register_hwid_atomic",
                AsyncMock(return_value=(True, 1, 2)),
            ),
            patch(
                "services.unified_subscription_service.white_internet_repo.register_hwid_atomic",
                AsyncMock(return_value=(True, 1, 1)),
            ),
            patch(
                "services.unified_subscription_service.VlessSubscriptionService.get_configured_vless_servers",
                AsyncMock(return_value=[mock_server]),
            ),
        ):
            resp = await self.client.get(
                f"/sub/{vless_sub.token}",
                headers={"X-Hwid": "test-device-uuid-1", "User-Agent": "Happ/3.9.8"},
            )
            self.assertEqual(resp.status, 200)
            body_text = await resp.text()
            decoded = base64.b64decode(body_text).decode("utf-8")

            # Standard node
            self.assertIn("nl.node.just1k.pro", decoded)
            # White Internet CDN nodes (Yandex and TurboFlare)
            self.assertIn("ya.just1k.pro", decoded)
            self.assertIn("turbo.just1k.pro", decoded)

    async def test_wi_exhausted_preserves_standard(self):
        """When White Internet traffic is 0 GB, a placeholder notice node is emitted while Standard works."""
        user = self._create_mock_user()
        vless_sub = self._create_mock_vless_sub()
        wi_sub = self._create_mock_wi_sub(is_exhausted=True)

        mock_server = MagicMock(spec=Server)
        mock_server.id = 1
        mock_server.name = "NL-DIRECT"
        mock_server.is_active = True
        mock_server.health_state = ServerHealthState.ONLINE
        mock_server.lifecycle_status = "ACTIVE"
        mock_server.country_flag = "🇳🇱"
        mock_server.extra_data = {"domain": "nl.node.just1k.pro", "vless_port": 443}

        @asynccontextmanager
        async def mock_session_scope():
            mock_sess = AsyncMock()
            yield mock_sess

        with (
            patch(
                "bot.handlers.unified_subscription_web.session_scope",
                mock_session_scope,
            ),
            patch(
                "services.unified_subscription_service.UnifiedSubscriptionService.get_user_and_subscriptions",
                AsyncMock(return_value=(user, vless_sub, wi_sub)),
            ),
            patch(
                "services.unified_subscription_service.SubscriptionService.check_vpn_access",
                return_value=True,
            ),
            patch(
                "services.unified_subscription_service.SubscriptionService.get_effective_device_limit",
                AsyncMock(return_value=2),
            ),
            patch(
                "services.unified_subscription_service.vless_subscription_repo.register_hwid_atomic",
                AsyncMock(return_value=(True, 1, 2)),
            ),
            patch(
                "services.unified_subscription_service.white_internet_repo.register_hwid_atomic",
                AsyncMock(return_value=(True, 1, 1)),
            ),
            patch(
                "services.unified_subscription_service.VlessSubscriptionService.get_configured_vless_servers",
                AsyncMock(return_value=[mock_server]),
            ),
        ):
            resp = await self.client.get(
                f"/sub/{vless_sub.token}",
                headers={"X-Hwid": "test-device-uuid-1", "User-Agent": "Happ/3.9.8"},
            )
            self.assertEqual(resp.status, 200)
            body_text = await resp.text()
            decoded = base64.b64decode(body_text).decode("utf-8")

            # Standard node MUST still be present and functional
            self.assertIn("nl.node.just1k.pro", decoded)
            # Notice placeholder node for White Internet exhaustion
            unquoted = urllib.parse.unquote(decoded)
            self.assertIn("Закончился трафик Белого Интернета", unquoted)

    async def test_wi_device_limit_reached_on_device_two(self):
        """Device 2 exceeds BI slot limit (1/1): receives Standard nodes + BI limit notice node."""
        user = self._create_mock_user(device_limit=2)
        vless_sub = self._create_mock_vless_sub()
        wi_sub = self._create_mock_wi_sub(device_limit=1)
        wi_sub.active_hwids = {"hwid-device-1": {"added": 12345}}

        mock_server = MagicMock(spec=Server)
        mock_server.id = 1
        mock_server.name = "NL-DIRECT"
        mock_server.is_active = True
        mock_server.health_state = ServerHealthState.ONLINE
        mock_server.lifecycle_status = "ACTIVE"
        mock_server.country_flag = "🇳🇱"
        mock_server.extra_data = {"domain": "nl.node.just1k.pro", "vless_port": 443}

        @asynccontextmanager
        async def mock_session_scope():
            mock_sess = AsyncMock()
            yield mock_sess

        with (
            patch(
                "bot.handlers.unified_subscription_web.session_scope",
                mock_session_scope,
            ),
            patch(
                "services.unified_subscription_service.UnifiedSubscriptionService.get_user_and_subscriptions",
                AsyncMock(return_value=(user, vless_sub, wi_sub)),
            ),
            patch(
                "services.unified_subscription_service.SubscriptionService.check_vpn_access",
                return_value=True,
            ),
            patch(
                "services.unified_subscription_service.SubscriptionService.get_effective_device_limit",
                AsyncMock(return_value=2),
            ),
            patch(
                "services.unified_subscription_service.vless_subscription_repo.register_hwid_atomic",
                AsyncMock(return_value=(True, 2, 2)),
            ),
            patch(
                "services.unified_subscription_service.VlessSubscriptionService.get_configured_vless_servers",
                AsyncMock(return_value=[mock_server]),
            ),
        ):
            # Connect device 2 (new HWID)
            resp = await self.client.get(
                f"/sub/{vless_sub.token}",
                headers={"X-Hwid": "hwid-device-2", "User-Agent": "Happ/3.9.8"},
            )
            self.assertEqual(resp.status, 200)
            body_text = await resp.text()
            decoded = base64.b64decode(body_text).decode("utf-8")

            # Standard node MUST be active for device 2
            self.assertIn("nl.node.just1k.pro", decoded)
            # White Internet notice explaining device limit reached for BI
            unquoted = urllib.parse.unquote(decoded)
            self.assertIn("Исчерпан лимит устройств Белого Интернета", unquoted)

    async def test_browser_request_renders_html_landing(self):
        """Browser request without HWID header returns 200 with HTML landing."""
        user = self._create_mock_user()
        vless_sub = self._create_mock_vless_sub()

        @asynccontextmanager
        async def mock_session_scope():
            mock_sess = AsyncMock()
            yield mock_sess

        with (
            patch(
                "bot.handlers.unified_subscription_web.session_scope",
                mock_session_scope,
            ),
            patch(
                "services.unified_subscription_service.UnifiedSubscriptionService.get_user_and_subscriptions",
                AsyncMock(return_value=(user, vless_sub, None)),
            ),
            patch(
                "services.unified_subscription_service.SubscriptionService.check_vpn_access",
                return_value=True,
            ),
            patch(
                "services.unified_subscription_service.SubscriptionService.get_effective_device_limit",
                AsyncMock(return_value=2),
            ),
        ):
            resp = await self.client.get(
                f"/sub/{vless_sub.token}",
                headers={
                    "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15",
                    "Accept": "text/html,application/xhtml+xml",
                },
            )
            self.assertEqual(resp.status, 200)
            self.assertIn("text/html", resp.headers.get("Content-Type", ""))
            body_text = await resp.text()
            self.assertIn("<!DOCTYPE html>", body_text)

    async def test_order_setting_wi_first(self):
        """When sub_nodes_order is set to 'wi_first', White Internet nodes appear before Standard nodes."""
        user = self._create_mock_user()
        vless_sub = self._create_mock_vless_sub()
        wi_sub = self._create_mock_wi_sub()
        origin_server = self._create_mock_origin_server()

        mock_server = MagicMock(spec=Server)
        mock_server.id = 1
        mock_server.name = "NL-DIRECT"
        mock_server.is_active = True
        mock_server.health_state = ServerHealthState.ONLINE
        mock_server.lifecycle_status = "ACTIVE"
        mock_server.country_flag = "🇳🇱"
        mock_server.extra_data = {"domain": "nl.node.just1k.pro", "vless_port": 443}

        @asynccontextmanager
        async def mock_session_scope():
            mock_sess = AsyncMock()
            mock_sess.scalar = AsyncMock(return_value=origin_server)
            yield mock_sess

        async def mock_get_setting(session, key, default=None):
            if key == "sub_nodes_order":
                return "wi_first"
            return default

        with (
            patch(
                "bot.handlers.unified_subscription_web.session_scope",
                mock_session_scope,
            ),
            patch(
                "services.unified_subscription_service.get_system_setting",
                mock_get_setting,
            ),
            patch(
                "services.unified_subscription_service.UnifiedSubscriptionService.get_user_and_subscriptions",
                AsyncMock(return_value=(user, vless_sub, wi_sub)),
            ),
            patch(
                "services.unified_subscription_service.SubscriptionService.check_vpn_access",
                return_value=True,
            ),
            patch(
                "services.unified_subscription_service.SubscriptionService.get_effective_device_limit",
                AsyncMock(return_value=2),
            ),
            patch(
                "services.unified_subscription_service.vless_subscription_repo.register_hwid_atomic",
                AsyncMock(return_value=(True, 1, 2)),
            ),
            patch(
                "services.unified_subscription_service.white_internet_repo.register_hwid_atomic",
                AsyncMock(return_value=(True, 1, 1)),
            ),
            patch(
                "services.unified_subscription_service.VlessSubscriptionService.get_configured_vless_servers",
                AsyncMock(return_value=[mock_server]),
            ),
        ):
            resp = await self.client.get(
                f"/sub/{vless_sub.token}",
                headers={"X-Hwid": "test-device-uuid-1", "User-Agent": "Happ/3.9.8"},
            )
            self.assertEqual(resp.status, 200)
            body_text = await resp.text()
            decoded = base64.b64decode(body_text).decode("utf-8")
            lines = [line for line in decoded.split("\n") if line.strip()]

            # First node must be White Internet (ya.just1k.pro)
            self.assertIn("ya.just1k.pro", lines[0])
            # Last node must be Standard (nl.node.just1k.pro)
            self.assertIn("nl.node.just1k.pro", lines[-1])

    async def test_expired_subscription_returns_notice_node(self):
        """User with expired Standard and no BI receives expired notice node."""
        user = self._create_mock_user(is_active=False)
        vless_sub = self._create_mock_vless_sub(is_active=False)

        @asynccontextmanager
        async def mock_session_scope():
            mock_sess = AsyncMock()
            yield mock_sess

        with (
            patch(
                "bot.handlers.unified_subscription_web.session_scope",
                mock_session_scope,
            ),
            patch(
                "services.unified_subscription_service.UnifiedSubscriptionService.get_user_and_subscriptions",
                AsyncMock(return_value=(user, vless_sub, None)),
            ),
            patch(
                "services.unified_subscription_service.SubscriptionService.check_vpn_access",
                return_value=False,
            ),
            patch(
                "services.unified_subscription_service.SubscriptionService.get_effective_device_limit",
                AsyncMock(return_value=2),
            ),
        ):
            resp = await self.client.get(
                f"/sub/{vless_sub.token}",
                headers={"X-Hwid": "test-device-uuid-1", "User-Agent": "Happ/3.9.8"},
            )
            self.assertEqual(resp.status, 200)
            body_text = await resp.text()
            decoded = base64.b64decode(body_text).decode("utf-8")
            unquoted = urllib.parse.unquote(decoded)
            self.assertIn("Подписка закончилась", unquoted)
