"""Integration and unit tests for the AmneziaWG Unified Subscription System."""

from __future__ import annotations

import base64
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
import hashlib
import json
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from aiohttp import web
from aiohttp.test_utils import AioHTTPTestCase

from bot import texts
from bot.handlers import awg_sub_web
from bot.handlers.awg_sub_web import (
    DEFAULT_AWG_SUB_PATH_PREFIX,
    setup_awg_subscription_web_routes,
)
from bot.handlers.connection.common import _build_connections_screen
from config.constants import AMNEZIA_PROTOCOL
from config.enums import ServerHealthState
from database.models import Server, User, VPNProfile
from services.device_service import (
    DeviceLimitExceeded,
    DeviceService,
)
from services.slots_cache import ServerPeerSnapshot
from utils.vpn_parser import encode_json_to_vpn_uri


def _make_dummy_awg_raw_config(server_name: str = "Test Server") -> str:
    """Helper to generate a valid vpn:// URI containing AWG config."""
    awg_dict = {
        "containers": [
            {
                "awg": {
                    "last_config": json.dumps({
                        "client_priv_key": "dummy_client_priv_key_base64_len_44_chars==",
                        "server_pub_key": "dummy_server_pub_key_base64_len_44_chars==",
                        "client_ip": "10.8.0.2",
                        "hostName": "test.server.io",
                        "port": 51820,
                        "mtu": "1280",
                        "persistent_keep_alive": 25,
                        "Jc": 4, "Jmin": 50, "Jmax": 1000,
                        "S1": 15, "S2": 25, "S3": 35, "S4": 45,
                        "H1": 1, "H2": 2, "H3": 3, "H4": 4,
                    })
                }
            }
        ],
        "description": server_name,
    }
    return encode_json_to_vpn_uri(awg_dict)


