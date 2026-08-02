import runpy
import unittest
from contextlib import contextmanager
from unittest.mock import AsyncMock, Mock, patch

from alembic import context
from alembic.config import Config


class _AsyncConnectionContext:
    def __init__(self, connection, error=None):
        self.connection = connection
        self.error = error

    async def __aenter__(self):
        if self.error is not None:
            raise self.error
        return self.connection

    async def __aexit__(self, exc_type, exc, traceback):
        return False


class AlembicAsyncEnvironmentTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        transaction = Mock()
        transaction.__enter__ = Mock(return_value=transaction)
        transaction.__exit__ = Mock(return_value=False)
        with (
            patch.object(context, "config", Config("alembic.ini"), create=True),
            patch.object(context, "is_offline_mode", return_value=True),
            patch.object(context, "configure"),
            patch.object(context, "begin_transaction", return_value=transaction),
            patch.object(context, "run_migrations"),
        ):
            cls.environment = runpy.run_path("alembic/env.py")

    def test_sync_migrations_use_alembic_transaction_context(self):
        events = []

        @contextmanager
        def transaction():
            events.append("enter")
            yield
            events.append("exit")

        with (
            patch.object(context, "configure") as configure,
            patch.object(context, "begin_transaction", side_effect=transaction),
            patch.object(
                context, "run_migrations", side_effect=lambda: events.append("migrate")
            ),
        ):
            connection = object()
            self.environment["do_run_migrations"](connection)

        configure.assert_called_once_with(
            connection=connection,
            target_metadata=self.environment["target_metadata"],
            compare_type=True,
            compare_server_default=True,
        )
        self.assertEqual(events, ["enter", "migrate", "exit"])

    async def test_async_path_uses_run_sync_and_always_disposes(self):
        connection = Mock(spec=["run_sync"])
        connection.run_sync = AsyncMock()
        engine = Mock()
        engine.connect.return_value = _AsyncConnectionContext(connection)
        engine.dispose = AsyncMock()
        self.environment["run_migrations_async"].__globals__[
            "async_engine_from_config"
        ] = Mock(return_value=engine)

        await self.environment["run_migrations_async"]()

        connection.run_sync.assert_awaited_once_with(
            self.environment["do_run_migrations"]
        )
        self.assertFalse(hasattr(connection, "begin_transaction"))
        engine.dispose.assert_awaited_once_with()

    async def test_async_engine_disposes_when_connection_fails(self):
        engine = Mock()
        engine.connect.return_value = _AsyncConnectionContext(
            None, RuntimeError("connection failed")
        )
        engine.dispose = AsyncMock()
        self.environment["run_migrations_async"].__globals__[
            "async_engine_from_config"
        ] = Mock(return_value=engine)

        with self.assertRaisesRegex(RuntimeError, "connection failed"):
            await self.environment["run_migrations_async"]()

        engine.dispose.assert_awaited_once_with()


if __name__ == "__main__":
    unittest.main()
