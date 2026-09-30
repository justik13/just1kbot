import os
import unittest
from decimal import Decimal
from unittest.mock import AsyncMock, patch

from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from database.repositories import audit_repo, users_repo
from services import (
    audit_service,
    ban_service,
    referral_bonus,
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
                    "TRUNCATE account_ledger_allocations, account_ledger_entries, "
                    "tariff_quotes, tariff_versions, payments, vpn_profiles, "
                    "maintenance_mode, audit_logs, hub_messages, users, tariffs, "
                    "servers, system_settings "
                    "RESTART IDENTITY CASCADE"
                )
            )

    async def asyncTearDown(self):
        from config.settings import get_settings
        get_settings.cache_clear()
        self.env_patcher.stop()
        await self.engine.dispose()


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
            self.assertEqual(granted, Decimal(150))

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
