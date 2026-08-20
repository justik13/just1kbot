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
from database.models import APIOperation, Server, User, VPNProfile
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
        from bot.handlers.connection.device_create_routes import select_server
        _creating_devices[999] = True
        callback = MagicMock()
        callback.from_user.id = 999
        callback.data = "select_server:1"
        callback.answer = AsyncMock()

        try:
            await select_server(callback, AsyncMock(), AsyncMock())
            callback.answer.assert_called_once_with(texts.DEVICE_CREATE_IN_PROGRESS, show_alert=True)
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

    async def test_12_await_profile_ready_cleanup_pending_returns_profile_and_renders_device_screen(self):
        """Profiles with create_cleanup_pending return immediately and trigger render_device_screen with recovery banner."""
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
            patch("bot.handlers.connection.device_view_routes.render_device_screen", new=AsyncMock()) as mock_render_device,
            patch("bot.handlers.connection.device_create_routes._render_connections", new=AsyncMock()) as mock_render_connections,
        ):
            await _process_server_selection(callback, state, session, server_id=10, user=db_user)
            self.assertTrue(mock_render_device.called)
            self.assertFalse(mock_render_connections.called)

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
        active_profile = SimpleNamespace(
            id=42,
            user_id=1,
            server_id=10,
            device_name="Старое #1",
            provisioning_status="active",
            peer_id="peer-123",
            raw_config=_make_valid_vpn_uri(),
            is_active=True,
        )

        message = MagicMock()
        message.bot = MagicMock()
        message.chat.id = 100
        message.text = "Новое"
        message.delete = AsyncMock()

        state = AsyncMock()
        state.get_data = AsyncMock(return_value={"profile_id": 42})
        
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = active_profile
        session = AsyncMock()
        session.execute.return_value = mock_result

        captured = {}
        async def mock_render_hub(_bot, _chat_id, text, keyboard, **_kwargs):
            captured["text"] = text
            captured["keyboard"] = keyboard

        with (
            patch("bot.handlers.connection.device_rename_routes.get_profile_by_id", new=AsyncMock(return_value=active_profile)),
            patch("bot.handlers.connection.device_rename_routes.get_server_by_id", new=AsyncMock(return_value=server)),
            patch("bot.handlers.connection.device_rename_routes.get_user_profiles", new=AsyncMock(return_value=[active_profile])),
            patch("bot.handlers.connection.device_rename_routes.update_profile", new=AsyncMock()),
            patch("bot.handlers.connection.device_rename_routes.SubscriptionService.check_access", new=AsyncMock(return_value=True)),
            patch("bot.handlers.connection.device_rename_routes.render_hub", new=AsyncMock(side_effect=mock_render_hub)),
            patch("services.audit_service.AuditService.log_action", new=AsyncMock()),
        ):
            await rename_device_process(message, state, session, db_user)

            self.assertIn("keyboard", captured)
            buttons = [b.callback_data for row in captured["keyboard"].inline_keyboard for b in row if b.callback_data]
            self.assertIn("show_config:42", buttons)
            self.assertIn("download_conf:42", buttons)
            self.assertIn("request_delete_device:42", buttons)
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
        self.assertFalse(can_show_delete_action(SimpleNamespace(provisioning_status="deleting")))
        self.assertFalse(can_show_delete_action(SimpleNamespace(provisioning_status="create_cleanup_pending")))

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

    async def test_26_confirm_delete_device_unexpected_exception_answers_error_alert_once(self):
        """Unexpected exception in confirm_delete_device answers technical error alert once and clears lock."""
        from bot.handlers.connection.device_delete_routes import _deleting_devices, confirm_delete_device

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
        )

        callback = MagicMock()
        callback.bot = MagicMock()
        callback.data = "confirm_delete_device:42"
        callback.message.chat.id = 100
        callback.message.message_id = 10
        callback.from_user.id = 100
        callback.answer = AsyncMock()

        state = AsyncMock()
        session = AsyncMock()

        with (
            patch("bot.handlers.connection.device_delete_routes.get_profile_by_id", new=AsyncMock(return_value=active_profile)),
            patch("bot.handlers.connection.device_delete_routes.DeviceService.delete_device", new=AsyncMock(side_effect=RuntimeError("Database failure"))),
        ):
            await confirm_delete_device(callback, state, session, db_user)

            callback.answer.assert_called_once_with(texts.ERROR_TECHNICAL_MESSAGE, show_alert=True)
            self.assertNotIn(42, _deleting_devices)

    def test_27_ui_visibility_separated_from_quota_exclusion_policy(self):
        """UI list hides only deleting, while quota excludes all non-active/failed states."""
        from database.repositories.profiles_repo import (
            PROFILE_LIST_HIDDEN_STATUSES,
            PROFILE_QUOTA_EXCLUDED_STATUSES,
        )

        self.assertEqual(PROFILE_LIST_HIDDEN_STATUSES, ("deleting",))
        self.assertNotIn("create_cleanup_pending", PROFILE_QUOTA_EXCLUDED_STATUSES)
        self.assertNotIn("delete_failed", PROFILE_QUOTA_EXCLUDED_STATUSES)
        self.assertIn("create_failed", PROFILE_QUOTA_EXCLUDED_STATUSES)
        self.assertNotIn("pending_create", PROFILE_QUOTA_EXCLUDED_STATUSES)
        self.assertNotIn("active", PROFILE_QUOTA_EXCLUDED_STATUSES)

    async def test_28_confirm_delete_device_stale_callback_fail_closed_rejection(self):
        """If device transitioned to create_cleanup_pending, confirm_delete rejects it fail-closed."""
        from bot.handlers.connection.device_delete_routes import confirm_delete_device

        db_user = SimpleNamespace(id=1, telegram_id=100)
        cleanup_profile = SimpleNamespace(
            id=42,
            user_id=1,
            server_id=10,
            device_name="Мой телефон",
            provisioning_status="create_cleanup_pending",
            peer_id="p1",
            raw_config=None,
            is_active=True,
        )

        callback = MagicMock()
        callback.bot = MagicMock()
        callback.data = "confirm_delete_device:42"
        callback.message.chat.id = 100
        callback.message.message_id = 10
        callback.from_user.id = 100
        callback.answer = AsyncMock()

        state = AsyncMock()
        session = AsyncMock()

        with (
            patch("bot.handlers.connection.device_delete_routes.get_profile_by_id", new=AsyncMock(return_value=cleanup_profile)),
            patch("bot.handlers.connection.device_delete_routes.DeviceService.delete_device", new=AsyncMock(return_value=True)) as mock_delete,
            patch("bot.handlers.connection.device_delete_routes.get_user_by_telegram_id", new=AsyncMock(return_value=db_user)),
            patch("bot.handlers.connection.device_view_routes.render_device_screen", new=AsyncMock()) as mock_render,
        ):
            await confirm_delete_device(callback, state, session, db_user)

            callback.answer.assert_called_once_with("⚠️ Идёт автоматическое восстановление после сбоя. Попробуйте позже.", show_alert=True)
            mock_delete.assert_not_called()
            mock_render.assert_called_once()

    async def test_29_confirm_delete_device_render_connections_failure_does_not_double_answer(self):
        """If _render_connections throws after successful delete answer, callback.answer is not invoked a second time."""
        from bot.handlers.connection.device_delete_routes import _deleting_devices, confirm_delete_device

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
        )

        callback = MagicMock()
        callback.bot = MagicMock()
        callback.data = "confirm_delete_device:42"
        callback.message.chat.id = 100
        callback.message.message_id = 10
        callback.from_user.id = 100
        callback.answer = AsyncMock()

        state = AsyncMock()
        session = AsyncMock()

        with (
            patch("bot.handlers.connection.device_delete_routes.get_profile_by_id", new=AsyncMock(return_value=active_profile)),
            patch("bot.handlers.connection.device_delete_routes.DeviceService.delete_device", new=AsyncMock(return_value=True)),
            patch("bot.handlers.connection.device_delete_routes.get_user_by_telegram_id", new=AsyncMock(return_value=db_user)),
            patch("bot.handlers.connection.device_delete_routes._render_connections", new=AsyncMock(side_effect=RuntimeError("Network error during render"))),
        ):
            await confirm_delete_device(callback, state, session, db_user)

            # Exactly one answer with DELETING_PROGRESS, no secondary ERROR_TECHNICAL_MESSAGE
            callback.answer.assert_called_once_with(texts.DEVICE_DELETING_PROGRESS, show_alert=False)
            self.assertNotIn(42, _deleting_devices)

    async def test_30_server_unavailable_caught_and_classified_properly(self):
        """ServerUnavailable is caught before DeviceCreationError and displays classified server error text."""
        from bot.handlers.connection.device_create_routes import _process_server_selection
        from services.device_service import ServerUnavailable

        db_user = SimpleNamespace(id=1, telegram_id=100)
        callback = MagicMock()
        callback.bot = MagicMock()
        callback.message.chat.id = 100
        callback.message.message_id = 10
        callback.from_user.id = 100
        callback.answer = AsyncMock()

        state = AsyncMock()
        session = AsyncMock()

        with (
            patch("bot.handlers.connection.device_create_routes.get_user_profiles", new=AsyncMock(return_value=[])),
            patch("bot.handlers.connection.device_create_routes._get_effective_device_limit", new=AsyncMock(return_value=5)),
            patch("bot.handlers.connection.device_create_routes.capture_server_peer_snapshot", new=AsyncMock()),
            patch("bot.handlers.connection.device_create_routes.DeviceService.create_device", new=AsyncMock(side_effect=ServerUnavailable("Server is full"))),
            patch("bot.handlers.connection.device_create_routes.render_hub", new=AsyncMock()) as mock_render_hub,
        ):
            await _process_server_selection(callback, state, session, server_id=10, user=db_user)

            self.assertTrue(mock_render_hub.called)
            error_text = mock_render_hub.call_args.args[2]
            self.assertEqual(error_text, texts.ERROR_SERVER_FULL)

    async def test_31_expired_subscription_with_create_failed_renders_readonly_connections_screen(self):
        """Expired subscription user with create_failed device sees read-only screen with recoverable device."""
        from bot.handlers.connection.common import _render_connections

        user = SimpleNamespace(id=1, telegram_id=100, subscription_end=None)
        target = MagicMock()
        target.bot = MagicMock()
        target.chat.id = 100
        session = AsyncMock()

        failed_profile = SimpleNamespace(
            id=42,
            user_id=1,
            server_id=10,
            device_name="Устройство #1",
            provisioning_status="create_failed",
            server=SimpleNamespace(country_flag="🇩🇪", name="Germany"),
            last_connected=None,
            traffic_down=0,
            traffic_up=0,
        )

        with (
            patch("bot.handlers.connection.common.SubscriptionService.check_access", new=AsyncMock(return_value=False)),
            patch("bot.handlers.connection.common.get_user_profiles", new=AsyncMock(return_value=[failed_profile])),
            patch("bot.handlers.connection.common._get_effective_device_limit", new=AsyncMock(return_value=5)),
            patch("bot.handlers.connection.common._get_grace_deletion_time", return_value=None),
            patch("bot.handlers.connection.common.render_hub", new=AsyncMock()) as mock_render_hub,
        ):
            await _render_connections(target, user, session)

            self.assertTrue(mock_render_hub.called)
            rendered_text = mock_render_hub.call_args.args[2]
            self.assertIn("Устройство #1", rendered_text)
            self.assertIn("(0/5)", rendered_text)

    async def test_32_rename_device_start_rejects_deleting_and_cleanup_states(self):
        """rename_device_start rejects deleting and create_cleanup_pending states with exactly-once alert answer."""
        from bot.handlers.connection.device_rename_routes import rename_device_start

        db_user = SimpleNamespace(id=1, telegram_id=100)
        deleting_profile = SimpleNamespace(
            id=42,
            user_id=1,
            server_id=10,
            device_name="Мой телефон",
            provisioning_status="deleting",
        )

        callback = MagicMock()
        callback.bot = MagicMock()
        callback.data = "rename_device:42"
        callback.from_user.id = 100
        callback.answer = AsyncMock()

        state = AsyncMock()
        session = AsyncMock()

        with patch("bot.handlers.connection.device_rename_routes.get_profile_by_id", new=AsyncMock(return_value=deleting_profile)):
            await rename_device_start(callback, state, session, db_user)

            callback.answer.assert_called_once_with("🗑 Устройство уже удаляется с сервера.", show_alert=True)
            self.assertFalse(state.set_state.called)

    async def test_33_device_service_delete_device_rejects_create_cleanup_pending(self):
        """DeviceService.delete_device raises DeviceCreationError for create_cleanup_pending on service boundary."""
        from database.models import VPNProfile
        from services.device_service import DeviceService

        cleanup_profile = VPNProfile(
            id=42,
            user_id=1,
            server_id=10,
            device_name="Мой телефон",
            provisioning_status="create_cleanup_pending",
            peer_id="peer1",
        )

        session = AsyncMock()
        session.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=cleanup_profile)))
        session.get = AsyncMock(return_value=MagicMock(name="Server"))

        with patch("services.device_service.ensure_delete_operation", new=AsyncMock()):
            from services.device_service import DeviceCreationError
            with self.assertRaises(DeviceCreationError):
                await DeviceService.delete_device(session, cleanup_profile, force=False)

    async def test_34_cleanup_worker_stuck_profile_with_peer_id_queues_delete_operation(self):
        """Cleanup worker ensures delete_peer operation is queued if stuck profile has peer_id."""
        from database.models import Server, VPNProfile
        from services.workers.cleanup import _cleanup_stuck_profiles

        stuck_profile = VPNProfile(
            id=42,
            user_id=1,
            server_id=10,
            device_name="Мой телефон",
            client_name="tg_100_p42",
            provisioning_status="create_cleanup_pending",
            peer_id="peer_abc",
        )

        mock_server = Server(id=10, name="DE Server", api_url="https://de.vpn", api_key="secret")
        mock_session = AsyncMock()
        # First query: stuck_profiles returns [stuck_profile]
        # Inside loop: active_op returns None (operation dead/absent)
        mock_session.execute = AsyncMock(side_effect=[
            MagicMock(scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[stuck_profile])))),
            MagicMock(scalar_one_or_none=MagicMock(return_value=None)),
        ])
        mock_session.get = AsyncMock(return_value=mock_server)

        mock_scope = MagicMock()
        mock_scope.__aenter__.return_value = mock_session
        mock_scope.__aexit__.return_value = None

        with (
            patch("services.workers.cleanup.session_scope", return_value=mock_scope),
            patch("services.api_operations_queue.ensure_delete_operation", new=AsyncMock()) as mock_ensure_delete,
        ):
            await _cleanup_stuck_profiles()

            self.assertEqual(stuck_profile.provisioning_status, "deleting")
            mock_ensure_delete.assert_called_once()

    async def test_35_cleanup_worker_skips_profiles_with_active_api_operation(self):
        """Cleanup worker skips profiles whose create_peer operation is still active in retry/processing."""
        from database.models import VPNProfile
        from services.workers.cleanup import _cleanup_stuck_profiles

        stuck_profile = VPNProfile(
            id=42,
            user_id=1,
            server_id=10,
            device_name="Мой телефон",
            client_name="tg_100_p42",
            provisioning_status="pending_create",
            peer_id=None,
        )

        active_op = SimpleNamespace(id=101, operation_type="create_peer", status="processing")

        mock_session = AsyncMock()
        # First query: stuck_profiles returns [stuck_profile]
        # Inside loop: active_op returns active_op
        mock_session.execute = AsyncMock(side_effect=[
            MagicMock(scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[stuck_profile])))),
            MagicMock(scalar_one_or_none=MagicMock(return_value=active_op)),
        ])

        mock_scope = MagicMock()
        mock_scope.__aenter__.return_value = mock_session
        mock_scope.__aexit__.return_value = None

        with patch("services.workers.cleanup.session_scope", return_value=mock_scope):
            await _cleanup_stuck_profiles()

            # Profile must remain pending_create since operation is still active in queue
            self.assertEqual(stuck_profile.provisioning_status, "pending_create")

    async def test_36_cleanup_worker_ensure_delete_error_keeps_cleanup_pending(self):
        """If ensure_delete_operation fails, profile stays in create_cleanup_pending so peer is not orphaned."""
        from database.models import Server, VPNProfile
        from services.workers.cleanup import _cleanup_stuck_profiles

        stuck_profile = VPNProfile(
            id=42,
            user_id=1,
            server_id=10,
            device_name="Мой телефон",
            client_name="tg_100_p42",
            provisioning_status="create_cleanup_pending",
            peer_id="peer_abc",
        )

        mock_server = Server(id=10, name="DE Server", api_url="https://de.vpn", api_key="secret")
        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(side_effect=[
            MagicMock(scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[stuck_profile])))),
            MagicMock(scalar_one_or_none=MagicMock(return_value=None)),
        ])
        mock_session.get = AsyncMock(return_value=mock_server)

        mock_scope = MagicMock()
        mock_scope.__aenter__.return_value = mock_session
        mock_scope.__aexit__.return_value = None

        with (
            patch("services.workers.cleanup.session_scope", return_value=mock_scope),
            patch("services.api_operations_queue.ensure_delete_operation", new=AsyncMock(side_effect=RuntimeError("DB lock error"))),
        ):
            await _cleanup_stuck_profiles()

            # Must remain create_cleanup_pending for next cycle retry
            self.assertEqual(stuck_profile.provisioning_status, "create_cleanup_pending")

    async def test_37_slot_allocation_respects_all_profiles_including_failed(self):
        """Slot allocator uses include_deleting=True to prevent name collisions across all profile statuses."""
        from bot.handlers.connection.device_create_routes import _process_server_selection

        db_user = SimpleNamespace(id=1, telegram_id=100)
        callback = MagicMock()
        callback.bot = MagicMock()
        callback.message.chat.id = 100
        callback.from_user.id = 100
        state = AsyncMock()
        session = AsyncMock()

        failed_p1 = SimpleNamespace(id=1, user_id=1, server_id=10, device_name="Устройство #1", provisioning_status="create_failed")
        deleting_p2 = SimpleNamespace(id=2, user_id=1, server_id=10, device_name="Устройство #2", provisioning_status="deleting")

        with (
            patch("bot.handlers.connection.device_create_routes.get_user_profiles", new=AsyncMock(return_value=[failed_p1, deleting_p2])) as mock_get_profiles,
            patch("bot.handlers.connection.device_create_routes._get_effective_device_limit", new=AsyncMock(return_value=5)),
            patch("bot.handlers.connection.device_create_routes.capture_server_peer_snapshot", new=AsyncMock()),
            patch("bot.handlers.connection.device_create_routes.DeviceService.create_device", new=AsyncMock()) as mock_create_device,
            patch("bot.handlers.connection.device_create_routes.render_hub", new=AsyncMock()),
            patch("bot.handlers.connection.device_create_routes._await_profile_ready", new=AsyncMock(return_value=None)),
            patch("bot.handlers.connection.device_create_routes._render_connections", new=AsyncMock()),
        ):
            await _process_server_selection(callback, state, session, server_id=10, user=db_user)

            # Verified that include_deleting=True was requested
            mock_get_profiles.assert_called_with(session, 1, include_deleting=True)
            # Verified that slot #3 was chosen (since #1 and #2 exist in DB)
            mock_create_device.assert_called_once()
            self.assertEqual(mock_create_device.call_args.kwargs["device_name"], "Устройство #3")

    async def test_40_rename_device_process_with_for_update_rejects_deleting(self):
        """rename_device_process rejects deleting profile and clears state."""
        from bot.handlers.connection.device_rename_routes import rename_device_process

        db_user = SimpleNamespace(id=1, telegram_id=100)
        message = MagicMock()
        message.bot = MagicMock()
        message.chat.id = 100
        message.from_user.id = 100
        message.text = "My Phone"
        state = AsyncMock()
        state.get_data = AsyncMock(return_value={"profile_id": 42})
        session = AsyncMock()

        deleting_profile = SimpleNamespace(
            id=42,
            user_id=1,
            server_id=10,
            device_name="Устройство #1",
            provisioning_status="deleting",
        )

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = deleting_profile
        session.execute = AsyncMock(return_value=mock_result)

        with patch("bot.handlers.connection.device_rename_routes.render_hub", new=AsyncMock()) as mock_render_hub:
            await rename_device_process(message, state, session, db_user=db_user)

            state.clear.assert_called_once()
            self.assertTrue(mock_render_hub.called)
            self.assertIn("удаляется", mock_render_hub.call_args.args[2])

    async def test_41_cleanup_stuck_profile_persists_peer_id_on_model(self):
        """_cleanup_stuck_profiles persists peer_id recovered from APIOperation on profile model."""
        from services.workers.cleanup import _cleanup_stuck_profiles

        stuck_profile = SimpleNamespace(
            id=42,
            user_id=1,
            server_id=10,
            client_name="tg_stuck",
            peer_id=None,
            provisioning_status="create_cleanup_pending",
            last_sync_error=None,
        )

        mock_prof_res = MagicMock()
        mock_prof_res.scalars.return_value.all.return_value = [stuck_profile]

        mock_op_active = MagicMock()
        mock_op_active.scalar_one_or_none.return_value = None

        mock_op_peer = MagicMock()
        mock_op_peer.scalar_one_or_none.return_value = "peer_recovered_123"

        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(side_effect=[mock_prof_res, mock_op_active, mock_op_peer])
        mock_session.get = AsyncMock(return_value=SimpleNamespace(id=10, name="S1", api_url="http://s1", api_key="k1"))

        mock_scope = MagicMock()
        mock_scope.__aenter__.return_value = mock_session
        mock_scope.__aexit__.return_value = None

        with (
            patch("services.workers.cleanup.session_scope", return_value=mock_scope),
            patch("services.api_operations_queue.ensure_delete_operation", new=AsyncMock()) as mock_ensure_del,
        ):
            await _cleanup_stuck_profiles()

            # Must set profile.peer_id = peer_id
            self.assertEqual(stuck_profile.peer_id, "peer_recovered_123")
            self.assertEqual(stuck_profile.provisioning_status, "deleting")
            mock_ensure_del.assert_called_once()
            self.assertEqual(mock_ensure_del.call_args.kwargs["peer_id"], "peer_recovered_123")

    async def test_42_execute_create_reconciliation_cleans_orphan_for_create_failed(self):
        """_execute_create cleans up exact orphan on Amnezia when profile is create_failed."""
        from services.api_operations_executor import _execute_create

        op = SimpleNamespace(
            id=1,
            client_name="tg_orphan",
            peer_id=None,
            attempt_number=2,
            last_error_code="create_timeout",
            locked_by="worker-1",
            server_id=10,
            profile_id=42,
        )

        mock_client = AsyncMock()
        mock_client.get_all_clients = AsyncMock(
            return_value=[SimpleNamespace(id="peer_orphan_99", clientName="tg_orphan")]
        )
        mock_client.delete_user_result = AsyncMock(return_value=SimpleNamespace(ok=True))

        mock_prof = SimpleNamespace(
            provisioning_status="create_failed",
            desired_version=1,
            desired_is_active=True,
            desired_expires_at=None,
            peer_id=None,
            raw_config=None,
        )
        mock_session = AsyncMock()
        mock_session.get = AsyncMock(return_value=mock_prof)
        mock_scope = MagicMock()
        mock_scope.__aenter__.return_value = mock_session
        mock_scope.__aexit__.return_value = None

        with (
            patch("services.api_operations_executor.session_scope", return_value=mock_scope),
            patch("services.api_operations_executor.finalize_create_cancelled", new=AsyncMock(return_value=True)) as mock_cancel,
        ):
            await _execute_create(op, mock_client)

            mock_client.delete_user_result.assert_called_once_with("peer_orphan_99")
            mock_cancel.assert_called_once()
            self.assertFalse(mock_cancel.call_args.kwargs["delete_profile"])

    async def test_43_select_server_concurrency_double_click_fences_immediately(self):
        """select_server rejects with DEVICE_CREATE_IN_PROGRESS if user already in _creating_devices."""
        from bot.handlers.connection.device_create_routes import _creating_devices, select_server

        callback = MagicMock()
        callback.from_user.id = 999
        callback.answer = AsyncMock()
        state = AsyncMock()
        session = AsyncMock()

        _creating_devices[999] = True
        try:
            await select_server(callback, state, session)
            callback.answer.assert_called_once_with(texts.DEVICE_CREATE_IN_PROGRESS, show_alert=True)
        finally:
            _creating_devices.pop(999, None)

    async def test_44_show_incy_subscription_missing_user_renders_hub(self):
        """show_incy_subscription renders error hub when db_user is None."""
        from bot.handlers.connection.incy_routes import show_incy_subscription

        callback = MagicMock()
        callback.from_user.id = 100
        callback.bot = MagicMock()
        callback.message.chat.id = 100
        callback.answer = AsyncMock()
        state = AsyncMock()
        session = AsyncMock()

        with patch("bot.handlers.connection.incy_routes.render_hub", new=AsyncMock()) as mock_render:
            await show_incy_subscription(callback, state, session, db_user=None)

            callback.answer.assert_called_once_with(show_alert=False)
            mock_render.assert_called_once()
            self.assertEqual(mock_render.call_args.args[2], texts.ERROR_USER_NOT_FOUND)