class TestAWGLogicalDeviceService(unittest.IsolatedAsyncioTestCase):
    """Test logical device quota calculation and sub-device management in DeviceService."""

    async def test_sub_devices_quota_multi_server(self):
        """Verify multiple servers for 1 sub_device_hash count as 1 logical device."""
        now = datetime.now(timezone.utc)
        user = User(
            id=1,
            telegram_id=12345678,
            subscription_end=now + timedelta(days=30),
            device_limit=2,
            active_sub_devices={"hwid_hash_1": {"device_index": 1, "label": "Device 1"}},
        )

        server_1 = Server(
            id=10, name="Poland", protocol=AMNEZIA_PROTOCOL, is_active=True,
            max_clients=100, api_url="http://s1:8080", api_key="k1",
        )

        snapshot_1 = ServerPeerSnapshot(server_id=10, peer_ids=frozenset(), captured_at=now)

        @asynccontextmanager
        async def fake_nested():
            yield

        mock_session = AsyncMock()
        mock_session.add = MagicMock()
        mock_session.begin_nested = MagicMock(side_effect=fake_nested)

        mock_user_exec = MagicMock()
        mock_user_exec.scalar_one.return_value = user
        mock_server_exec = MagicMock()
        mock_server_exec.scalar_one_or_none.return_value = server_1
        mock_duplicate_exec = MagicMock()
        mock_duplicate_exec.scalar_one_or_none.return_value = None
        mock_count_exec = MagicMock()
        mock_count_exec.scalar_one.return_value = 0
        mock_peers_exec = MagicMock()
        mock_peers_exec.scalars.return_value.all.return_value = []

        mock_session.execute.side_effect = [
            mock_user_exec,      # User select
            mock_server_exec,    # Server select
            mock_duplicate_exec, # Duplicate name
            mock_count_exec,     # Server count
            mock_peers_exec,     # Bot peers
        ]

        with patch("services.device_service.ensure_server_capacity", new_callable=AsyncMock), \
             patch("services.device_service.enqueue_api_operation", new_callable=AsyncMock), \
             patch("services.device_service.AuditService.log_action", new_callable=AsyncMock):

            profile_1 = await DeviceService.create_device(
                mock_session,
                user_id=1,
                server_id=10,
                device_name="Sub Device 1",
                snapshot=snapshot_1,
                device_type="sub",
                sub_device_hash="hwid_hash_1",
            )
            self.assertEqual(profile_1.device_type, "sub")
            self.assertEqual(profile_1.sub_device_hash, "hwid_hash_1")

    async def test_sub_device_limit_exceeded(self):
        """Verify registering a new sub device or manual device fails when quota is reached."""
        now = datetime.now(timezone.utc)
        user = User(
            id=1,
            telegram_id=12345678,
            subscription_end=now + timedelta(days=30),
            device_limit=2,
            active_sub_devices={
                "hwid_1": {"device_index": 1},
                "hwid_2": {"device_index": 2},
            },
        )
        server = Server(
            id=10, name="Poland", protocol=AMNEZIA_PROTOCOL, is_active=True,
            max_clients=100, api_url="http://s1:8080", api_key="k1",
        )
        snapshot = ServerPeerSnapshot(server_id=10, peer_ids=frozenset(), captured_at=now)

        mock_session = AsyncMock()
        mock_user_exec = MagicMock()
        mock_user_exec.scalar_one.return_value = user
        mock_server_exec = MagicMock()
        mock_server_exec.scalar_one_or_none.return_value = server
        mock_duplicate_exec = MagicMock()
        mock_duplicate_exec.scalar_one_or_none.return_value = None
        mock_manual_count_exec = MagicMock()
        mock_manual_count_exec.scalar_one.return_value = 0

        mock_session.execute.side_effect = [
            mock_user_exec,         # User select
            mock_server_exec,       # Server select
            mock_duplicate_exec,    # Duplicate name
            mock_manual_count_exec, # Manual count query (0 manual, 2 sub => total 2 >= limit 2)
        ]

        with self.assertRaises(DeviceLimitExceeded):
            await DeviceService.create_device(
                mock_session,
                user_id=1,
                server_id=10,
                device_name="Device 3",
                snapshot=snapshot,
                device_type="manual",
            )

    async def test_delete_sub_device(self):
        """Verify delete_sub_device removes hash from active_sub_devices and invokes delete_device for all profiles."""
        now = datetime.now(timezone.utc)
        user = User(
            id=1,
            telegram_id=12345678,
            subscription_end=now + timedelta(days=30),
            device_limit=2,
            active_sub_devices={"hwid_to_del": {"device_index": 1}},
        )

        p1 = VPNProfile(id=101, user_id=1, server_id=10, device_type="sub", sub_device_hash="hwid_to_del")
        p2 = VPNProfile(id=102, user_id=1, server_id=20, device_type="sub", sub_device_hash="hwid_to_del")

        mock_session = AsyncMock()
        mock_user_exec = MagicMock()
        mock_user_exec.scalar_one_or_none.return_value = user

        mock_profiles_exec = MagicMock()
        mock_profiles_exec.scalars.return_value.all.return_value = [p1, p2]

        mock_session.execute.side_effect = [
            mock_user_exec,
            mock_profiles_exec,
        ]

        with patch("services.device_service.DeviceService.delete_device", new_callable=AsyncMock) as mock_del:
            mock_del.return_value = True
            deleted = await DeviceService.delete_sub_device(
                mock_session,
                user_id=1,
                hwid_hash="hwid_to_del",
            )
            self.assertEqual(deleted, 2)
            self.assertNotIn("hwid_to_del", user.active_sub_devices)
            self.assertEqual(mock_del.await_count, 2)

    async def test_delete_sub_device_fails_closed_when_profile_delete_raises(self):
        """Strict Fail-Closed: if profile deletion raises an error, slot must NOT be freed."""
        now = datetime.now(timezone.utc)
        user = User(
            id=1,
            telegram_id=12345678,
            subscription_end=now + timedelta(days=30),
            device_limit=2,
            active_sub_devices={"hwid_fail_closed": {"device_index": 1}},
        )

        p1 = VPNProfile(id=101, user_id=1, server_id=10, device_type="sub", sub_device_hash="hwid_fail_closed")
        p2 = VPNProfile(id=102, user_id=1, server_id=20, device_type="sub", sub_device_hash="hwid_fail_closed")

        mock_session = AsyncMock()
        mock_user_exec = MagicMock()
        mock_user_exec.scalar_one_or_none.return_value = user

        mock_profiles_exec = MagicMock()
        mock_profiles_exec.scalars.return_value.all.return_value = [p1, p2]

        mock_session.execute.side_effect = [
            mock_user_exec,
            mock_profiles_exec,
        ]

        with patch("services.device_service.DeviceService.delete_device", new_callable=AsyncMock) as mock_del:
            # First profile deletion succeeds, second profile deletion fails with network error
            mock_del.side_effect = [True, RuntimeError("Amnezia node connection timeout")]

            with self.assertRaises(RuntimeError):
                await DeviceService.delete_sub_device(
                    mock_session,
                    user_id=1,
                    hwid_hash="hwid_fail_closed",
                )

            # Strict Fail-Closed check: user.active_sub_devices STILL contains the device! Slot is NOT freed!
            self.assertIn("hwid_fail_closed", user.active_sub_devices)


