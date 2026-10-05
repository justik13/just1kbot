import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from aiogram.fsm.context import FSMContext

from bot import texts
from bot.handlers.admin.servers.delete_routes import confirm_delete_server, confirm_purge_server
from bot.states import AdminStates
from config.enums import (
    ServerHealthState,
    ServerLifecycleStatus,
    WhiteInternetProvisioningStatus,
    WhiteInternetStatus,
)


class AdminServerDeleteSafetyTests(unittest.IsolatedAsyncioTestCase):
    async def test_confirmation_callback_must_match_fsm_target(self):
        callback = MagicMock()
        callback.from_user.id = 1
        callback.data = "confirm_server_delete:20"
        callback.answer = AsyncMock()
        state = AsyncMock(spec=FSMContext)
        state.get_state.return_value = AdminStates.confirming_server_delete
        state.get_data.return_value = {"delete_server_id": 10}

        with patch(
            "bot.handlers.admin.servers.delete_routes.is_admin",
            return_value=True,
        ), patch(
            "bot.handlers.admin.servers.delete_routes.parse_callback_id",
            return_value=20,
        ):
            await confirm_delete_server(callback, state, AsyncMock())

        callback.answer.assert_awaited_once_with(
            texts.ERROR_INVALID_REQUEST,
            show_alert=True,
        )
        state.clear.assert_awaited_once()

    async def test_deletion_blocked_when_active_white_internet_subscriptions_exist(self):
        callback = MagicMock()
        callback.from_user.id = 1
        callback.data = "confirm_server_delete:10"
        callback.answer = AsyncMock()
        callback.message.edit_text = AsyncMock()
        state = AsyncMock(spec=FSMContext)
        state.get_state.return_value = AdminStates.confirming_server_delete
        state.get_data.return_value = {"delete_server_id": 10}

        mock_server = MagicMock()
        mock_server.id = 10
        mock_server.name = "Test Origin"
        mock_server.api_url = "http://127.0.0.1:8444"
        mock_server.api_key = "secret"

        mock_session = AsyncMock()
        # 1st execute: select server with_for_update
        # 2nd execute: select profiles
        # 3rd execute: select operations
        # 4th execute: select active WhiteInternetSubscription
        res_server = MagicMock()
        res_server.scalar_one_or_none.return_value = mock_server

        res_profiles = MagicMock()
        res_profiles.scalars.return_value.all.return_value = []

        res_ops = MagicMock()
        res_ops.scalars.return_value.all.return_value = []

        mock_active_sub = MagicMock()
        mock_active_sub.id = 1
        res_wl = MagicMock()
        res_wl.scalars.return_value.all.return_value = [mock_active_sub]

        mock_session.execute.side_effect = [res_server, res_profiles, res_ops, res_wl]

        with patch(
            "bot.handlers.admin.servers.delete_routes.is_admin",
            return_value=True,
        ), patch(
            "bot.handlers.admin.servers.delete_routes.parse_callback_id",
            return_value=10,
        ):
            await confirm_delete_server(callback, state, mock_session)

        mock_session.rollback.assert_awaited_once()
        self.assertTrue(callback.answer.call_args[1]["show_alert"])
        self.assertIn("активных подписок White Internet", callback.answer.call_args[0][0])

    async def test_confirm_purge_server_decommissions_server(self):
        callback = MagicMock()
        callback.from_user.id = 1
        callback.data = "confirm_server_purge:10"
        callback.answer = AsyncMock()
        callback.message.edit_text = AsyncMock()

        mock_server = MagicMock()
        mock_server.id = 10
        mock_server.name = "Dead Origin"
        mock_server.is_active = True
        mock_server.lifecycle_status = ServerLifecycleStatus.ACTIVE
        mock_server.health_state = ServerHealthState.ONLINE

        mock_sub = MagicMock()
        mock_sub.id = 1
        mock_sub.origin_node_id = 10
        mock_sub.status = WhiteInternetStatus.ACTIVE
        mock_sub.provisioning_status = WhiteInternetProvisioningStatus.ACTIVE
        mock_sub.pending_hard_delete = False

        mock_session = AsyncMock()
        res_server = MagicMock()
        res_server.scalar_one_or_none.return_value = mock_server

        res_subs = MagicMock()
        res_subs.scalars.return_value.all.return_value = [mock_sub]

        # 1st execute: select server with_for_update
        # 2nd execute: update WhiteInternetOrphanCleanup
        # 3rd execute: select WhiteInternetSubscription with_for_update
        mock_session.execute.side_effect = [res_server, MagicMock(), res_subs]

        with patch(
            "bot.handlers.admin.servers.delete_routes.is_admin",
            return_value=True,
        ), patch(
            "bot.handlers.admin.servers.delete_routes.parse_callback_id",
            return_value=10,
        ), patch(
            "bot.handlers.admin.servers.delete_routes._show_servers_list",
            new=AsyncMock(),
        ), patch(
            "bot.handlers.admin.servers.delete_routes.AuditService.log_action",
            new=AsyncMock(),
        ):
            await confirm_purge_server(callback, mock_session)

        self.assertFalse(mock_server.is_active)
        self.assertEqual(mock_server.lifecycle_status, ServerLifecycleStatus.DECOMMISSIONED)
        self.assertEqual(mock_server.health_state, ServerHealthState.MANUAL_DISABLED)
        self.assertEqual(mock_sub.status, WhiteInternetStatus.DISABLED)
        self.assertEqual(mock_sub.provisioning_status, WhiteInternetProvisioningStatus.SYNCED_INACTIVE)
        self.assertIsNone(mock_sub.origin_node_id)
        mock_session.commit.assert_awaited_once()
        callback.answer.assert_awaited_once()

    async def test_edit_server_url_blocked_when_white_internet_subscriptions_exist(self):
        from bot.handlers.admin.servers.edit_routes import process_edit_server_url
        from database.models import Server

        message = AsyncMock()
        message.from_user.id = 1
        message.text = "https://new-origin.example.com:8443"
        message.chat.id = 100

        state = AsyncMock(spec=FSMContext)
        state.get_data.return_value = {"server_id": 10}

        server = Server(id=10, name="xray-origin", api_url="https://old.example.com:8443", api_key="secret", protocol="xray")
        session = AsyncMock()

        exec_res = MagicMock()
        exec_res.scalar_one.return_value = 0
        session.execute.return_value = exec_res
        session.scalar.return_value = 5

        with patch("bot.handlers.admin.servers.edit_routes.is_admin", return_value=True), \
             patch("bot.handlers.admin.servers.edit_routes.get_server_by_id", return_value=server), \
             patch("bot.handlers.admin.servers.edit_routes.get_server_by_api_url", return_value=None), \
             patch("bot.handlers.admin.servers.edit_routes.is_safe_url", return_value=True), \
             patch("services.xray_node_client.XrayNodeClient.check_health", return_value=(True, 1, {})), \
             patch("bot.handlers.admin.servers.edit_routes.render_hub") as mock_render:

            await process_edit_server_url(message, state, session)

            mock_render.assert_called_once()
            text = mock_render.call_args[0][2]
            self.assertIn("Связанных устройств: <b>5</b>", text)
            state.clear.assert_called_once()

    async def test_edit_server_key_blocked_when_white_internet_subscriptions_exist(self):
        from bot.handlers.admin.servers.edit_routes import process_edit_server_key
        from database.models import Server

        message = AsyncMock()
        message.from_user.id = 1
        message.text = "new-secret-key-12345"
        message.chat.id = 100

        state = AsyncMock(spec=FSMContext)
        state.get_data.return_value = {"server_id": 10}

        server = Server(id=10, name="xray-origin", api_url="https://old.example.com:8443", api_key="secret", protocol="xray")
        session = AsyncMock()
        exec_res = MagicMock()
        exec_res.scalar_one.return_value = 0
        session.execute.return_value = exec_res
        session.scalar.return_value = 3

        with patch("bot.handlers.admin.servers.edit_routes.is_admin", return_value=True), \
             patch("bot.handlers.admin.servers.edit_routes.get_server_by_id", return_value=server), \
             patch("services.xray_node_client.XrayNodeClient.check_health", return_value=(True, 1, {})), \
             patch("bot.handlers.admin.servers.edit_routes.render_hub") as mock_render:

            await process_edit_server_key(message, state, session)

            mock_render.assert_called_once()
            text = mock_render.call_args[0][2]
            self.assertIn("Связанных устройств: <b>3</b>", text)
            state.clear.assert_called_once()


    async def test_confirm_delete_server_phased_execution_and_traffic_archiving(self):
        callback = MagicMock()
        callback.from_user.id = 1
        callback.data = "confirm_server_delete:10"
        callback.answer = AsyncMock()
        callback.message.edit_text = AsyncMock()
        state = AsyncMock(spec=FSMContext)
        state.get_state.return_value = AdminStates.confirming_server_delete
        state.get_data.return_value = {"delete_server_id": 10}

        mock_server = MagicMock()
        mock_server.id = 10
        mock_server.name = "Test Origin"
        mock_server.api_url = "http://127.0.0.1:8444"
        mock_server.api_key = "secret"
        mock_server.protocol = "amneziawg"
        mock_server.is_active = True
        mock_server.lifecycle_status = ServerLifecycleStatus.ACTIVE

        mock_profile = MagicMock()
        mock_profile.id = 101
        mock_profile.server_id = 10
        mock_profile.user_id = 42
        mock_profile.device_name = "Phone"
        mock_profile.traffic_down = 300
        mock_profile.traffic_up = 200
        mock_profile.peer_id = None
        mock_profile.client_name = "client1"

        mock_user = MagicMock()
        mock_user.id = 42
        mock_user.archived_device_traffic = None

        mock_session = AsyncMock()
        mock_session.expire_all = MagicMock()
        mock_session.get.return_value = mock_user

        res_server = MagicMock()
        res_server.scalar_one_or_none.return_value = mock_server

        res_profiles = MagicMock()
        res_profiles.scalars.return_value.all.return_value = [mock_profile]

        res_ops = MagicMock()
        res_ops.scalars.return_value.all.return_value = []

        res_wl = MagicMock()
        res_wl.scalars.return_value.all.return_value = []

        res_user_profiles = MagicMock()
        res_user_profiles.scalars.return_value.all.return_value = [mock_profile]

        res_orphan_profiles = MagicMock()
        res_orphan_profiles.scalars.return_value.all.return_value = []

        res_server_del = MagicMock()
        res_server_del.scalar_one_or_none.return_value = mock_server

        mock_session.execute.side_effect = [
            res_server,          # select server for_update
            res_profiles,        # select profiles
            res_ops,             # select operations for_update
            res_wl,              # select wl subs for_update
            res_user_profiles,   # Phase 2: select user_profiles for_update
            res_orphan_profiles, # Phase 2: select orphan_profiles
            res_server_del,      # Phase 3: select server_to_delete for_update
            MagicMock(),         # Phase 3: update WhiteInternetSubscription
        ]
        mock_session.scalar.side_effect = [0, 0]  # orphans_count, trial_resets_count

        with patch(
            "bot.handlers.admin.servers.delete_routes.is_admin",
            return_value=True,
        ), patch(
            "bot.handlers.admin.servers.delete_routes.parse_callback_id",
            return_value=10,
        ), patch(
            "bot.handlers.admin.servers.delete_routes.delete_profiles_by_server_id",
            new=AsyncMock(return_value=1),
        ), patch(
            "bot.handlers.admin.servers.delete_routes.delete_server",
            new=AsyncMock(),
        ), patch(
            "bot.handlers.admin.servers.delete_routes.cleanup_server_circuit_breakers",
        ), patch(
            "services.slots_cache.invalidate_server_cache",
        ), patch(
            "services.workers.node_monitor.clear_server_monitor_state",
        ), patch(
            "bot.handlers.admin.servers.delete_routes.AuditService.log_action",
            new=AsyncMock(),
        ), patch(
            "bot.handlers.admin.servers.delete_routes._show_servers_list",
            new=AsyncMock(),
        ):
            await confirm_delete_server(callback, state, mock_session)

        # Verify Phase 1: deactivated before Phase 2
        self.assertFalse(mock_server.is_active)
        self.assertEqual(mock_server.lifecycle_status, ServerLifecycleStatus.DECOMMISSIONING)

        # Verify Phase 2: traffic archived to user
        from utils.traffic_helpers import get_archived_traffic_for_device
        self.assertIsNotNone(mock_user.archived_device_traffic)
        self.assertEqual(get_archived_traffic_for_device(mock_user.archived_device_traffic, "Phone"), 500)
        self.assertEqual(mock_user.archived_device_traffic.get("slot_phone"), 500)

        # Verify 3 transactional phases committed
        self.assertEqual(mock_session.commit.await_count, 3)
        mock_session.delete.assert_any_call(mock_profile)


if __name__ == "__main__":
    unittest.main()


