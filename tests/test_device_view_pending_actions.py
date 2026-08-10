import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from bot.handlers.connection.device_view_routes import manage_device


class TestDeviceViewPendingActions(unittest.IsolatedAsyncioTestCase):
    async def test_pending_profile_keeps_same_device_actions_as_ready_profile(self):
        db_user = SimpleNamespace(id=7, telegram_id=700)
        server = SimpleNamespace(
            country_flag="🇩🇪",
            name="Germany",
            protocol="amneziawg2",
        )

        pending_profile = SimpleNamespace(
            id=1,
            user_id=7,
            server_id=9,
            device_name="Устройство #1",
            provisioning_status="pending_create",
            peer_id=None,
            raw_config=None,
            traffic_down=0,
            traffic_up=0,
            last_connected=None,
        )
        ready_profile = SimpleNamespace(
            id=1,
            user_id=7,
            server_id=9,
            device_name="Устройство #1",
            provisioning_status="active",
            peer_id="peer-1",
            raw_config="amnezia://ready",
            traffic_down=0,
            traffic_up=0,
            last_connected=None,
        )

        def make_callback():
            callback = MagicMock()
            callback.data = "manage_device:1"
            callback.message.chat.id = 700
            callback.message.message_id = 42
            callback.answer = AsyncMock()
            return callback

        rendered_keyboards = []

        async def capture_render_hub(_bot, _chat_id, _text, keyboard, **_kwargs):
            rendered_keyboards.append(keyboard)

        state = AsyncMock()
        session = AsyncMock()

        with (
            patch(
                "bot.handlers.connection.device_view_routes.get_profile_by_id",
                side_effect=[pending_profile, ready_profile],
            ),
            patch(
                "bot.handlers.connection.device_view_routes.get_server_by_id",
                return_value=server,
            ),
            patch(
                "bot.handlers.connection.device_view_routes.SubscriptionService.check_access",
                new=AsyncMock(return_value=True),
            ),
            patch(
                "bot.handlers.connection.device_view_routes.render_hub",
                new=AsyncMock(side_effect=capture_render_hub),
            ),
        ):
            await manage_device(make_callback(), state, session, db_user)
            await manage_device(make_callback(), state, session, db_user)

        self.assertEqual(len(rendered_keyboards), 2)
        self.assertEqual(
            rendered_keyboards[0].model_dump(),
            rendered_keyboards[1].model_dump(),
        )
        callback_data = {
            button.callback_data
            for row in rendered_keyboards[0].inline_keyboard
            for button in row
        }
        self.assertIn("show_config:1", callback_data)
        self.assertIn("download_conf:1", callback_data)


if __name__ == "__main__":
    unittest.main()
