import os
import unittest
from decimal import Decimal
from unittest.mock import AsyncMock, patch

from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from database.models import Tariff
from database.repositories import account_ledger_repo, audit_repo, users_repo
from services import (
    account_purchase,
    audit_service,
    ban_service,
    referral_bonus,
    tariff_value_calculator,
    yookassa_service,
)

DB = os.getenv("TEST_DATABASE_URL")


@unittest.skipUnless(DB, "TEST_DATABASE_URL is not set")
class ServicesCoreFullCoverageTests(unittest.IsolatedAsyncioTestCase):
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


    async def test_tariff_value_calculator(self):
        t_src = tariff_value_calculator.TariffVersionSnapshot(
            tariff_id=1, version_id=1, duration_hours=720, price_rub=Decimal(300)
        )
        t_tgt = tariff_value_calculator.TariffVersionSnapshot(
            tariff_id=2, version_id=2, duration_hours=720, price_rub=Decimal(600)
        )

        calc = tariff_value_calculator.calculate_tariff_value(
            operation_type="change",
            source_paid_hours=360,
            source_paid_value_rub=Decimal(150),
            source_tariff=t_src,
            target_tariff=t_tgt,
            confirmed_additional_payment_rub=Decimal(450),
            bonus_hours=0,
        )
        self.assertTrue(calc.invariant_holds)

    async def test_referral_bonus(self):
        async with self.sessions.begin() as session:
            referrer = await users_repo.create_user(session, telegram_id=1001, username="ref_1")
            self.assertIsNotNone(referrer)
            user = await users_repo.create_user(session, telegram_id=1002, username="ref_2", referred_by=1001)
            await session.flush()

            granted = await referral_bonus.grant_referral_bonus_for_topup(
                session,
                purchaser_user_id=user.id,
                payment_id=1,
                topup_amount=Decimal(1000),
            )
            self.assertEqual(granted, Decimal(100))

    async def test_account_purchase_and_topup(self):
        async with self.sessions.begin() as session:
            u = await users_repo.create_user(session, telegram_id=2001, username="buyer")
            t = Tariff(name="Base", duration_days=30, device_limit=2, price_rub=500, is_active=True)
            session.add(t)
            await session.flush()

            entry, _ = await account_ledger_repo.create_admin_adjustment(
                session,
                user_id=u.id,
                signed_amount=Decimal(1000),
                idempotency_key="adj_1001",
                metadata={"reason": "test_topup"},
            )
            self.assertIsNotNone(entry.id)

            intent = await account_purchase.prepare_account_purchase(
                session,
                user_id=u.id,
                tariff_id=t.id,
            )
            self.assertIsNotNone(intent.quote)
            self.assertEqual(intent.shortage, Decimal(0))

    async def test_ban_and_maintenance_service(self):
        async with self.sessions.begin() as session:
            u = await users_repo.create_user(session, telegram_id=3001, username="to_be_banned")
            await session.flush()

            success, msg = await ban_service.BanService.toggle_ban(
                session,
                admin_id=12345,
                telegram_id=u.telegram_id,
            )
            self.assertTrue(success)

            banned_u = await users_repo.get_user_by_telegram_id(session, u.telegram_id)
            self.assertTrue(banned_u.is_banned)

    async def test_audit_service(self):
        async with self.sessions.begin() as session:
            await audit_service.AuditService.log_action(
                session,
                admin_id=99999,
                action="ADMIN_TEST",
                target_type="System",
                target_id=1,
                details="Detailed test message",
            )
            logs = await audit_repo.get_recent_audit_logs(session, limit=1)
            self.assertEqual(len(logs), 1)

    async def test_yookassa_service(self):
        with patch("aiohttp.ClientSession.request") as mock_req:
            mock_resp = AsyncMock()
            mock_resp.status = 200
            mock_resp.json.return_value = {
                "id": "22d38f5d-000f-5000-9000-100000000000",
                "status": "pending",
            }
            mock_req.return_value.__aenter__.return_value = mock_resp

            payload = {"amount": {"value": "500.00", "currency": "RUB"}, "confirmation": {"type": "redirect", "return_url": "https://t.me/bot"}}
            res = await yookassa_service.YooKassaService.create_payment_result(
                payload,
                idempotency_key="idemp_yoo_1",
            )
            self.assertTrue(res.ok)
            self.assertEqual(res.value["id"], "22d38f5d-000f-5000-9000-100000000000")


if __name__ == "__main__":
    unittest.main()
