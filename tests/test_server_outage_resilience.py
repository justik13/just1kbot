import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from aiogram import Bot
from sqlalchemy.ext.asyncio import AsyncSession

from bot.handlers.admin.users.mass_bonus import _run_mass_bonus_background
from bot.handlers.connection.device_create_routes import _await_profile_ready
from database.models import Server
from database.repositories.profiles_repo import (
    PROFILE_LIST_HIDDEN_STATUSES,
    PROFILE_QUOTA_EXCLUDED_STATUSES,
)
from database.repositories.servers_repo import get_available_servers
from services.device_service import RESERVING_STATUSES


class TestServerOutageResilience(unittest.IsolatedAsyncioTestCase):

    def test_delete_failed_excluded_from_quota_and_hidden_from_list(self):
        """Verify delete_failed does not consume user quota and is hidden from UI."""
        self.assertIn("delete_failed", PROFILE_QUOTA_EXCLUDED_STATUSES)
        self.assertIn("delete_failed", PROFILE_LIST_HIDDEN_STATUSES)
        self.assertNotIn("delete_failed", RESERVING_STATUSES)

    async def test_get_available_servers_filters_unhealthy(self):
        """Available servers must exclude unhealthy or problem states."""
        from config.enums import ServerHealthState

        mock_session = AsyncMock(spec=AsyncSession)
        s_online = Server(
            id=1, name="NL", protocol="amneziawg2", is_active=True,
            health_state=ServerHealthState.ONLINE, max_clients=100, capabilities=[]
        )
        s_problem = Server(
            id=2, name="DE", protocol="amneziawg2", is_active=True,
            health_state=ServerHealthState.PROBLEM, max_clients=100, capabilities=[]
        )
        s_disabled = Server(
            id=3, name="EE", protocol="amneziawg2", is_active=True,
            health_state=ServerHealthState.AUTO_DISABLED, max_clients=100, capabilities=[]
        )

        with patch("database.repositories.servers_repo.get_active_servers", return_value=[s_online, s_problem, s_disabled]), \
             patch("database.repositories.servers_repo.get_server_peer_counts", return_value={1: 10}), \
             patch("database.repositories.servers_repo.get_cached_peer_count", return_value=10):
            available = await get_available_servers(mock_session)
            self.assertEqual(len(available), 1)
            self.assertEqual(available[0].id, 1)

    async def test_await_profile_ready_default_timeout(self):
        """Verify _await_profile_ready has a default 15s timeout for UI in-place waiting."""
        import inspect
        sig = inspect.signature(_await_profile_ready)
        self.assertEqual(sig.parameters["timeout_seconds"].default, 15.0)

    async def test_mass_bonus_server_audience_filters_by_server_id(self):
        """Mass bonus with server target filters users with devices on that server."""
        mock_bot = MagicMock(spec=Bot)
        mock_bot.send_message = AsyncMock()

        mock_session = AsyncMock(spec=AsyncSession)
        mock_result = MagicMock()
        mock_result.all.return_value = [(1, 1001), (2, 1002)]
        mock_session.execute.return_value = mock_result

        with patch("database.connection.session_scope") as mock_scope, \
             patch("bot.handlers.admin.users.mass_bonus.create_admin_adjustment", return_value=(MagicMock(), True)), \
             patch("services.audit_service.AuditService.log_action", return_value=None):
            mock_scope.return_value.__aenter__.return_value = mock_session

            await _run_mass_bonus_background(
                bot=mock_bot,
                admin_id=999,
                target_aud="server_2",
                amount=100,
                reason="Server outage compensation",
                batch_id="test_batch",
            )

            first_call_stmt = mock_session.execute.call_args_list[0][0][0]
            sql_str = str(first_call_stmt.compile(compile_kwargs={"literal_binds": True}))
            self.assertIn("vpn_profiles", sql_str)
            self.assertIn("white_internet_subscriptions", sql_str)
            self.assertIn("server_id = 2", sql_str)

    async def test_cleanup_stuck_profiles_handles_delete_failed_and_revives_dead_operation(self):
        """Cleanup worker picks up delete_failed profiles and queues/revives delete_peer operation."""
        from database.models import Server, VPNProfile
        from services.workers.cleanup import _cleanup_stuck_profiles

        stuck_profile = VPNProfile(
            id=50,
            user_id=1,
            server_id=10,
            device_name="Test Phone",
            client_name="tg_100_p50",
            provisioning_status="delete_failed",
            peer_id="peer_xyz",
        )

        mock_server = Server(id=10, name="DE Server", api_url="https://de.vpn", api_key="secret", is_active=True)
        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(side_effect=[
            # 1. select stuck profiles
            MagicMock(scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[stuck_profile])))),
            # 2. check active operations -> None
            MagicMock(scalar_one_or_none=MagicMock(return_value=None)),
            # 3. resolve endpoint snapshot
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
            call_kwargs = mock_ensure_delete.call_args.kwargs
            self.assertEqual(call_kwargs["profile_id"], 50)
            self.assertEqual(call_kwargs["peer_id"], "peer_xyz")

    async def test_device_service_slot_allocation_filters_delete_failed_profiles(self):
        """DeviceService slot allocation assigns slot #2 when slot #2 was delete_failed on dead server."""
        from datetime import datetime, timezone
        from database.models import Server, User, VPNProfile
        from services.device_service import DeviceService
        from services.slots_cache import ServerPeerSnapshot

        user = User(id=1, telegram_id=100, device_limit=2, subscription_end=datetime(2099, 1, 1, tzinfo=timezone.utc), is_banned=False)
        server_healthy = Server(id=20, name="NL", api_url="https://nl.vpn", api_key="k", protocol="amneziawg2", is_active=True, max_clients=100)
        p1 = VPNProfile(id=1, user_id=1, server_id=10, device_name="Устройство #1", provisioning_status="active")

        mock_session = AsyncMock()
        mock_session.add = MagicMock()
        mock_session.execute = AsyncMock(side_effect=[
            # 1. select User FOR UPDATE
            MagicMock(scalar_one=MagicMock(return_value=user)),
            # 2. select Server FOR UPDATE
            MagicMock(scalar_one_or_none=MagicMock(return_value=server_healthy)),
            # 3. select user profiles excluding PROFILE_QUOTA_EXCLUDED_STATUSES -> [p1]
            MagicMock(scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[p1])))),
            # 4. select duplicate -> None
            MagicMock(scalar_one_or_none=MagicMock(return_value=None)),
            # 5. user_count -> 1
            MagicMock(scalar_one=MagicMock(return_value=1)),
            # 6. server_count -> 0
            MagicMock(scalar_one=MagicMock(return_value=0)),
            # 7. bot_peer_ids -> []
            MagicMock(scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[])))),
            # 8. duplicate client_name -> None
            MagicMock(scalar_one_or_none=MagicMock(return_value=None)),
        ])

        mock_nested = MagicMock()
        mock_nested.__aenter__ = AsyncMock()
        mock_nested.__aexit__ = AsyncMock()
        mock_session.begin_nested = MagicMock(return_value=mock_nested)

        snapshot = ServerPeerSnapshot(server_id=20, peer_ids=frozenset(), captured_at=datetime.now(timezone.utc))

        with (
            patch("services.device_service.enqueue_api_operation", new=AsyncMock()),
            patch("services.device_service.is_admin", return_value=True),
        ):
            profile = await DeviceService.create_device(
                mock_session,
                user_id=1,
                server_id=20,
                device_name=None,
                snapshot=snapshot,
            )
            # Slot #2 was freed because delete_failed was excluded from query
            self.assertEqual(profile.device_name, "Устройство #2")

    async def test_ping_server_records_circuit_breaker_success_and_failure(self):
        """Admin ping_server records success on healthy response and failure on error/timeout."""
        from bot.handlers.admin.servers.card_routes import ping_server
        from database.models import Server

        mock_cb = AsyncMock()
        mock_cb.is_available.return_value = True

        mock_callback = AsyncMock()
        mock_callback.from_user.id = 1
        mock_callback.data = "admin_server_ping:1"
        mock_callback.message.edit_text = AsyncMock()

        mock_server = Server(
            id=1,
            name="Test Node",
            protocol="amneziawg2",
            api_url="https://awg.example.com",
            api_key="key",
            is_active=True,
            max_clients=100,
        )

        mock_session = AsyncMock()

        with (
            patch("bot.handlers.admin.servers.card_routes.is_admin", return_value=True),
            patch("bot.handlers.admin.servers.card_routes.get_server_by_id", return_value=mock_server),
            patch("services.amnezia_client._get_circuit_breaker", return_value=mock_cb),
            patch("services.amnezia_client.AmneziaClient.healthcheck", new=AsyncMock(return_value=True)),
            patch("bot.handlers.admin.servers.card_routes._show_server_card", new=AsyncMock()) as mock_card,
        ):
            await ping_server(mock_callback, mock_session)
            mock_cb.record_success.assert_called_once()
            mock_cb.record_failure.assert_not_called()
            mock_card.assert_called_once()

        mock_cb.reset_mock()
        with (
            patch("bot.handlers.admin.servers.card_routes.is_admin", return_value=True),
            patch("bot.handlers.admin.servers.card_routes.get_server_by_id", return_value=mock_server),
            patch("services.amnezia_client._get_circuit_breaker", return_value=mock_cb),
            patch("services.amnezia_client.AmneziaClient.healthcheck", new=AsyncMock(side_effect=TimeoutError())),
            patch("bot.handlers.admin.servers.card_routes._show_server_card", new=AsyncMock()),
        ):
            await ping_server(mock_callback, mock_session)
            mock_cb.record_failure.assert_called_once()
            mock_cb.record_success.assert_not_called()


if __name__ == "__main__":
    unittest.main()

