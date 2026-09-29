"""Postgres behavioral matrix for referral qualifying-activity qualification.

Runs only with TEST_DATABASE_URL (CI). Skipped on Windows dev machines.

Active = qualifying top-up ≥ REFERRAL_ACTIVE_MIN_TOPUP_RUB (68 = discounted Base-30):
  A:  paid topup Order 100, no Payment            -> counts
  A2: paid topup Order 10 (dust)                  -> NOT counted
  A3: paid non-topup Order 500 (tariff purchase)  -> NOT counted
  B:  succeeded+fulfilled+credited Payment 100    -> counts (legacy, no Order)
  B2: succeeded+fulfilled+credited Payment 10     -> NOT counted (dust)
  C:  succeeded Payment, credited_at NULL         -> NOT counted
  D:  refunded/reversed Payment                   -> NOT counted
  E:  no Order, no Payment (freebie sub)          -> NOT counted
  F:  deleted referral with paid topup Order      -> NOT counted
  G:  pending topup Order only                    -> NOT counted
  H:  zero-amount paid topup Order                 -> NOT counted

Expected: active == 2 (A, B).
"""

import os
import unittest
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import patch

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from database.models import Order, Payment
from database.repositories import users_repo

DB = os.getenv("TEST_DATABASE_URL")


@unittest.skipUnless(DB, "TEST_DATABASE_URL is not set")
class ReferralPaidActivityMatrixTests(unittest.IsolatedAsyncioTestCase):
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
                "DATABASE_URL": os.getenv(
                    "TEST_DATABASE_URL",
                    "postgresql+asyncpg://projectx:projectx@localhost:5432/projectx_test",
                ),
            },
        )
        self.env_patcher.start()
        from config.settings import get_settings

        get_settings.cache_clear()

        self.engine = create_async_engine(DB)
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)

        try:
            from tests.db_utils import TRUNCATE_SQL
        except ImportError:
            from db_utils import TRUNCATE_SQL

        from sqlalchemy import text

        async with self.engine.begin() as conn:
            await conn.execute(text(TRUNCATE_SQL))

    async def asyncTearDown(self):
        from config.settings import get_settings

        get_settings.cache_clear()
        self.env_patcher.stop()
        await self.engine.dispose()

    def _payment(
        self,
        user_id,
        tag,
        provider_status="succeeded",
        fulfillment_status="succeeded",
        credited=True,
        amount=Decimal("100"),
    ):
        return Payment(
            user_id=user_id,
            amount=amount,
            public_order_id=f"matrix-{tag}",
            provider_idempotency_key=f"matrix-key-{tag}",
            provider_status=provider_status,
            fulfillment_status=fulfillment_status,
            credited_at=(
                datetime.now(timezone.utc) if credited else None
            ),
        )

    async def test_matrix(self):
        async with self.sessions.begin() as session:
            ref = await users_repo.create_user(session, telegram_id=900001)
            a = await users_repo.create_user(session, telegram_id=900002, referred_by=900001)
            a2 = await users_repo.create_user(session, telegram_id=900010, referred_by=900001)
            a3 = await users_repo.create_user(session, telegram_id=900011, referred_by=900001)
            b = await users_repo.create_user(session, telegram_id=900003, referred_by=900001)
            b2 = await users_repo.create_user(session, telegram_id=900012, referred_by=900001)
            c = await users_repo.create_user(session, telegram_id=900004, referred_by=900001)
            d = await users_repo.create_user(session, telegram_id=900005, referred_by=900001)
            # E: nothing (freebie) — user exists, no Order/Payment rows
            await users_repo.create_user(session, telegram_id=900006, referred_by=900001)
            f = await users_repo.create_user(session, telegram_id=900007, referred_by=900001)
            g = await users_repo.create_user(session, telegram_id=900008, referred_by=900001)
            h = await users_repo.create_user(session, telegram_id=900009, referred_by=900001)

            # A: qualifying paid topup order
            session.add(Order(user_id=a.id, service_type="topup", amount_rub=Decimal("100"), status="paid"))
            # A2: dust topup below threshold
            session.add(Order(user_id=a2.id, service_type="topup", amount_rub=Decimal("10"), status="paid"))
            # A3: direct tariff purchase is NOT a top-up
            session.add(Order(user_id=a3.id, service_type="awg", amount_rub=Decimal("500"), status="paid"))
            # B: real legacy topup, no order
            session.add(self._payment(b.id, "b"))
            # B2: legacy dust topup
            session.add(self._payment(b2.id, "b2", amount=Decimal("10")))
            # C: succeeded but never credited
            session.add(self._payment(c.id, "c", credited=False))
            # D: refunded/reversed
            session.add(
                self._payment(
                    d.id,
                    "d",
                    provider_status="refunded",
                    fulfillment_status="reversed",
                )
            )
            # E: nothing (freebie) — no rows
            # F: deleted referral with qualifying paid topup order
            session.add(Order(user_id=f.id, service_type="topup", amount_rub=Decimal("100"), status="paid"))
            f.is_deleted = True
            # G: pending topup order only
            session.add(Order(user_id=g.id, service_type="topup", amount_rub=Decimal("100"), status="pending"))
            # H: zero-amount paid topup order
            session.add(Order(user_id=h.id, service_type="topup", amount_rub=Decimal("0"), status="paid"))

            count = await users_repo.get_user_active_referrals_count(session, 900001)
            self.assertEqual(count, 2)

            leaders = await users_repo.get_referral_leaderboard(session, limit=5)
            self.assertIn((900001, 2), leaders)

            rank, active = await users_repo.get_user_referral_rank(session, 900001)
            self.assertEqual(active, 2)
            self.assertEqual(rank, 1)
            self.assertIsNotNone(ref)


if __name__ == "__main__":
    unittest.main()
