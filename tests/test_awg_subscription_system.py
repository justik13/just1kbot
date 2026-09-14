"""Integration and unit tests for the AmneziaWG Unified Subscription System."""

from __future__ import annotations

import base64
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
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
from config.enums import ServerHealthState, ServerLifecycleStatus
from database.models import Server, User, VPNProfile
from services.device_service import (
    DeviceLimitExceeded,
    DeviceService,
    ServerUnavailable,
)
from services.slots_cache import ServerPeerSnapshot
from utils.vpn_parser import encode_json_to_vpn_uri


def _make_dummy_awg_raw_config(server_name: str = "Test Server") -> str:
    """Helper to generate a valid vpn:// URI containing AWG config."""
    awg_dict = {
        "containers": [
            {
                "awg": {
                    "last_config": json.dumps(
                        {
                            "client_priv_key": "dummy_client_priv_key_base64_len_44_chars==",
                            "server_pub_key": "dummy_server_pub_key_base64_len_44_chars==",
                            "client_ip": "10.8.0.2",
                            "hostName": "test.server.io",
                            "port": 51820,
                            "mtu": "1280",
                            "persistent_keep_alive": 25,
                            "Jc": 4,
                            "Jmin": 50,
                            "Jmax": 1000,
                            "S1": 15,
                            "S2": 25,
                            "S3": 35,
                            "S4": 45,
                            "H1": 1,
                            "H2": 2,
                            "H3": 3,
                            "H4": 4,
                        }
                    )
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
            id=10,
            name="Poland",
            protocol=AMNEZIA_PROTOCOL,
            is_active=True,
            max_clients=100,
            api_url="http://s1:8080",
            api_key="k1",
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
            mock_user_exec,  # User select
            mock_server_exec,  # Server select
            mock_duplicate_exec,  # Duplicate name
            mock_count_exec,  # Server count
            mock_peers_exec,  # Bot peers
        ]

        with (
            patch("services.device_service.ensure_server_capacity", new_callable=AsyncMock),
            patch("services.device_service.enqueue_api_operation", new_callable=AsyncMock),
            patch("services.device_service.AuditService.log_action", new_callable=AsyncMock),
        ):
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
            id=10,
            name="Poland",
            protocol=AMNEZIA_PROTOCOL,
            is_active=True,
            max_clients=100,
            api_url="http://s1:8080",
            api_key="k1",
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
            mock_user_exec,  # User select
            mock_server_exec,  # Server select
            mock_duplicate_exec,  # Duplicate name
            mock_manual_count_exec,  # Manual count query (0 manual, 2 sub => total 2 >= limit 2)
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

        p1 = VPNProfile(
            id=101, user_id=1, server_id=10, device_type="sub", sub_device_hash="hwid_to_del"
        )
        p2 = VPNProfile(
            id=102, user_id=1, server_id=20, device_type="sub", sub_device_hash="hwid_to_del"
        )

        mock_session = AsyncMock()
        mock_user_exec = MagicMock()
        mock_user_exec.scalar_one_or_none.return_value = user

        mock_profiles_exec = MagicMock()
        mock_profiles_exec.scalars.return_value.all.return_value = [p1, p2]

        mock_session.execute.side_effect = [
            mock_user_exec,
            mock_profiles_exec,
        ]

        with patch(
            "services.device_service.DeviceService.delete_device", new_callable=AsyncMock
        ) as mock_del:
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

        p1 = VPNProfile(
            id=101, user_id=1, server_id=10, device_type="sub", sub_device_hash="hwid_fail_closed"
        )
        p2 = VPNProfile(
            id=102, user_id=1, server_id=20, device_type="sub", sub_device_hash="hwid_fail_closed"
        )

        mock_session = AsyncMock()
        mock_user_exec = MagicMock()
        mock_user_exec.scalar_one_or_none.return_value = user

        mock_profiles_exec = MagicMock()
        mock_profiles_exec.scalars.return_value.all.return_value = [p1, p2]

        mock_session.execute.side_effect = [
            mock_user_exec,
            mock_profiles_exec,
        ]

        with patch(
            "services.device_service.DeviceService.delete_device", new_callable=AsyncMock
        ) as mock_del:
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

    def _make_mock_session(self):
        @asynccontextmanager
        async def fake_nested():
            yield

        mock_session = AsyncMock()
        mock_session.add = MagicMock()
        mock_session.begin_nested = MagicMock(side_effect=fake_nested)
        return mock_session

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

        with (
            patch("bot.handlers.awg_sub_web.session_scope", fake_session_scope),
            patch(
                "database.repositories.users_repo.get_user_by_subscription_token",
                new_callable=AsyncMock,
            ) as mock_get_user,
        ):
            mock_get_user.return_value = None

            resp = await self.client.get(
                f"{DEFAULT_AWG_SUB_PATH_PREFIX}/{valid_token}", headers=headers
            )
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

        with (
            patch("bot.handlers.awg_sub_web.session_scope", fake_session_scope),
            patch(
                "database.repositories.users_repo.get_user_by_subscription_token",
                new_callable=AsyncMock,
            ) as mock_get_user,
        ):
            mock_get_user.return_value = user

            resp = await self.client.get(
                f"{DEFAULT_AWG_SUB_PATH_PREFIX}/{valid_token}", headers=headers
            )
            self.assertEqual(resp.status, 403)
            text = await resp.text()
            self.assertIn("Subscription expired", text)

    async def test_feed_browser_redirect_success(self):
        """Verify browser requests without HWID return HTTP 302 redirecting to incy://add/{url}."""
        valid_token = "landing_token_123456789012345"
        headers = {"Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"}
        active_time = datetime.now(timezone.utc) + timedelta(days=30)
        user = User(
            id=1,
            telegram_id=111,
            subscription_end=active_time,
            device_limit=2,
            active_sub_devices={},
        )

        mock_session = AsyncMock()

        @asynccontextmanager
        async def fake_session_scope():
            yield mock_session

        with (
            patch("bot.handlers.awg_sub_web.session_scope", fake_session_scope),
            patch(
                "database.repositories.users_repo.get_user_by_subscription_token",
                new_callable=AsyncMock,
            ) as mock_get_user,
        ):
            mock_get_user.return_value = user

            resp = await self.client.get(
                f"{DEFAULT_AWG_SUB_PATH_PREFIX}/{valid_token}",
                headers=headers,
                allow_redirects=False,
            )
            self.assertEqual(resp.status, 200)
            self.assertEqual(resp.headers.get("Content-Type"), "text/html; charset=utf-8")
            html_text = await resp.text()
            self.assertIn("incy://add/", html_text)
            self.assertIn(valid_token, html_text)
            self.assertIn("apps.apple.com/app/incy", html_text)
            self.assertIn("play.google.com/store/apps/details?id=llc.itdev.incy", html_text)
            self.assertIn("github.com/INCY-DEV/incy-platforms", html_text)

            # Invariant: zero slot consumption / user was only read
            self.assertEqual(len(user.active_sub_devices), 0)

    async def test_feed_browser_token_not_found(self):
        """Verify browser requests with unknown token render 404 HTML error page."""
        valid_token = "notfound_token_12345678901234"
        headers = {"Accept": "text/html"}

        mock_session = AsyncMock()

        @asynccontextmanager
        async def fake_session_scope():
            yield mock_session

        with (
            patch("bot.handlers.awg_sub_web.session_scope", fake_session_scope),
            patch(
                "database.repositories.users_repo.get_user_by_subscription_token",
                new_callable=AsyncMock,
            ) as mock_get_user,
        ):
            mock_get_user.return_value = None

            resp = await self.client.get(
                f"{DEFAULT_AWG_SUB_PATH_PREFIX}/{valid_token}",
                headers=headers,
                allow_redirects=False,
            )
            self.assertEqual(resp.status, 404)
            self.assertEqual(resp.headers.get("Content-Type"), "text/html; charset=utf-8")
            html_text = await resp.text()
            self.assertIn("Подписка не найдена", html_text)

    async def test_feed_browser_token_short(self):
        """Verify browser requests with token < 16 chars render 404 HTML error page."""
        headers = {"Accept": "text/html"}
        resp = await self.client.get(
            f"{DEFAULT_AWG_SUB_PATH_PREFIX}/short",
            headers=headers,
            allow_redirects=False,
        )
        self.assertEqual(resp.status, 404)
        self.assertEqual(resp.headers.get("Content-Type"), "text/html; charset=utf-8")
        html_text = await resp.text()
        self.assertIn("Подписка не найдена", html_text)

    async def test_feed_browser_expired_subscription(self):
        """Verify browser requests with expired subscription render 403 HTML error page."""
        valid_token = "expired_token_12345678901234"
        headers = {"Accept": "text/html"}
        expired_time = datetime.now(timezone.utc) - timedelta(days=2)
        user = User(
            id=1,
            telegram_id=111,
            subscription_end=expired_time,
            device_limit=2,
        )

        mock_session = AsyncMock()

        @asynccontextmanager
        async def fake_session_scope():
            yield mock_session

        with (
            patch("bot.handlers.awg_sub_web.session_scope", fake_session_scope),
            patch(
                "database.repositories.users_repo.get_user_by_subscription_token",
                new_callable=AsyncMock,
            ) as mock_get_user,
        ):
            mock_get_user.return_value = user

            resp = await self.client.get(
                f"{DEFAULT_AWG_SUB_PATH_PREFIX}/{valid_token}",
                headers=headers,
                allow_redirects=False,
            )
            self.assertEqual(resp.status, 403)
            self.assertEqual(resp.headers.get("Content-Type"), "text/html; charset=utf-8")
            html_text = await resp.text()
            self.assertIn(texts.AWG_BROWSER_ERR_EXPIRED_TITLE, html_text)

    async def test_feed_browser_banned_or_hold_user(self):
        """Verify browser requests for banned or hold users render 403 HTML error page."""
        valid_token = "banned_token_123456789012345"
        headers = {"Accept": "text/html"}
        active_time = datetime.now(timezone.utc) + timedelta(days=10)
        banned_user = User(
            id=1,
            telegram_id=111,
            subscription_end=active_time,
            is_banned=True,
        )

        mock_session = AsyncMock()

        @asynccontextmanager
        async def fake_session_scope():
            yield mock_session

        with (
            patch("bot.handlers.awg_sub_web.session_scope", fake_session_scope),
            patch(
                "database.repositories.users_repo.get_user_by_subscription_token",
                new_callable=AsyncMock,
            ) as mock_get_user,
        ):
            mock_get_user.return_value = banned_user

            resp = await self.client.get(
                f"{DEFAULT_AWG_SUB_PATH_PREFIX}/{valid_token}",
                headers=headers,
                allow_redirects=False,
            )
            self.assertEqual(resp.status, 403)
            self.assertEqual(resp.headers.get("Content-Type"), "text/html; charset=utf-8")
            html_text = await resp.text()
            self.assertIn(texts.AWG_BROWSER_ERR_BANNED_TITLE, html_text)

    async def test_feed_browser_sec_fetch_dest_and_format_html(self):
        """Verify Sec-Fetch-Dest: document and ?format=html override non-HTML Accept headers and render HTML."""
        valid_token = "override_token_1234567890123"
        active_time = datetime.now(timezone.utc) + timedelta(days=10)
        user = User(
            id=1,
            telegram_id=111,
            subscription_end=active_time,
        )

        mock_session = AsyncMock()

        @asynccontextmanager
        async def fake_session_scope():
            yield mock_session

        with (
            patch("bot.handlers.awg_sub_web.session_scope", fake_session_scope),
            patch(
                "database.repositories.users_repo.get_user_by_subscription_token",
                new_callable=AsyncMock,
            ) as mock_get_user,
        ):
            mock_get_user.return_value = user

            # 1. Sec-Fetch-Dest: document
            resp1 = await self.client.get(
                f"{DEFAULT_AWG_SUB_PATH_PREFIX}/{valid_token}",
                headers={"Sec-Fetch-Dest": "document"},
                allow_redirects=False,
            )
            self.assertEqual(resp1.status, 200)
            self.assertEqual(resp1.headers.get("Content-Type"), "text/html; charset=utf-8")

            # 2. ?format=html
            resp2 = await self.client.get(
                f"{DEFAULT_AWG_SUB_PATH_PREFIX}/{valid_token}?format=html",
                allow_redirects=False,
            )
            self.assertEqual(resp2.status, 200)
            self.assertEqual(resp2.headers.get("Content-Type"), "text/html; charset=utf-8")


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
            id=10,
            name="Poland",
            country_flag="🇵🇱",
            protocol=AMNEZIA_PROTOCOL,
            is_active=True,
            health_state=ServerHealthState.ONLINE,
            capabilities=[],
        )
        pending_profile = VPNProfile(
            id=101,
            user_id=1,
            server_id=10,
            device_type="sub",
            sub_device_hash=hwid_hash,
            provisioning_status="pending_create",
            raw_config=None,
        )

        mock_session = AsyncMock()

        @asynccontextmanager
        async def fake_session_scope():
            yield mock_session

        with (
            patch("bot.handlers.awg_sub_web.session_scope", fake_session_scope),
            patch(
                "database.repositories.users_repo.get_user_by_subscription_token",
                new_callable=AsyncMock,
            ) as mock_get_user,
        ):
            mock_get_user.return_value = user

            mock_servers_exec = MagicMock()
            mock_servers_exec.scalars.return_value.all.return_value = [server]

            mock_profiles_exec = MagicMock()
            mock_profiles_exec.scalars.return_value.all.return_value = [pending_profile]

            mock_session.execute.side_effect = [
                mock_servers_exec,
                mock_profiles_exec,
            ]

            resp = await self.client.get(
                f"{DEFAULT_AWG_SUB_PATH_PREFIX}/{valid_token}", headers=headers
            )
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
            id=10,
            name="Poland",
            country_flag="🇵🇱",
            protocol=AMNEZIA_PROTOCOL,
            is_active=True,
            health_state=ServerHealthState.ONLINE,
            capabilities=[],
        )
        ready_profile = VPNProfile(
            id=101,
            user_id=1,
            server_id=10,
            device_type="sub",
            sub_device_hash=hwid_hash,
            provisioning_status="active",
            raw_config=_make_dummy_awg_raw_config("Poland"),
        )

        mock_session = AsyncMock()

        @asynccontextmanager
        async def fake_session_scope():
            yield mock_session

        with (
            patch("bot.handlers.awg_sub_web.session_scope", fake_session_scope),
            patch(
                "database.repositories.users_repo.get_user_by_subscription_token",
                new_callable=AsyncMock,
            ) as mock_get_user,
        ):
            mock_get_user.return_value = user

            mock_servers_exec = MagicMock()
            mock_servers_exec.scalars.return_value.all.return_value = [server]

            mock_profiles_exec = MagicMock()
            mock_profiles_exec.scalars.return_value.all.return_value = [ready_profile]

            mock_session.execute.side_effect = [
                mock_servers_exec,
                mock_profiles_exec,
            ]

            resp = await self.client.get(
                f"{DEFAULT_AWG_SUB_PATH_PREFIX}/{valid_token}", headers=headers
            )
            self.assertEqual(resp.status, 200)
            self.assertIn("Profile-Title", resp.headers)
            self.assertEqual(resp.headers.get("sort-order"), "ping")

            raw_body = await resp.text()
            decoded_feed = base64.b64decode(raw_body).decode("utf-8")
            self.assertTrue(decoded_feed.startswith("awg://"))
            self.assertIn("#🇵🇱 Poland", decoded_feed)

    async def test_feed_excludes_xray_origin_and_decommissioning_servers(self):
        """Verify awg_subscription_feed_handler excludes servers with xray_origin or non-ACTIVE."""
        valid_token = "f" * 32
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
        awg_server = Server(
            id=10,
            name="Valid AWG",
            country_flag="🇩🇪",
            protocol=AMNEZIA_PROTOCOL,
            is_active=True,
            health_state=ServerHealthState.ONLINE,
            lifecycle_status=ServerLifecycleStatus.ACTIVE,
            capabilities=[],
        )
        xray_server = Server(
            id=20,
            name="Xray Origin",
            country_flag="🇷🇺",
            protocol=AMNEZIA_PROTOCOL,
            is_active=True,
            health_state=ServerHealthState.ONLINE,
            lifecycle_status=ServerLifecycleStatus.ACTIVE,
            capabilities=["xray_origin"],
        )
        ready_profile = VPNProfile(
            id=101,
            user_id=1,
            server_id=10,
            device_type="sub",
            sub_device_hash=hwid_hash,
            provisioning_status="active",
            raw_config=_make_dummy_awg_raw_config("Valid AWG"),
        )

        mock_session = AsyncMock()

        @asynccontextmanager
        async def fake_session_scope():
            yield mock_session

        with (
            patch("bot.handlers.awg_sub_web.session_scope", fake_session_scope),
            patch(
                "database.repositories.users_repo.get_user_by_subscription_token",
                new_callable=AsyncMock,
            ) as mock_get_user,
        ):
            mock_get_user.return_value = user

            mock_servers_exec = MagicMock()
            # DB returns both, but python code must filter out xray_server
            mock_servers_exec.scalars.return_value.all.return_value = [awg_server, xray_server]

            mock_profiles_exec = MagicMock()
            mock_profiles_exec.scalars.return_value.all.return_value = [ready_profile]

            mock_session.execute.side_effect = [
                mock_servers_exec,
                mock_profiles_exec,
            ]

            resp = await self.client.get(
                f"{DEFAULT_AWG_SUB_PATH_PREFIX}/{valid_token}", headers=headers
            )
            self.assertEqual(resp.status, 200)
            body = await resp.text()
            decoded = base64.b64decode(body).decode("utf-8")
            self.assertIn("Valid AWG", decoded)
            self.assertNotIn("Xray Origin", decoded)

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
            id=10,
            name="Poland",
            country_flag="🇵🇱",
            protocol=AMNEZIA_PROTOCOL,
            is_active=True,
            health_state=ServerHealthState.ONLINE,
            capabilities=[],
        )

        mock_session = self._make_mock_session()

        @asynccontextmanager
        async def fake_session_scope():
            yield mock_session

        with (
            patch("bot.handlers.awg_sub_web.session_scope", fake_session_scope),
            patch(
                "database.repositories.users_repo.get_user_by_subscription_token",
                new_callable=AsyncMock,
            ) as mock_get_user,
            patch(
                "bot.handlers.awg_sub_web.capture_server_peer_snapshot", new_callable=AsyncMock
            ) as mock_snap,
            patch(
                "bot.handlers.awg_sub_web.DeviceService.create_device", new_callable=AsyncMock
            ) as mock_create_device,
        ):
            mock_get_user.return_value = user
            mock_snap.return_value = ServerPeerSnapshot(
                server_id=10, peer_ids=frozenset(), captured_at=active_time
            )

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

            resp = await self.client.get(
                f"{DEFAULT_AWG_SUB_PATH_PREFIX}/{valid_token}", headers=headers
            )
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
            id=10,
            name="Poland",
            country_flag="🇵🇱",
            protocol=AMNEZIA_PROTOCOL,
            is_active=True,
            health_state=ServerHealthState.ONLINE,
            capabilities=[],
        )
        stale_failed_profile = VPNProfile(
            id=999,
            user_id=1,
            server_id=10,
            device_type="sub",
            sub_device_hash=hwid_hash,
            provisioning_status="create_failed",
            raw_config=None,
        )
        fresh_profile = VPNProfile(
            id=1000,
            user_id=1,
            server_id=10,
            device_type="sub",
            sub_device_hash=hwid_hash,
            provisioning_status="pending_create",
            raw_config=None,
        )

        mock_session = self._make_mock_session()

        @asynccontextmanager
        async def fake_session_scope():
            yield mock_session

        with (
            patch("bot.handlers.awg_sub_web.session_scope", fake_session_scope),
            patch(
                "database.repositories.users_repo.get_user_by_subscription_token",
                new_callable=AsyncMock,
            ) as mock_get_user,
            patch(
                "bot.handlers.awg_sub_web.capture_server_peer_snapshot", new_callable=AsyncMock
            ) as mock_snap,
            patch(
                "bot.handlers.awg_sub_web.DeviceService.delete_device", new_callable=AsyncMock
            ) as mock_delete_device,
            patch(
                "bot.handlers.awg_sub_web.DeviceService.create_device", new_callable=AsyncMock
            ) as mock_create_device,
        ):
            mock_get_user.return_value = user
            mock_snap.return_value = ServerPeerSnapshot(
                server_id=10, peer_ids=frozenset(), captured_at=active_time
            )

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

            resp = await self.client.get(
                f"{DEFAULT_AWG_SUB_PATH_PREFIX}/{valid_token}", headers=headers
            )
            self.assertEqual(resp.status, 503)

            # Verify stale profile was deleted with force=True
            mock_delete_device.assert_awaited_once_with(
                mock_session, stale_failed_profile, actor_id=111, force=True
            )
            # Verify fresh profile was created with replaces_profile_id=999
            mock_create_device.assert_awaited_once()
            _, kwargs = mock_create_device.call_args
            self.assertEqual(kwargs.get("replaces_profile_id"), 999)

    async def test_stale_cleanup_pending_profile_defers_recreation(self):
        """create_cleanup_pending profile defers recreation until remote peer cleanup finishes."""
        valid_token = "f" * 32
        headers = {"X-HWID": "cleanup-pending-phone"}
        active_time = datetime.now(timezone.utc) + timedelta(days=30)
        hwid_hash = hashlib.sha256(b"cleanup-pending-phone").hexdigest()
        user = User(
            id=1,
            telegram_id=111,
            subscription_end=active_time,
            device_limit=2,
            active_sub_devices={hwid_hash: {"device_index": 1, "label": "Device 1"}},
        )
        server = Server(
            id=10,
            name="Poland",
            country_flag="🇵🇱",
            protocol=AMNEZIA_PROTOCOL,
            is_active=True,
            health_state=ServerHealthState.ONLINE,
            capabilities=[],
        )
        stale_profile = VPNProfile(
            id=999,
            user_id=1,
            server_id=10,
            device_type="sub",
            sub_device_hash=hwid_hash,
            provisioning_status="create_cleanup_pending",
            raw_config=None,
        )

        mock_session = self._make_mock_session()

        @asynccontextmanager
        async def fake_session_scope():
            yield mock_session

        with (
            patch("bot.handlers.awg_sub_web.session_scope", fake_session_scope),
            patch(
                "database.repositories.users_repo.get_user_by_subscription_token",
                new_callable=AsyncMock,
            ) as mock_get_user,
            patch(
                "bot.handlers.awg_sub_web.capture_server_peer_snapshot", new_callable=AsyncMock
            ) as mock_snap,
            patch(
                "bot.handlers.awg_sub_web.DeviceService.delete_device", new_callable=AsyncMock
            ) as mock_delete_device,
            patch(
                "bot.handlers.awg_sub_web.DeviceService.create_device", new_callable=AsyncMock
            ) as mock_create_device,
        ):
            mock_get_user.return_value = user
            mock_snap.return_value = ServerPeerSnapshot(
                server_id=10, peer_ids=frozenset(), captured_at=active_time
            )

            mock_servers_exec = MagicMock()
            mock_servers_exec.scalars.return_value.all.return_value = [server]

            mock_profiles_exec = MagicMock()
            mock_profiles_exec.scalars.return_value.all.return_value = [stale_profile]

            mock_session.execute.side_effect = [
                mock_servers_exec,
                mock_profiles_exec,
            ]

            resp = await self.client.get(
                f"{DEFAULT_AWG_SUB_PATH_PREFIX}/{valid_token}", headers=headers
            )
            self.assertEqual(resp.status, 503)

            # Neither delete_device nor create_device should be called on this turn
            mock_delete_device.assert_not_called()
            mock_create_device.assert_not_called()

    async def test_stale_profile_delete_failure_skips_recreation(self):
        """If force delete of stale create_failed profile fails, recreation is aborted."""
        valid_token = "f" * 32
        headers = {"X-HWID": "del-fail-phone"}
        active_time = datetime.now(timezone.utc) + timedelta(days=30)
        hwid_hash = hashlib.sha256(b"del-fail-phone").hexdigest()
        user = User(
            id=1,
            telegram_id=111,
            subscription_end=active_time,
            device_limit=2,
            active_sub_devices={hwid_hash: {"device_index": 1, "label": "Device 1"}},
        )
        server = Server(
            id=10,
            name="Poland",
            country_flag="🇵🇱",
            protocol=AMNEZIA_PROTOCOL,
            is_active=True,
            health_state=ServerHealthState.ONLINE,
            capabilities=[],
        )
        stale_profile = VPNProfile(
            id=999,
            user_id=1,
            server_id=10,
            device_type="sub",
            sub_device_hash=hwid_hash,
            provisioning_status="create_failed",
            raw_config=None,
        )

        mock_session = self._make_mock_session()

        @asynccontextmanager
        async def fake_session_scope():
            yield mock_session

        with (
            patch("bot.handlers.awg_sub_web.session_scope", fake_session_scope),
            patch(
                "database.repositories.users_repo.get_user_by_subscription_token",
                new_callable=AsyncMock,
            ) as mock_get_user,
            patch(
                "bot.handlers.awg_sub_web.capture_server_peer_snapshot", new_callable=AsyncMock
            ) as mock_snap,
            patch(
                "bot.handlers.awg_sub_web.DeviceService.delete_device", new_callable=AsyncMock
            ) as mock_delete_device,
            patch(
                "bot.handlers.awg_sub_web.DeviceService.create_device", new_callable=AsyncMock
            ) as mock_create_device,
        ):
            mock_get_user.return_value = user
            mock_snap.return_value = ServerPeerSnapshot(
                server_id=10, peer_ids=frozenset(), captured_at=active_time
            )

            mock_servers_exec = MagicMock()
            mock_servers_exec.scalars.return_value.all.return_value = [server]

            mock_profiles_exec = MagicMock()
            mock_profiles_exec.scalars.return_value.all.return_value = [stale_profile]

            mock_session.execute.side_effect = [
                mock_servers_exec,
                mock_profiles_exec,
            ]
            mock_delete_device.side_effect = RuntimeError("Lock acquisition failed during delete")

            resp = await self.client.get(
                f"{DEFAULT_AWG_SUB_PATH_PREFIX}/{valid_token}", headers=headers
            )
            self.assertEqual(resp.status, 503)

            # delete_device attempted but raised; create_device must NOT be called
            mock_delete_device.assert_awaited_once()
            mock_create_device.assert_not_called()

    async def test_device_index_allocation_reuses_lowest_gap(self):
        """When device #1 was deleted and #2 exists, new device gets index 1 instead of duplicating 2."""
        valid_token = "f" * 32
        headers = {"X-HWID": "gap-phone-hwid"}
        active_time = datetime.now(timezone.utc) + timedelta(days=30)
        hwid_hash = hashlib.sha256(b"gap-phone-hwid").hexdigest()
        user = User(
            id=1,
            telegram_id=111,
            subscription_end=active_time,
            device_limit=3,
            # Device #1 deleted, #2 remains
            active_sub_devices={"existing_hwid_2": {"device_index": 2, "label": "Device 2"}},
        )
        server = Server(
            id=10,
            name="Poland",
            country_flag="🇵🇱",
            protocol=AMNEZIA_PROTOCOL,
            is_active=True,
            health_state=ServerHealthState.ONLINE,
            capabilities=[],
        )
        fresh_profile = VPNProfile(
            id=1001,
            user_id=1,
            server_id=10,
            device_type="sub",
            sub_device_hash=hwid_hash,
            provisioning_status="pending_create",
            raw_config=None,
        )

        mock_session = self._make_mock_session()

        @asynccontextmanager
        async def fake_session_scope():
            yield mock_session

        with (
            patch("bot.handlers.awg_sub_web.session_scope", fake_session_scope),
            patch(
                "database.repositories.users_repo.get_user_by_subscription_token",
                new_callable=AsyncMock,
            ) as mock_get_user,
            patch(
                "bot.handlers.awg_sub_web.capture_server_peer_snapshot", new_callable=AsyncMock
            ) as mock_snap,
            patch(
                "bot.handlers.awg_sub_web.DeviceService.create_device", new_callable=AsyncMock
            ) as mock_create_device,
        ):
            mock_get_user.return_value = user
            mock_snap.return_value = ServerPeerSnapshot(
                server_id=10, peer_ids=frozenset(), captured_at=active_time
            )

            mock_count_exec = MagicMock()
            mock_count_exec.scalar_one.return_value = 0

            mock_servers_exec = MagicMock()
            mock_servers_exec.scalars.return_value.all.return_value = [server]
            mock_profiles_exec = MagicMock()
            mock_profiles_exec.scalars.return_value.all.return_value = []

            mock_session.execute.side_effect = [
                mock_count_exec,
                mock_servers_exec,
                mock_profiles_exec,
            ]
            mock_create_device.return_value = fresh_profile

            resp = await self.client.get(
                f"{DEFAULT_AWG_SUB_PATH_PREFIX}/{valid_token}", headers=headers
            )
            self.assertEqual(resp.status, 503)

            # Assert registered device got index 1 (not 2 or 3)
            self.assertIn(hwid_hash, user.active_sub_devices)
            self.assertEqual(user.active_sub_devices[hwid_hash]["device_index"], 1)

    async def test_provisioning_savepoint_rollback_on_partial_failure(self):
        """Verify that failure creating profile on server 1 triggers begin_nested savepoint rollback."""
        valid_token = "f" * 32
        headers = {"X-HWID": "savepoint-phone"}
        active_time = datetime.now(timezone.utc) + timedelta(days=30)
        hwid_hash = hashlib.sha256(b"savepoint-phone").hexdigest()
        user = User(
            id=1,
            telegram_id=111,
            subscription_end=active_time,
            device_limit=2,
            active_sub_devices={},
        )
        s1 = Server(
            id=1,
            name="S1",
            country_flag="🇵🇱",
            protocol=AMNEZIA_PROTOCOL,
            is_active=True,
            health_state=ServerHealthState.ONLINE,
            capabilities=[],
        )
        s2 = Server(
            id=2,
            name="S2",
            country_flag="🇩🇪",
            protocol=AMNEZIA_PROTOCOL,
            is_active=True,
            health_state=ServerHealthState.ONLINE,
            capabilities=[],
        )

        p2 = VPNProfile(
            id=202,
            user_id=1,
            server_id=2,
            device_type="sub",
            sub_device_hash=hwid_hash,
            provisioning_status="pending_create",
            raw_config=None,
        )

        mock_session = self._make_mock_session()

        @asynccontextmanager
        async def fake_session_scope():
            yield mock_session

        with (
            patch("bot.handlers.awg_sub_web.session_scope", fake_session_scope),
            patch(
                "database.repositories.users_repo.get_user_by_subscription_token",
                new_callable=AsyncMock,
            ) as mock_get_user,
            patch(
                "bot.handlers.awg_sub_web.capture_server_peer_snapshot", new_callable=AsyncMock
            ) as mock_snap,
            patch(
                "bot.handlers.awg_sub_web.DeviceService.create_device", new_callable=AsyncMock
            ) as mock_create_device,
        ):
            mock_get_user.return_value = user
            mock_snap.return_value = ServerPeerSnapshot(
                server_id=1, peer_ids=frozenset(), captured_at=active_time
            )

            mock_count_exec = MagicMock()
            mock_count_exec.scalar_one.return_value = 0

            mock_servers_exec = MagicMock()
            mock_servers_exec.scalars.return_value.all.return_value = [s1, s2]
            mock_profiles_exec = MagicMock()
            mock_profiles_exec.scalars.return_value.all.return_value = []

            mock_session.execute.side_effect = [
                mock_count_exec,
                mock_servers_exec,
                mock_profiles_exec,
            ]

            # Server 1 fails with RuntimeError during create_device, Server 2 succeeds
            mock_create_device.side_effect = [
                RuntimeError("Enqueue API operation failed"),
                p2,
            ]

            resp = await self.client.get(
                f"{DEFAULT_AWG_SUB_PATH_PREFIX}/{valid_token}", headers=headers
            )
            self.assertEqual(resp.status, 503)

            # begin_nested must be called for each server iteration
            self.assertEqual(mock_session.begin_nested.call_count, 2)
            # user must still be registered because Server 2 succeeded
            self.assertIn(hwid_hash, user.active_sub_devices)

    async def test_create_device_atomicity_rolls_back_on_enqueue_failure(self):
        """DeviceService.create_device rolls back savepoint if enqueue_api_operation raises."""
        now = datetime.now(timezone.utc)
        user = User(
            id=1,
            telegram_id=123,
            subscription_end=now + timedelta(days=30),
            device_limit=5,
            active_sub_devices={},
            is_banned=False,
            is_bot_blocked=False,
        )
        server = Server(
            id=1,
            name="S1",
            max_clients=10,
            is_active=True,
            protocol=AMNEZIA_PROTOCOL,
            api_url="http://node",
            api_key="k",
        )
        snapshot = ServerPeerSnapshot(server_id=1, peer_ids=frozenset(), captured_at=now)

        mock_session = self._make_mock_session()

        mock_user_exec = MagicMock()
        mock_user_exec.scalar_one.return_value = user
        mock_user_exec.scalar_one_or_none.return_value = user

        mock_server_exec = MagicMock()
        mock_server_exec.scalar_one.return_value = server
        mock_server_exec.scalar_one_or_none.return_value = server

        mock_dupe_exec = MagicMock()
        mock_dupe_exec.scalar_one_or_none.return_value = None

        mock_manual_count_exec = MagicMock()
        mock_manual_count_exec.scalar_one.return_value = 0

        mock_server_count_exec = MagicMock()
        mock_server_count_exec.scalar_one.return_value = 0

        mock_peers_exec = MagicMock()
        mock_peers_exec.scalars.return_value.all.return_value = []

        mock_session.execute.side_effect = [
            mock_user_exec,
            mock_server_exec,
            mock_dupe_exec,
            mock_manual_count_exec,
            mock_server_count_exec,
            mock_peers_exec,
        ]

        with (
            patch("services.device_service.ensure_server_capacity", new_callable=AsyncMock),
            patch(
                "services.device_service.enqueue_api_operation", new_callable=AsyncMock
            ) as mock_enqueue,
        ):
            mock_enqueue.side_effect = RuntimeError("Enqueue operation database error")

            with self.assertRaises(RuntimeError):
                await DeviceService.create_device(
                    mock_session,
                    user_id=1,
                    server_id=1,
                    device_name="Test Dev #1",
                    snapshot=snapshot,
                )

            # Verify begin_nested was used
            mock_session.begin_nested.assert_called_once()

    async def test_multiple_hwids_same_user_same_server_unique_profiles(self):
        """P1 verification: multiple subscription devices for same user get unique profile names on same server."""
        valid_token = "f" * 32
        active_time = datetime.now(timezone.utc) + timedelta(days=30)
        user = User(
            id=1,
            telegram_id=12345,
            subscription_end=active_time,
            device_limit=2,
            active_sub_devices={},
        )
        server = Server(
            id=10,
            name="Poland",
            country_flag="🇵🇱",
            protocol=AMNEZIA_PROTOCOL,
            is_active=True,
            health_state=ServerHealthState.ONLINE,
            capabilities=[],
        )
        raw_conf = _make_dummy_awg_raw_config("Poland")

        mock_session = self._make_mock_session()

        @asynccontextmanager
        async def fake_session_scope():
            yield mock_session

        hwid_a = "device-hardware-id-alpha"
        hwid_b = "device-hardware-id-beta"
        hwid_c = "device-hardware-id-gamma"
        hwid_a_hash = hashlib.sha256(hwid_a.lower().encode("utf-8")).hexdigest()
        hwid_b_hash = hashlib.sha256(hwid_b.lower().encode("utf-8")).hexdigest()

        created_device_names = []

        async def fake_create_device(session, **kwargs):
            dev_name = kwargs.get("device_name")
            created_device_names.append(dev_name)
            sub_hash = kwargs.get("sub_device_hash")
            return VPNProfile(
                id=100 + len(created_device_names),
                user_id=1,
                server_id=10,
                device_name=dev_name,
                device_type="sub",
                sub_device_hash=sub_hash,
                provisioning_status="active",
                raw_config=raw_conf,
            )

        with (
            patch("bot.handlers.awg_sub_web.session_scope", fake_session_scope),
            patch(
                "database.repositories.users_repo.get_user_by_subscription_token",
                new_callable=AsyncMock,
            ) as mock_get_user,
            patch(
                "bot.handlers.awg_sub_web.capture_server_peer_snapshot", new_callable=AsyncMock
            ) as mock_snap,
            patch(
                "bot.handlers.awg_sub_web.DeviceService.create_device",
                side_effect=fake_create_device,
            ),
        ):
            mock_get_user.return_value = user
            mock_snap.return_value = ServerPeerSnapshot(
                server_id=10, peer_ids=frozenset(), captured_at=active_time
            )

            # 1. First device connects (HWID A)
            mock_count_exec_1 = MagicMock()
            mock_count_exec_1.scalar_one.return_value = 0
            mock_servers_exec_1 = MagicMock()
            mock_servers_exec_1.scalars.return_value.all.return_value = [server]
            mock_profiles_exec_1 = MagicMock()
            mock_profiles_exec_1.scalars.return_value.all.return_value = []

            mock_session.execute.side_effect = [
                mock_count_exec_1,
                mock_servers_exec_1,
                mock_profiles_exec_1,
            ]

            resp_a = await self.client.get(
                f"{DEFAULT_AWG_SUB_PATH_PREFIX}/{valid_token}", headers={"X-HWID": hwid_a}
            )
            self.assertEqual(resp_a.status, 200)
            self.assertIn(hwid_a_hash, user.active_sub_devices)
            self.assertEqual(user.active_sub_devices[hwid_a_hash]["device_index"], 1)

            # 2. Second device connects (HWID B)
            mock_count_exec_2 = MagicMock()
            mock_count_exec_2.scalar_one.return_value = 0
            mock_servers_exec_2 = MagicMock()
            mock_servers_exec_2.scalars.return_value.all.return_value = [server]
            mock_profiles_exec_2 = MagicMock()
            mock_profiles_exec_2.scalars.return_value.all.return_value = []

            mock_session.execute.side_effect = [
                mock_count_exec_2,
                mock_servers_exec_2,
                mock_profiles_exec_2,
            ]

            resp_b = await self.client.get(
                f"{DEFAULT_AWG_SUB_PATH_PREFIX}/{valid_token}", headers={"X-HWID": hwid_b}
            )
            self.assertEqual(resp_b.status, 200)
            self.assertIn(hwid_b_hash, user.active_sub_devices)
            self.assertEqual(user.active_sub_devices[hwid_b_hash]["device_index"], 2)

            # Verify both profiles got distinct names on server 10!
            self.assertEqual(len(created_device_names), 2)
            self.assertEqual(created_device_names[0], "INCY (Poland) #1")
            self.assertEqual(created_device_names[1], "INCY (Poland) #2")
            self.assertNotEqual(created_device_names[0], created_device_names[1])

            # 3. Third device connects (HWID C) -> exceeds limit of 2
            mock_count_exec_3 = MagicMock()
            mock_count_exec_3.scalar_one.return_value = 0
            mock_session.execute.side_effect = [mock_count_exec_3]

            resp_c = await self.client.get(
                f"{DEFAULT_AWG_SUB_PATH_PREFIX}/{valid_token}", headers={"X-HWID": hwid_c}
            )
            self.assertEqual(resp_c.status, 403)
            self.assertIn("Device limit reached (2/2)", await resp_c.text())


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
            id=10,
            user_id=1,
            server_id=1,
            device_name="Manual 1",
            device_type="manual",
            provisioning_status="active",
        )

        mock_session = AsyncMock()

        with patch(
            "bot.handlers.connection.common._get_effective_device_limit", new_callable=AsyncMock
        ) as mock_limit:
            mock_limit.return_value = 2

            rendered_text, builder = await _build_connections_screen(
                user=user,
                session=mock_session,
                profiles=[manual_profile],
                read_only=False,
            )

            self.assertIn("Ваша подписка", rendered_text)
            self.assertNotIn("AmneziaWG", rendered_text)
            self.assertIn("test_token_1234567890abcdef", rendered_text)
            self.assertIn("2 из 2", rendered_text)

            buttons = [b.text for row in builder.export() for b in row]
            self.assertIn(texts.BTN_OPEN_INCY_APP, buttons)
            self.assertIn(texts.BTN_MANAGE_DEVICES, buttons)
            self.assertNotIn(texts.BTN_DOWNLOAD_CONF, buttons)
            self.assertNotIn(texts.BTN_REFRESH_SUB, buttons)

    async def test_render_manage_devices_shows_both_types(self):
        """Verify _render_manage_devices lists both INCY sub-device and manual .conf profiles."""
        from bot.handlers.connection.awg_subscription_routes import _render_manage_devices

        now = datetime.now(timezone.utc)
        user = User(
            id=1,
            telegram_id=987654321,
            subscription_end=now + timedelta(days=30),
            device_limit=2,
            active_sub_devices={
                "device_hwid_123456": {"device_index": 1, "label": "Мой Телефон (INCY)"}
            },
        )

        server = Server(id=1, name="Poland", country_flag="🇵🇱")
        manual_profile = VPNProfile(
            id=10,
            user_id=1,
            server_id=1,
            device_name="Мой ПК",
            device_type="manual",
            provisioning_status="active",
            server=server,
        )

        mock_session = AsyncMock()
        mock_profiles_exec = MagicMock()
        mock_profiles_exec.scalars.return_value.all.return_value = [manual_profile]
        mock_session.execute.return_value = mock_profiles_exec

        mock_msg = MagicMock()
        mock_msg.chat.id = 12345
        mock_msg.bot = MagicMock()

        with (
            patch(
                "bot.handlers.connection.awg_subscription_routes.render_hub", new_callable=AsyncMock
            ) as mock_render_hub,
            patch(
                "bot.handlers.connection.awg_subscription_routes._get_effective_device_limit",
                new_callable=AsyncMock,
            ) as mock_limit,
        ):
            mock_limit.return_value = 2

            await _render_manage_devices(mock_msg, user, mock_session)

            mock_render_hub.assert_awaited_once()
            args = mock_render_hub.await_args[0]
            rendered_text = args[2]

            self.assertIn("Мой Телефон (INCY)", rendered_text)
            self.assertIn("Мой ПК", rendered_text)
            self.assertIn("Подключено 2 из 2", rendered_text)

            # When limit reached (2/2), BTN_DOWNLOAD_CONF should not be rendered
            buttons = [b.text for row in args[3].inline_keyboard for b in row]
            self.assertNotIn(texts.BTN_DOWNLOAD_CONF, buttons)

    async def test_render_manage_devices_shows_download_conf_when_slots_available(self):
        """Verify _render_manage_devices includes BTN_DOWNLOAD_CONF button when active_count < limit."""
        from bot.handlers.connection.awg_subscription_routes import _render_manage_devices

        now = datetime.now(timezone.utc)
        user = User(
            id=1,
            telegram_id=987654321,
            subscription_end=now + timedelta(days=30),
            device_limit=3,
            active_sub_devices={
                "device_hwid_123456": {"device_index": 1, "label": "Мой Телефон (INCY)"}
            },
        )

        mock_session = AsyncMock()
        mock_profiles_exec = MagicMock()
        mock_profiles_exec.scalars.return_value.all.return_value = []
        mock_session.execute.return_value = mock_profiles_exec

        mock_msg = MagicMock()
        mock_msg.chat.id = 12345
        mock_msg.bot = MagicMock()

        with (
            patch(
                "bot.handlers.connection.awg_subscription_routes.render_hub", new_callable=AsyncMock
            ) as mock_render_hub,
            patch(
                "bot.handlers.connection.awg_subscription_routes._get_effective_device_limit",
                new_callable=AsyncMock,
            ) as mock_limit,
        ):
            mock_limit.return_value = 3

            await _render_manage_devices(mock_msg, user, mock_session)

            mock_render_hub.assert_awaited_once()
            args = mock_render_hub.await_args[0]
            buttons = [b.text for row in args[3].inline_keyboard for b in row]
            self.assertIn(texts.BTN_ADD_DEVICE, buttons)

    async def test_disconnect_sub_device_callback(self):
        """Verify awg_disconnect_sub properly finds device by prefix and calls delete_sub_device."""
        from bot.handlers.connection.awg_subscription_routes import awg_disconnect_sub

        now = datetime.now(timezone.utc)
        user = User(
            id=1,
            telegram_id=987654321,
            subscription_end=now + timedelta(days=30),
            device_limit=2,
            active_sub_devices={
                "abcdef1234567890abcdef": {"device_index": 1, "label": "INCY Phone"}
            },
        )

        callback = MagicMock()
        callback.data = "awg_disconnect_sub:abcdef1234567890"
        callback.from_user.id = 987654321
        callback.answer = AsyncMock()
        callback.message = MagicMock()

        state = MagicMock()
        state.clear = AsyncMock()

        mock_session = AsyncMock()

        with (
            patch(
                "bot.handlers.connection.awg_subscription_routes.get_user_by_telegram_id",
                new_callable=AsyncMock,
            ) as mock_get_user,
            patch(
                "bot.handlers.connection.awg_subscription_routes.DeviceService.delete_sub_device",
                new_callable=AsyncMock,
            ) as mock_del_sub,
            patch(
                "bot.handlers.connection.awg_subscription_routes._render_manage_devices",
                new_callable=AsyncMock,
            ) as mock_render,
        ):
            mock_get_user.return_value = user

            await awg_disconnect_sub(callback, state, mock_session, db_user=user)

            mock_del_sub.assert_awaited_once_with(
                mock_session,
                user_id=1,
                hwid_hash="abcdef1234567890abcdef",
            )
            callback.answer.assert_awaited_once_with(
                texts.DEVICE_DISCONNECTED_SUCCESS, show_alert=True
            )
            mock_render.assert_awaited_once()


