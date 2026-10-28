import os
import unittest
from datetime import timedelta
from decimal import Decimal
from unittest.mock import AsyncMock, patch

from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from database.models import Order
from database.repositories import audit_repo, users_repo
from services import (
    audit_service,
    ban_service,
    referral_bonus,
    yookassa_service,
)
from services.fulfillment_service import FulfillmentService
from utils.datetime_helpers import now_utc

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

        from tests.db_utils import TRUNCATE_SQL
        async with self.sessions.begin() as session:
            await session.execute(text(TRUNCATE_SQL))

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

    async def test_fulfillment_service_order_lifecycle(self):
        async with self.sessions.begin() as session:
            user = await users_repo.create_user(session, telegram_id=5001, username="fulfil_test")
            base_now = now_utc()
            initial_end = base_now + timedelta(days=10)
            user.subscription_end = initial_end
            user.notified_3d = True
            user.notified_1d = True
            user.notification_retry_count = 3
            await session.flush()

            order = Order(
                user_id=user.id,
                service_type="standard",
                amount_rub=Decimal("100.00"),
                duration_days=30,
                device_limit=2,
                payment_method="yookassa",
                status="paid",
            )
            session.add(order)
            await session.flush()

            # 1. Fulfill order: linear extension of subscription and flag resets
            await FulfillmentService.fulfill_order(session, order)

            expected_end = initial_end + timedelta(days=30)
            self.assertEqual(user.subscription_end, expected_end)
            self.assertEqual(user.device_limit, 2)
            self.assertFalse(user.notified_3d)
            self.assertFalse(user.notified_1d)
            self.assertEqual(user.notification_retry_count, 0)
            self.assertIn("previous_subscription_end", order.metadata_ or {})

            # 2. Revoke order: rollback subscription duration
            await FulfillmentService.revoke_order(session, order)
            self.assertEqual(user.subscription_end, initial_end)

            # 3. Revoke beyond current date drops to cutoff
            user.subscription_end = now_utc() + timedelta(days=5)
            await FulfillmentService.revoke_order(session, order)
            self.assertLess(user.subscription_end, now_utc())


if __name__ == "__main__":
    unittest.main()