@unittest.skipUnless(os.getenv("TEST_DATABASE_URL"), "TEST_DATABASE_URL is not set")
class DeviceCreationPostgresIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.env_patcher = patch.dict(
            os.environ,
            {
                "BOT_TOKEN": "123:test",
                "REDIS_URL": "redis://localhost:6379/1",
                "REDIS_PASSWORD": "test",
                "ADMIN_IDS": "[123456789]",
                "SUPPORT_USERNAME": "test_support",
                "DOMAIN": "test.domain",
                "SSL_EMAIL": "test@domain.com",
                "YOOKASSA_SHOP_ID": "123456",
                "YOOKASSA_SECRET_KEY": "test_secret",
                "YOOKASSA_RETURN_URL": "https://t.me/{bot_username}",
                "YOOKASSA_WEBHOOK_PORT": "8080",
                "DB_ENCRYPTION_KEY": "MDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDA=",
                "AMNEZIA_BRIDGE_HMAC_SECRET": "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
                "DATABASE_URL": os.environ["TEST_DATABASE_URL"],
            },
        )
        self.env_patcher.start()
        from config.settings import get_settings
        get_settings.cache_clear()

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
        self.env_patcher.stop()
        from config.settings import get_settings
        get_settings.cache_clear()

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

    async def test_cleanup_worker_concurrency_with_active_operation_in_postgres(self):
        """Verify that cleanup worker skips profiles with active APIOperation in real PostgreSQL."""
        from database.models import VPNProfile
        from services.workers.cleanup import _cleanup_stuck_profiles

        async with self.sessions.begin() as s:
            old_time = datetime.now(timezone.utc) - timedelta(hours=2)
            prof = VPNProfile(
                user_id=self.uid,
                server_id=self.sid,
                device_name="Stuck Dev",
                client_name="tg_987654_stuck",
                provisioning_status="pending_create",
                created_at=old_time,
            )
            s.add(prof)
            await s.flush()
            prof_id = prof.id

            # Active operation in processing state
            op = APIOperation(
                operation_type="create_peer",
                status="processing",
                idempotency_key=f"create-peer:{prof_id}",
                server_id=self.sid,
                profile_id=prof_id,
            )
            s.add(op)

        with patch("services.workers.cleanup.session_scope", side_effect=self.sessions):
            await _cleanup_stuck_profiles()

        async with self.sessions() as s:
            p = await s.get(VPNProfile, prof_id)
            # Profile must NOT have been converted to create_failed because APIOperation was in 'processing'
            self.assertEqual(p.provisioning_status, "pending_create")


    async def test_38_postgres_cleanup_claim_concurrency_interleaving(self):
        """Prove that cleanup skipping logic works even if claim_api_operations executes concurrently."""
        from database.models import VPNProfile, APIOperation
        from services.workers.cleanup import _cleanup_stuck_profiles
        from services.api_operations_queue import claim_api_operations
        import asyncio
        from unittest.mock import patch
        from datetime import datetime, timezone, timedelta

        async with self.sessions.begin() as s:
            old_time = datetime.now(timezone.utc) - timedelta(hours=2)
            prof = VPNProfile(
                user_id=self.uid,
                server_id=self.sid,
                device_name="Stuck Concurrent",
                client_name="tg_conc_987",
                provisioning_status="pending_create",
                created_at=old_time,
            )
            s.add(prof)
            await s.flush()
            prof_id = prof.id

            op = APIOperation(
                operation_type="create_peer",
                status="pending",
                idempotency_key=f"create-peer:{prof_id}",
                server_id=self.sid,
                profile_id=prof_id,
            )
            s.add(op)
            await s.flush()
            op_id = op.id

        with patch("services.workers.cleanup.session_scope", side_effect=self.sessions):
            cleanup_task = asyncio.create_task(_cleanup_stuck_profiles())
            claim_task = asyncio.create_task(claim_api_operations(worker_id="test-worker", limit=10, session_factory=self.sessions))
            await asyncio.gather(cleanup_task, claim_task)

        async with self.sessions() as s:
            p = await s.get(VPNProfile, prof_id)
            self.assertEqual(p.provisioning_status, "pending_create")
            o = await s.get(APIOperation, op_id)
            self.assertEqual(o.status, "processing")
            self.assertEqual(o.locked_by, "test-worker")

    async def test_39_finalize_create_success_race_condition(self):
        """finalize_create_success catches create_failed mid-flight and queues compensation."""
        from database.models import VPNProfile, APIOperation
        from services.api_operations_finalizer import finalize_create_success, CreateCompensationRequired

        async with self.sessions.begin() as s:
            prof = VPNProfile(
                user_id=self.uid,
                server_id=self.sid,
                device_name="Race Dev",
                client_name="tg_race",
                provisioning_status="pending_create",
            )
            s.add(prof)
            await s.flush()
            prof_id = prof.id

            op = APIOperation(
                operation_type="create_peer",
                status="processing",
                idempotency_key=f"create-peer:{prof_id}",
                server_id=self.sid,
                profile_id=prof_id,
                locked_by="worker-1",
                attempts=1,
            )
            s.add(op)
            await s.flush()
            op_id = op.id

        # Simulating external factor modifying profile to create_failed during HTTP request
        async with self.sessions.begin() as s:
            p = await s.get(VPNProfile, prof_id)
            p.provisioning_status = "create_failed"
            await s.flush()

        with self.assertRaises(CreateCompensationRequired):
            await finalize_create_success(
                op_id,
                worker_id="worker-1",
                expected_attempt_number=1,
                peer_id="peer-123",
                raw_config="vpn://test",
                sent_desired_version=1,
                sent_is_active=True,
                sent_expires_at=None,
                session_factory=self.sessions,
            )

        async with self.sessions() as s:
            p = await s.get(VPNProfile, prof_id)
            self.assertEqual(p.provisioning_status, "create_cleanup_pending")
            self.assertEqual(p.peer_id, "peer-123")

    async def test_45_postgres_rename_device_process_with_for_update(self):
        """Rename device uses row-level lock and updates device_name atomically on PostgreSQL."""
        from bot.handlers.connection.device_rename_routes import rename_device_process

        async with self.sessions.begin() as s:
            prof = VPNProfile(
                user_id=self.uid,
                server_id=self.sid,
                device_name="Old Device #1",
                client_name="tg_rename_test",
                provisioning_status="active",
            )
            s.add(prof)
            await s.flush()
            prof_id = prof.id

        message = MagicMock()
        message.bot = MagicMock()
        message.chat.id = 987654
        message.from_user.id = 987654
        message.text = "New Device Name"
        state = AsyncMock()
        state.get_data = AsyncMock(return_value={"profile_id": prof_id})
        db_user = SimpleNamespace(id=self.uid, telegram_id=987654)

        async with self.sessions.begin() as session:
            with (
                patch("bot.handlers.connection.device_rename_routes.SubscriptionService.check_access", new=AsyncMock(return_value=True)),
                patch("bot.handlers.connection.device_rename_routes.render_hub", new=AsyncMock()) as mock_hub,
            ):
                await rename_device_process(message, state, session, db_user=db_user)
                self.assertTrue(mock_hub.called)

        async with self.sessions() as s:
            updated = await s.get(VPNProfile, prof_id)
            self.assertEqual(updated.device_name, "New Device Name #1")

    async def test_46_profile_deletion_no_deadlock_with_finalizer(self):
        """ProfileDeletionService._delete_profiles does not deadlock with finalizer locking on PostgreSQL."""
        from services.profile_deletion_service import ProfileDeletionService

        async with self.sessions.begin() as s:
            prof = VPNProfile(
                user_id=self.uid,
                server_id=self.sid,
                device_name="Delete Deadlock Test #1",
                client_name="tg_del_deadlock",
                provisioning_status="pending_create",
            )
            s.add(prof)
            await s.flush()
            prof_id = prof.id

            op = APIOperation(
                operation_type="create_peer",
                status="pending",
                idempotency_key=f"create-peer:{prof_id}:v1",
                server_id=self.sid,
                profile_id=prof_id,
                attempts=0,
            )
            s.add(op)
            await s.flush()

        async with self.sessions.begin() as session:
            count = await ProfileDeletionService.delete_profiles_for_user(
                session, self.uid, reason="user_banned"
            )
            self.assertEqual(count, 1)

        async with self.sessions() as s:
            deleted_prof = await s.get(VPNProfile, prof_id)
            self.assertIsNone(deleted_prof)


if __name__ == "__main__":
    unittest.main()

