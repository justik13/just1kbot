import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

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
        )

        mock_session = AsyncMock()
        mock_session.get = AsyncMock(return_value=active_profile)
        mock_session.close = AsyncMock()

        with patch("bot.handlers.connection.device_create_routes.get_session", new=AsyncMock(return_value=mock_session)):
            res = await _await_profile_ready(42, timeout_seconds=1.0, poll_interval=0.05)
            self.assertIsNotNone(res)
            self.assertEqual(res.provisioning_status, "active")
            self.assertTrue(mock_session.close.called)


if __name__ == "__main__":
    unittest.main()
