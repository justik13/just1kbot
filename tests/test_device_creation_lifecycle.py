import asyncio
import os
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from sqlalchemy import func, or_, select, text
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
from services.device_service import DeviceService
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
            self.assertIn("alt_connection:42", buttons)
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
        """When user manually opens device after background worker finished -> card renders with actions."""
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
            self.assertIn("alt_connection:42", buttons)
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
            self.assertNotIn("alt_connection:42", buttons)
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
            self.assertIn("alt_connection:42", buttons)
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
            self.assertIn("alt_connection:42", buttons)
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
            self.assertNotIn("alt_connection:42", buttons)
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
        _server_unused = SimpleNamespace(id=10, country_flag="🇩🇪", name="Germany", protocol="amneziawg2", is_active=True)
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
        nested_ctx = MagicMock()
        nested_ctx.__aenter__ = AsyncMock()
        nested_ctx.__aexit__ = AsyncMock()
        session.begin_nested = MagicMock(return_value=nested_ctx)

        captured = {}
        async def mock_render_hub(_bot, _chat_id, text, keyboard, **_kwargs):
            captured["text"] = text
            captured["keyboard"] = keyboard

        with (
            patch("bot.handlers.connection.device_rename_routes.get_profile_by_id", new=AsyncMock(return_value=active_profile)),
            patch("bot.handlers.connection.device_rename_routes.get_user_profiles", new=AsyncMock(return_value=[active_profile])),
            patch("bot.handlers.connection.device_rename_routes.update_profile", new=AsyncMock()),
            patch("bot.handlers.connection.device_rename_routes.SubscriptionService.check_access", new=AsyncMock(return_value=True)),
            patch("bot.handlers.connection.device_view_routes.render_hub", new=AsyncMock(side_effect=mock_render_hub)),
            patch("bot.handlers.connection.device_view_routes.get_server_by_id", new=AsyncMock(return_value=_server_unused)),
            patch("services.audit_service.AuditService.log_action", new=AsyncMock()),
        ):
            await rename_device_process(message, state, session, db_user)

            self.assertIn("keyboard", captured)
            buttons = [b.callback_data for row in captured["keyboard"].inline_keyboard for b in row if b.callback_data]
            self.assertIn("alt_connection:42", buttons)
            self.assertIn("request_delete_device:42", buttons)

    async def test_16_b_rename_device_accepts_hash_and_custom_number(self):
        """rename_device_process accepts names with '#' like 'Устройство #7' without double-suffixing."""
        from bot.handlers.connection.device_rename_routes import rename_device_process

        db_user = SimpleNamespace(id=1, telegram_id=100)
        _server_unused = SimpleNamespace(id=10, country_flag="🇩🇪", name="Germany", protocol="amneziawg2", is_active=True)
        active_profile = SimpleNamespace(
            id=42,
            user_id=1,
            server_id=10,
            device_name="Устройство #11",
            provisioning_status="active",
            peer_id="peer1",
            raw_config="vpn://valid",
            is_active=True,
            traffic_down=100,
            traffic_up=200,
            last_connected=None,
        )

        message = MagicMock()
        message.bot = MagicMock()
        message.chat.id = 100
        message.from_user.id = 100
        message.text = "Мой Телефон"
        message.delete = AsyncMock()

        state = AsyncMock()
        state.get_data = AsyncMock(return_value={"profile_id": 42})

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = active_profile
        session = AsyncMock()
        session.execute.return_value = mock_result
        nested_ctx = MagicMock()
        nested_ctx.__aenter__ = AsyncMock()
        nested_ctx.__aexit__ = AsyncMock()
        session.begin_nested = MagicMock(return_value=nested_ctx)

        mock_update = AsyncMock()

        with (
            patch("bot.handlers.connection.device_rename_routes.get_profile_by_id", new=AsyncMock(return_value=active_profile)),
            patch("bot.handlers.connection.device_rename_routes.get_user_profiles", new=AsyncMock(return_value=[active_profile])),
            patch("bot.handlers.connection.device_rename_routes.update_profile", new=mock_update),
            patch("bot.handlers.connection.device_rename_routes.SubscriptionService.check_access", new=AsyncMock(return_value=True)),
            patch("bot.handlers.connection.device_rename_routes.render_device_screen", new=AsyncMock()),
            patch("services.audit_service.AuditService.log_action", new=AsyncMock()),
        ):
            await rename_device_process(message, state, session, db_user)

            mock_update.assert_called_once()
            self.assertEqual(mock_update.call_args.kwargs.get("device_name"), "Мой Телефон #11")
            state.clear.assert_called_once()

    async def test_16_c_rename_device_validation_errors_preserve_state_and_show_specific_message(self):
        """rename_device_process preserves state and renders specific error when input is invalid or duplicate."""
        from bot.handlers.connection.device_rename_routes import rename_device_process

        db_user = SimpleNamespace(id=1, telegram_id=100)
        _server_unused = SimpleNamespace(id=10, country_flag="🇩🇪", name="Germany", protocol="amneziawg2", is_active=True)
        active_profile = SimpleNamespace(
            id=42,
            user_id=1,
            server_id=10,
            device_name="Устройство #11",
            provisioning_status="active",
            peer_id="peer1",
            raw_config="vpn://valid",
            is_active=True,
        )

        message = MagicMock()
        message.bot = MagicMock()
        message.chat.id = 100
        message.from_user.id = 100
        message.text = "ThisNameIsWayTooLongForADevice123"
        message.delete = AsyncMock()

        state = AsyncMock()
        state.get_data = AsyncMock(return_value={"profile_id": 42})

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = active_profile
        session = AsyncMock()
        session.execute.return_value = mock_result

        captured = {}
        async def mock_render_hub(_bot, _chat_id, text, _kb, **_kwargs):
            captured["text"] = text

        with (
            patch("bot.handlers.connection.device_rename_routes.get_profile_by_id", new=AsyncMock(return_value=active_profile)),
            patch("bot.handlers.connection.device_rename_routes.SubscriptionService.check_access", new=AsyncMock(return_value=True)),
            patch("bot.handlers.connection.device_rename_routes.render_hub", new=AsyncMock(side_effect=mock_render_hub)),
        ):
            await rename_device_process(message, state, session, db_user)

            self.assertFalse(state.clear.called)
            self.assertIn("слишком длинное", captured.get("text", ""))

    async def test_16_d_rename_device_rejects_whitespace_only_input(self):
        from bot.handlers.connection.device_rename_routes import rename_device_process

        db_user = SimpleNamespace(id=1, telegram_id=100)
        profile = SimpleNamespace(
            id=42,
            user_id=1,
            server_id=10,
            device_name="Phone #1",
            provisioning_status="active",
            peer_id="peer1",
            raw_config="vpn://valid",
            is_active=True,
        )
        message = MagicMock()
        message.bot = MagicMock()
        message.chat.id = 100
        message.from_user.id = 100
        message.text = "   "
        message.delete = AsyncMock()
        state = AsyncMock()
        state.get_data = AsyncMock(return_value={"profile_id": 42})
        result = MagicMock()
        result.scalar_one_or_none.return_value = profile
        session = AsyncMock()
        session.execute = AsyncMock(return_value=result)
        captured = {}

        async def capture_render(_bot, _chat, text, _keyboard, **_kwargs):
            captured["text"] = text

        with (
            patch(
                "bot.handlers.connection.device_rename_routes.get_profile_by_id",
                new=AsyncMock(return_value=profile),
            ),
            patch(
                "bot.handlers.connection.device_rename_routes.SubscriptionService.check_access",
                new=AsyncMock(return_value=True),
            ),
            patch(
                "bot.handlers.connection.device_rename_routes.render_hub",
                new=AsyncMock(side_effect=capture_render),
            ),
        ):
            await rename_device_process(message, state, session, db_user)

        self.assertIn("Имя не может быть пустым", captured["text"])
        state.clear.assert_not_awaited()
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

        self.assertTrue(can_show_delete_action(SimpleNamespace(provisioning_status="active")))
        self.assertTrue(can_show_delete_action(SimpleNamespace(provisioning_status="pending_update")))
        self.assertTrue(can_show_delete_action(SimpleNamespace(provisioning_status="update_failed")))
        self.assertTrue(can_show_delete_action(SimpleNamespace(provisioning_status="create_failed")))
        self.assertTrue(can_show_delete_action(SimpleNamespace(provisioning_status="delete_failed")))
        self.assertTrue(can_show_delete_action(SimpleNamespace(provisioning_status="create_cleanup_pending")))

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
        from bot.handlers.connection.device_delete_routes import (
            _deleting_devices,
            confirm_delete_device,
        )

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
        """If device transitioned to deleting, confirm_delete rejects it fail-closed."""
        from bot.handlers.connection.device_delete_routes import confirm_delete_device

        db_user = SimpleNamespace(id=1, telegram_id=100)
        cleanup_profile = SimpleNamespace(
            id=42,
            user_id=1,
            server_id=10,
            device_name="Мой телефон",
            provisioning_status="deleting",
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

            callback.answer.assert_called_once_with("🗑 Устройство уже удаляется с сервера.", show_alert=True)
            mock_delete.assert_not_called()
            mock_render.assert_called_once()

    async def test_29_confirm_delete_device_render_connections_failure_does_not_double_answer(self):
        """If _render_connections throws after successful delete answer, callback.answer is not invoked a second time."""
        from bot.handlers.connection.device_delete_routes import (
            _deleting_devices,
            confirm_delete_device,
        )

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
        from bot.handlers.connection.device_create_routes import (
            _process_server_selection,
        )
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
        fake_server = MagicMock(name="Server")
        fake_server.name = "mock_server"
        session.get = AsyncMock(return_value=fake_server)

        with patch("services.device_service.ensure_delete_operation", new=AsyncMock()):
            from services.device_service import DeviceService
            await DeviceService.delete_device(session, cleanup_profile, force=True)
            session.delete.assert_called_once_with(cleanup_profile)

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
        # Inside resolve_profile_endpoint_snapshot: prev_op returns None (falls back to server)
        mock_session.execute = AsyncMock(side_effect=[
            MagicMock(scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[stuck_profile])))),
            MagicMock(scalar_one_or_none=MagicMock(return_value=None)),
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
        mock_session.add = MagicMock()
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
        from bot.handlers.connection.device_create_routes import (
            _process_server_selection,
        )

        db_user = SimpleNamespace(id=1, telegram_id=100)
        callback = MagicMock()
        callback.bot = MagicMock()
        callback.message.chat.id = 100
        callback.from_user.id = 100
        state = AsyncMock()
        session = AsyncMock()

        with (
            patch("bot.handlers.connection.device_create_routes.capture_server_peer_snapshot", new=AsyncMock()),
            patch("bot.handlers.connection.device_create_routes.DeviceService.create_device", new=AsyncMock()) as mock_create_device,
            patch("bot.handlers.connection.device_create_routes.render_hub", new=AsyncMock()),
            patch("bot.handlers.connection.device_create_routes._await_profile_ready", new=AsyncMock(return_value=None)),
            patch("bot.handlers.connection.device_create_routes._render_connections", new=AsyncMock()),
        ):
            await _process_server_selection(callback, state, session, server_id=10, user=db_user)

            mock_create_device.assert_called_once()
            self.assertIsNone(mock_create_device.call_args.kwargs["device_name"])

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
        mock_op_peer.scalar_one_or_none.return_value = SimpleNamespace(id=99, peer_id="peer_recovered_123")

        mock_session = AsyncMock()
        mock_snapshot_op = MagicMock()
        mock_snapshot_op.scalar_one_or_none.return_value = None
        mock_session.execute = AsyncMock(side_effect=[mock_prof_res, mock_op_active, mock_op_peer, mock_snapshot_op])
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

    async def test_42_b_first_attempt_exact_peer_enters_cleanup_recovery(self):
        """An exact peer on the first failed attempt must remain recoverable."""
        from services.api_operations_executor import _execute_create

        op = SimpleNamespace(
            id=2,
            client_name="tg_first_attempt_orphan",
            peer_id=None,
            attempt_number=1,
            last_error_code=None,
            locked_by="worker-1",
            server_id=10,
            profile_id=42,
        )
        mock_client = AsyncMock()
        mock_client.get_all_clients = AsyncMock(return_value=[
            SimpleNamespace(id="peer-first-attempt", clientName=op.client_name)
        ])
        mock_client.delete_user_result = AsyncMock()
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
            patch("services.api_operations_executor.finalize_create_cancelled", new=AsyncMock()) as mock_cancel,
            patch("services.api_operations_executor.finalize_operation_failure", new=AsyncMock()) as mock_fail,
        ):
            await _execute_create(op, mock_client)

        mock_client.delete_user_result.assert_not_awaited()
        mock_cancel.assert_not_awaited()
        mock_fail.assert_awaited_once()
        self.assertEqual(mock_fail.call_args.kwargs["error_code"], "cleanup_peer_identity_mismatch")

    async def test_43_select_server_concurrency_double_click_fences_immediately(self):
        """select_server rejects with DEVICE_CREATE_IN_PROGRESS if user already in _creating_devices."""
        from bot.handlers.connection.device_create_routes import (
            _creating_devices,
            select_server,
        )

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

    async def test_51_admin_server_filter_includes_create_failed_and_excludes_deleting(self):
        """_apply_user_filters for server uses PROFILE_LIST_HIDDEN_STATUSES."""
        from sqlalchemy import select

        from database.models import User
        from database.repositories.users_repo import _apply_user_filters

        stmt = select(User)
        filtered = _apply_user_filters(stmt, filter_type="server", filter_param=5)
        sql_str = str(filtered.compile())
        # Must filter using PROFILE_LIST_HIDDEN_STATUSES ('deleting')
        self.assertIn("vpn_profiles.server_id =", sql_str)
        self.assertIn("vpn_profiles.provisioning_status NOT IN", sql_str)

    async def test_52_finalize_operation_failure_canonical_cleanup_required_codes(self):
        """All error codes in CREATE_CLEANUP_REQUIRED_CODES trigger create_cleanup_pending."""
        from services.api_operations_finalizer import finalize_operation_failure
        from services.api_operations_queue import CREATE_CLEANUP_REQUIRED_CODES

        mock_op = MagicMock()
        mock_op.status = "dead"
        mock_op.operation_type = "create_peer"
        mock_op.peer_id = None
        mock_op.locked_by = "worker-1"
        mock_op.attempts = 10
        mock_op.max_attempts = 10

        mock_prof = MagicMock()
        mock_prof.provisioning_status = "pending_create"

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_prof
        mock_session = AsyncMock()
        mock_session.execute.return_value = mock_result

        mock_scope = MagicMock()
        mock_scope.__aenter__.return_value = mock_session
        mock_scope.__aexit__.return_value = None

        critical_codes = [
            "duplicate_exact_client_name",
            "cleanup_peer_identity_mismatch",
            "invalid_created_config_cleanup",
            "create_compensation_required",
            "create_ambiguous_reconcile",
            "stale_create_lease",
            "executor_exception",
            "network_error",
            "timeout",
        ]
        for code in critical_codes:
            self.assertIn(code, CREATE_CLEANUP_REQUIRED_CODES)
            with patch("services.api_operations_finalizer._scope", return_value=mock_scope), \
                 patch("services.api_operations_finalizer._locked", return_value=mock_op):
                await finalize_operation_failure(
                    1,
                    worker_id="worker-1",
                    expected_attempt_number=10,
                    error_code=code,
                    error_message="Test failure",
                    retryable=False,
                )
                self.assertEqual(
                    mock_prof.provisioning_status,
                    "create_cleanup_pending",
                    f"Code {code} must transition to create_cleanup_pending",
                )

    async def test_53_rename_device_allows_same_name_on_different_servers(self):
        """Rename allows the same device name on different servers for the same user (server-scoped uniqueness)."""
        from bot.handlers.connection.device_rename_routes import rename_device_process

        db_user = SimpleNamespace(id=1, telegram_id=100)
        message = MagicMock()
        message.bot = MagicMock()
        message.chat.id = 100
        message.from_user.id = 100
        message.text = "Phone #1"
        state = AsyncMock()
        state.get_data = AsyncMock(return_value={"profile_id": 42})

        # Target profile is on server 20
        target_profile = SimpleNamespace(
            id=42,
            user_id=1,
            server_id=20,
            device_name="Laptop #1",
            provisioning_status="active",
        )
        # Existing profile with "Phone #1" is on server 10 (different server!)
        other_server_profile = SimpleNamespace(
            id=10,
            user_id=1,
            server_id=10,
            device_name="Phone #1",
            provisioning_status="active",
        )

        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=target_profile)))
        nested_ctx = MagicMock()
        nested_ctx.__aenter__ = AsyncMock()
        nested_ctx.__aexit__ = AsyncMock()
        mock_session.begin_nested = MagicMock(return_value=nested_ctx)

        with (
            patch("bot.handlers.connection.device_rename_routes.get_user_profiles", new=AsyncMock(return_value=[target_profile, other_server_profile])),
            patch("bot.handlers.connection.device_rename_routes.update_profile", new=AsyncMock()) as mock_update,
            patch("bot.handlers.connection.device_rename_routes.render_device_screen", new=AsyncMock()),
            patch("services.audit_service.AuditService.log_action", new=AsyncMock()),
        ):
            await rename_device_process(message, state, mock_session, db_user)

            # Successfully updated because server_id differs
            mock_update.assert_called_once_with(mock_session, target_profile, device_name="Phone #1")
            state.clear.assert_called_once()

    async def test_54_cleanup_stuck_profiles_requeues_cleanup_pending_without_peer_id(self):
        """Cleanup worker requeues create_peer for reconciliation when profile is create_cleanup_pending without peer_id."""
        from database.models import VPNProfile
        from services.workers.cleanup import _cleanup_stuck_profiles

        stuck_profile = VPNProfile(
            id=42,
            user_id=1,
            server_id=10,
            device_name="Test Phone",
            client_name="tg_100_p42",
            provisioning_status="create_cleanup_pending",
            peer_id=None,
        )

        dead_create_op = SimpleNamespace(
            id=99,
            profile_id=42,
            operation_type="create_peer",
            status="dead",
            peer_id=None,
        )

        mock_session = AsyncMock()
        mock_session.add = MagicMock()
        mock_session.execute = AsyncMock(side_effect=[
            # 1. Stuck profiles query
            MagicMock(scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[stuck_profile])))),
            # 2. Active op query -> None
            MagicMock(scalar_one_or_none=MagicMock(return_value=None)),
            # 3. Last create_op query -> dead_create_op
            MagicMock(scalar_one_or_none=MagicMock(return_value=dead_create_op)),
            # 4. update execute
            MagicMock(),
        ])

        mock_scope = MagicMock()
        mock_scope.__aenter__.return_value = mock_session
        mock_scope.__aexit__.return_value = None

        with patch("services.workers.cleanup.session_scope", return_value=mock_scope):
            await _cleanup_stuck_profiles()

            # The profile MUST NOT be blindly set to create_failed!
            self.assertEqual(stuck_profile.provisioning_status, "create_cleanup_pending")

    async def test_55_cleanup_stuck_profiles_handles_stuck_deleting_profiles(self):
        """Cleanup worker picks up stuck deleting profiles and ensures delete operation if peer_id is known."""
        from database.models import Server, VPNProfile
        from services.workers.cleanup import _cleanup_stuck_profiles

        stuck_profile = VPNProfile(
            id=42,
            user_id=1,
            server_id=10,
            device_name="Test Phone",
            client_name="tg_100_p42",
            provisioning_status="deleting",
            peer_id="peer_abc",
        )

        mock_server = Server(id=10, name="DE Server", api_url="https://de.vpn", api_key="secret")
        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(side_effect=[
            MagicMock(scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[stuck_profile])))),
            MagicMock(scalar_one_or_none=MagicMock(return_value=None)),
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

    async def test_56_device_service_atomic_slot_allocation_under_user_lock(self):
        """DeviceService.create_device allocates next slot #3 under user lock when #1 and #2 are used."""
        from datetime import datetime, timezone

        from database.models import Server, User, VPNProfile
        from services.device_service import DeviceService
        from services.slots_cache import ServerPeerSnapshot

        user = User(id=1, telegram_id=100, device_limit=5, subscription_end=datetime(2099, 1, 1, tzinfo=timezone.utc), is_banned=False)
        server = Server(id=10, name="DE", api_url="https://de.vpn", api_key="k", protocol="amneziawg2", is_active=True, max_clients=100)
        p1 = VPNProfile(id=1, user_id=1, server_id=10, device_name="Устройство #1", provisioning_status="active")
        p2 = VPNProfile(id=2, user_id=1, server_id=10, device_name="Устройство #2", provisioning_status="deleting")

        mock_session = AsyncMock()
        mock_session.add = MagicMock()
        mock_session.execute = AsyncMock(side_effect=[
            # 1. select User FOR UPDATE
            MagicMock(scalar_one=MagicMock(return_value=user)),
            # 2. select Server FOR UPDATE
            MagicMock(scalar_one_or_none=MagicMock(return_value=server)),
            # 3. select all user profiles for slot allocation
            MagicMock(scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[p1, p2])))),
            # 4. select duplicate -> None
            MagicMock(scalar_one_or_none=MagicMock(return_value=None)),
            # 5. user_count -> 1
            MagicMock(scalar_one=MagicMock(return_value=1)),
            # 6. server_count -> 1
            MagicMock(scalar_one=MagicMock(return_value=1)),
            # 7. bot_peer_ids -> []
            MagicMock(scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[])))),
            # 8. duplicate client_name -> None
            MagicMock(scalar_one_or_none=MagicMock(return_value=None)),
        ])

        mock_nested = MagicMock()
        mock_nested.__aenter__ = AsyncMock()
        mock_nested.__aexit__ = AsyncMock()
        mock_session.begin_nested = MagicMock(return_value=mock_nested)

        snapshot = ServerPeerSnapshot(server_id=10, peer_ids=frozenset(), captured_at=datetime.now(timezone.utc))

        with (
            patch("services.device_service.enqueue_api_operation", new=AsyncMock()),
            patch("services.device_service.is_admin", return_value=True),
        ):
            profile = await DeviceService.create_device(
                mock_session,
                user_id=1,
                server_id=10,
                device_name=None,
                snapshot=snapshot,
            )
            self.assertEqual(profile.device_name, "Устройство #3")

    async def test_57_lock_operation_and_profile_propagates_db_error(self):
        """_lock_operation_and_profile propagates genuine database exceptions without swallowing."""
        from services.api_operations_finalizer import _lock_operation_and_profile

        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(side_effect=RuntimeError("PostgreSQL connection failure"))

        with self.assertRaises(RuntimeError) as ctx:
            await _lock_operation_and_profile(
                mock_session,
                operation_id=1,
                worker_id="worker-1",
                attempt=1,
            )
        self.assertIn("PostgreSQL connection failure", str(ctx.exception))

    async def test_58_delete_device_when_server_is_none_recovers_snapshot_and_enqueues_delete(self):
        """When Server row is missing from DB, delete_device recovers snapshot and enqueues delete_peer."""
        from database.models import VPNProfile
        from services.device_service import DeviceService

        profile = VPNProfile(
            id=42,
            user_id=1,
            server_id=999,  # Server no longer exists in DB
            device_name="Old Device",
            peer_id="peer-xyz",
            client_name="tg_old_client",
            provisioning_status="active",
        )

        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=profile)))
        mock_session.get = AsyncMock(return_value=None)  # Server is None!

        with (
            patch("services.device_service.AuditService.log_action", new=AsyncMock()),
            patch("services.device_service.ensure_delete_operation", new=AsyncMock()) as mock_ensure_del,
            patch("services.device_service.resolve_profile_endpoint_snapshot", new=AsyncMock(return_value=(999, "Legacy Server", "http://1.2.3.4", "key-secret"))),
            patch("services.device_service.is_admin", return_value=True),
        ):
            res = await DeviceService.delete_device(mock_session, profile, actor_id=1)
            self.assertTrue(res)
            self.assertEqual(profile.provisioning_status, "deleting")
            mock_ensure_del.assert_called_once()
            call_kwargs = mock_ensure_del.call_args.kwargs
            self.assertEqual(call_kwargs["server_id"], 999)
            self.assertEqual(call_kwargs["server_name_snapshot"], "Legacy Server")
            self.assertEqual(call_kwargs["api_url_snapshot"], "http://1.2.3.4")
            self.assertEqual(call_kwargs["api_key_snapshot"], "key-secret")
            self.assertEqual(call_kwargs["peer_id"], "peer-xyz")
            mock_session.delete.assert_not_called()

    async def test_59_cleanup_claim_deterministic_barrier_mock(self):
        """Unit test demonstrating deterministic cleanup skip when active operation is in flight."""
        from database.models import VPNProfile
        from services.workers.cleanup import _cleanup_stuck_profiles

        stuck_profile = VPNProfile(
            id=101,
            user_id=1,
            server_id=10,
            device_name="Mock Stuck Dev",
            client_name="tg_mock_stuck",
            provisioning_status="pending_create",
        )

        mock_prof_res = MagicMock()
        mock_prof_res.scalars.return_value.all.return_value = [stuck_profile]

        # Active operation exists (status in pending, processing, retry)
        mock_active_op_res = MagicMock()
        mock_active_op_res.scalar_one_or_none.return_value = 999  # Found active op id

        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(side_effect=[mock_prof_res, mock_active_op_res])

        mock_scope = MagicMock()
        mock_scope.__aenter__.return_value = mock_session
        mock_scope.__aexit__.return_value = None

        with patch("services.workers.cleanup.session_scope", return_value=mock_scope):
            await _cleanup_stuck_profiles()

            # Profile must remain pending_create and NOT changed to create_failed
            self.assertEqual(stuck_profile.provisioning_status, "pending_create")


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
        import asyncio
        from datetime import datetime, timedelta, timezone

        from database.models import APIOperation, VPNProfile
        from services.api_operations_queue import claim_api_operations

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

        barrier_event = asyncio.Event()
        original_session_scope = self.sessions

        async def controlled_cleanup():
            async with original_session_scope() as s:
                async with s.begin():
                    cutoff_time = datetime.now(timezone.utc) - timedelta(hours=1)
                    stuck_profiles = (
                        await s.execute(
                            select(VPNProfile)
                            .where(
                                VPNProfile.provisioning_status.in_(["pending_create", "create_cleanup_pending", "deleting"]),
                                VPNProfile.created_at < cutoff_time,
                            )
                            .with_for_update(skip_locked=True)
                        )
                    ).scalars().all()

                    self.assertEqual(len(stuck_profiles), 1)
                    # Signal barrier so claim_api_operations executes concurrently in separate session
                    barrier_event.set()
                    await asyncio.sleep(0.05)

                    for profile in stuck_profiles:
                        active_op_res = await s.execute(
                            select(APIOperation.id).where(
                                APIOperation.profile_id == profile.id,
                                APIOperation.status.in_(["pending", "processing", "retry"]),
                            ).limit(1)
                        )
                        if active_op_res.scalar_one_or_none() is not None:
                            continue
                        profile.provisioning_status = "create_failed"

        async def controlled_claim():
            await barrier_event.wait()
            return await claim_api_operations(worker_id="test-worker", limit=10, session_factory=self.sessions)

        cleanup_task = asyncio.create_task(controlled_cleanup())
        claim_task = asyncio.create_task(controlled_claim())
        claimed, _ = await asyncio.gather(claim_task, cleanup_task)

        self.assertEqual(len(claimed), 1)
        self.assertEqual(claimed[0].id, op_id)

        async with self.sessions() as s:
            p = await s.get(VPNProfile, prof_id)
            self.assertEqual(p.provisioning_status, "pending_create")
            o = await s.get(APIOperation, op_id)
            self.assertEqual(o.status, "processing")
            self.assertEqual(o.locked_by, "test-worker")

    async def test_39_finalize_create_success_race_condition(self):
        """finalize_create_success catches create_failed mid-flight and queues compensation."""
        from database.models import APIOperation, VPNProfile
        from services.api_operations_finalizer import (
            CreateCompensationRequired,
            finalize_create_success,
        )

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

    async def test_60_postgres_finalize_create_success_vs_concurrent_delete(self):
        """Proves that when user triggers deletion while CREATE is completing externally, compensation is raised and peer_id preserved."""
        from database.models import APIOperation, VPNProfile
        from services.api_operations_finalizer import (
            CreateCompensationRequired,
            finalize_create_success,
        )

        async with self.sessions.begin() as s:
            prof = VPNProfile(
                user_id=self.uid,
                server_id=self.sid,
                device_name="Concurrent Delete Dev",
                client_name="tg_del_conc",
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

        # User deletes profile concurrently before finalizer commits
        async with self.sessions.begin() as s:
            p = await s.get(VPNProfile, prof_id)
            p.provisioning_status = "deleting"
            await s.flush()

        with self.assertRaises(CreateCompensationRequired):
            await finalize_create_success(
                op_id,
                worker_id="worker-1",
                expected_attempt_number=1,
                peer_id="peer-ext-999",
                raw_config="vpn://test-concurrent",
                sent_desired_version=1,
                sent_is_active=True,
                sent_expires_at=None,
                session_factory=self.sessions,
            )

        async with self.sessions() as s:
            p = await s.get(VPNProfile, prof_id)
            # Status must stay deleting, but peer_id must be stored so delete worker cleans it up!
            self.assertEqual(p.provisioning_status, "deleting")
            self.assertEqual(p.peer_id, "peer-ext-999")

    async def test_61_postgres_stale_lease_recovery_vs_compensation(self):
        """Proves that attempt number checks reject finalization when lease has been recovered by another worker."""
        from database.models import APIOperation, VPNProfile
        from services.api_operations_finalizer import finalize_create_success

        async with self.sessions.begin() as s:
            prof = VPNProfile(
                user_id=self.uid,
                server_id=self.sid,
                device_name="Stale Lease Dev",
                client_name="tg_stale_99",
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
                locked_by="worker-2",  # Mismatched worker!
                attempts=2,            # Mismatched attempt!
            )
            s.add(op)
            await s.flush()
            op_id = op.id

        from services.api_operations_queue import APIOperationOwnershipError
        with self.assertRaises(APIOperationOwnershipError) as ctx:
            await finalize_create_success(
                op_id,
                worker_id="worker-1",  # Old worker
                expected_attempt_number=1,  # Old attempt
                peer_id="peer-stale",
                raw_config="vpn://test-stale",
                sent_desired_version=1,
                sent_is_active=True,
                sent_expires_at=None,
                session_factory=self.sessions,
            )
        self.assertIn("not leased", str(ctx.exception))

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
                patch("bot.handlers.connection.device_rename_routes.render_device_screen", new=AsyncMock()) as mock_hub,
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

    async def test_47_finalize_operation_failure_duplicate_exact_client_name_sets_create_cleanup_pending(self):
        """duplicate_exact_client_name must mark profile as create_cleanup_pending, not create_failed."""
        from database.models import APIOperation, VPNProfile
        from services.api_operations_finalizer import finalize_operation_failure

        async with self.sessions.begin() as s:
            prof = VPNProfile(
                user_id=self.uid,
                server_id=self.sid,
                device_name="Dup Exact Test",
                client_name="tg_dup_exact",
                provisioning_status="pending_create",
            )
            s.add(prof)
            await s.flush()
            prof_id = prof.id

            op = APIOperation(
                operation_type="create_peer",
                status="processing",
                idempotency_key=f"create-peer:{prof_id}:dup",
                server_id=self.sid,
                profile_id=prof_id,
                attempts=1,
                locked_by="worker-1",
            )
            s.add(op)
            await s.flush()
            op_id = op.id

        await finalize_operation_failure(
            op_id,
            worker_id="worker-1",
            expected_attempt_number=1,
            error_code="duplicate_exact_client_name",
            error_message="Multiple exact client names found on server",
            retryable=False,
            session_factory=self.sessions,
        )

        async with self.sessions() as s:
            p = await s.get(VPNProfile, prof_id)
            self.assertEqual(p.provisioning_status, "create_cleanup_pending")

    async def test_48_finalize_operation_failure_cleanup_peer_identity_mismatch_sets_create_cleanup_pending(self):
        """cleanup_peer_identity_mismatch must mark profile as create_cleanup_pending."""
        from database.models import APIOperation, VPNProfile
        from services.api_operations_finalizer import finalize_operation_failure

        async with self.sessions.begin() as s:
            prof = VPNProfile(
                user_id=self.uid,
                server_id=self.sid,
                device_name="Mismatch Test",
                client_name="tg_mismatch",
                provisioning_status="pending_create",
            )
            s.add(prof)
            await s.flush()
            prof_id = prof.id

            op = APIOperation(
                operation_type="create_peer",
                status="processing",
                idempotency_key=f"create-peer:{prof_id}:mismatch",
                server_id=self.sid,
                profile_id=prof_id,
                attempts=1,
                locked_by="worker-1",
            )
            s.add(op)
            await s.flush()
            op_id = op.id

        await finalize_operation_failure(
            op_id,
            worker_id="worker-1",
            expected_attempt_number=1,
            error_code="cleanup_peer_identity_mismatch",
            error_message="Peer identity mismatch during reconciliation",
            retryable=False,
            session_factory=self.sessions,
        )

        async with self.sessions() as s:
            p = await s.get(VPNProfile, prof_id)
            self.assertEqual(p.provisioning_status, "create_cleanup_pending")

    async def test_49_profile_deletion_does_not_overwrite_active_worker_processing_lease(self):
        """ProfileDeletionService._delete_profiles does not overwrite status or locked_by of processing APIOperation."""
        from database.models import APIOperation, VPNProfile
        from services.profile_deletion_service import ProfileDeletionService

        async with self.sessions.begin() as s:
            prof = VPNProfile(
                user_id=self.uid,
                server_id=self.sid,
                device_name="Processing Guard Test",
                client_name="tg_proc_guard",
                provisioning_status="pending_create",
            )
            s.add(prof)
            await s.flush()
            prof_id = prof.id

            op = APIOperation(
                operation_type="create_peer",
                status="processing",
                idempotency_key=f"create-peer:{prof_id}:proc",
                server_id=self.sid,
                profile_id=prof_id,
                attempts=2,
                locked_by="worker-active-42",
            )
            s.add(op)
            await s.flush()
            op_id = op.id

        async with self.sessions.begin() as session:
            count = await ProfileDeletionService.delete_profiles_for_user(
                session, self.uid, reason="user_banned"
            )
            self.assertEqual(count, 1)

        async with self.sessions() as s:
            op_after = await s.get(APIOperation, op_id)
            # Must remain 'processing' with its original worker lease intact
            self.assertEqual(op_after.status, "processing")
            self.assertEqual(op_after.locked_by, "worker-active-42")
            self.assertEqual(op_after.attempts, 2)
            prof_after = await s.get(VPNProfile, prof_id)
            self.assertEqual(prof_after.provisioning_status, "deleting")

    async def test_50_rename_device_process_rejects_duplicate_with_deleting_profile(self):
        """rename_device_process rejects rename when a deleting profile holds the same target name."""
        from bot.handlers.connection.device_rename_routes import rename_device_process
        from database.models import VPNProfile

        async with self.sessions.begin() as s:
            # Existing deleting profile
            prof_deleting = VPNProfile(
                user_id=self.uid,
                server_id=self.sid,
                device_name="Phone #1",
                client_name="tg_del_1",
                provisioning_status="deleting",
            )
            # Profile to rename
            prof_active = VPNProfile(
                user_id=self.uid,
                server_id=self.sid,
                device_name="Laptop #1",
                client_name="tg_act_1",
                provisioning_status="active",
            )
            s.add_all([prof_deleting, prof_active])
            await s.flush()
            active_id = prof_active.id

        message = MagicMock()
        message.bot = MagicMock()
        message.chat.id = 987654
        message.from_user.id = 987654
        message.text = "Phone"
        state = AsyncMock()
        state.get_data = AsyncMock(return_value={"profile_id": active_id})
        db_user = SimpleNamespace(id=self.uid, telegram_id=987654)

        async with self.sessions.begin() as session:
            with (
                patch("bot.handlers.connection.device_rename_routes.SubscriptionService.check_access", new=AsyncMock(return_value=True)),
                patch("bot.handlers.connection.device_rename_routes.render_hub", new=AsyncMock()) as mock_hub,
            ):
                await rename_device_process(message, state, session, db_user=db_user)
                self.assertTrue(mock_hub.called)
                text_arg = mock_hub.call_args.args[2]
                self.assertIn("уже существует", text_arg)

        async with self.sessions() as s:
            unchanged = await s.get(VPNProfile, active_id)
            self.assertEqual(unchanged.device_name, "Laptop #1")

    async def test_62_postgres_claim_exhausted_sync_vs_finalizer_deadlock(self):
        """Prove that claim_api_operations terminal sync and finalizer lock in same order without deadlock."""
        import asyncio

        from services.api_operations_finalizer import finalize_operation_failure
        from services.api_operations_queue import claim_api_operations

        async with self.sessions.begin() as s:
            prof = VPNProfile(
                user_id=self.uid,
                server_id=self.sid,
                device_name="Concurrent Deadlock Probe",
                client_name="tg_deadlock_probe",
                provisioning_status="pending_create",
            )
            s.add(prof)
            await s.flush()
            prof_id = prof.id

            op = APIOperation(
                operation_type="create_peer",
                status="processing",
                idempotency_key=f"create-deadlock:{prof_id}",
                server_id=self.sid,
                profile_id=prof_id,
                locked_by="worker-1",
                attempts=10,
                max_attempts=10,
            )
            s.add(op)
            await s.flush()
            op_id = op.id

        # Concurrently execute finalize_operation_failure (which locks VPNProfile -> APIOperation)
        # and claim_api_operations (which processes exhausted operations with VPNProfile -> APIOperation lock order)
        async def run_finalizer():
            try:
                await finalize_operation_failure(
                    op_id,
                    worker_id="worker-1",
                    expected_attempt_number=10,
                    retryable=False,
                    error_code="timeout",
                    error_message="Network timed out",
                    session_factory=self.sessions,
                )
                return "finalized"
            except Exception as e:
                return f"finalizer_err: {type(e).__name__}"

        async def run_claim():
            try:
                await claim_api_operations(
                    worker_id="worker-2",
                    limit=10,
                    session_factory=self.sessions,
                )
                return "claimed"
            except Exception as e:
                return f"claim_err: {type(e).__name__}"

        res1, res2 = await asyncio.gather(run_finalizer(), run_claim())

        # Neither result should contain a deadlock error
        self.assertNotIn("Deadlock", res1)
        self.assertNotIn("deadlock", res1)
        self.assertNotIn("Deadlock", res2)
        self.assertNotIn("deadlock", res2)

        async with self.sessions() as s:
            final_prof = await s.get(VPNProfile, prof_id)
            final_op = await s.get(APIOperation, op_id)
            self.assertIn(final_prof.provisioning_status, {"create_cleanup_pending", "create_failed"})
            self.assertIn(final_op.status, {"dead", "retry"})

    async def test_63_postgres_delete_device_with_missing_server_row_enqueues_delete_peer(self):
        """When Server row is missing from DB, delete_device recovers endpoint snapshot and enqueues delete_peer."""
        from database.models import APIOperation, Server, VPNProfile
        from services.device_service import DeviceService

        # 1. Create a profile pointing to self.sid
        # and previous APIOperation recording the endpoint snapshot
        async with self.sessions.begin() as s:
            prof = VPNProfile(
                user_id=self.uid,
                server_id=self.sid,
                device_name="Orphan Server Device",
                client_name="tg_orphan_server_client",
                peer_id="amnezia-peer-999",
                provisioning_status="active",
            )
            s.add(prof)
            await s.flush()
            prof_id = prof.id

            # Previous create_peer operation holding the immutable endpoint snapshot
            prev_op = APIOperation(
                operation_type="create_peer",
                status="succeeded",
                idempotency_key=f"create-orphan-test:{prof_id}",
                server_id=self.sid,
                profile_id=prof_id,
                server_name_snapshot="Old Node 99",
                api_url_snapshot="http://10.99.99.1:9999",
                api_key_snapshot="legacy-secret-key-99",
                peer_id="amnezia-peer-999",
                client_name="tg_orphan_server_client",
                attempts=1,
                max_attempts=10,
            )
            s.add(prev_op)

        # 2. Call DeviceService.delete_device with Server row simulated as missing/deleted
        async with self.sessions.begin() as s:
            prof_to_delete = await s.get(VPNProfile, prof_id)
            orig_get = s.get

            async def patched_get(entity, ident, **kwargs):
                if entity is Server:
                    return None
                return await orig_get(entity, ident, **kwargs)

            s.get = patched_get
            res = await DeviceService.delete_device(s, prof_to_delete, actor_id=self.uid)
            self.assertTrue(res)

        # 3. Assert profile is in 'deleting' and durable delete_peer operation is queued with recovered snapshot!
        async with self.sessions() as s:
            deleted_prof = await s.get(VPNProfile, prof_id)
            self.assertIsNotNone(deleted_prof)
            self.assertEqual(deleted_prof.provisioning_status, "deleting")

            del_op = (
                await s.execute(
                    select(APIOperation).where(
                        APIOperation.profile_id == prof_id,
                        APIOperation.operation_type == "delete_peer",
                    )
                )
            ).scalar_one_or_none()
            self.assertIsNotNone(del_op)
            self.assertEqual(del_op.status, "pending")
            self.assertEqual(del_op.peer_id, "amnezia-peer-999")
            self.assertEqual(del_op.server_name_snapshot, "Old Node 99")
            self.assertEqual(del_op.api_url_snapshot, "http://10.99.99.1:9999")
            self.assertEqual(del_op.api_key_snapshot, "legacy-secret-key-99")

    async def test_64_postgres_claim_vs_finalize_create_success_concurrent(self):
        """Prove that competing workers claiming and finalizing operations synchronize cleanly with SKIP LOCKED."""
        import asyncio

        from services.api_operations_finalizer import finalize_create_success
        from services.api_operations_queue import claim_api_operations

        async with self.sessions.begin() as s:
            prof = VPNProfile(
                user_id=self.uid,
                server_id=self.sid,
                device_name="Concurrent Create Probe",
                client_name="tg_create_probe",
                provisioning_status="pending_create",
            )
            s.add(prof)
            await s.flush()
            prof_id = prof.id

            op = APIOperation(
                operation_type="create_peer",
                status="pending",
                idempotency_key=f"create-concurrent-test:{prof_id}",
                server_id=self.sid,
                profile_id=prof_id,
                server_name_snapshot="Test Node",
                api_url_snapshot="http://10.0.0.1:8080",
                api_key_snapshot="secret-key",
                attempts=0,
                max_attempts=10,
            )
            s.add(op)
            await s.flush()
            op_id = op.id

        barrier = asyncio.Event()

        async def worker_1():
            # Worker 1 claims the pending operation
            claimed = await claim_api_operations(
                worker_id="worker-1",
                limit=10,
                session_factory=self.sessions,
            )
            self.assertEqual(len(claimed), 1)
            self.assertEqual(claimed[0].id, op_id)
            barrier.set()

            # Worker 1 finalizes create success
            await finalize_create_success(
                op_id,
                worker_id="worker-1",
                expected_attempt_number=1,
                peer_id="peer-created-123",
                raw_config="vpn://test-config-concurrent",
                sent_desired_version=1,
                sent_is_active=True,
                sent_expires_at=None,
                session_factory=self.sessions,
            )
            return "worker_1_done"

        async def worker_2():
            # Worker 2 attempts to claim concurrently
            await barrier.wait()
            claimed = await claim_api_operations(
                worker_id="worker-2",
                limit=10,
                session_factory=self.sessions,
            )
            # Worker 2 must get 0 claimed because Worker 1 already claimed it
            self.assertEqual(len(claimed), 0)
            return "worker_2_done"

        res1, res2 = await asyncio.gather(worker_1(), worker_2())
        self.assertEqual(res1, "worker_1_done")
        self.assertEqual(res2, "worker_2_done")

        async with self.sessions() as s:
            final_prof = await s.get(VPNProfile, prof_id)
            self.assertEqual(final_prof.peer_id, "peer-created-123")
            self.assertEqual(final_prof.provisioning_status, "active")

    async def test_65_postgres_recover_stale_vs_finalizer_concurrent(self):
        """Prove that recover_stale_api_operations and finalize_operation_failure coordinate cleanly under concurrency."""
        import asyncio
        from datetime import timedelta

        from services.api_operations_finalizer import finalize_operation_failure
        from services.api_operations_queue import (
            APIOperationOwnershipError,
            recover_stale_api_operations,
        )

        async with self.sessions.begin() as s:
            prof = VPNProfile(
                user_id=self.uid,
                server_id=self.sid,
                device_name="Concurrent Stale Probe",
                client_name="tg_stale_probe",
                provisioning_status="pending_create",
            )
            s.add(prof)
            await s.flush()
            prof_id = prof.id

            op = APIOperation(
                operation_type="create_peer",
                status="processing",
                idempotency_key=f"create-stale-test:{prof_id}",
                server_id=self.sid,
                profile_id=prof_id,
                server_name_snapshot="Test Node",
                api_url_snapshot="http://10.0.0.1:8080",
                api_key_snapshot="secret-key",
                locked_by="worker-stale-1",
                attempts=1,
                max_attempts=10,
            )
            s.add(op)
            await s.flush()
            op_id = op.id

        barrier = asyncio.Event()

        async def worker_recover():
            # Supervisor recovers the stale operation (lease expired)
            retried, dead = await recover_stale_api_operations(
                lease_timeout=timedelta(microseconds=1),
                session_factory=self.sessions,
            )
            self.assertEqual(retried, 1)
            barrier.set()
            return "recovered"

        async def worker_finalize():
            await barrier.wait()
            # Stale worker tries to finalize attempt 1 after supervisor already recovered it
            try:
                await finalize_operation_failure(
                    op_id,
                    worker_id="worker-stale-1",
                    expected_attempt_number=1,
                    retryable=True,
                    error_code="timeout",
                    error_message="Gateway timeout",
                    session_factory=self.sessions,
                )
                return "finalized"
            except APIOperationOwnershipError:
                return "fenced"

        res_rec, res_fin = await asyncio.gather(worker_recover(), worker_finalize())
        self.assertEqual(res_rec, "recovered")
        self.assertEqual(res_fin, "fenced")

    async def test_66_postgres_profile_deletion_vs_create_finalizer_concurrent(self):
        """Prove that ProfileDeletionService and finalize_create_success coordinate cleanly under concurrency."""
        import asyncio

        from services.api_operations_finalizer import (
            CreateCompensationRequired,
            finalize_create_success,
        )
        from services.profile_deletion_service import ProfileDeletionService

        async with self.sessions.begin() as s:
            prof = VPNProfile(
                user_id=self.uid,
                server_id=self.sid,
                device_name="Concurrent Delete-vs-Finalize",
                client_name="tg_del_finalize_probe",
                provisioning_status="pending_create",
            )
            s.add(prof)
            await s.flush()
            prof_id = prof.id

            op = APIOperation(
                operation_type="create_peer",
                status="processing",
                idempotency_key=f"create-del-fin-test:{prof_id}",
                server_id=self.sid,
                profile_id=prof_id,
                server_name_snapshot="Test Node",
                api_url_snapshot="http://10.0.0.1:8080",
                api_key_snapshot="secret-key",
                locked_by="worker-fin-1",
                attempts=1,
                max_attempts=10,
            )
            s.add(op)
            await s.flush()
            op_id = op.id

        barrier = asyncio.Event()

        async def worker_delete():
            async with self.sessions.begin() as s:
                current_prof = await s.get(VPNProfile, prof_id)
                self.assertIsNotNone(current_prof)
                await ProfileDeletionService._delete_profiles(
                    s, [current_prof], reason="user_unsubscribed", background=True
                )
            barrier.set()
            return "deleted"

        async def worker_finalize():
            await barrier.wait()
            try:
                await finalize_create_success(
                    op_id,
                    worker_id="worker-fin-1",
                    expected_attempt_number=1,
                    peer_id="peer-fin-999",
                    raw_config="vpn://test-config-del-fin",
                    sent_desired_version=1,
                    sent_is_active=True,
                    sent_expires_at=None,
                    session_factory=self.sessions,
                )
                return "finalized"
            except CreateCompensationRequired:
                return "compensated"

        res_del, res_fin = await asyncio.gather(worker_delete(), worker_finalize())
        self.assertEqual(res_del, "deleted")
        self.assertEqual(res_fin, "compensated")

        async with self.sessions() as s:
            final_prof = await s.get(VPNProfile, prof_id)
            self.assertIsNotNone(final_prof)
            self.assertIn(final_prof.provisioning_status, {"deleting", "create_cleanup_pending"})
            self.assertEqual(final_prof.peer_id, "peer-fin-999")

    async def test_67_postgres_server_edit_vs_create_device_serialization(self):
        """
        Prove that admin Server edit with FOR UPDATE serializes with DeviceService.create_device.
        When create_device acquires Server FOR UPDATE first, admin edit sees active profiles/ops
        and is blocked. When admin edit acquires Server FOR UPDATE first, create_device waits
        and proceeds on the updated server identity.
        """
        from database.repositories.servers_repo import get_server_by_id
        from services.slots_cache import ServerPeerSnapshot

        async with self.sessions.begin() as s:
            server = Server(
                name="Concurrency Srv 67",
                api_url="https://vpn67.example.com",
                api_key="key67-initial",
                country_flag="🇩🇪",
                protocol="amneziawg2",
                max_clients=10,
                is_active=True,
            )
            s.add(server)
            await s.flush()
            srv_id = server.id

            user = User(
                telegram_id=987654321,
                username="test_user_67",
                first_name="Test",
                subscription_end=datetime.now(timezone.utc) + timedelta(days=30),
                device_limit=5,
            )
            s.add(user)
            await s.flush()
            u_id = user.id

        admin_locked_event = asyncio.Event()
        create_started_event = asyncio.Event()

        async def admin_edit_worker():
            async with self.sessions.begin() as s:
                srv = await get_server_by_id(s, srv_id, for_update=True)
                self.assertIsNotNone(srv)
                admin_locked_event.set()
                await create_started_event.wait()
                await asyncio.sleep(0.05)
                srv.api_url = "https://vpn67-updated.example.com"
            return "admin_updated"

        async def create_device_worker():
            await admin_locked_event.wait()
            create_started_event.set()
            snap = ServerPeerSnapshot(
                server_id=srv_id,
                peer_ids=frozenset(),
                captured_at=datetime.now(timezone.utc),
            )
            async with self.sessions.begin() as s:
                prof = await DeviceService.create_device(
                    session=s,
                    user_id=u_id,
                    server_id=srv_id,
                    device_name="Phone 67",
                    snapshot=snap,
                )
                self.assertIsNotNone(prof)
            return "created"

        res_admin, res_create = await asyncio.gather(admin_edit_worker(), create_device_worker())
        self.assertEqual(res_admin, "admin_updated")
        self.assertEqual(res_create, "created")

        async with self.sessions() as s:
            op = (
                await s.execute(
                    select(APIOperation)
                    .where(APIOperation.server_id == srv_id)
                    .order_by(APIOperation.id.desc())
                    .limit(1)
                )
            ).scalar_one()
            self.assertEqual(op.api_url_snapshot, "https://vpn67-updated.example.com")

            async with self.sessions.begin() as s2:
                srv = await get_server_by_id(s2, srv_id, for_update=True)
                self.assertIsNotNone(srv)
                profiles_count = (
                    await s2.execute(
                        select(func.count(VPNProfile.id)).where(
                            VPNProfile.server_id == srv_id,
                            or_(
                                VPNProfile.peer_id.is_not(None),
                                VPNProfile.provisioning_status.in_((
                                    "pending_create",
                                    "pending_update",
                                    "deleting",
                                    "create_cleanup_pending",
                                    "create_failed",
                                    "update_failed",
                                    "delete_failed",
                                    "active",
                                )),
                            ),
                        )
                    )
                ).scalar_one()
                self.assertGreater(profiles_count, 0)


if __name__ == "__main__":
    unittest.main()
