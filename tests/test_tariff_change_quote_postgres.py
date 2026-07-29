"""PostgreSQL boundary smoke tests live here; skipped without TEST_DATABASE_URL."""
import os
import unittest


@unittest.skipUnless(os.getenv("TEST_DATABASE_URL"), "TEST_DATABASE_URL is not configured")
class TariffChangeQuotePostgresTests(unittest.IsolatedAsyncioTestCase):
    async def test_environment_is_postgresql(self):
        self.assertTrue(os.environ["TEST_DATABASE_URL"].startswith("postgresql"))
