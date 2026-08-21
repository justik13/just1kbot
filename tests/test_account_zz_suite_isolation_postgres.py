"""Keep account-ledger integration fixtures from leaking into later test modules."""

import os
import unittest

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

DB = os.getenv("TEST_DATABASE_URL")


@unittest.skipUnless(DB, "TEST_DATABASE_URL is not set")
class AccountSuiteIsolationPostgresTests(unittest.IsolatedAsyncioTestCase):
    async def test_account_fixture_boundary_cleans_database(self):
        engine = create_async_engine(DB)
        try:
            async with engine.begin() as connection:
                await connection.execute(
                    text("TRUNCATE users RESTART IDENTITY CASCADE")
                )
        finally:
            await engine.dispose()
