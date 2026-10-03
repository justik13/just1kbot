"""Unit tests for the simulator DB-target guard (no DB, no network, no subprocess)."""
import unittest

from scripts.simulate.db_targets import is_local_db_url


class SimulateDbGuardTests(unittest.TestCase):
    def test_local_targets(self):
        cases = [
            "sqlite+aiosqlite:///:memory:",
            "sqlite+aiosqlite:///sim.db",
            "SQLITE:///C:/sim.db",
            "postgresql+asyncpg://u:p@localhost:5432/bot",
            "postgresql://u:p@127.0.0.1/bot",
            "postgresql://u:p@[::1]:5432/bot",
        ]
        for url in cases:
            with self.subTest(url=url):
                self.assertTrue(is_local_db_url(url))

    def test_remote_or_invalid_targets(self):
        cases = [
            "postgresql+asyncpg://u:p@db.internal:5432/bot",
            "postgresql://u:p@192.168.1.10/bot",
            "postgresql://u:p@example.com/bot",
            "",
            None,
            "not a url",
        ]
        for url in cases:
            with self.subTest(url=url):
                self.assertFalse(is_local_db_url(url))


if __name__ == "__main__":
    unittest.main()
