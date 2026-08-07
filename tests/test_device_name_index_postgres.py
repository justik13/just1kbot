import os
import unittest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from database.models import Server, User, VPNProfile


@unittest.skipUnless(os.getenv("TEST_DATABASE_URL"), "TEST_DATABASE_URL is not set")
class DeviceNameIndexPostgresTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine(os.environ["TEST_DATABASE_URL"])
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)
        async with self.sessions.begin() as s:
            await s.execute(
                text(
                    "TRUNCATE account_balance_reservations, "
                    "account_ledger_allocations, account_ledger_entries, "
                    "entitlement_entries, paid_value_ledger, "
                    "tariff_quotes, tariff_versions, payments, api_operations, vpn_profiles, users, servers "
                    "RESTART IDENTITY CASCADE"
                )
            )

    async def asyncTearDown(self):
        async with self.sessions.begin() as s:
            await s.execute(
                text(
                    "TRUNCATE account_balance_reservations, "
                    "account_ledger_allocations, account_ledger_entries, "
                    "entitlement_entries, paid_value_ledger, "
                    "tariff_quotes, tariff_versions, payments, api_operations, vpn_profiles, users, servers "
                    "RESTART IDENTITY CASCADE"
                )
            )
        await self.engine.dispose()

    async def test_device_name_index_uses_column_expression(self):
        async with self.sessions() as s:
            definition = (
                (
                    await s.execute(
                        text(
                            "SELECT pg_get_indexdef(indexrelid) FROM pg_index WHERE indexrelid='uq_vpn_profiles_user_server_device_name'::regclass"
                        )
                    )
                )
                .scalar_one()
                .lower()
            )
        self.assertIn("lower((device_name)::text)", definition)
        self.assertNotIn("lower('device_name'::text)", definition)

    async def test_device_name_index_allows_distinct_names_and_scopes_duplicates(self):
        async with self.sessions.begin() as s:
            u1 = User(telegram_id=92001)
            u2 = User(telegram_id=92002)
            a = Server(name="a", api_url="https://a", api_key="k")
            b = Server(name="b", api_url="https://b", api_key="k")
            s.add_all([u1, u2, a, b])
            await s.flush()

            def profile(user, server, name):
                return VPNProfile(
                    user_id=user.id,
                    server_id=server.id,
                    device_name=name,
                    peer_id=f"{user.id}-{server.id}-{name}",
                    raw_config="vpn://x",
                    provisioning_status="active",
                    desired_version=1,
                    desired_is_active=True,
                    is_active=True,
                )

            s.add_all(
                [
                    profile(u1, a, "phone"),
                    profile(u1, a, "tablet"),
                    profile(u1, a, "laptop"),
                ]
            )
            await s.flush()
            s.add_all([profile(u2, a, "phone"), profile(u1, b, "phone")])
            await s.flush()
            with self.assertRaises(IntegrityError):
                async with s.begin_nested():
                    s.add(profile(u1, a, "Phone"))
                    await s.flush()