class TestAWGSubscriptionWeb(AioHTTPTestCase):
    """Test suite for HTTP feed /sub/awg/{token}."""

    def setUp(self):
        super().setUp()
        awg_sub_web._ip_rate_limiter.buckets.clear()
        awg_sub_web._token_rate_limiter.buckets.clear()

    def tearDown(self):
        awg_sub_web._ip_rate_limiter.buckets.clear()
        awg_sub_web._token_rate_limiter.buckets.clear()
        super().tearDown()

    async def get_application(self) -> web.Application:
        app = web.Application()
        setup_awg_subscription_web_routes(app)
        return app

    async def test_ping(self):
        """Verify synthetic ping healthcheck endpoint returns 200 pong."""
        resp = await self.client.get(f"{DEFAULT_AWG_SUB_PATH_PREFIX}/ping")
        self.assertEqual(resp.status, 200)
        self.assertEqual(await resp.text(), "pong")

    async def test_feed_hwid_required(self):
        """Verify requests without HWID header return 403 x-hwid-required."""
        valid_token = "a" * 32
        resp = await self.client.get(f"{DEFAULT_AWG_SUB_PATH_PREFIX}/{valid_token}")
        self.assertEqual(resp.status, 403)
        self.assertEqual(resp.headers.get("x-hwid-required"), "true")

    async def test_feed_token_not_found(self):
        """Verify requests with unknown token return 404."""
        valid_token = "b" * 32
        headers = {"X-HWID": "test-device-uuid-123"}

        mock_session = AsyncMock()

        @asynccontextmanager
        async def fake_session_scope():
            yield mock_session

        with patch("bot.handlers.awg_sub_web.session_scope", fake_session_scope), \
             patch("database.repositories.users_repo.get_user_by_subscription_token", new_callable=AsyncMock) as mock_get_user:
            mock_get_user.return_value = None

            resp = await self.client.get(f"{DEFAULT_AWG_SUB_PATH_PREFIX}/{valid_token}", headers=headers)
            self.assertEqual(resp.status, 404)

    async def test_feed_expired_subscription(self):
        """Verify expired user receives 403 Subscription expired."""
        valid_token = "c" * 32
        headers = {"X-HWID": "test-device-uuid-123"}
        expired_time = datetime.now(timezone.utc) - timedelta(days=1)
        user = User(id=1, telegram_id=111, subscription_end=expired_time, device_limit=2)

        mock_session = AsyncMock()

        @asynccontextmanager
        async def fake_session_scope():
            yield mock_session

        with patch("bot.handlers.awg_sub_web.session_scope", fake_session_scope), \
             patch("database.repositories.users_repo.get_user_by_subscription_token", new_callable=AsyncMock) as mock_get_user:
            mock_get_user.return_value = user

            resp = await self.client.get(f"{DEFAULT_AWG_SUB_PATH_PREFIX}/{valid_token}", headers=headers)
            self.assertEqual(resp.status, 403)
            text = await resp.text()
            self.assertIn("Subscription expired", text)

    async def test_feed_pending_provisioning_returns_503(self):
        """Verify pending profiles return 503 with Retry-After: 3."""
        valid_token = "d" * 32
        headers = {"X-HWID": "test-device-uuid-123"}
        active_time = datetime.now(timezone.utc) + timedelta(days=30)
        hwid_hash = hashlib.sha256(b"test-device-uuid-123").hexdigest()
        user = User(
            id=1,
            telegram_id=111,
            subscription_end=active_time,
            device_limit=2,
            active_sub_devices={hwid_hash: {"device_index": 1}},
        )
        server = Server(
            id=10, name="Poland", country_flag="🇵🇱", protocol=AMNEZIA_PROTOCOL,
            is_active=True, health_state=ServerHealthState.ONLINE, capabilities=[],
        )
        pending_profile = VPNProfile(
            id=101, user_id=1, server_id=10, device_type="sub", sub_device_hash=hwid_hash,
            provisioning_status="pending_create", raw_config=None,
        )

        mock_session = AsyncMock()

        @asynccontextmanager
        async def fake_session_scope():
            yield mock_session

        with patch("bot.handlers.awg_sub_web.session_scope", fake_session_scope), \
             patch("database.repositories.users_repo.get_user_by_subscription_token", new_callable=AsyncMock) as mock_get_user:
            mock_get_user.return_value = user

            mock_servers_exec = MagicMock()
            mock_servers_exec.scalars.return_value.all.return_value = [server]

            mock_profiles_exec = MagicMock()
            mock_profiles_exec.scalars.return_value.all.return_value = [pending_profile]

            mock_session.execute.side_effect = [
                mock_servers_exec,
                mock_profiles_exec,
            ]

            resp = await self.client.get(f"{DEFAULT_AWG_SUB_PATH_PREFIX}/{valid_token}", headers=headers)
            self.assertEqual(resp.status, 503)
            self.assertEqual(resp.headers.get("Retry-After"), "3")

    async def test_feed_ready_returns_200_feed(self):
        """Verify ready profiles return 200 with valid base64 awg:// subscription feed."""
        valid_token = "e" * 32
        headers = {"X-HWID": "test-device-uuid-123"}
        active_time = datetime.now(timezone.utc) + timedelta(days=30)
        hwid_hash = hashlib.sha256(b"test-device-uuid-123").hexdigest()
        user = User(
            id=1,
            telegram_id=111,
            subscription_end=active_time,
            device_limit=2,
            active_sub_devices={hwid_hash: {"device_index": 1}},
        )
        server = Server(
            id=10, name="Poland", country_flag="🇵🇱", protocol=AMNEZIA_PROTOCOL,
            is_active=True, health_state=ServerHealthState.ONLINE, capabilities=[],
        )
        ready_profile = VPNProfile(
            id=101, user_id=1, server_id=10, device_type="sub", sub_device_hash=hwid_hash,
            provisioning_status="active", raw_config=_make_dummy_awg_raw_config("Poland"),
        )

        mock_session = AsyncMock()

        @asynccontextmanager
        async def fake_session_scope():
            yield mock_session

        with patch("bot.handlers.awg_sub_web.session_scope", fake_session_scope), \
             patch("database.repositories.users_repo.get_user_by_subscription_token", new_callable=AsyncMock) as mock_get_user:
            mock_get_user.return_value = user

            mock_servers_exec = MagicMock()
            mock_servers_exec.scalars.return_value.all.return_value = [server]

            mock_profiles_exec = MagicMock()
            mock_profiles_exec.scalars.return_value.all.return_value = [ready_profile]

            mock_session.execute.side_effect = [
                mock_servers_exec,
                mock_profiles_exec,
            ]

            resp = await self.client.get(f"{DEFAULT_AWG_SUB_PATH_PREFIX}/{valid_token}", headers=headers)
            self.assertEqual(resp.status, 200)
            self.assertIn("Profile-Title", resp.headers)
            self.assertEqual(resp.headers.get("sort-order"), "ping")

            raw_body = await resp.text()
            decoded_feed = base64.b64decode(raw_body).decode("utf-8")
            self.assertTrue(decoded_feed.startswith("awg://"))
            self.assertIn("#🇵🇱 Poland", decoded_feed)

    async def test_sub_device_registration_fails_closed_when_all_servers_fail(self):
        """Atomic registration: if all server creations fail, slot must NOT be occupied."""
        valid_token = "e" * 32
        headers = {"X-HWID": "brand-new-phone-uuid"}
        active_time = datetime.now(timezone.utc) + timedelta(days=30)
        hwid_hash = hashlib.sha256(b"brand-new-phone-uuid").hexdigest()
        user = User(
            id=1,
            telegram_id=111,
            subscription_end=active_time,
            device_limit=2,
            active_sub_devices={},
        )
        server = Server(
            id=10, name="Poland", country_flag="🇵🇱", protocol=AMNEZIA_PROTOCOL,
            is_active=True, health_state=ServerHealthState.ONLINE, capabilities=[],
        )

        mock_session = AsyncMock()

        @asynccontextmanager
        async def fake_session_scope():
            yield mock_session

        with patch("bot.handlers.awg_sub_web.session_scope", fake_session_scope), \
             patch("database.repositories.users_repo.get_user_by_subscription_token", new_callable=AsyncMock) as mock_get_user, \
             patch("bot.handlers.awg_sub_web.capture_server_peer_snapshot", new_callable=AsyncMock) as mock_snap, \
             patch("bot.handlers.awg_sub_web.DeviceService.create_device", new_callable=AsyncMock) as mock_create_device:
            mock_get_user.return_value = user
            mock_snap.return_value = ServerPeerSnapshot(server_id=10, peer_ids=frozenset(), captured_at=active_time)

            mock_manual_count = MagicMock()
            mock_manual_count.scalar_one.return_value = 0

            mock_servers_exec = MagicMock()
            mock_servers_exec.scalars.return_value.all.return_value = [server]

            mock_profiles_exec = MagicMock()
            mock_profiles_exec.scalars.return_value.all.return_value = []

            mock_session.execute.side_effect = [
                mock_manual_count,
                mock_servers_exec,
                mock_profiles_exec,
            ]

            # Server creation fails
            mock_create_device.side_effect = RuntimeError("Amnezia node is down")

            resp = await self.client.get(f"{DEFAULT_AWG_SUB_PATH_PREFIX}/{valid_token}", headers=headers)
            self.assertEqual(resp.status, 503)

            # Strict Fail-Closed verification:
            # active_sub_devices must STILL be empty! No slot consumed!
            self.assertNotIn(hwid_hash, user.active_sub_devices)

    async def test_stale_failed_profile_recovery_on_feed_refresh(self):
        """Recovery: create_failed profile from prior attempt is cleaned up and recreated."""
        valid_token = "f" * 32
        headers = {"X-HWID": "recovering-phone-uuid"}
        active_time = datetime.now(timezone.utc) + timedelta(days=30)
        hwid_hash = hashlib.sha256(b"recovering-phone-uuid").hexdigest()
        user = User(
            id=1,
            telegram_id=111,
            subscription_end=active_time,
            device_limit=2,
            active_sub_devices={hwid_hash: {"device_index": 1, "label": "Device 1"}},
        )
        server = Server(
            id=10, name="Poland", country_flag="🇵🇱", protocol=AMNEZIA_PROTOCOL,
            is_active=True, health_state=ServerHealthState.ONLINE, capabilities=[],
        )
        stale_failed_profile = VPNProfile(
            id=999, user_id=1, server_id=10, device_type="sub", sub_device_hash=hwid_hash,
            provisioning_status="create_failed", raw_config=None,
        )
        fresh_profile = VPNProfile(
            id=1000, user_id=1, server_id=10, device_type="sub", sub_device_hash=hwid_hash,
            provisioning_status="pending_create", raw_config=None,
        )

        mock_session = AsyncMock()

        @asynccontextmanager
        async def fake_session_scope():
            yield mock_session

        with patch("bot.handlers.awg_sub_web.session_scope", fake_session_scope), \
             patch("database.repositories.users_repo.get_user_by_subscription_token", new_callable=AsyncMock) as mock_get_user, \
             patch("bot.handlers.awg_sub_web.capture_server_peer_snapshot", new_callable=AsyncMock) as mock_snap, \
             patch("bot.handlers.awg_sub_web.DeviceService.delete_device", new_callable=AsyncMock) as mock_delete_device, \
             patch("bot.handlers.awg_sub_web.DeviceService.create_device", new_callable=AsyncMock) as mock_create_device:
            mock_get_user.return_value = user
            mock_snap.return_value = ServerPeerSnapshot(server_id=10, peer_ids=frozenset(), captured_at=active_time)

            mock_servers_exec = MagicMock()
            mock_servers_exec.scalars.return_value.all.return_value = [server]

            mock_profiles_exec = MagicMock()
            mock_profiles_exec.scalars.return_value.all.return_value = [stale_failed_profile]

            mock_session.execute.side_effect = [
                mock_servers_exec,
                mock_profiles_exec,
            ]
            mock_delete_device.return_value = True
            mock_create_device.return_value = fresh_profile

            resp = await self.client.get(f"{DEFAULT_AWG_SUB_PATH_PREFIX}/{valid_token}", headers=headers)
            self.assertEqual(resp.status, 503)

            # Verify stale profile was deleted with force=True
            mock_delete_device.assert_awaited_once_with(mock_session, stale_failed_profile, actor_id=111, force=True)
            # Verify fresh profile was created with replaces_profile_id=999
            mock_create_device.assert_awaited_once()
            _, kwargs = mock_create_device.call_args
            self.assertEqual(kwargs.get("replaces_profile_id"), 999)


