import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from bot.handlers.connection.device_view_routes import device_help, manage_device


def _make_valid_vpn_uri() -> str:
    from utils.vpn_parser import encode_json_to_vpn_uri
    last_cfg = {
        "client_priv_key": "c_priv_key=",
        "client_pub_key": "c_pub_key=",
        "server_pub_key": "s_pub_key=",
        "client_ip": "10.8.1.25",
        "hostName": "vpn.node.com",
        "port": 51820,
        "mtu": "1280",
        "persistent_keep_alive": "25",
        "Jc": "4",
        "Jmin": "10",
        "Jmax": "50",
        "S1": "87",
        "S2": "61",
        "S3": "49",
        "S4": "1",
        "H1": "100-200",
        "H2": "300-400",
        "H3": "500-600",
        "H4": "700-800",
    }
    conf = (
        "[Interface]\n"
        "PrivateKey = c_priv_key=\n"
        "Address = 10.8.1.25/32\n"
        "DNS = 8.8.8.8, 8.8.4.4\n"
        "MTU = 1280\n"
        "Jc = 4\nJmin = 10\nJmax = 50\nS1 = 87\nS2 = 61\nS3 = 49\nS4 = 1\n"
        "H1 = 100-200\nH2 = 300-400\nH3 = 500-600\nH4 = 700-800\n\n"
        "[Peer]\n"
        "PublicKey = s_pub_key=\n"
        "AllowedIPs = 0.0.0.0/0, ::/0\n"
        "Endpoint = vpn.node.com:51820\n"
        "PersistentKeepalive = 25\n"
    )
    last_cfg["config"] = conf
    return encode_json_to_vpn_uri({
        "defaultContainer": "amnezia-awg2",
        "containers": [{
            "container": "amnezia-awg2",
            "awg": {
                "protocol_version": "2",
                "port": "51820",
                "Jc": "4",
                "Jmin": "10",
                "Jmax": "50",
                "S1": "87",
                "S2": "61",
                "S3": "49",
                "S4": "1",
                "H1": "100-200",
                "H2": "300-400",
                "H3": "500-600",
                "H4": "700-800",
                "last_config": json.dumps(last_cfg),
            }
        }]
    })


class TestDeviceViewPendingActions(unittest.IsolatedAsyncioTestCase):
    async def test_pending_create_profile_hides_config_and_delete_actions_while_ready_shows_them(self):
        valid_raw_config = _make_valid_vpn_uri()

        db_user = SimpleNamespace(id=7, telegram_id=700)
        server = SimpleNamespace(
            country_flag="🇩🇪",
            name="Germany",
            protocol="amneziawg2",
            is_active=True,
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
            is_active=True,
        )
        ready_profile = SimpleNamespace(
            id=1,
            user_id=7,
            server_id=9,
            device_name="Устройство #1",
            provisioning_status="active",
            peer_id="peer-1",
            raw_config=valid_raw_config,
            traffic_down=0,
            traffic_up=0,
            last_connected=None,
            is_active=True,
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

        pending_callback_data = {
            button.callback_data
            for row in rendered_keyboards[0].inline_keyboard
            for button in row
            if button.callback_data
        }
        self.assertNotIn("alt_connection:1", pending_callback_data)
        self.assertNotIn("request_delete_device:1", pending_callback_data)
        self.assertNotIn("rename_device:1", pending_callback_data)
        self.assertIn("support_help:device_1", pending_callback_data)

        ready_callback_data = {
            button.callback_data
            for row in rendered_keyboards[1].inline_keyboard
            for button in row
            if button.callback_data
        }
        self.assertIn("alt_connection:1", ready_callback_data)
        self.assertIn("request_delete_device:1", ready_callback_data)
        self.assertNotIn("rename_device:1", ready_callback_data)
        self.assertIn("support_help:device_1", ready_callback_data)

    async def test_device_help_topic_buttons_keep_device_context(self):
        callback = MagicMock()
        callback.data = "device_help:42"
        callback.message.chat.id = 700
        callback.message.message_id = 99
        callback.answer = AsyncMock()

        captured = {}

        async def capture_render_hub(_bot, _chat_id, _text, keyboard, **_kwargs):
            captured["keyboard"] = keyboard

        profile = MagicMock(user_id=42)
        with patch(
            "bot.handlers.connection.device_view_routes.render_hub",
            new=AsyncMock(side_effect=capture_render_hub),
        ), patch(
            "bot.handlers.connection.device_view_routes.get_profile_by_id",
            new=AsyncMock(return_value=profile),
        ):
            await device_help(callback, AsyncMock(), AsyncMock(), SimpleNamespace(id=42))

        callback_data = {
            button.callback_data
            for row in captured["keyboard"].inline_keyboard
            for button in row
            if button.callback_data
        }
        self.assertIn("help_download:device_42", callback_data)
        self.assertIn("help_ios:device_42", callback_data)
        self.assertIn("help_windows:device_42", callback_data)
        self.assertIn("help_split:device_42", callback_data)
        self.assertIn("manage_device:42", callback_data)

    async def test_device_help_rejects_foreign_profile(self):
        callback = MagicMock()
        callback.data = "device_help:42"
        callback.answer = AsyncMock()

        with patch(
            "bot.handlers.connection.device_view_routes.get_profile_by_id",
            new=AsyncMock(return_value=SimpleNamespace(user_id=99)),
        ), patch(
            "bot.handlers.connection.device_view_routes.render_hub",
            new=AsyncMock(),
        ) as render:
            await device_help(
                callback,
                AsyncMock(),
                AsyncMock(),
                SimpleNamespace(id=42),
            )

        callback.answer.assert_awaited_once()
        render.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
