import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from bot.handlers.admin.users import device_routes
from bot import texts


class TestAdminDeviceContext(unittest.IsolatedAsyncioTestCase):
    async def test_device_list_shows_explicit_id_and_bound_server(self):
        server = SimpleNamespace(name="Germany 01", country_flag="🇩🇪")
        profile = SimpleNamespace(
            id=36,
            device_name="Pixel 9",
            server=server,
            last_handshake_at=None,
            updated_at=None,
            traffic_down=1024,
            traffic_up=2048,
            last_connected=None,
        )
        user = SimpleNamespace(id=7, telegram_id=872658825)
        callback = MagicMock()
        callback.from_user.id = 100
        callback.data = "admin_user_devices:872658825"
        callback.answer = AsyncMock()
        callback.message.edit_text = AsyncMock()
        session = MagicMock()

        with (
            patch.object(device_routes, "is_admin", return_value=True),
            patch.object(device_routes, "_get_user_with_profiles", new=AsyncMock(return_value=user)),
            patch.object(device_routes, "get_user_profiles", new=AsyncMock(return_value=[profile])),
            patch.object(device_routes, "get_admin_user_devices_keyboard", return_value=MagicMock()),
        ):
            await device_routes.admin_user_devices(callback, session)

        rendered = callback.message.edit_text.await_args.args[0]
        self.assertIn("🆔 ID устройства: <code>36</code>", rendered)
        self.assertIn("🖥 Сервер: 🇩🇪 <b>Germany 01</b>", rendered)
        self.assertNotIn("ID устройства: 36)", rendered)

    async def test_device_list_has_safe_fallback_when_server_relationship_is_missing(self):
        profile = SimpleNamespace(
            id=36,
            device_name="Pixel 9",
            server=None,
            last_handshake_at=None,
            updated_at=None,
            traffic_down=0,
            traffic_up=0,
            last_connected=None,
        )
        user = SimpleNamespace(id=7, telegram_id=872658825)
        callback = MagicMock()
        callback.from_user.id = 100
        callback.data = "admin_user_devices:872658825"
        callback.answer = AsyncMock()
        callback.message.edit_text = AsyncMock()
        session = MagicMock()

        with (
            patch.object(device_routes, "is_admin", return_value=True),
            patch.object(device_routes, "_get_user_with_profiles", new=AsyncMock(return_value=user)),
            patch.object(device_routes, "get_user_profiles", new=AsyncMock(return_value=[profile])),
            patch.object(device_routes, "get_admin_user_devices_keyboard", return_value=MagicMock()),
        ):
            await device_routes.admin_user_devices(callback, session)

        rendered = callback.message.edit_text.await_args.args[0]
        self.assertIn("🖥 Сервер: 🌐 <b>Неизвестный сервер</b>", rendered)

    async def test_non_admin_cannot_access_device_list(self):
        callback = MagicMock()
        callback.from_user.id = 100
        callback.answer = AsyncMock()
        session = MagicMock()

        with patch.object(device_routes, "is_admin", return_value=False):
            await device_routes.admin_user_devices(callback, session)

        callback.answer.assert_awaited_once_with(
            texts.ERROR_ACCESS_DENIED,
            show_alert=True,
        )


if __name__ == "__main__":
    unittest.main()