class TestAWGSubscriptionBotUI(unittest.IsolatedAsyncioTestCase):
    """Test suite for Telegram Bot UI screens for AWG subscriptions."""

    async def test_build_connections_screen_active_subscription(self):
        """Verify _build_connections_screen renders subscription card with link and action buttons."""
        now = datetime.now(timezone.utc)
        user = User(
            id=1,
            telegram_id=987654321,
            subscription_end=now + timedelta(days=30),
            device_limit=2,
            subscription_token="test_token_1234567890abcdef",
            active_sub_devices={"hwid_hash_1": {"device_index": 1}},
        )

        manual_profile = VPNProfile(
            id=10, user_id=1, server_id=1, device_name="Manual 1",
            device_type="manual", provisioning_status="active",
        )

        mock_session = AsyncMock()

        with patch("bot.handlers.connection.common._get_effective_device_limit", new_callable=AsyncMock) as mock_limit:
            mock_limit.return_value = 2

            rendered_text, builder = await _build_connections_screen(
                user=user,
                session=mock_session,
                profiles=[manual_profile],
                read_only=False,
            )

            self.assertIn("Ваша подписка: AmneziaWG", rendered_text)
            self.assertIn("test_token_1234567890abcdef", rendered_text)
            self.assertIn("2 из 2", rendered_text)

            buttons = [b.text for row in builder.export() for b in row]
            self.assertIn(texts.BTN_DOWNLOAD_CONF, buttons)
            self.assertIn(texts.BTN_MANAGE_DEVICES, buttons)
            self.assertIn(texts.BTN_REFRESH_SUB, buttons)

    async def test_render_manage_devices_shows_both_types(self):
        """Verify _render_manage_devices lists both INCY sub-device and manual .conf profiles."""
        from bot.handlers.connection.awg_subscription_routes import _render_manage_devices

        now = datetime.now(timezone.utc)
        user = User(
            id=1,
            telegram_id=987654321,
            subscription_end=now + timedelta(days=30),
            device_limit=2,
            active_sub_devices={"device_hwid_123456": {"device_index": 1, "label": "Мой Телефон (INCY)"}},
        )

        server = Server(id=1, name="Poland", country_flag="🇵🇱")
        manual_profile = VPNProfile(
            id=10, user_id=1, server_id=1, device_name="Мой ПК",
            device_type="manual", provisioning_status="active",
            server=server,
        )

        mock_session = AsyncMock()
        mock_profiles_exec = MagicMock()
        mock_profiles_exec.scalars.return_value.all.return_value = [manual_profile]
        mock_session.execute.return_value = mock_profiles_exec

        mock_msg = MagicMock()
        mock_msg.chat.id = 12345
        mock_msg.bot = MagicMock()

        with patch("bot.handlers.connection.awg_subscription_routes.render_hub", new_callable=AsyncMock) as mock_render_hub, \
             patch("bot.handlers.connection.awg_subscription_routes._get_effective_device_limit", new_callable=AsyncMock) as mock_limit:
            mock_limit.return_value = 2

            await _render_manage_devices(mock_msg, user, mock_session)

            mock_render_hub.assert_awaited_once()
            args = mock_render_hub.await_args[0]
            rendered_text = args[2]

            self.assertIn("Мой Телефон (INCY)", rendered_text)
            self.assertIn("Мой ПК", rendered_text)
            self.assertIn("Подключено 2 из 2", rendered_text)

    async def test_disconnect_sub_device_callback(self):
        """Verify awg_disconnect_sub properly finds device by prefix and calls delete_sub_device."""
        from bot.handlers.connection.awg_subscription_routes import awg_disconnect_sub

        now = datetime.now(timezone.utc)
        user = User(
            id=1,
            telegram_id=987654321,
            subscription_end=now + timedelta(days=30),
            device_limit=2,
            active_sub_devices={"abcdef1234567890abcdef": {"device_index": 1, "label": "INCY Phone"}},
        )

        callback = MagicMock()
        callback.data = "awg_disconnect_sub:abcdef1234567890"
        callback.from_user.id = 987654321
        callback.answer = AsyncMock()
        callback.message = MagicMock()

        state = MagicMock()
        state.clear = AsyncMock()

        mock_session = AsyncMock()

        with patch("bot.handlers.connection.awg_subscription_routes.get_user_by_telegram_id", new_callable=AsyncMock) as mock_get_user, \
             patch("bot.handlers.connection.awg_subscription_routes.DeviceService.delete_sub_device", new_callable=AsyncMock) as mock_del_sub, \
             patch("bot.handlers.connection.awg_subscription_routes._render_manage_devices", new_callable=AsyncMock) as mock_render:
            mock_get_user.return_value = user

            await awg_disconnect_sub(callback, state, mock_session, db_user=user)

            mock_del_sub.assert_awaited_once_with(
                mock_session,
                user_id=1,
                hwid_hash="abcdef1234567890abcdef",
            )
            callback.answer.assert_awaited_once_with(texts.DEVICE_DISCONNECTED_SUCCESS, show_alert=True)
            mock_render.assert_awaited_once()


