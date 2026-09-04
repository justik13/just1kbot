import unittest
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from bot import texts
from bot.handlers.admin.users import device_routes
from utils.datetime_helpers import now_utc


class TestAdminDeviceContext(unittest.IsolatedAsyncioTestCase):
    async def test_device_list_shows_explicit_id_and_bound_server(self):
        server = SimpleNamespace(name="Germany 01", country_flag="🇩🇪")
        profile = SimpleNamespace(
            id=36,
            device_name="Pixel 9",
            server=server,
            last_connected=None,
            traffic_down=1024,
            traffic_up=2048,
        )
        user = SimpleNamespace(id=7, telegram_id=123456789)
        callback = MagicMock()
        callback.from_user.id = 100
        callback.data = "admin_user_devices:123456789"
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
            last_connected=None,
            traffic_down=0,
            traffic_up=0,
        )
        user = SimpleNamespace(id=7, telegram_id=123456789)
        callback = MagicMock()
        callback.from_user.id = 100
        callback.data = "admin_user_devices:123456789"
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

    async def test_device_activity_uses_last_connected_not_nonexistent_handshake_field(self):
        profile = SimpleNamespace(
            id=36,
            device_name="Pixel 9",
            server=SimpleNamespace(name="Germany 01", country_flag="🇩🇪"),
            last_connected=now_utc() - timedelta(minutes=1),
            traffic_down=0,
            traffic_up=0,
        )
        user = SimpleNamespace(id=7, telegram_id=123456789)
        callback = MagicMock()
        callback.from_user.id = 100
        callback.data = "admin_user_devices:123456789"
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
        self.assertIn("В сети (активность ≤ 3 мин)", rendered)

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

    async def test_delete_confirm_rejects_profile_from_another_user(self):
        profile = SimpleNamespace(id=36, user_id=999, device_name="Pixel 9", server=None)
        user = SimpleNamespace(id=7, telegram_id=123456789)
        callback = MagicMock()
        callback.from_user.id = 100
        callback.data = "admin_delete_device:123456789:36"
        callback.answer = AsyncMock()
        callback.message.edit_text = AsyncMock()
        session = MagicMock()

        with (
            patch.object(device_routes, "is_admin", return_value=True),
            patch.object(device_routes, "get_profile_by_id", new=AsyncMock(return_value=profile)),
            patch.object(device_routes, "_get_user_with_profiles", new=AsyncMock(return_value=user)),
        ):
            await device_routes.admin_delete_device_confirm(callback, session)

        callback.answer.assert_any_await(texts.ERROR_PROFILE_NOT_FOUND, show_alert=True)
        callback.message.edit_text.assert_not_awaited()

    async def test_delete_apply_rechecks_profile_ownership_before_destructive_action(self):
        profile = SimpleNamespace(id=36, user_id=999, device_name="Pixel 9")
        user = SimpleNamespace(id=7, telegram_id=123456789)
        callback = MagicMock()
        callback.from_user.id = 100
        callback.data = "admin_delete_device_apply:123456789:36"
        callback.answer = AsyncMock()
        callback.message.edit_text = AsyncMock()
        session = MagicMock()

        with (
            patch.object(device_routes, "is_admin", return_value=True),
            patch.object(device_routes, "get_profile_by_id", new=AsyncMock(return_value=profile)),
            patch.object(device_routes, "_get_user_with_profiles", new=AsyncMock(return_value=user)),
            patch.object(device_routes.DeviceService, "delete_device", new=AsyncMock()) as delete_device,
        ):
            await device_routes.admin_delete_device_apply(callback, session)

        callback.answer.assert_any_await(texts.ERROR_PROFILE_NOT_FOUND, show_alert=True)
        delete_device.assert_not_awaited()

    async def test_profile_count_excludes_all_non_visible_deletion_states(self):
        from database.repositories.profiles_repo import (
            PROFILE_QUOTA_EXCLUDED_STATUSES,
            get_user_profiles_count,
        )

        result = MagicMock()
        result.scalar_one.return_value = 2
        session = MagicMock()
        session.execute = AsyncMock(return_value=result)

        await get_user_profiles_count(session, user_id=7)

        stmt = session.execute.await_args.args[0]
        sql = str(stmt.compile(compile_kwargs={"literal_binds": True}))
        self.assertIn("provisioning_status", sql)
        self.assertIn("deleting", sql)
        # create_failed (no peer on server) is excluded from quota
        self.assertIn("create_failed", sql)
        # create_cleanup_pending and delete_failed have active server peers —
        # they must NOT be excluded from quota to prevent downgrade exploits.
        self.assertNotIn("create_cleanup_pending", PROFILE_QUOTA_EXCLUDED_STATUSES)
        self.assertNotIn("delete_failed", PROFILE_QUOTA_EXCLUDED_STATUSES)


if __name__ == "__main__":
    unittest.main()
