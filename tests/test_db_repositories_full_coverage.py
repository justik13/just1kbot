import os
import unittest
from unittest.mock import patch


from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from database.models import (
    Tariff,
)
from database.repositories import (
    users_repo,
    servers_repo,
    tariffs_repo,
    profiles_repo,
    maintenance_repo,
    audit_repo,
)

DB = os.getenv("TEST_DATABASE_URL")


@unittest.skipUnless(DB, "TEST_DATABASE_URL is not set")
class DBRepositoriesFullCoverageTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.env_patcher = patch.dict(
            os.environ,
            {
                "BOT_TOKEN": "123:test",
                "REDIS_URL": "redis://localhost:6379/1",
                "REDIS_PASSWORD": "test",
                "ADMIN_IDS": "[123456789]",
                "SUPPORT_USERNAME": "test_support",
                "DOMAIN": "test.domain",
                "SSL_EMAIL": "test@domain.com",
                "YOOKASSA_SHOP_ID": "123456",
                "YOOKASSA_SECRET_KEY": "test_secret",
                "YOOKASSA_RETURN_URL": "https://t.me/{bot_username}",
                "YOOKASSA_WEBHOOK_PORT": "8080",
                "DB_ENCRYPTION_KEY": "MDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDA=",
                "DATABASE_URL": os.getenv("TEST_DATABASE_URL", "postgresql+asyncpg://projectx:projectx@localhost:5432/projectx_test"),
            },
        )
        self.env_patcher.start()
        from config.settings import get_settings
        get_settings.cache_clear()

        self.engine = create_async_engine(DB)
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)

        async with self.sessions.begin() as session:
            await session.execute(
                text(
                    "TRUNCATE account_balance_reservations, "
                    "account_ledger_allocations, account_ledger_entries, "
                    "entitlement_entries, paid_value_ledger, "
                    "tariff_quotes, tariff_versions, payments, vpn_profiles, "
                    "maintenance_mode, audit_logs, hub_messages, users, tariffs, servers, system_settings, payment_disputes "
                    "RESTART IDENTITY CASCADE"
                )
            )

    async def asyncTearDown(self):
        from config.settings import get_settings
        get_settings.cache_clear()
        self.env_patcher.stop()
        await self.engine.dispose()


    async def test_users_repo(self):
        async with self.sessions.begin() as session:
            u = await users_repo.create_user(
                session,
                telegram_id=111222333,
                username="repos_test_user",
                first_name="Repos",
            )
            self.assertIsNotNone(u.telegram_id)

            fetched = await users_repo.get_user_by_telegram_id(session, 111222333)
            self.assertIsNotNone(fetched)

            updated = await users_repo.update_user(session, u, device_limit=5)
            self.assertEqual(updated.device_limit, 5)

            ext = await users_repo.extend_subscription(session, u, 10)
            self.assertIsNotNone(ext.subscription_end)

            perm = await users_repo.extend_subscription(session, u, 40000)
            self.assertTrue(perm.subscription_end.year >= 2099)

            count = await users_repo.get_user_count(session)
            self.assertGreaterEqual(count, 1)

            active_count = await users_repo.get_active_subscriptions_count(session)
            self.assertGreaterEqual(active_count, 1)

    async def test_servers_repo(self):
        async with self.sessions.begin() as session:
            s = await servers_repo.create_server(
                session,
                name="RepoServer",
                country_flag="🇩🇪",
                api_url="https://server1.example.test:8080",
                api_key="secret_key_123",
                max_clients=10,
            )
            self.assertIsNotNone(s.id)

            active = await servers_repo.get_active_servers(session)
            self.assertIn(s.id, [x.id for x in active])

            avail = await servers_repo.get_available_servers(session)
            self.assertIn(s.id, [x.id for x in avail])

            upd = await servers_repo.update_server(session, s, max_clients=20)
            self.assertEqual(upd.max_clients, 20)

    async def test_tariffs_repo(self):
        async with self.sessions.begin() as session:
            t = Tariff(
                name="Standard Tariff",
                duration_days=30,
                device_limit=2,
                price_rub=300,
                is_active=True,
                sort_order=1,
            )
            session.add(t)
            await session.flush()
            await session.refresh(t)
            self.assertIsNotNone(t.id)

            active_t = await tariffs_repo.get_active_tariffs(session)
            self.assertIn(t.id, [x.id for x in active_t])

            by_id = await tariffs_repo.get_tariff_by_id(session, t.id)
            self.assertIsNotNone(by_id)

    async def test_profiles_repo(self):
        async with self.sessions.begin() as session:
            u = await users_repo.create_user(session, telegram_id=999888, username="profile_u")
            s = await servers_repo.create_server(session, name="ProfServer", api_url="https://ps.test", api_key="k")
            await session.flush()

            p = await profiles_repo.create_profile(
                session,
                user_id=u.id,
                server_id=s.id,
                device_name="My iPhone",
                peer_id="peer_123",
                raw_config="vpn://config",
            )
            self.assertIsNotNone(p.id)

            profs = await profiles_repo.get_user_profiles(session, u.id)
            self.assertEqual(len(profs), 1)

            upd = await profiles_repo.update_profile(session, p, device_name="iPhone 15")
            self.assertEqual(upd.device_name, "iPhone 15")

    async def test_maintenance_repo(self):
        async with self.sessions.begin() as session:
            m = await maintenance_repo.set_maintenance_mode(
                session,
                is_enabled=True,
                message="Maintenance in progress",
                updated_by=12345,
            )
            self.assertIsNotNone(m.id)

            is_enabled = await maintenance_repo.is_maintenance_enabled(session)
            self.assertTrue(is_enabled)

    async def test_audit_repo(self):
        async with self.sessions.begin() as session:
            log = await audit_repo.create_audit_log(
                session,
                admin_id=12345,
                action="TEST_ACTION",
                details="Test details",
            )
            self.assertIsNotNone(log.id)

            recent = await audit_repo.get_recent_audit_logs(session, limit=5)
            self.assertGreaterEqual(len(recent), 1)


if __name__ == "__main__":
    unittest.main()