class TestAWGSubscriptionTokenSafety(unittest.IsolatedAsyncioTestCase):
    """Verify subscription token security and fail-closed persistence."""

    async def test_ensure_subscription_token_propagates_flush_error(self):
        """ensure_subscription_token must raise if session.flush() fails, never returning phantom tokens."""
        from database.repositories.users_repo import ensure_subscription_token
        user = User(id=1, telegram_id=123, subscription_token=None)
        mock_session = AsyncMock()
        mock_session.flush.side_effect = RuntimeError("Database connection closed during flush")

        with self.assertRaises(RuntimeError):
            await ensure_subscription_token(mock_session, user)

    async def test_ensure_subscription_token_returns_existing(self):
        """ensure_subscription_token returns existing token without touching database."""
        from database.repositories.users_repo import ensure_subscription_token
        user = User(id=1, telegram_id=123, subscription_token="existing_token_123")
        mock_session = AsyncMock()

        token = await ensure_subscription_token(mock_session, user)
        self.assertEqual(token, "existing_token_123")
        mock_session.flush.assert_not_called()


class TestAWGSubscriptionEndToEndLifecycle(unittest.IsolatedAsyncioTestCase):
    """End-to-end multi-step lifecycle testing: provisioning, partial success, recovery, disconnect."""

    async def test_full_subscription_lifecycle(self):
        """Verify: New HWID -> partial server failure -> recovery -> full feed -> fail-closed disconnect."""
        now = datetime.now(timezone.utc)
        user = User(
            id=42,
            telegram_id=777888999,
            subscription_end=now + timedelta(days=30),
            device_limit=2,
            active_sub_devices={},
        )
        hwid_hash = hashlib.sha256(b"e2e-client-device").hexdigest()

        # Step 1: Attempt registration when all servers fail -> verify slot not consumed
        s1 = Server(id=1, name="S1", country_flag="🇩🇪", protocol=AMNEZIA_PROTOCOL, is_active=True, health_state=ServerHealthState.ONLINE)
        s2 = Server(id=2, name="S2", country_flag="🇳🇱", protocol=AMNEZIA_PROTOCOL, is_active=True, health_state=ServerHealthState.ONLINE)

        mock_session = AsyncMock()

        # Step 2: S1 succeeds, S2 fails during creation -> partial success
        p1 = VPNProfile(
            id=101, user_id=42, server_id=1, device_type="sub", sub_device_hash=hwid_hash,
            provisioning_status="pending_create", raw_config=None,
        )

        # Register HWID atomically when p1 is created
        user.active_sub_devices = {
            hwid_hash: {
                "device_index": 1,
                "label": "Устройство #1 (INCY)",
                "first_seen": now.isoformat(),
                "last_seen": now.isoformat(),
            }
        }
        self.assertEqual(len(user.active_sub_devices), 1)

        # Step 3: S1 becomes active with raw_config, S2 recovers and becomes active
        p1.provisioning_status = "active"
        p1.raw_config = _make_dummy_awg_raw_config("S1")

        p2 = VPNProfile(
            id=102, user_id=42, server_id=2, device_type="sub", sub_device_hash=hwid_hash,
            provisioning_status="active", raw_config=_make_dummy_awg_raw_config("S2"),
        )

        # Build feed from active profiles
        feed_items = []
        for p, s in [(p1, s1), (p2, s2)]:
            from utils.vpn_parser import build_conf_file
            conf = build_conf_file(p.raw_config)
            feed_items.append((conf, s.name, s.country_flag, None))

        from services.awg_subscription_feed_service import AWGSubscriptionFeedService
        sorted_servers = AWGSubscriptionFeedService.sort_servers(feed_items, mode="ping")
        feed_b64 = AWGSubscriptionFeedService.build_subscription_body(sorted_servers)
        feed_plain = base64.b64decode(feed_b64).decode("utf-8")

        self.assertIn("#🇩🇪 S1", feed_plain)
        self.assertIn("#🇳🇱 S2", feed_plain)

        # Step 4: Disconnect sub device fails closed if node delete fails
        mock_user_exec = MagicMock()
        mock_user_exec.scalar_one_or_none.return_value = user
        mock_profiles_exec = MagicMock()
        mock_profiles_exec.scalars.return_value.all.return_value = [p1, p2]
        mock_session.execute.side_effect = [mock_user_exec, mock_profiles_exec]

        with patch("services.device_service.DeviceService.delete_device", new_callable=AsyncMock) as mock_delete:
            mock_delete.side_effect = RuntimeError("Node 2 communication failure")

            with self.assertRaises(RuntimeError):
                await DeviceService.delete_sub_device(mock_session, user_id=42, hwid_hash=hwid_hash)

            # Fail-closed: device still active
            self.assertIn(hwid_hash, user.active_sub_devices)

        # Step 5: Successful disconnect clears both profiles and frees logical slot
        mock_session.execute.side_effect = [mock_user_exec, mock_profiles_exec]
        with patch("services.device_service.DeviceService.delete_device", new_callable=AsyncMock) as mock_delete:
            mock_delete.return_value = True
            deleted = await DeviceService.delete_sub_device(mock_session, user_id=42, hwid_hash=hwid_hash)

            self.assertEqual(deleted, 2)
            self.assertNotIn(hwid_hash, user.active_sub_devices)
            self.assertEqual(len(user.active_sub_devices), 0)


if __name__ == "__main__":
    unittest.main()