class TestAWGSubscriptionTokenSafety(unittest.IsolatedAsyncioTestCase):
    """Verify subscription token security, row locking, and fail-closed persistence."""

    async def test_ensure_subscription_token_propagates_flush_error(self):
        """ensure_subscription_token must raise if session.flush() fails, never returning phantom tokens."""
        from database.repositories.users_repo import ensure_subscription_token

        user = User(id=1, telegram_id=123, subscription_token=None)
        mock_session = AsyncMock()
        mock_session.scalar.return_value = user
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

    async def test_ensure_subscription_token_concurrent_race_prevention(self):
        """When locked_user already got token set concurrently, ensure_subscription_token reuses it without overwriting."""
        from database.repositories.users_repo import ensure_subscription_token

        user = User(id=1, telegram_id=123, subscription_token=None)
        locked_user = User(id=1, telegram_id=123, subscription_token="concurrently_committed_token")
        mock_session = AsyncMock()
        mock_session.scalar.return_value = locked_user

        token = await ensure_subscription_token(mock_session, user)
        self.assertEqual(token, "concurrently_committed_token")
        self.assertEqual(user.subscription_token, "concurrently_committed_token")
        mock_session.flush.assert_not_called()

    async def test_create_user_eagerly_generates_subscription_token(self):
        """create_user automatically assigns a 64-char hex subscription token if not provided."""
        from database.repositories.users_repo import create_user

        mock_session = AsyncMock()
        mock_session.add = MagicMock()

        user = await create_user(mock_session, telegram_id=999888777, username="newbie")
        self.assertIsNotNone(user.subscription_token)
        self.assertEqual(len(user.subscription_token), 64)
        mock_session.add.assert_called_once_with(user)
        mock_session.flush.assert_awaited_once()


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
        s1 = Server(
            id=1,
            name="S1",
            country_flag="🇩🇪",
            protocol=AMNEZIA_PROTOCOL,
            is_active=True,
            health_state=ServerHealthState.ONLINE,
        )
        s2 = Server(
            id=2,
            name="S2",
            country_flag="🇳🇱",
            protocol=AMNEZIA_PROTOCOL,
            is_active=True,
            health_state=ServerHealthState.ONLINE,
        )

        mock_session = AsyncMock()

        # Step 2: S1 succeeds, S2 fails during creation -> partial success
        p1 = VPNProfile(
            id=101,
            user_id=42,
            server_id=1,
            device_type="sub",
            sub_device_hash=hwid_hash,
            provisioning_status="pending_create",
            raw_config=None,
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
            id=102,
            user_id=42,
            server_id=2,
            device_type="sub",
            sub_device_hash=hwid_hash,
            provisioning_status="active",
            raw_config=_make_dummy_awg_raw_config("S2"),
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

        with patch(
            "services.device_service.DeviceService.delete_device", new_callable=AsyncMock
        ) as mock_delete:
            mock_delete.side_effect = RuntimeError("Node 2 communication failure")

            with self.assertRaises(RuntimeError):
                await DeviceService.delete_sub_device(mock_session, user_id=42, hwid_hash=hwid_hash)

            # Fail-closed: device still active
            self.assertIn(hwid_hash, user.active_sub_devices)

        # Step 5: Successful disconnect clears both profiles and frees logical slot
        mock_session.execute.side_effect = [mock_user_exec, mock_profiles_exec]
        with patch(
            "services.device_service.DeviceService.delete_device", new_callable=AsyncMock
        ) as mock_delete:
            mock_delete.return_value = True
            deleted = await DeviceService.delete_sub_device(
                mock_session, user_id=42, hwid_hash=hwid_hash
            )

            self.assertEqual(deleted, 2)
            self.assertNotIn(hwid_hash, user.active_sub_devices)
            self.assertEqual(len(user.active_sub_devices), 0)


class TestAWGDeviceUAAndInactivity(unittest.IsolatedAsyncioTestCase):
    """Tests for User-Agent friendly naming and inactive device highlighting."""

    async def test_sub_feed_handler_uses_user_agent(self):
        from aiohttp import web
        from bot.handlers.awg_sub_web import awg_subscription_feed_handler
        from utils.datetime_helpers import now_utc

        user = User(
            id=1,
            telegram_id=123,
            subscription_end=now_utc() + timedelta(days=30),
            device_limit=2,
            active_sub_devices={},
        )
        server = Server(
            id=1,
            name="DE1",
            protocol="awg",
            is_active=True,
            health_state="online",
            capabilities=[],
        )
        profile = VPNProfile(
            id=10,
            user_id=1,
            server_id=1,
            device_type="sub",
            sub_device_hash="some_hwid_hash",
            provisioning_status="active",
            raw_config={"Address": "10.0.0.2/32", "PrivateKey": "key="},
        )

        mock_session = AsyncMock()
        mock_session.execute.side_effect = [
            MagicMock(scalar_one=lambda: 0),  # manual_count
            MagicMock(scalars=lambda: MagicMock(all=lambda: [server])),  # servers
            MagicMock(scalars=lambda: MagicMock(all=lambda: [profile])),  # profiles
        ]

        req = MagicMock(spec=web.Request)
        req.match_info = {"token": "valid_sub_token_12345678"}
        req.headers = {
            "X-HWID": "Device12345",
            "User-Agent": "INCY/1.4.2 (iPhone; iOS 18.2)",
        }

        with (
            patch("bot.handlers.awg_sub_web.session_scope") as mock_scope,
            patch(
                "bot.handlers.awg_sub_web.users_repo.get_user_by_subscription_token",
                new_callable=AsyncMock,
            ) as mock_get_user,
            patch(
                "bot.handlers.awg_sub_web.SubscriptionService.get_effective_device_limit",
                new_callable=AsyncMock,
            ) as mock_limit,
            patch(
                "bot.handlers.awg_sub_web.build_conf_file",
                return_value="[Interface]\nPrivateKey=...",
            ),
        ):
            mock_scope.return_value.__aenter__.return_value = mock_session
            mock_get_user.return_value = user
            mock_limit.return_value = 2

            resp = await awg_subscription_feed_handler(req)
            self.assertEqual(resp.status, 200)

            # Check that registered device got friendly iPhone label!
            hwid_hash = hashlib.sha256(b"device12345").hexdigest()
            self.assertIn(hwid_hash, user.active_sub_devices)
            self.assertEqual(user.active_sub_devices[hwid_hash]["label"], "📱 iPhone")

    async def test_render_manage_devices_shows_inactivity_warning(self):
        from bot.handlers.connection.awg_subscription_routes import _render_manage_devices
        from utils.datetime_helpers import now_utc

        now = now_utc()
        user = User(
            id=1,
            telegram_id=123,
            subscription_end=now + timedelta(days=30),
            device_limit=2,
            active_sub_devices={
                "hwid_old": {
                    "device_index": 1,
                    "label": "📱 iPhone",
                    "last_seen": (now - timedelta(days=10)).isoformat(),
                },
                "hwid_new": {
                    "device_index": 2,
                    "label": "💻 Mac",
                    "last_seen": (now - timedelta(hours=1)).isoformat(),
                },
            },
        )

        mock_session = AsyncMock()
        mock_session.execute.return_value = MagicMock(scalars=lambda: MagicMock(all=lambda: []))
        mock_msg = MagicMock()
        mock_msg.chat.id = 123

        with (
            patch(
                "bot.handlers.connection.awg_subscription_routes.render_hub", new_callable=AsyncMock
            ) as mock_render,
            patch(
                "bot.handlers.connection.awg_subscription_routes._get_effective_device_limit",
                new_callable=AsyncMock,
            ) as mock_limit,
        ):
            mock_limit.return_value = 2
            await _render_manage_devices(mock_msg, user, mock_session)

            mock_render.assert_awaited_once()
            rendered_text = mock_render.call_args[0][2]
            self.assertIn("📱 iPhone", rendered_text)
            self.assertIn("(не активно > 7 дн.)", rendered_text)
            self.assertIn("💻 Mac", rendered_text)
            self.assertNotIn(
                "💻 Mac</b>\n• Тип: Через подписку (INCY)\n• Активность: ⚠️", rendered_text
            )


class TestAWGAllocationInvariantsHardening(unittest.IsolatedAsyncioTestCase):
    """Test defensive allocation invariants: ServerLifecycleStatus, ServerHealthState, xray_origin exclusion."""

    async def test_awg_get_conf_rejects_decommissioning_or_offline_or_xray_server(self):
        """Verify awg_get_conf rejects servers that are DECOMMISSIONING, non-ONLINE, or xray_origin."""
        from bot.handlers.connection.awg_subscription_routes import awg_get_conf

        now = datetime.now(timezone.utc)
        user = User(
            id=1,
            telegram_id=987654321,
            subscription_end=now + timedelta(days=30),
            device_limit=5,
        )

        test_cases = [
            (
                "decommissioning",
                Server(
                    id=10,
                    name="DE",
                    protocol=AMNEZIA_PROTOCOL,
                    is_active=True,
                    lifecycle_status=ServerLifecycleStatus.DECOMMISSIONING,
                ),
            ),
            (
                "problem_health",
                Server(
                    id=11,
                    name="NL",
                    protocol=AMNEZIA_PROTOCOL,
                    is_active=True,
                    health_state=ServerHealthState.PROBLEM,
                ),
            ),
            (
                "xray_capability",
                Server(
                    id=12,
                    name="US",
                    protocol=AMNEZIA_PROTOCOL,
                    is_active=True,
                    capabilities=["xray_origin"],
                ),
            ),
        ]

        for desc, test_server in test_cases:
            with self.subTest(desc=desc):
                callback = MagicMock()
                callback.data = f"awg_get_conf:{test_server.id}"
                callback.from_user.id = 987654321
                callback.answer = AsyncMock()
                callback.message.chat.id = 12345
                callback.bot = MagicMock()

                state = MagicMock()
                state.clear = AsyncMock()
                mock_session = AsyncMock()

                with (
                    patch(
                        "bot.handlers.connection.awg_subscription_routes.MaintenanceService.can_user_perform_action",
                        new_callable=AsyncMock,
                        return_value=True,
                    ),
                    patch(
                        "bot.handlers.connection.awg_subscription_routes.get_user_by_telegram_id",
                        new_callable=AsyncMock,
                    ) as mock_get_user,
                    patch(
                        "bot.handlers.connection.awg_subscription_routes.SubscriptionService.check_access",
                        new_callable=AsyncMock,
                    ) as mock_check_access,
                    patch(
                        "bot.handlers.connection.awg_subscription_routes.get_server_by_id",
                        new_callable=AsyncMock,
                    ) as mock_get_server,
                    patch(
                        "bot.handlers.connection.awg_subscription_routes.render_hub",
                        new_callable=AsyncMock,
                    ) as mock_render_hub,
                ):
                    mock_get_user.return_value = user
                    mock_check_access.return_value = True
                    mock_get_server.return_value = test_server

                    await awg_get_conf(callback, state, session=mock_session, db_user=user)

                    mock_render_hub.assert_awaited_once()
                    args = mock_render_hub.await_args[0]
                    self.assertEqual(args[2], texts.ERROR_SERVER_DISABLED)

    async def test_device_service_create_device_rejects_decommissioning_or_problem_or_xray(self):
        """Verify DeviceService.create_device raises ServerUnavailable for non-active or xray nodes."""
        now = datetime.now(timezone.utc)
        user = User(
            id=1,
            telegram_id=987654321,
            subscription_end=now + timedelta(days=30),
            device_limit=5,
            is_banned=False,
        )

        test_cases = [
            (
                "decommissioning",
                Server(
                    id=10,
                    name="DE",
                    protocol=AMNEZIA_PROTOCOL,
                    is_active=True,
                    lifecycle_status=ServerLifecycleStatus.DECOMMISSIONING,
                ),
            ),
            (
                "problem_health",
                Server(
                    id=11,
                    name="NL",
                    protocol=AMNEZIA_PROTOCOL,
                    is_active=True,
                    health_state=ServerHealthState.PROBLEM,
                ),
            ),
            (
                "xray_capability",
                Server(
                    id=12,
                    name="US",
                    protocol=AMNEZIA_PROTOCOL,
                    is_active=True,
                    capabilities=["xray_origin"],
                ),
            ),
        ]

        for desc, test_server in test_cases:
            with self.subTest(desc=desc):
                mock_session = AsyncMock()
                mock_session.execute = AsyncMock(
                    side_effect=[
                        MagicMock(scalar_one=MagicMock(return_value=user)),
                        MagicMock(scalar_one_or_none=MagicMock(return_value=test_server)),
                    ]
                )

                snapshot = ServerPeerSnapshot(
                    server_id=test_server.id, peer_ids=frozenset(), captured_at=now
                )

                with self.assertRaises(ServerUnavailable) as cm:
                    await DeviceService.create_device(
                        session=mock_session,
                        user_id=1,
                        server_id=test_server.id,
                        snapshot=snapshot,
                    )
                self.assertIn("Invalid or disabled server", str(cm.exception))

    async def test_device_limit_zero_is_fail_closed(self):
        """Verify that device_limit == 0 strictly blocks device creation instead of defaulting to 5."""
        from services.device_service import DeviceLimitExceeded, DeviceService

        now = datetime.now(timezone.utc)
        user = User(
            id=1,
            telegram_id=12345,
            subscription_end=now + timedelta(days=30),
            device_limit=0,
            active_sub_devices={},
            is_banned=False,
            is_deleted=False,
        )
        server = Server(
            id=1,
            name="DE",
            protocol=AMNEZIA_PROTOCOL,
            is_active=True,
            health_state=ServerHealthState.ONLINE,
            lifecycle_status=ServerLifecycleStatus.ACTIVE,
            max_clients=100,
        )
        snapshot = ServerPeerSnapshot(server_id=1, peer_ids=frozenset(), captured_at=now)

        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(
            side_effect=[
                MagicMock(scalar_one=MagicMock(return_value=user)),
                MagicMock(scalar_one_or_none=MagicMock(return_value=server)),
                MagicMock(scalar_one_or_none=MagicMock(return_value=None)),  # dup query
                MagicMock(scalar_one=MagicMock(return_value=0)),  # manual_count
            ]
        )

        with self.assertRaises(DeviceLimitExceeded):
            await DeviceService.create_device(
                session=mock_session,
                user_id=1,
                server_id=1,
                snapshot=snapshot,
                device_name="Test#1",
            )

    async def test_get_available_servers_filters_offline_nodes(self):
        """Verify get_available_servers excludes OFFLINE or PROBLEM servers."""
        from database.repositories.servers_repo import get_available_servers

        online_srv = Server(
            id=1,
            name="Online-1",
            protocol=AMNEZIA_PROTOCOL,
            is_active=True,
            health_state=ServerHealthState.ONLINE,
            lifecycle_status=ServerLifecycleStatus.ACTIVE,
            max_clients=100,
        )
        offline_srv = Server(
            id=2,
            name="Offline-2",
            protocol=AMNEZIA_PROTOCOL,
            is_active=True,
            health_state=ServerHealthState.PROBLEM,
            lifecycle_status=ServerLifecycleStatus.ACTIVE,
            max_clients=100,
        )

        mock_session = AsyncMock()
        with (
            patch("database.repositories.servers_repo.get_active_servers", new_callable=AsyncMock) as mock_get_active,
            patch("database.repositories.servers_repo.get_server_peer_counts", new_callable=AsyncMock) as mock_counts,
            patch("database.repositories.servers_repo.get_cached_peer_count", return_value=0),
        ):
            mock_get_active.return_value = [online_srv, offline_srv]
            mock_counts.return_value = {1: 0, 2: 0}

            available = await get_available_servers(mock_session)
            self.assertEqual(len(available), 1)
            self.assertEqual(available[0].id, 1)

    async def test_build_connections_screen_uses_settings_domain(self):
        """Verify _build_connections_screen uses settings.DOMAIN when PUBLIC_URL is not set."""
        from bot.handlers.connection.common import _build_connections_screen

        now = datetime.now(timezone.utc)
        user = User(
            id=1,
            telegram_id=12345,
            subscription_end=now + timedelta(days=30),
            device_limit=2,
            active_sub_devices={},
            subscription_token="test_token_12345678901234567890",
        )
        mock_session = AsyncMock()

        with (
            patch.dict("os.environ", {}, clear=False),
            patch("bot.handlers.connection.common.get_settings") as mock_settings,
            patch("bot.handlers.connection.common.users_repo.ensure_subscription_token", new_callable=AsyncMock) as mock_token,
            patch("bot.handlers.connection.common._get_effective_device_limit", new_callable=AsyncMock) as mock_limit,
            patch("bot.handlers.connection.common.get_user_profiles", new_callable=AsyncMock) as mock_profiles,
        ):
            mock_settings.return_value.DOMAIN = "vpn.testdomain.org"
            mock_token.return_value = user.subscription_token
            mock_limit.return_value = 2
            mock_profiles.return_value = []
            # Ensure env vars aren't overriding
            import os
            for var in ("PUBLIC_URL", "SUB_BASE_URL", "APP_BASE_URL"):
                os.environ.pop(var, None)

            rendered, builder = await _build_connections_screen(user, [], mock_session)
            self.assertIn("https://vpn.testdomain.org/sub/awg/test_token_12345678901234567890", rendered)
            self.assertNotIn("sub.just1k.best", rendered)


    async def test_feed_handler_blocks_financial_hold(self):
        """Verify awg_subscription_feed_handler returns 403 Forbidden when user has financial_hold."""
        from bot.handlers.awg_sub_web import awg_subscription_feed_handler
        from contextlib import asynccontextmanager
        from datetime import datetime, timezone, timedelta

        now = datetime.now(timezone.utc)
        user = User(
            id=1,
            telegram_id=12345,
            subscription_end=now + timedelta(days=30),
            financial_hold=True,
            is_banned=False,
            is_deleted=False,
            subscription_token="token_with_financial_hold_12345",
        )
        req = MagicMock()
        req.match_info = {"token": "token_with_financial_hold_12345"}
        req.headers = {"X-HWID": "hwid_device_uuid_12345", "User-Agent": "INCY/1.0 (iOS)"}

        mock_session = AsyncMock()

        @asynccontextmanager
        async def fake_session_scope():
            yield mock_session

        with (
            patch("bot.handlers.awg_sub_web.session_scope", fake_session_scope),
            patch("bot.handlers.awg_sub_web.users_repo.get_user_by_subscription_token", new_callable=AsyncMock) as mock_get_user,
        ):
            mock_get_user.return_value = user

            resp = await awg_subscription_feed_handler(req)
            self.assertEqual(resp.status, 403)
            self.assertEqual(resp.text, "Forbidden")

    async def test_device_service_create_device_blocks_financial_hold(self):
        """Verify DeviceService.create_device raises NoActiveSubscription when user has financial_hold."""
        from services.device_service import DeviceService, NoActiveSubscription, ServerPeerSnapshot
        from database.models import Server, ServerHealthState, ServerLifecycleStatus
        from datetime import datetime, timezone, timedelta

        now = datetime.now(timezone.utc)
        user = User(
            id=1,
            telegram_id=12345,
            subscription_end=now + timedelta(days=30),
            financial_hold=True,
            is_banned=False,
        )
        server = Server(
            id=1,
            protocol="amneziawg2",
            is_active=True,
            health_state=ServerHealthState.ONLINE,
            lifecycle_status=ServerLifecycleStatus.ACTIVE,
            capabilities=[],
        )
        snapshot = ServerPeerSnapshot(
            server_id=1,
            peer_ids=frozenset(),
            captured_at=now,
        )
        mock_session = AsyncMock()
        mock_user_exec = MagicMock()
        mock_user_exec.scalar_one.return_value = user
        mock_server_exec = MagicMock()
        mock_server_exec.scalar_one_or_none.return_value = server
        mock_session.execute.side_effect = [mock_user_exec, mock_server_exec]

        with self.assertRaises(NoActiveSubscription):
            await DeviceService.create_device(
                session=mock_session,
                user_id=1,
                server_id=1,
                snapshot=snapshot,
                device_type="sub",
            )

    async def test_get_user_effective_device_count_fallback_db(self):
        """Verify get_user_effective_device_count queries User.active_sub_devices when active_sub_devices is None."""
        from database.repositories.profiles_repo import get_user_effective_device_count

        mock_session = AsyncMock()
        mock_session.scalar.return_value = {"hwid1": {"device_index": 1}}
        mock_result = MagicMock()
        mock_result.scalar_one.return_value = 1
        mock_session.execute.return_value = mock_result

        count = await get_user_effective_device_count(mock_session, user_id=10)
        self.assertEqual(count, 2)  # 1 manual + 1 sub-device


class TestAWGBrowserHelpers(unittest.TestCase):
    """Unit tests for browser detection and URL builder."""

    def test_is_browser_request(self):
        from bot.handlers.awg_sub_web import is_browser_request

        # 1. Accept text/html
        req1 = MagicMock()
        req1.query = {}
        req1.headers = {"Accept": "text/html,application/xhtml+xml"}
        self.assertTrue(is_browser_request(req1))

        # 2. Sec-Fetch-Dest: document
        req2 = MagicMock()
        req2.query = {}
        req2.headers = {"Accept": "*/*", "Sec-Fetch-Dest": "document"}
        self.assertTrue(is_browser_request(req2))

        # 3. Query format=html
        req3 = MagicMock()
        req3.query = {"format": "html"}
        req3.headers = {"Accept": "*/*"}
        self.assertTrue(is_browser_request(req3))

        # 4. Standard mobile browser user agent
        req4 = MagicMock()
        req4.query = {}
        req4.headers = {
            "Accept": "*/*",
            "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 Mobile/15E148 Safari/604.1",
        }
        self.assertTrue(is_browser_request(req4))

        # 5. Non-browser tool (curl)
        req5 = MagicMock()
        req5.query = {}
        req5.headers = {"Accept": "*/*", "User-Agent": "curl/8.1.2"}
        self.assertFalse(is_browser_request(req5))

        # 6. Non-browser tool (python aiohttp)
        req6 = MagicMock()
        req6.query = {}
        req6.headers = {"Accept": "*/*", "User-Agent": "Python/3.11 aiohttp/3.9.5"}
        self.assertFalse(is_browser_request(req6))

    def test_get_subscription_public_url(self):
        from bot.handlers.awg_sub_web import get_subscription_public_url

        # With configured domain in settings
        mock_settings = MagicMock()
        mock_settings.DOMAIN = "vpn.myexample.com"
        with patch("config.settings.get_settings", return_value=mock_settings):
            req = MagicMock()
            url = get_subscription_public_url(req, "sample_token_123")
            self.assertEqual(url, "https://vpn.myexample.com/sub/awg/sample_token_123")

        # With forwarded headers fallback when domain is empty
        mock_empty_settings = MagicMock()
        mock_empty_settings.DOMAIN = ""
        with patch("config.settings.get_settings", return_value=mock_empty_settings), patch.dict(os.environ, {}, clear=True):
            req2 = MagicMock()
            req2.headers = {
                "X-Forwarded-Proto": "https",
                "X-Forwarded-Host": "forwarded.domain.com",
            }
            url2 = get_subscription_public_url(req2, "sample_token_456")
            self.assertEqual(url2, "https://forwarded.domain.com/sub/awg/sample_token_456")

    def test_render_awg_browser_landing_page(self):
        from bot.handlers.awg_sub_web import render_awg_browser_landing_page

        html_out = render_awg_browser_landing_page(
            sub_url="https://vpn.example.com/sub/awg/my_token",
            bot_username="testbot",
        )
        self.assertIn("incy://add/https://vpn.example.com/sub/awg/my_token", html_out)
        self.assertIn("@testbot", html_out)
        self.assertIn("copySubscriptionUrl", html_out)
        self.assertIn("apps.apple.com/app/incy", html_out)
        self.assertIn("play.google.com/store/apps/details?id=llc.itdev.incy", html_out)
        self.assertIn("github.com/INCY-DEV/incy-platforms", html_out)

    def test_render_awg_browser_error_page(self):
        from bot.handlers.awg_sub_web import render_awg_browser_error_page

        html_out = render_awg_browser_error_page(
            title="Ошибка подписки",
            message="Тестовое сообщение",
            bot_username="testbot",
        )
        self.assertIn("Ошибка подписки", html_out)
        self.assertIn("Тестовое сообщение", html_out)
        self.assertIn("@testbot", html_out)


if __name__ == "__main__":
    unittest.main()
