import asyncio
import os
import unittest
import uuid
from decimal import Decimal

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from database.models import AccountLedgerEntry, Payment, User
from database.repositories.account_ledger_repo import get_account_balance
from services.account_topup import settle_succeeded_topup
from utils import now_utc

try:
    from tests.db_utils import TRUNCATE_SQL
except ImportError:  # direct unittest discover run
    from db_utils import TRUNCATE_SQL


DB = os.getenv("TEST_DATABASE_URL")


@unittest.skipUnless(DB, "TEST_DATABASE_URL is not set")
class AccountTopupConcurrencyPostgresTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from unittest.mock import patch
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
                "AMNEZIA_BRIDGE_HMAC_SECRET": "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
                "DATABASE_URL": os.environ["TEST_DATABASE_URL"],
            },
        )
        self.env_patcher.start()
        from config.settings import get_settings
        get_settings.cache_clear()

        self.engine = create_async_engine(DB, pool_size=5)
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)
        async with self.sessions.begin() as session:
            await session.execute(text(TRUNCATE_SQL))

            self.user = User(telegram_id=int(uuid.uuid4().int % 1000000000))
            session.add(self.user)
            await session.flush()

            self.payment = Payment(
                user_id=self.user.id,
                amount=Decimal("1000.00"),
                currency="RUB",
                public_order_id="topup_a",
                provider_idempotency_key="key_a",
                external_id="ext_a",
                provider_status="succeeded",
                provider_confirmed_at=now_utc(),
                fulfillment_status="not_ready",
                reconciliation_status="ok",
                checkout_status="active",
                ui_visible=False,
            )
            session.add(self.payment)
            await session.flush()
            self.payment_id = self.payment.id

    async def asyncTearDown(self):
        await self.engine.dispose()
        self.env_patcher.stop()
        from config.settings import get_settings
        get_settings.cache_clear()

    async def test_concurrent_same_payment_topup(self):
        # Simulate two concurrent webhooks processing the exact same payment.
        # Exactly one ledger credit entry must be created regardless of concurrency.

        async def process_payment():
            async with self.sessions() as session, session.begin():
                payment = await session.get(Payment, self.payment_id)
                await settle_succeeded_topup(session, payment=payment, source="test")

        results = await asyncio.gather(
            process_payment(),
            process_payment(),
            return_exceptions=True
        )
        # At least one must succeed; exceptions from the losing transaction are acceptable.
        successes = [r for r in results if not isinstance(r, Exception)]
        self.assertGreaterEqual(len(successes), 1)

        async with self.sessions() as session:
            balance = await get_account_balance(session, user_id=self.user.id)

            # Since both tried to settle the same 1000 RUB payment concurrently,
            # exactly one should have succeeded and the other should have either
            # raised an exception or exited early because the payment status changed.
            # The balance must be exactly 1000 RUB.
            self.assertEqual(balance.real_position, Decimal("1000.00"))

            # Check how many ledger entries were created for topup
            result = await session.execute(
                select(AccountLedgerEntry).where(
                    AccountLedgerEntry.user_id == self.user.id,
                    AccountLedgerEntry.payment_id == self.payment_id,
                    AccountLedgerEntry.entry_type == "payment_credit"
                )
            )
            entries = result.scalars().all()
            self.assertEqual(len(entries), 1, "Exactly one topup ledger entry must be created")

            payment_db = await session.get(Payment, self.payment_id)
            self.assertIsNotNone(payment_db.credited_at)
            self.assertEqual(payment_db.fulfillment_status, "succeeded")
