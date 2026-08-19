import os
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from bot import texts
from bot.handlers.connection.device_create_routes import (
    _await_profile_ready,
    _creating_devices,
    _process_server_selection,
)
from bot.handlers.connection.device_delete_routes import cancel_delete_device
from bot.handlers.connection.device_view_routes import (
    manage_device,
    render_device_screen,
)
from database.models import APIOperation, Server, User
from utils.vpn_parser import encode_json_to_vpn_uri


def _make_valid_vpn_uri() -> str:
    return encode_json_to_vpn_uri({
        "containers": [{
            "awg": {
                "protocol_version": 2,
                "last_config": "{\"config\": \"[Interface]\\nPrivateKey = test\\n[Peer]\\nPublicKey = test\\n\"}"
            }
        }]
    })


class TestDeviceCreationLifecycle(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        super().setUp()
        self.bridge_patch = patch("services.amnezia_bridge_token_service.AmneziaBridgeTokenService.is_enabled", return_value=False)
        self.bridge_patch.start()
        self.admin_patch = patch("utils.admin.is_admin", return_value=False)
        self.admin_patch.start()
        self.maint_patch = patch("services.maintenance_service.MaintenanceService.can_user_perform_action", return_value=True)
        self.maint_patch.start()
        self.sub_patch = patch("services.subscription.SubscriptionService.check_access", new=AsyncMock(return_value=True))
        self.sub_patch.start()
        self.server_mock = SimpleNamespace(id=10, country_flag="🇩🇪", name="Germany", protocol="amneziawg2", is_active=True)
        self.server_patch = patch("bot.handlers.connection.device_create_routes.get_server_by_id", new=AsyncMock(return_value=self.server_mock))
        self.server_patch.start()

        self.render_hub_patch = patch("bot.handlers.connection.device_create_routes.render_hub", new=AsyncMock())
        self.render_hub_patch.start()

        self.limit_patch = patch("bot.handlers.connection.device_create_routes._get_effective_device_limit", new=AsyncMock(return_value=5))
        self.limit_patch.start()

    def tearDown(self):
        self.limit_patch.stop()
        self.render_hub_patch.stop()
        self.server_patch.stop()
        self.sub_patch.stop()
        self.maint_patch.stop()
        self.admin_patch.stop()
        self.bridge_patch.stop()
        super().tearDown()

    async def test_1_create_fast_success_renders_ready_device(self):
        """Worker completes before UI timeout -> opens ready device card with config actions."""
        valid_uri = _make_valid_vpn_uri()
        db_user = SimpleNamespace(id=1, telegram_id=100)
        server = SimpleNamespace(id=10, country_flag="🇩🇪", name="Germany", protocol="amneziawg2", is_active=True)
        
        created_profile = SimpleNamespace(
            id=42,
            user_id=1,
            server_id=10,
            device_name="Устройство #1",
            provisioning_status="active",
            peer_id="peer-42",
            raw_config=valid_uri,
            is_active=True,
            traffic_down=0,
            traffic_up=0,
            last_connected=None,
        )

        callback = MagicMock()
        callback.bot = MagicMock()
        callback.message.chat.id = 100
        callback.message.message_id = 10
        callback.from_user.id = 100
        callback.answer = AsyncMock()

        state = AsyncMock()
        session = AsyncMock()

        with (
            patch("bot.handlers.connection.device_create_routes.capture_server_peer_snapshot", new=AsyncMock()),
            patch("bot.handlers.connection.device_create_routes.DeviceService.create_device", new=AsyncMock(return_value=created_profile)),
            patch("bot.handlers.connection.device_create_routes._await_profile_ready", new=AsyncMock(return_value=created_profile)),
            patch("bot.handlers.connection.device_create_routes.get_user_by_telegram_id", new=AsyncMock(return_value=db_user)),
            patch("bot.handlers.connection.device_create_routes.get_user_profiles", new=AsyncMock(return_value=[])),
            patch("bot.handlers.connection.device_create_routes.SubscriptionService.check_access", new=AsyncMock(return_value=True)),
            patch("bot.handlers.connection.device_create_routes.render_hub", new=AsyncMock()),
            patch("bot.handlers.connection.device_view_routes.get_server_by_id", new=AsyncMock(return_value=server)),
            patch("bot.handlers.connection.device_view_routes.render_hub") as mock_render_hub,
        ):
            await _process_server_selection(callback, state, session, server_id=10, user=db_user)

            # Assert device card was rendered
            self.assertTrue(mock_render_hub.called)
            rendered_keyboard = mock_render_hub.call_args[0][3]
            buttons = [b.callback_data for row in rendered_keyboard.inline_keyboard for b in row if b.callback_data]
            self.assertIn("show_config:42", buttons)
            self.assertIn("download_conf:42", buttons)
            self.assertIn("request_delete_device:42", buttons)

    async def test_2_create_timeout_renders_connections_list(self):
        """Worker exceeds 4s UI window -> renders connections list with pending_create status."""
        db_user = SimpleNamespace(id=1, telegram_id=100)
        pending_profile = SimpleNamespace(
            id=42,
            user_id=1,
            server_id=10,
            device_name="Устройство #1",
            provisioning_status="pending_create",
            peer_id=None,
            raw_config=None,
            is_active=True,
        )

        callback = MagicMock()
        callback.bot = MagicMock()
        callback.message.chat.id = 100
        callback.message.message_id = 10
        callback.from_user.id = 100
        callback.answer = AsyncMock()

        state = AsyncMock()
        session = AsyncMock()

        with (
            patch("bot.handlers.connection.device_create_routes.capture_server_peer_snapshot", new=AsyncMock()),
            patch("bot.handlers.connection.device_create_routes.DeviceService.create_device", new=AsyncMock(return_value=pending_profile)),
            patch("bot.handlers.connection.device_create_routes._await_profile_ready", new=AsyncMock(return_value=None)), # Timeout
            patch("bot.handlers.connection.device_create_routes.get_user_by_telegram_id", new=AsyncMock(return_value=db_user)),
            patch("bot.handlers.connection.device_create_routes.get_user_profiles", new=AsyncMock(return_value=[])),
            patch("bot.handlers.connection.device_create_routes.render_hub", new=AsyncMock()),
            patch("bot.handlers.connection.device_create_routes._render_connections", new=AsyncMock()) as mock_render_connections,
        ):
            await _process_server_selection(callback, state, session, server_id=10, user=db_user)

            # Assert connections list was rendered instead of broken device card
            self.assertTrue(mock_render_connections.called)

    async def test_3_worker_finishes_after_timeout_restores_actions(self):
        """When worker finishes after timeout, subsequent entry to device card shows full actions."""
        valid_uri = _make_valid_vpn_uri()
        db_user = SimpleNamespace(id=1, telegram_id=100)
        server = SimpleNamespace(id=10, country_flag="🇩🇪", name="Germany", protocol="amneziawg2", is_active=True)
        
        ready_profile = SimpleNamespace(
            id=42,
            user_id=1,
            server_id=10,
            device_name="Устройство #1",
            provisioning_status="active",
            peer_id="peer-42",
            raw_config=valid_uri,
            is_active=True,
            traffic_down=0,
            traffic_up=0,
            last_connected=None,
        )

        callback = MagicMock()
        callback.data = "manage_device:42"
        callback.message.chat.id = 100
        callback.message.message_id = 10
        callback.answer = AsyncMock()

        state = AsyncMock()
        session = AsyncMock()

        captured = {}
        async def mock_render_hub(_bot, _chat_id, _text, keyboard, **_kwargs):
            captured["keyboard"] = keyboard

        with (
            patch("bot.handlers.connection.device_view_routes.get_profile_by_id", new=AsyncMock(return_value=ready_profile)),
            patch("bot.handlers.connection.device_view_routes.get_server_by_id", new=AsyncMock(return_value=server)),
            patch("bot.handlers.connection.device_view_routes.SubscriptionService.check_access", new=AsyncMock(return_value=True)),
            patch("bot.handlers.connection.device_view_routes.render_hub", new=AsyncMock(side_effect=mock_render_hub)),
        ):
            await manage_device(callback, state, session, db_user)

            self.assertIn("keyboard", captured)
            buttons = [b.callback_data for row in captured["keyboard"].inline_keyboard for b in row if b.callback_data]
            self.assertIn("show_config:42", buttons)
            self.assertIn("download_conf:42", buttons)
            self.assertIn("request_delete_device:42", buttons)

    async def test_4_worker_create_failed_shows_error_state(self):
        """When worker marks create_failed -> error banner rendered, config actions hidden, delete allowed."""
        db_user = SimpleNamespace(id=1, telegram_id=100)
        server = SimpleNamespace(id=10, country_flag="🇩🇪", name="Germany", protocol="amneziawg2", is_active=True)
        
        failed_profile = SimpleNamespace(
            id=42,
            user_id=1,
            server_id=10,
            device_name="Устройство #1",
            provisioning_status="create_failed",
            peer_id=None,
            raw_config=None,
            is_active=True,
            traffic_down=0,
            traffic_up=0,
            last_connected=None,
        )

        callback = MagicMock()
        callback.data = "manage_device:42"
        callback.message.chat.id = 100
        callback.message.message_id = 10
        callback.answer = AsyncMock()

        captured = {}
        async def mock_render_hub(_bot, _chat_id, text, keyboard, **_kwargs):
            captured["text"] = text
            captured["keyboard"] = keyboard

        with (
            patch("bot.handlers.connection.device_view_routes.get_profile_by_id", new=AsyncMock(return_value=failed_profile)),
            patch("bot.handlers.connection.device_view_routes.get_server_by_id", new=AsyncMock(return_value=server)),
            patch("bot.handlers.connection.device_view_routes.SubscriptionService.check_access", new=AsyncMock(return_value=True)),
            patch("bot.handlers.connection.device_view_routes.render_hub", new=AsyncMock(side_effect=mock_render_hub)),
        ):
            await manage_device(callback, AsyncMock(), AsyncMock(), db_user)

            self.assertIn("❌ <b>Не удалось создать устройство на сервере.</b>", captured["text"])
            buttons = [b.callback_data for row in captured["keyboard"].inline_keyboard for b in row if b.callback_data]
            self.assertNotIn("show_config:42", buttons)
            self.assertNotIn("download_conf:42", buttons)
            self.assertIn("request_delete_device:42", buttons)

    async def test_5_pending_update_preserves_valid_config_actions(self):
        """Background pending_update with existing valid config keeps VPN accessible."""
        valid_uri = _make_valid_vpn_uri()
        db_user = SimpleNamespace(id=1, telegram_id=100)
        server = SimpleNamespace(id=10, country_flag="🇩🇪", name="Germany", protocol="amneziawg2", is_active=True)
        
        updating_profile = SimpleNamespace(
            id=42,
            user_id=1,
            server_id=10,
            device_name="Устройство #1",
            provisioning_status="pending_update",
            peer_id="peer-42",
            raw_config=valid_uri,
            is_active=True,
            traffic_down=0,
            traffic_up=0,
            last_connected=None,
        )

        captured = {}
        async def mock_render_hub(_bot, _chat_id, text, keyboard, **_kwargs):
            captured["text"] = text
            captured["keyboard"] = keyboard

        with (
            patch("bot.handlers.connection.device_view_routes.get_profile_by_id", new=AsyncMock(return_value=updating_profile)),
            patch("bot.handlers.connection.device_view_routes.get_server_by_id", new=AsyncMock(return_value=server)),
            patch("bot.handlers.connection.device_view_routes.SubscriptionService.check_access", new=AsyncMock(return_value=True)),
            patch("bot.handlers.connection.device_view_routes.render_hub", new=AsyncMock(side_effect=mock_render_hub)),
        ):
            await render_device_screen(MagicMock(), 100, updating_profile, db_user, AsyncMock())

            self.assertIn("🔄 <b>Конфигурация устройства обновляется...</b>", captured["text"])
            buttons = [b.callback_data for row in captured["keyboard"].inline_keyboard for b in row if b.callback_data]
            self.assertIn("show_config:42", buttons)
            self.assertIn("download_conf:42", buttons)
            self.assertIn("request_delete_device:42", buttons)

    async def test_6_update_failed_preserves_valid_config_actions(self):
        """Background update_failed with existing valid config keeps VPN accessible."""
        valid_uri = _make_valid_vpn_uri()
        db_user = SimpleNamespace(id=1, telegram_id=100)
        server = SimpleNamespace(id=10, country_flag="🇩🇪", name="Germany", protocol="amneziawg2", is_active=True)
        
        update_failed_profile = SimpleNamespace(
            id=42,
            user_id=1,
            server_id=10,
            device_name="Устройство #1",
            provisioning_status="update_failed",
            peer_id="peer-42",
            raw_config=valid_uri,
            is_active=True,
            traffic_down=0,
            traffic_up=0,
            last_connected=None,
        )

        captured = {}
        async def mock_render_hub(_bot, _chat_id, text, keyboard, **_kwargs):
            captured["text"] = text
            captured["keyboard"] = keyboard

        with (
            patch("bot.handlers.connection.device_view_routes.get_profile_by_id", new=AsyncMock(return_value=update_failed_profile)),
            patch("bot.handlers.connection.device_view_routes.get_server_by_id", new=AsyncMock(return_value=server)),
            patch("bot.handlers.connection.device_view_routes.SubscriptionService.check_access", new=AsyncMock(return_value=True)),
            patch("bot.handlers.connection.device_view_routes.render_hub", new=AsyncMock(side_effect=mock_render_hub)),
        ):
            await render_device_screen(MagicMock(), 100, update_failed_profile, db_user, AsyncMock())

            self.assertIn("⚠️ <b>Не удалось обновить конфигурацию на сервере", captured["text"])
            buttons = [b.callback_data for row in captured["keyboard"].inline_keyboard for b in row if b.callback_data]
            self.assertIn("show_config:42", buttons)
            self.assertIn("download_conf:42", buttons)
            self.assertIn("request_delete_device:42", buttons)

    async def test_7_duplicate_create_click_blocked_by_cache(self):
        """Second click while device is being created is ignored via _creating_devices lock."""
        _creating_devices[999] = True
        callback = MagicMock()
        callback.from_user.id = 999
        callback.answer = AsyncMock()

        try:
            with patch("bot.handlers.connection.device_create_routes.render_hub") as mock_render_hub:
                await _process_server_selection(callback, AsyncMock(), AsyncMock(), server_id=1, user=SimpleNamespace(id=1, telegram_id=999))
                self.assertTrue(mock_render_hub.called)
                self.assertEqual(mock_render_hub.call_args[0][2], texts.DEVICE_CREATE_IN_PROGRESS)
        finally:
            _creating_devices.pop(999, None)

    async def test_8_cancel_delete_respects_current_profile_state(self):
        """Canceling deletion when device is in pending_create builds fresh keyboard with hidden Delete button."""
        db_user = SimpleNamespace(id=1, telegram_id=100)
        server = SimpleNamespace(id=10, country_flag="🇩🇪", name="Germany", protocol="amneziawg2", is_active=True)
        
        pending_profile = SimpleNamespace(
            id=42,
            user_id=1,
            server_id=10,
            device_name="Устройство #1",
            provisioning_status="pending_create",
            peer_id=None,
            raw_config=None,
            is_active=True,
            traffic_down=0,
            traffic_up=0,
            last_connected=None,
        )

        callback = MagicMock()
        callback.data = "cancel_delete_device:42"
        callback.message.chat.id = 100
        callback.message.message_id = 10
        callback.from_user.id = 100
        callback.answer = AsyncMock()

        captured = {}
        async def mock_render_hub(_bot, _chat_id, text, keyboard, **_kwargs):
            captured["text"] = text
            captured["keyboard"] = keyboard

        with (
            patch("bot.handlers.connection.device_delete_routes.get_profile_by_id", new=AsyncMock(return_value=pending_profile)),
            patch("bot.handlers.connection.device_view_routes.get_server_by_id", new=AsyncMock(return_value=server)),
            patch("bot.handlers.connection.device_view_routes.SubscriptionService.check_access", new=AsyncMock(return_value=True)),
            patch("bot.handlers.connection.device_view_routes.render_hub", new=AsyncMock(side_effect=mock_render_hub)),
        ):
            await cancel_delete_device(callback, AsyncMock(), AsyncMock(), db_user)

            self.assertIn("keyboard", captured)
            buttons = [b.callback_data for row in captured["keyboard"].inline_keyboard for b in row if b.callback_data]
            self.assertNotIn("show_config:42", buttons)
            self.assertNotIn("download_conf:42", buttons)
            self.assertNotIn("request_delete_device:42", buttons)
            self.assertIn("rename_device:42", buttons)

    async def test_9_await_profile_ready_polling_mechanics(self):
        """_await_profile_ready uses monotonic clock and independent sessions to return active profile."""
        valid_uri = _make_valid_vpn_uri()
        active_profile = SimpleNamespace(
            id=42,
            provisioning_status="active",
            peer_id="peer-42",
            raw_config=valid_uri,
            is_active=True,
        )

        mock_session = AsyncMock()
        mock_session.get = AsyncMock(return_value=active_profile)
        mock_session.close = AsyncMock()

        with patch("bot.handlers.connection.device_create_routes.get_session", new=AsyncMock(return_value=mock_session)):
            res = await _await_profile_ready(42, timeout_seconds=1.0, poll_interval=0.05)
            self.assertIsNotNone(res)
            self.assertEqual(res.provisioning_status, "active")
            self.assertTrue(mock_session.close.called)

    async def test_10_await_profile_ready_closes_session_on_exception(self):
        """If session.get raises an exception, session.close is still called in finally block."""
        mock_session = AsyncMock()
        mock_session.get = AsyncMock(side_effect=RuntimeError("Database query failed"))
        mock_session.close = AsyncMock()

        with patch("bot.handlers.connection.device_create_routes.get_session", new=AsyncMock(return_value=mock_session)):
            with self.assertRaises(RuntimeError):
                await _await_profile_ready(42, timeout_seconds=1.0, poll_interval=0.05)
            self.assertTrue(mock_session.close.called)

    async def test_11_await_profile_ready_waits_for_both_raw_config_and_peer_id(self):
        """Does not return active if raw_config or peer_id is missing/empty."""
        valid_uri = _make_valid_vpn_uri()
        partial_profile_1 = SimpleNamespace(id=42, provisioning_status="active", peer_id=None, raw_config=valid_uri, is_active=True)
        partial_profile_2 = SimpleNamespace(id=42, provisioning_status="active", peer_id="p1", raw_config=None, is_active=True)
        valid_profile = SimpleNamespace(id=42, provisioning_status="active", peer_id="p1", raw_config=valid_uri, is_active=True)

        responses = [partial_profile_1, partial_profile_2, valid_profile]

        mock_session = AsyncMock()
        mock_session.get = AsyncMock(side_effect=responses)
        mock_session.close = AsyncMock()

        with patch("bot.handlers.connection.device_create_routes.get_session", new=AsyncMock(return_value=mock_session)):
            res = await _await_profile_ready(42, timeout_seconds=2.0, poll_interval=0.01)
            self.assertEqual(res, valid_profile)
            self.assertEqual(mock_session.close.call_count, 3)

    async def test_12_await_profile_ready_cleanup_pending_returns_profile_and_renders_connections(self):
        """Profiles with create_cleanup_pending return immediately and trigger connections list render."""
        cleanup_profile = SimpleNamespace(
            id=42,
            provisioning_status="create_cleanup_pending",
            peer_id=None,
            raw_config=None,
        )

        db_user = SimpleNamespace(id=1, telegram_id=100)
        callback = MagicMock()
        callback.bot = MagicMock()
        callback.message.chat.id = 100
        callback.from_user.id = 100
        callback.answer = AsyncMock()

        state = AsyncMock()
        session = AsyncMock()

        with (
            patch("bot.handlers.connection.device_create_routes.capture_server_peer_snapshot", new=AsyncMock()),
            patch("bot.handlers.connection.device_create_routes.DeviceService.create_device", new=AsyncMock(return_value=cleanup_profile)),
            patch("bot.handlers.connection.device_create_routes._await_profile_ready", new=AsyncMock(return_value=cleanup_profile)),
            patch("bot.handlers.connection.device_create_routes.get_user_by_telegram_id", new=AsyncMock(return_value=db_user)),
            patch("bot.handlers.connection.device_create_routes.get_user_profiles", new=AsyncMock(return_value=[])),
            patch("bot.handlers.connection.device_create_routes.render_hub", new=AsyncMock()),
            patch("bot.handlers.connection.device_create_routes._render_connections", new=AsyncMock()) as mock_render_connections,
        ):
            await _process_server_selection(callback, state, session, server_id=10, user=db_user)
            self.assertTrue(mock_render_connections.called)

    async def test_13_session_commit_failure_cleans_up_creating_lock(self):
        """If session.commit fails during creation, user lock is properly cleared and error is rendered."""
        db_user = SimpleNamespace(id=1, telegram_id=100)
        session = AsyncMock()
        # Fail on the second commit (the one after create_device)
        session.commit = AsyncMock(side_effect=[None, RuntimeError("DB commit failed")])

        callback = MagicMock()
        callback.bot = MagicMock()
        callback.message.chat.id = 100
        callback.from_user.id = 100
        callback.answer = AsyncMock()

        created_profile = SimpleNamespace(id=42, provisioning_status="pending_create")

        with (
            patch("bot.handlers.connection.device_create_routes.capture_server_peer_snapshot", new=AsyncMock()),
            patch("bot.handlers.connection.device_create_routes.DeviceService.create_device", new=AsyncMock(return_value=created_profile)),
            patch("bot.handlers.connection.device_create_routes.get_user_by_telegram_id", new=AsyncMock(return_value=db_user)),
            patch("bot.handlers.connection.device_create_routes.get_user_profiles", new=AsyncMock(return_value=[])),
            patch("bot.handlers.connection.device_create_routes.render_hub", new=AsyncMock()) as mock_render_hub,
        ):
            await _process_server_selection(callback, AsyncMock(), session, server_id=10, user=db_user)

            self.assertNotIn(100, _creating_devices)
            self.assertTrue(mock_render_hub.called)
            self.assertEqual(mock_render_hub.call_args[0][2], texts.ERROR_TECHNICAL_MESSAGE)

    async def test_14_await_profile_ready_zero_or_negative_timeout_no_infinite_loop(self):
        """Zero or negative timeout evaluates once and returns None without hanging."""
        pending_profile = SimpleNamespace(
            id=42,
            provisioning_status="pending_create",
            peer_id=None,
            raw_config=None,
        )

        mock_session = AsyncMock()
        mock_session.get = AsyncMock(return_value=pending_profile)
        mock_session.close = AsyncMock()

        with patch("bot.handlers.connection.device_create_routes.get_session", new=AsyncMock(return_value=mock_session)):
            res = await _await_profile_ready(42, timeout_seconds=0.0)
            self.assertIsNone(res)
            self.assertEqual(mock_session.get.call_count, 1)
            self.assertEqual(mock_session.close.call_count, 1)

    async def test_15_cancelled_error_in_process_selection_cleans_up_creating_lock(self):
        """If task is cancelled during _await_profile_ready, finally block releases lock."""
        db_user = SimpleNamespace(id=1, telegram_id=100)
        created_profile = SimpleNamespace(id=42, provisioning_status="pending_create")

        callback = MagicMock()
        callback.bot = MagicMock()
        callback.message.chat.id = 100
        callback.from_user.id = 100
        callback.answer = AsyncMock()

        session = AsyncMock()

        import asyncio
        with (
            patch("bot.handlers.connection.device_create_routes.capture_server_peer_snapshot", new=AsyncMock()),
            patch("bot.handlers.connection.device_create_routes.DeviceService.create_device", new=AsyncMock(return_value=created_profile)),
            patch("bot.handlers.connection.device_create_routes._await_profile_ready", new=AsyncMock(side_effect=asyncio.CancelledError())),
            patch("bot.handlers.connection.device_create_routes.get_user_by_telegram_id", new=AsyncMock(return_value=db_user)),
            patch("bot.handlers.connection.device_create_routes.get_user_profiles", new=AsyncMock(return_value=[])),
            patch("bot.handlers.connection.device_create_routes.render_hub", new=AsyncMock()),
            self.assertRaises(asyncio.CancelledError),
        ):
            await _process_server_selection(callback, AsyncMock(), session, server_id=10, user=db_user)

    async def test_16_rename_device_renders_keyboard_with_capability_flags(self):
        """Rename success screen builds keyboard with dynamic capability flags."""
        from bot.handlers.connection.device_rename_routes import rename_device_process

        db_user = SimpleNamespace(id=1, telegram_id=100)
        server = SimpleNamespace(id=10, country_flag="🇩🇪", name="Germany", protocol="amneziawg2", is_active=True)
        pending_profile = SimpleNamespace(
            id=42,
            user_id=1,
            server_id=10,
            device_name="Старое #1",
            provisioning_status="pending_create",
            peer_id=None,
            raw_config=None,
            is_active=True,
        )

        message = MagicMock()
        message.bot = MagicMock()
        message.chat.id = 100
        message.text = "Новое"
        message.delete = AsyncMock()

        state = AsyncMock()
        state.get_data = AsyncMock(return_value={"profile_id": 42})
        session = AsyncMock()

        captured = {}
        async def mock_render_hub(_bot, _chat_id, text, keyboard, **_kwargs):
            captured["text"] = text
            captured["keyboard"] = keyboard

        with (
            patch("bot.handlers.connection.device_rename_routes.get_profile_by_id", new=AsyncMock(return_value=pending_profile)),
            patch("bot.handlers.connection.device_rename_routes.get_server_by_id", new=AsyncMock(return_value=server)),
            patch("bot.handlers.connection.device_rename_routes.get_user_profiles", new=AsyncMock(return_value=[pending_profile])),
            patch("bot.handlers.connection.device_rename_routes.update_profile", new=AsyncMock()),
            patch("bot.handlers.connection.device_rename_routes.SubscriptionService.check_access", new=AsyncMock(return_value=True)),
            patch("bot.handlers.connection.device_rename_routes.render_hub", new=AsyncMock(side_effect=mock_render_hub)),
            patch("services.audit_service.AuditService.log_action", new=AsyncMock()),
        ):
            await rename_device_process(message, state, session, db_user)

            self.assertIn("keyboard", captured)
            buttons = [b.callback_data for row in captured["keyboard"].inline_keyboard for b in row if b.callback_data]
            # Since pending_create: no show_config, no download_conf, no delete
            self.assertNotIn("show_config:42", buttons)
            self.assertNotIn("download_conf:42", buttons)
            self.assertNotIn("request_delete_device:42", buttons)
    async def test_17_expired_subscription_with_pending_create_hides_delete_button(self):
        """Expired subscription with pending_create hides Delete button in render_device_screen."""
        db_user = SimpleNamespace(id=1, telegram_id=100)
        server = SimpleNamespace(id=10, country_flag="🇩🇪", name="Germany", protocol="amneziawg2", is_active=True)
        pending_profile = SimpleNamespace(
            id=42,
            user_id=1,
            server_id=10,
            device_name="Устройство #1",
            provisioning_status="pending_create",
            peer_id=None,
            raw_config=None,
            is_active=True,
            traffic_down=0,
            traffic_up=0,
            last_connected=None,
        )

        captured = {}
        async def mock_render_hub(_bot, _chat_id, text, keyboard, **_kwargs):
            captured["text"] = text
            captured["keyboard"] = keyboard

        with (
            patch("bot.handlers.connection.device_view_routes.get_server_by_id", new=AsyncMock(return_value=server)),
            patch("bot.handlers.connection.device_view_routes.SubscriptionService.check_access", new=AsyncMock(return_value=False)),
            patch("bot.handlers.connection.device_view_routes.render_hub", new=AsyncMock(side_effect=mock_render_hub)),
        ):
            await render_device_screen(MagicMock(), 100, pending_profile, db_user, AsyncMock())

            self.assertIn("keyboard", captured)
            buttons = [b.callback_data for row in captured["keyboard"].inline_keyboard for b in row if b.callback_data]
            self.assertNotIn("request_delete_device:42", buttons)
            self.assertIn("back_to_connections", buttons)
            self.assertIn("back_to_main_menu", buttons)

    async def test_18_expired_subscription_with_deleting_or_cleanup_hides_delete_button(self):
        """Expired subscription with deleting or create_cleanup_pending hides Delete button."""
        db_user = SimpleNamespace(id=1, telegram_id=100)
        server = SimpleNamespace(id=10, country_flag="🇩🇪", name="Germany", protocol="amneziawg2", is_active=True)
        deleting_profile = SimpleNamespace(
            id=42,
            user_id=1,
            server_id=10,
            device_name="Устройство #1",
            provisioning_status="deleting",
            peer_id="p1",
            raw_config=None,
            is_active=True,
            traffic_down=0,
            traffic_up=0,
            last_connected=None,
        )

        captured = {}
        async def mock_render_hub(_bot, _chat_id, text, keyboard, **_kwargs):
            captured["text"] = text
            captured["keyboard"] = keyboard

        with (
            patch("bot.handlers.connection.device_view_routes.get_server_by_id", new=AsyncMock(return_value=server)),
            patch("bot.handlers.connection.device_view_routes.SubscriptionService.check_access", new=AsyncMock(return_value=False)),
            patch("bot.handlers.connection.device_view_routes.render_hub", new=AsyncMock(side_effect=mock_render_hub)),
        ):
            await render_device_screen(MagicMock(), 100, deleting_profile, db_user, AsyncMock())

            buttons = [b.callback_data for row in captured["keyboard"].inline_keyboard for b in row if b.callback_data]
            self.assertNotIn("request_delete_device:42", buttons)

    async def test_19_expired_subscription_with_active_shows_delete_button(self):
        """Expired subscription with active profile allows deleting old device."""
        db_user = SimpleNamespace(id=1, telegram_id=100)
        server = SimpleNamespace(id=10, country_flag="🇩🇪", name="Germany", protocol="amneziawg2", is_active=True)
        active_profile = SimpleNamespace(
            id=42,
            user_id=1,
            server_id=10,
            device_name="Устройство #1",
            provisioning_status="active",
            peer_id="p1",
            raw_config="vpn://test",
            is_active=True,
            traffic_down=0,
            traffic_up=0,
            last_connected=None,
        )

        captured = {}
        async def mock_render_hub(_bot, _chat_id, text, keyboard, **_kwargs):
            captured["text"] = text
            captured["keyboard"] = keyboard

        with (
            patch("bot.handlers.connection.device_view_routes.get_server_by_id", new=AsyncMock(return_value=server)),
            patch("bot.handlers.connection.device_view_routes.SubscriptionService.check_access", new=AsyncMock(return_value=False)),
            patch("bot.handlers.connection.device_view_routes.render_hub", new=AsyncMock(side_effect=mock_render_hub)),
        ):
            await render_device_screen(MagicMock(), 100, active_profile, db_user, AsyncMock())

            buttons = [b.callback_data for row in captured["keyboard"].inline_keyboard for b in row if b.callback_data]
            self.assertIn("request_delete_device:42", buttons)

    async def test_20_stale_request_delete_on_pending_create_rejected_with_alert(self):
        """Stale click on request_delete_device when profile is pending_create is rejected immediately."""
        from bot.handlers.connection.device_delete_routes import request_delete_device

        db_user = SimpleNamespace(id=1, telegram_id=100)
        pending_profile = SimpleNamespace(
            id=42,
            user_id=1,
            server_id=10,
            device_name="Устройство #1",
            provisioning_status="pending_create",
            peer_id=None,
            raw_config=None,
            is_active=True,
            traffic_down=0,
            traffic_up=0,
            last_connected=None,
        )

        callback = MagicMock()
        callback.bot = MagicMock()
        callback.data = "request_delete_device:42"
        callback.message.chat.id = 100
        callback.message.message_id = 10
        callback.from_user.id = 100
        callback.answer = AsyncMock()

        state = AsyncMock()
        session = AsyncMock()

        with (
            patch("bot.handlers.connection.device_delete_routes.get_profile_by_id", new=AsyncMock(return_value=pending_profile)),
            patch("bot.handlers.connection.device_view_routes.render_device_screen", new=AsyncMock()) as mock_render_screen,
        ):
            await request_delete_device(callback, state, session, db_user)

            callback.answer.assert_called_once_with(texts.DEVICE_CREATE_IN_PROGRESS, show_alert=True)
            self.assertTrue(mock_render_screen.called)

    async def test_21_stale_request_delete_on_deleting_rejected_with_alert(self):
        """Stale click on request_delete_device when profile is deleting is rejected immediately."""
        from bot.handlers.connection.device_delete_routes import request_delete_device

        db_user = SimpleNamespace(id=1, telegram_id=100)
        deleting_profile = SimpleNamespace(
            id=42,
            user_id=1,
            server_id=10,
            device_name="Устройство #1",
            provisioning_status="deleting",
            peer_id="p1",
            raw_config=None,
            is_active=True,
            traffic_down=0,
            traffic_up=0,
            last_connected=None,
        )

        callback = MagicMock()
        callback.bot = MagicMock()
        callback.data = "request_delete_device:42"
        callback.message.chat.id = 100
        callback.message.message_id = 10
        callback.from_user.id = 100
        callback.answer = AsyncMock()

        state = AsyncMock()
        session = AsyncMock()

        with (
            patch("bot.handlers.connection.device_delete_routes.get_profile_by_id", new=AsyncMock(return_value=deleting_profile)),
            patch("bot.handlers.connection.device_view_routes.render_device_screen", new=AsyncMock()) as mock_render_screen,
        ):
            await request_delete_device(callback, state, session, db_user)

            callback.answer.assert_called_once_with("🗑 Устройство уже удаляется с сервера.", show_alert=True)
            self.assertTrue(mock_render_screen.called)

    async def test_22_await_profile_ready_polling_sequence_pending_to_active(self):
        """Sequential polling progression: pending -> pending -> active resolves successfully."""
        pending_1 = SimpleNamespace(id=42, provisioning_status="pending_create", peer_id=None, raw_config=None, is_active=True)
        pending_2 = SimpleNamespace(id=42, provisioning_status="pending_create", peer_id=None, raw_config=None, is_active=True)
        ready_3 = SimpleNamespace(id=42, provisioning_status="active", peer_id="p1", raw_config=_make_valid_vpn_uri(), is_active=True)

        responses = [pending_1, pending_2, ready_3]

        mock_session = AsyncMock()
        mock_session.get = AsyncMock(side_effect=responses)
        mock_session.close = AsyncMock()

        with patch("bot.handlers.connection.device_create_routes.get_session", new=AsyncMock(return_value=mock_session)):
            res = await _await_profile_ready(42, timeout_seconds=2.0, poll_interval=0.01)

            self.assertEqual(res, ready_3)
            self.assertEqual(mock_session.get.call_count, 3)
            self.assertEqual(mock_session.close.call_count, 3)

    def test_23_can_show_delete_action_fail_closed_on_unknown_or_null_state(self):
        """can_show_delete_action follows strict fail-closed whitelist policy."""
        from bot.handlers.connection.device_view_routes import can_show_delete_action

        self.assertFalse(can_show_delete_action(None))
        self.assertFalse(can_show_delete_action(SimpleNamespace(provisioning_status=None)))
        self.assertFalse(can_show_delete_action(SimpleNamespace(provisioning_status="")))
        self.assertFalse(can_show_delete_action(SimpleNamespace(provisioning_status="unknown_custom_state")))
        self.assertFalse(can_show_delete_action(SimpleNamespace(provisioning_status="pending_create")))
        self.assertFalse(can_show_delete_action(SimpleNamespace(provisioning_status="create_cleanup_pending")))
        self.assertFalse(can_show_delete_action(SimpleNamespace(provisioning_status="deleting")))

        self.assertTrue(can_show_delete_action(SimpleNamespace(provisioning_status="active")))
        self.assertTrue(can_show_delete_action(SimpleNamespace(provisioning_status="pending_update")))
        self.assertTrue(can_show_delete_action(SimpleNamespace(provisioning_status="update_failed")))
        self.assertTrue(can_show_delete_action(SimpleNamespace(provisioning_status="create_failed")))
        self.assertTrue(can_show_delete_action(SimpleNamespace(provisioning_status="delete_failed")))

    async def test_24_await_profile_ready_rejects_corrupted_raw_config_until_valid(self):
        """Profile with active status and peer_id but invalid raw_config is not treated as ready."""
        corrupted_profile = SimpleNamespace(
            id=42,
            provisioning_status="active",
            peer_id="p1",
            raw_config="corrupted_not_vpn_uri",
            is_active=True,
        )
        valid_profile = SimpleNamespace(
            id=42,
            provisioning_status="active",
            peer_id="p1",
            raw_config=_make_valid_vpn_uri(),
            is_active=True,
        )

        responses = [corrupted_profile, valid_profile]

        mock_session = AsyncMock()
        mock_session.get = AsyncMock(side_effect=responses)
        mock_session.close = AsyncMock()

        with patch("bot.handlers.connection.device_create_routes.get_session", new=AsyncMock(return_value=mock_session)):
            res = await _await_profile_ready(42, timeout_seconds=2.0, poll_interval=0.01)

            self.assertEqual(res, valid_profile)
    async def test_25_request_delete_device_on_active_profile_answers_once_without_alert(self):
        """Valid delete request on active profile answers callback once without alert and renders confirm dialog."""
        from bot.handlers.connection.device_delete_routes import request_delete_device

        db_user = SimpleNamespace(id=1, telegram_id=100)
        active_profile = SimpleNamespace(
            id=42,
            user_id=1,
            server_id=10,
            device_name="Мой телефон",
            provisioning_status="active",
            peer_id="p1",
            raw_config=_make_valid_vpn_uri(),
            is_active=True,
            traffic_down=0,
            traffic_up=0,
            last_connected=None,
        )

        callback = MagicMock()
        callback.bot = MagicMock()
        callback.data = "request_delete_device:42"
        callback.message.chat.id = 100
        callback.message.message_id = 10
        callback.from_user.id = 100
        callback.answer = AsyncMock()

        state = AsyncMock()
        session = AsyncMock()

        with (
            patch("bot.handlers.connection.device_delete_routes.get_profile_by_id", new=AsyncMock(return_value=active_profile)),
            patch("bot.handlers.connection.device_delete_routes.render_hub", new=AsyncMock()) as mock_render_hub,
        ):
            await request_delete_device(callback, state, session, db_user)

            callback.answer.assert_called_once_with(show_alert=False)
            self.assertTrue(mock_render_hub.called)


@unittest.skipUnless(os.getenv("TEST_DATABASE_URL"), "TEST_DATABASE_URL is not set")
class DeviceCreationPostgresIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine(os.environ["TEST_DATABASE_URL"])
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)
        async with self.sessions.begin() as s:
            await s.execute(
                text(
                    "TRUNCATE account_balance_reservations, "
                    "account_ledger_allocations, account_ledger_entries, "
                    "entitlement_entries, paid_value_ledger, "
                    "tariff_quotes, tariff_versions, payments, api_operations, vpn_profiles, users, servers, audit_logs, system_settings, payment_disputes "
                    "RESTART IDENTITY CASCADE"
                )
            )
            u = User(
                telegram_id=987654,
                username="testuser",
                device_limit=10,
                subscription_end=datetime.now(timezone.utc) + timedelta(days=30),
            )
            v = Server(
                name="Germany Live",
                country_flag="🇩🇪",
                api_url="http://127.0.0.1:8080",
                api_key="secret",
                protocol="amneziawg2",
                is_active=True,
                max_clients=100,
            )
            s.add_all([u, v])
            await s.flush()
            self.uid = u.id
            self.sid = v.id

    async def asyncTearDown(self):
        async with self.sessions.begin() as s:
            await s.execute(
                text(
                    "TRUNCATE account_balance_reservations, "
                    "account_ledger_allocations, account_ledger_entries, "
                    "entitlement_entries, paid_value_ledger, "
                    "tariff_quotes, tariff_versions, payments, api_operations, vpn_profiles, users, servers, audit_logs, system_settings, payment_disputes "
                    "RESTART IDENTITY CASCADE"
                )
            )
        await self.engine.dispose()

    async def test_create_device_commit_makes_operation_visible_to_second_session(self):
        """Verify that create_device followed by session.commit makes create_peer visible to independent session."""
        from services.device_service import DeviceService
        from services.slots_cache import ServerPeerSnapshot

        snapshot = ServerPeerSnapshot(
            self.sid,
            frozenset(),
            datetime.now(timezone.utc),
        )

        # 2. Session A: create_device and commit
        with patch("services.device_service.ensure_server_capacity", new=AsyncMock()):
            async with self.sessions() as session_a:
                profile = await DeviceService.create_device(
                    session_a,
                    user_id=self.uid,
                    server_id=self.sid,
                    device_name="Integration Dev #1",
                    snapshot=snapshot,
                )
                await session_a.commit()
                created_profile_id = profile.id

        # 3. Session B: query from completely separate database connection
        async with self.sessions() as session_b:
            res = await session_b.execute(
                select(APIOperation).where(
                    APIOperation.profile_id == created_profile_id,
                    APIOperation.operation_type == "create_peer",
                )
            )
            op = res.scalar_one_or_none()

            self.assertIsNotNone(op, "APIOperation(create_peer) must be visible in session_b immediately after commit")
            self.assertEqual(op.status, "pending")
            self.assertEqual(op.profile_id, created_profile_id)
            self.assertEqual(op.operation_type, "create_peer")


if __name__ == "__main__":
    unittest.main()

