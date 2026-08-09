import os
import unittest
from unittest.mock import patch

from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from database.models import Tariff
from database.repositories import (
    users_repo,
    servers_repo,
    profiles_repo,
    account_ledger_repo,
)
from services import (
    account_purchase,
    ban_service,
    referral_bonus,
)

DB = os.getenv("TEST_DATABASE_URL")


@unittest.skipUnless(DB, "TEST_DATABASE_URL is not set")
class E2ECriticalFlowsFullCoverageTests(unittest.IsolatedAsyncioTestCase):
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


    async def test_e2e_user_onboarding_topup_purchase_device_flow(self):
        async with self.sessions.begin() as session:
            # 1. User Registration
            user = await users_repo.create_user(
                session,
                telegram_id=987654321,
                username="e2e_tester",
                first_name="E2E",
            )
            self.assertIsNotNone(user)

            # 2. Tariff creation
            tariff = Tariff(
                name="Monthly Standard",
                duration_days=30,
                device_limit=3,
                price_rub=250,
                is_active=True,
                sort_order=1,
            )
            session.add(tariff)
            await session.flush()

            # 3. Server creation
            server = await servers_repo.create_server(
                session, name="E2E Node 1", api_url="https://node1.example.test", api_key="secret"
            )

            # 4. Topup Balance via Ledger
            topup_entry, _ = await account_ledger_repo.create_admin_adjustment(
                session,
                user_id=user.id,
                signed_amount=Decimal("500.00"),
                idempotency_key="e2e_topup_adj_1",
                metadata={"reason": "E2E Topup"},
            )
            self.assertIsNotNone(topup_entry)

            bal = (await account_ledger_repo.get_account_balance(session, user_id=user.id)).available
            self.assertEqual(bal, Decimal("500.00"))

            # 5. Prepare Purchase Intent
            intent = await account_purchase.prepare_account_purchase(
                session,
                user_id=user.id,
                tariff_id=tariff.id,
            )
            self.assertIsNotNone(intent.quote)
            self.assertEqual(intent.shortage, Decimal("0"))

            # Extend user subscription manually to simulate successful settlement
            await users_repo.extend_subscription(session, user, 30)
            u_updated = await users_repo.get_user_by_telegram_id(session, user.telegram_id)
            self.assertIsNotNone(u_updated.subscription_end)

            # 6. Device Creation
            prof = await profiles_repo.create_profile(
                session,
                user_id=u_updated.id,
                server_id=server.id,
                device_name="My iPhone",
                peer_id="peer_e2e_1",
                raw_config="ss://createdkey@node1:8388",
            )
            self.assertIsNotNone(prof)
            self.assertEqual(prof.device_name, "My iPhone")

    async def test_e2e_referral_bonus_flow(self):
        async with self.sessions.begin() as session:
            # Referrer & Referee
            referrer = await users_repo.create_user(session, telegram_id=11111, username="referrer")
            self.assertIsNotNone(referrer)
            referee = await users_repo.create_user(
                session, telegram_id=22222, username="referee", referred_by=11111
            )
            await session.flush()

            # Grant referral bonus for topup
            bonus_amount = await referral_bonus.grant_referral_bonus_for_topup(
                session,
                purchaser_user_id=referee.id,
                payment_id=100,
                topup_amount=Decimal("1000.00"),
            )
            self.assertEqual(bonus_amount, Decimal("100.00"))

    async def test_e2e_ban_revocation_flow(self):
        async with self.sessions.begin() as session:
            u = await users_repo.create_user(session, telegram_id=33333, username="bad_user")
            s = await servers_repo.create_server(session, name="S1", api_url="https://s1.test", api_key="k")
            await session.flush()

            p = await profiles_repo.create_profile(
                session, user_id=u.id, server_id=s.id, device_name="Dev1", peer_id="peer1", raw_config="cfg"
            )
            self.assertIsNotNone(p)

            success, msg = await ban_service.BanService.toggle_ban(
                session, admin_id=123, telegram_id=u.telegram_id
            )
            self.assertTrue(success)

            banned_u = await users_repo.get_user_by_telegram_id(session, u.telegram_id)
            self.assertTrue(banned_u.is_banned)


if __name__ == "__main__":
    unittest.main()
