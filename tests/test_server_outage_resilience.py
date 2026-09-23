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
            # 3. check dead op cooldown -> None (cooldown expired / safe to retry)
            MagicMock(scalar_one_or_none=MagicMock(return_value=None)),
            # 4. resolve endpoint snapshot
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

    async def test_cleanup_stuck_profiles_skips_delete_failed_during_cooldown(self):
        """Cleanup worker does not revive dead delete operations within the 6-hour cooldown window."""
        from datetime import datetime, timezone
        from database.models import APIOperation, Server, VPNProfile
        from services.workers.cleanup import _cleanup_stuck_profiles

        stuck_profile = VPNProfile(
            id=52,
            user_id=1,
            server_id=10,
            device_name="Cooling Device",
            client_name="tg_100_p52",
            provisioning_status="delete_failed",
            peer_id="peer_cool",
        )

        dead_op = APIOperation(
            id=999,
            profile_id=52,
            operation_type="delete_peer",
            status="dead",
            completed_at=datetime.now(timezone.utc),
        )

        mock_server = Server(id=10, name="DE Server", api_url="https://de.vpn", api_key="secret", is_active=True)
        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(side_effect=[
            # 1. select stuck profiles
            MagicMock(scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[stuck_profile])))),
            # 2. check active operations -> None
            MagicMock(scalar_one_or_none=MagicMock(return_value=None)),
            # 3. check dead op cooldown -> dead_op (completed just now!)
            MagicMock(scalar_one_or_none=MagicMock(return_value=dead_op)),
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

            # Must NOT revive in cooldown window!
            mock_ensure_delete.assert_not_called()
            self.assertEqual(stuck_profile.provisioning_status, "delete_failed")

    async def test_device_service_slot_allocation_filters_delete_failed_profiles(self):
        """DeviceService slot allocation assigns slot #2 when slot #2 was delete_failed on dead server, but #3 if create_failed."""
        from datetime import datetime, timezone
        from database.models import Server, User, VPNProfile
        from services.device_service import DeviceService
        from services.slots_cache import ServerPeerSnapshot

        user = User(id=1, telegram_id=100, device_limit=2, subscription_end=datetime(2099, 1, 1, tzinfo=timezone.utc), is_banned=False)
        server_healthy = Server(id=20, name="NL", api_url="https://nl.vpn", api_key="k", protocol="amneziawg2", is_active=True, max_clients=100)
        p1 = VPNProfile(id=1, user_id=1, server_id=10, device_name="Устройство #1", provisioning_status="active")
        # Scenario A: User had #1 (active) and #2 (delete_failed).
        # Query filters out delete_failed (PROFILE_LIST_HIDDEN_STATUSES), so only p1 is returned -> slot #2 is allocated!
        mock_session = AsyncMock()
        mock_session.add = MagicMock()
        mock_session.execute = AsyncMock(side_effect=[
            # 1. select User FOR UPDATE
            MagicMock(scalar_one=MagicMock(return_value=user)),
            # 2. select Server FOR UPDATE
            MagicMock(scalar_one_or_none=MagicMock(return_value=server_healthy)),
            # 3. select user profiles excluding PROFILE_LIST_HIDDEN_STATUSES -> [p1]
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
            # Slot #2 was reused because delete_failed was excluded from query
            self.assertEqual(profile.device_name, "Устройство #2")

        # Scenario B: User had #1 (active) and #2 (create_failed - visible in UI).
        # Query does NOT filter create_failed, so [p1, p2_failed] is returned -> slot #3 is allocated!
        p2_create_failed = VPNProfile(id=3, user_id=1, server_id=20, device_name="Устройство #2", provisioning_status="create_failed")
        mock_session.execute = AsyncMock(side_effect=[
            MagicMock(scalar_one=MagicMock(return_value=user)),
            MagicMock(scalar_one_or_none=MagicMock(return_value=server_healthy)),
            MagicMock(scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[p1, p2_create_failed])))),
            MagicMock(scalar_one_or_none=MagicMock(return_value=None)),
            MagicMock(scalar_one=MagicMock(return_value=1)),
            MagicMock(scalar_one=MagicMock(return_value=0)),
            MagicMock(scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[])))),
            MagicMock(scalar_one_or_none=MagicMock(return_value=None)),
        ])
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
            # Slot #3 allocated, avoiding DuplicateDeviceName collision with visible create_failed #2!
            self.assertEqual(profile.device_name, "Устройство #3")

        # Scenario C: User had #1 (active on s10) and #2 (delete_failed on server_healthy id=20).
        # When creating on the SAME server 20, #2 is physically present on server 20,
        # so slot #3 must be allocated to prevent DuplicateDeviceName on server 20!
        p2_delete_failed_on_s20 = VPNProfile(
            id=4,
            user_id=1,
            server_id=20,
            device_name="Устройство #2",
            provisioning_status="delete_failed",
        )
        mock_session.execute = AsyncMock(side_effect=[
            MagicMock(scalar_one=MagicMock(return_value=user)),
            MagicMock(scalar_one_or_none=MagicMock(return_value=server_healthy)),
            # select all user profiles: [p1 on s10, p2_delete_failed on s20]
            MagicMock(scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[p1, p2_delete_failed_on_s20])))),
            MagicMock(scalar_one_or_none=MagicMock(return_value=None)),
            MagicMock(scalar_one=MagicMock(return_value=1)),
            MagicMock(scalar_one=MagicMock(return_value=0)),
            MagicMock(scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[])))),
            MagicMock(scalar_one_or_none=MagicMock(return_value=None)),
        ])
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
            # Slot #3 allocated on server 20, avoiding DuplicateDeviceName collision with delete_failed #2 on same server!
            self.assertEqual(profile.device_name, "Устройство #3")

    async def test_is_server_allocatable_predicate(self):
        """Test is_server_allocatable predicate across all states and capabilities."""
        from config.constants import AMNEZIA_PROTOCOL, XRAY_PROTOCOL
        from config.enums import ServerHealthState, ServerLifecycleStatus
        from database.repositories.servers_repo import is_server_allocatable

        # Valid AWG server
        s_valid = Server(
            id=1, name="NL", protocol=AMNEZIA_PROTOCOL, is_active=True,
            health_state=ServerHealthState.ONLINE, lifecycle_status=ServerLifecycleStatus.ACTIVE,
            capabilities=[]
        )
        self.assertTrue(is_server_allocatable(s_valid, AMNEZIA_PROTOCOL))

        # None server
        self.assertFalse(is_server_allocatable(None, AMNEZIA_PROTOCOL))

        # Inactive server
        s_inactive = Server(
            id=2, name="NL", protocol=AMNEZIA_PROTOCOL, is_active=False,
            health_state=ServerHealthState.ONLINE, capabilities=[]
        )
        self.assertFalse(is_server_allocatable(s_inactive, AMNEZIA_PROTOCOL))

        # Wrong protocol
        self.assertFalse(is_server_allocatable(s_valid, XRAY_PROTOCOL))

        # Unhealthy states
        for bad_health in (ServerHealthState.AUTO_DISABLED, ServerHealthState.MANUAL_DISABLED, ServerHealthState.PROBLEM):
            s_bad_health = Server(
                id=3, name="NL", protocol=AMNEZIA_PROTOCOL, is_active=True,
                health_state=bad_health, capabilities=[]
            )
            self.assertFalse(is_server_allocatable(s_bad_health, AMNEZIA_PROTOCOL))

        # Decommissioning lifecycle states
        for bad_lc in (ServerLifecycleStatus.DECOMMISSIONING, ServerLifecycleStatus.DECOMMISSIONED, ServerLifecycleStatus.ARCHIVED):
            s_bad_lc = Server(
                id=4, name="NL", protocol=AMNEZIA_PROTOCOL, is_active=True,
                health_state=ServerHealthState.ONLINE, lifecycle_status=bad_lc, capabilities=[]
            )
            self.assertFalse(is_server_allocatable(s_bad_lc, AMNEZIA_PROTOCOL))

        # Xray origin capability excluded from AWG allocation
        s_xray_origin = Server(
            id=5, name="NL", protocol=AMNEZIA_PROTOCOL, is_active=True,
            health_state=ServerHealthState.ONLINE, capabilities=["xray_origin"]
        )
        self.assertFalse(is_server_allocatable(s_xray_origin, AMNEZIA_PROTOCOL))

    async def test_ping_server_checks_circuit_breaker_availability(self):
        """Admin ping_server respects circuit breaker availability."""
        from bot import texts
        from bot.handlers.admin.servers.card_routes import ping_server
        from database.models import Server

        mock_callback = AsyncMock()
        mock_callback.from_user.id = 1
        mock_callback.data = "admin_server_ping:1"
        mock_callback.answer = AsyncMock()

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

        # 1. When CB is open -> do not call healthcheck, render NO_HEALTHZ on server card
        with (
            patch("bot.handlers.admin.servers.card_routes.is_admin", return_value=True),
            patch("bot.handlers.admin.servers.card_routes.get_server_by_id", return_value=mock_server),
            patch("services.amnezia_client.is_server_circuit_available", new=AsyncMock(return_value=False)),
            patch("services.amnezia_client.AmneziaClient.healthcheck", new=AsyncMock()) as mock_health,
            patch("bot.handlers.admin.servers.card_routes._show_server_card", new=AsyncMock()) as mock_card,
        ):
            await ping_server(mock_callback, mock_session)
            mock_health.assert_not_called()
            mock_card.assert_called_once()
            self.assertEqual(mock_card.call_args.kwargs["ping_result"], texts.ADMIN_SERVER_PING_NO_HEALTHZ)

        # 2. When CB is available -> call healthcheck and update server card with latency
        with (
            patch("bot.handlers.admin.servers.card_routes.is_admin", return_value=True),
            patch("bot.handlers.admin.servers.card_routes.get_server_by_id", return_value=mock_server),
            patch("services.amnezia_client.is_server_circuit_available", new=AsyncMock(return_value=True)),
            patch("services.amnezia_client.AmneziaClient.healthcheck", new=AsyncMock(return_value=True)) as mock_health,
            patch("bot.handlers.admin.servers.card_routes._show_server_card", new=AsyncMock()) as mock_card,
        ):
            await ping_server(mock_callback, mock_session)
            mock_health.assert_called_once()
            mock_card.assert_called_once()
            self.assertIn("ms", mock_card.call_args.kwargs["ping_result"])

    async def test_circuit_breaker_helper_and_client_method(self):
        """is_server_circuit_available and AmneziaClient.is_circuit_available delegate cleanly."""
        from services.amnezia_client import AmneziaClient, is_server_circuit_available

        mock_cb = AsyncMock()
        mock_cb.is_available.return_value = True

        with patch("services.amnezia_client._get_circuit_breaker", return_value=mock_cb):
            res1 = await is_server_circuit_available("https://awg.test")
            self.assertTrue(res1)
            mock_cb.is_available.assert_called_once()

            client = AmneziaClient("https://awg.test", "key")
            res2 = await client.is_circuit_available()
            self.assertTrue(res2)

    async def test_cleanup_stuck_profiles_skips_inactive_server_and_preserves_delete_failed(self):
        """Cleanup worker skips inactive servers and keeps delete_failed status on enqueue failure."""
        from database.models import Server, VPNProfile
        from services.workers.cleanup import _cleanup_stuck_profiles

        stuck_profile = VPNProfile(
            id=51,
            user_id=1,
            server_id=11,
            device_name="Test Device",
            client_name="tg_100_p51",
            provisioning_status="delete_failed",
            peer_id="peer_abc",
        )

        # Inactive server -> should skip enqueueing delete_peer
        mock_server_inactive = Server(id=11, name="Dead Server", api_url="https://dead.vpn", api_key="k", is_active=False)
        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(side_effect=[
            MagicMock(scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[stuck_profile])))),
            MagicMock(scalar_one_or_none=MagicMock(return_value=None)),  # active ops -> None
        ])
        mock_session.get = AsyncMock(return_value=mock_server_inactive)

        mock_scope = MagicMock()
        mock_scope.__aenter__.return_value = mock_session
        mock_scope.__aexit__.return_value = None

        with (
            patch("services.workers.cleanup.session_scope", return_value=mock_scope),
            patch("services.api_operations_queue.ensure_delete_operation", new=AsyncMock()) as mock_ensure_delete,
        ):
            await _cleanup_stuck_profiles()
            mock_ensure_delete.assert_not_called()
            self.assertEqual(stuck_profile.provisioning_status, "delete_failed")

        # Active server, but ensure_delete_operation raises exception -> must preserve delete_failed
        mock_server_active = Server(id=11, name="Active Server", api_url="https://act.vpn", api_key="k", is_active=True)
        mock_session.execute = AsyncMock(side_effect=[
            MagicMock(scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[stuck_profile])))),
            MagicMock(scalar_one_or_none=MagicMock(return_value=None)),  # active ops -> None
            MagicMock(scalar_one_or_none=MagicMock(return_value=None)),  # dead op cooldown -> None
        ])
        mock_session.get = AsyncMock(return_value=mock_server_active)

        with (
            patch("services.workers.cleanup.session_scope", return_value=mock_scope),
            patch("services.api_operations_queue.resolve_profile_endpoint_snapshot", return_value=(11, "Active", "https://act.vpn", "k")),
            patch("services.api_operations_queue.ensure_delete_operation", side_effect=Exception("DB lock error")),
        ):
            await _cleanup_stuck_profiles()
            # Stays delete_failed, not downgraded to create_cleanup_pending!
            self.assertEqual(stuck_profile.provisioning_status, "delete_failed")


if __name__ == "__main__":
    unittest.main()

