"""Ambiguous payment creation must be durable in a real PostgreSQL transaction.

A unit test with a mocked session cannot prove the invariant that matters: the
order must survive whatever the request does *after* create_order() raised,
because the external side effect (a YooKassa payment) may already exist while
the Telegram call that follows can fail. This suite pins that against the real
transaction boundary: the request session is rolled back afterwards, and the
order must still be there.
"""
import os
import unittest
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

try:
    from tests.db_utils import TRUNCATE_SQL
except ImportError:  # direct unittest discover run
    from db_utils import TRUNCATE_SQL

DB = os.getenv("TEST_DATABASE_URL")


@unittest.skipUnless(DB, "TEST_DATABASE_URL is not set")
class AmbiguousOrderDurabilityPostgresTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine(DB)
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)
        async with self.sessions.begin() as session:
            await session.execute(text(TRUNCATE_SQL))
            from database.models import User as DBUser

            self.user = DBUser(telegram_id=515151)
            session.add(self.user)
            await session.flush()
            self.user_id = self.user.id
        from config.settings import get_settings

        get_settings.cache_clear()

    async def asyncTearDown(self):
        await self.engine.dispose()
        from config.settings import get_settings

        get_settings.cache_clear()

    async def test_order_survives_rollback_after_handler_failure(self):
        """The ambiguous order must persist even if the request later fails."""
        from database.models import Order
        from integrations.payment_gateways.base import (
            PaymentCreationAmbiguousError,
        )
        from services.order_service import OrderService

        gateway = MagicMock()
        gateway.create_payment_url = AsyncMock(
            side_effect=PaymentCreationAmbiguousError(
                "payment_creation_ambiguous:timeout"
            )
        )

        # Request session, exactly like DBSessionMiddleware/session_scope.
        request_session = self.sessions()
        with patch(
            "services.order_service.get_payment_gateway", return_value=gateway
        ):
            with self.assertRaises(PaymentCreationAmbiguousError):
                await OrderService.create_order(
                    request_session,
                    user_id=self.user_id,
                    service_type="topup",
                    amount_rub=Decimal("500"),
                    payment_method="yookassa",
                )

        # Simulate what session_scope() does when the handler fails while
        # sending its Telegram response: the whole request rolls back.
        await request_session.rollback()
        await request_session.close()

        # A fresh connection must still see the order: the payment may exist
        # at the provider, so the webhook needs something to settle.
        async with self.sessions() as verify:
            result = await verify.execute(select(Order))
            rows = result.scalars().all()
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0].user_id, self.user_id)
            self.assertEqual(rows[0].status, "pending")
            self.assertTrue(rows[0].metadata_.get("payment_creation_ambiguous"))
            self.assertIsNone(rows[0].payment_url)
            self.assertIsNone(rows[0].external_id)


if __name__ == "__main__":
    unittest.main()
