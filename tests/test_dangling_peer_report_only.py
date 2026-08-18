import unittest
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from services.workers import cleanup


class _Scalars:
    def __init__(self, values):
        self._values = values

    def all(self):
        return self._values


class _Result:
    def __init__(self, values=None, first=None):
        self._values = values or []
        self._first = first

    def scalars(self):
        return _Scalars(self._values)

    def all(self):
        return self._values

    def first(self):
        return self._first


class DanglingPeerReportOnlyTests(unittest.IsolatedAsyncioTestCase):
    async def _run_cleanup(
        self,
        *,
        snapshot=(),
        client_name="tg_123_phone",
        api_result="default",
        api_error=None,
        fresh_result=None,
        fresh_error=None,
    ):
        server = SimpleNamespace(
            id=10,
            name="vpn-one",
            api_url="https://vpn.invalid",
            api_key="highly-secret-api-key",
        )
        peer = SimpleNamespace(
            id="peer-1",
            clientName=client_name,
            name=client_name,
        )
        sessions = []
        scope_count = 0

        class InitialSession:
            def __init__(self):
                self.execute_count = 0
                self.add = MagicMock()

            async def execute(inner_self, _statement):
                inner_self.execute_count += 1
                if inner_self.execute_count == 1:
                    return _Result([server])
                return _Result(list(snapshot))

        class FreshSession:
            def __init__(self):
                self.add = MagicMock()

            async def execute(self, _statement):
                if fresh_error:
                    raise fresh_error
                return _Result(first=fresh_result)

        @asynccontextmanager
        async def fake_scope():
            nonlocal scope_count
            session = InitialSession() if scope_count == 0 else FreshSession()
            scope_count += 1
            sessions.append(session)
            yield session

        client = MagicMock()
        if api_error:
            client.get_all_clients = AsyncMock(side_effect=api_error)
        else:
            clients = [peer] if api_result == "default" else api_result
            client.get_all_clients = AsyncMock(return_value=clients)
        client.delete_user = AsyncMock()
        test_logger = MagicMock()

        with (
            patch.object(cleanup, "session_scope", fake_scope),
            patch.object(cleanup, "AmneziaClient", return_value=client),
            patch.object(cleanup, "logger", test_logger),
        ):
            cleanup._unmanaged_peers_log_cache.clear()
            cleanup._unmanaged_peers_summary_last_logged = None
            await cleanup._cleanup_dangling_peers()

        log_calls = (
            test_logger.warning.call_args_list
            + test_logger.error.call_args_list
        )
        logs = "\n".join(
            str(call.args[0]) % call.args[1:]
            for call in log_calls
        )
        return client, sessions, logs

    async def test_unknown_tg_peer_is_reported_but_not_deleted(self):
        client, sessions, logs = await self._run_cleanup()

        client.delete_user.assert_not_awaited()
        for session in sessions:
            session.add.assert_not_called()
        self.assertIn("Unmanaged VPN peer detected", logs)
        self.assertIn("server_id=10", logs)
        self.assertIn("automatic deletion disabled", logs)
        self.assertNotIn("highly-secret-api-key", logs)

    async def test_known_peer_on_same_server_is_not_reported(self):
        client, sessions, logs = await self._run_cleanup(
            snapshot=[(10, "peer-1")]
        )

        client.delete_user.assert_not_awaited()
        for session in sessions:
            session.add.assert_not_called()
        self.assertNotIn("Unmanaged VPN peer", logs)

    async def test_same_peer_on_other_server_is_unmanaged(self):
        client, sessions, logs = await self._run_cleanup(
            snapshot=[(20, "peer-1")]
        )

        client.delete_user.assert_not_awaited()
        for session in sessions:
            session.add.assert_not_called()
        self.assertIn("Unmanaged VPN peer detected", logs)
        self.assertIn("server_id=10", logs)

    async def test_fresh_double_check_finds_profile(self):
        client, sessions, logs = await self._run_cleanup(
            fresh_result=(1,)
        )

        client.delete_user.assert_not_awaited()
        for session in sessions:
            session.add.assert_not_called()
        self.assertNotIn("Unmanaged VPN peer", logs)

    async def test_fresh_double_check_error_fails_safe(self):
        client, sessions, logs = await self._run_cleanup(
            fresh_error=RuntimeError("database unavailable")
        )

        client.delete_user.assert_not_awaited()
        for session in sessions:
            session.add.assert_not_called()
        self.assertIn("Double-check failed", logs)
        self.assertNotIn("Unmanaged VPN peer detected", logs)

    async def test_non_telegram_peer_is_ignored(self):
        client, sessions, logs = await self._run_cleanup(
            client_name="external-client"
        )

        client.delete_user.assert_not_awaited()
        for session in sessions:
            session.add.assert_not_called()
        self.assertNotIn("Unmanaged VPN peer", logs)

    async def test_manual_tg_named_peer_is_report_only(self):
        client, sessions, logs = await self._run_cleanup(
            client_name="tg_manually_created"
        )

        client.delete_user.assert_not_awaited()
        for session in sessions:
            session.add.assert_not_called()
        self.assertIn("Unmanaged VPN peer detected", logs)
        self.assertIn("automatic deletion disabled", logs)

    async def test_api_unavailable_fails_safe(self):
        for api_result, api_error in (
            (None, None),
            (None, RuntimeError("api unavailable")),
        ):
            with self.subTest(api_error=api_error):
                client, sessions, logs = await self._run_cleanup(
                    api_result=api_result,
                    api_error=api_error,
                )
                client.delete_user.assert_not_awaited()
                for session in sessions:
                    session.add.assert_not_called()
                self.assertNotIn("Unmanaged VPN peer", logs)


    async def test_cleanup_old_records_executes_without_compile_error(self):
        mock_session = AsyncMock()
        mock_res = MagicMock(rowcount=0)
        mock_res.scalars.return_value.all.return_value = []
        mock_session.execute.return_value = mock_res
        with patch("services.workers.cleanup.session_scope") as mock_scope, \
             patch("services.workers.cleanup.clear_audit_logs", return_value=0):
            mock_scope.return_value.__aenter__.return_value = mock_session
            await cleanup._cleanup_old_records()
            self.assertGreaterEqual(mock_session.execute.call_count, 3)


if __name__ == "__main__":
    unittest.main()
