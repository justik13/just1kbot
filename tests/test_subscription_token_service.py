import asyncio
import os
import unittest
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from database.models import User
from integrations.incy.token_service import (
    MAX_SUBSCRIPTION_TOKEN_LENGTH,
    SubscriptionTokenService,
)

DB = os.getenv("TEST_DATABASE_URL")
TRUNCATE_SQL = (
    "TRUNCATE provider_refund_operations, webhook_inbox, payment_refunds, "
    "account_balance_reservations, "
    "account_ledger_allocations, account_ledger_entries, "
    "payment_events, audit_logs, payments, users, system_settings, payment_disputes RESTART IDENTITY CASCADE"
)


def _make_mock_session(user_in_db: User):
    session = AsyncMock()
    session.begin_nested = MagicMock()
    nested = AsyncMock()
    nested.__aenter__.return_value = nested
    nested.__aexit__.return_value = None
    session.begin_nested.return_value = nested

    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = user_in_db
    session.execute.return_value = mock_result
    return session, nested


class SubscriptionTokenServiceUnitTests(unittest.IsolatedAsyncioTestCase):
    async def test_generate_token_format(self):
        token = SubscriptionTokenService.generate_token()
        self.assertIsInstance(token, str)
        self.assertGreaterEqual(len(token), 32)
        self.assertLessEqual(len(token), MAX_SUBSCRIPTION_TOKEN_LENGTH)

    async def test_get_or_create_token_when_none(self):
        user = User(id=1, telegram_id=12345, subscription_token=None)
        session, _nested = _make_mock_session(user)

        token = await SubscriptionTokenService.get_or_create_token(session, user)
        self.assertIsNotNone(token)
        self.assertEqual(user.subscription_token, token)
        session.flush.assert_awaited_once()

    async def test_get_or_create_token_idempotent(self):
        user = User(id=1, telegram_id=12345, subscription_token="existing_token_xyz")
        session, _ = _make_mock_session(user)

        token = await SubscriptionTokenService.get_or_create_token(session, user)
        self.assertEqual(token, "existing_token_xyz")
        session.flush.assert_not_awaited()

    async def test_get_or_create_token_collision_retry(self):
        user = User(id=10, telegram_id=12345, subscription_token=None)
        session, _ = _make_mock_session(user)

        # Fail first attempt with collision, succeed on second
        session.flush.side_effect = [
            IntegrityError("duplicate key", params=None, orig=Exception()),
            None,
        ]

        token = await SubscriptionTokenService.get_or_create_token(session, user)
        self.assertIsNotNone(token)
        self.assertEqual(user.subscription_token, token)
        self.assertEqual(session.flush.await_count, 2)

    async def test_rotate_token(self):
        user = User(id=1, telegram_id=12345, subscription_token="old_token_123")
        session, _ = _make_mock_session(user)

        new_token = await SubscriptionTokenService.rotate_token(session, user)
        self.assertNotEqual(new_token, "old_token_123")
        self.assertEqual(user.subscription_token, new_token)
        session.flush.assert_awaited_once()

    async def test_get_user_by_token_validation(self):
        session = AsyncMock()
        res_empty = await SubscriptionTokenService.get_user_by_token(session, "")
        self.assertIsNone(res_empty)

        res_huge = await SubscriptionTokenService.get_user_by_token(
            session, "a" * (MAX_SUBSCRIPTION_TOKEN_LENGTH + 1)
        )
        self.assertIsNone(res_huge)

    @patch("integrations.incy.token_service.get_user_by_subscription_token")
    async def test_get_user_by_token_delegates(self, mock_get_repo):
        user = User(id=42, telegram_id=999, subscription_token="valid_token")
        mock_get_repo.return_value = user
        session = AsyncMock()

        found = await SubscriptionTokenService.get_user_by_token(session, "valid_token")
        self.assertEqual(found, user)
        mock_get_repo.assert_awaited_once_with(session, "valid_token")


@unittest.skipUnless(DB, "TEST_DATABASE_URL is not set")
class SubscriptionTokenServicePostgresConcurrencyTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine(DB, pool_size=10, max_overflow=20)
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)

        async with self.sessions.begin() as session:
            await session.execute(text(TRUNCATE_SQL))
            self.user = User(telegram_id=int(uuid.uuid4().int % 1000000000))
            session.add(self.user)
            await session.flush()
            self.user_id = self.user.id

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def test_concurrent_get_or_create_token_returns_identical_token(self):
        async def worker_get_token():
            async with self.sessions.begin() as session:
                user = await session.get(User, self.user_id)
                return await SubscriptionTokenService.get_or_create_token(session, user)

        # Run 2 concurrent requests with independent sessions
        token_a, token_b = await asyncio.gather(
            worker_get_token(),
            worker_get_token(),
        )

        self.assertIsNotNone(token_a)
        self.assertIsNotNone(token_b)
        # Both concurrent callers must receive the EXACT same token
        self.assertEqual(token_a, token_b)

        # Verify token stored in DB matches
        async with self.sessions.begin() as session:
            db_user = await session.get(User, self.user_id)
            self.assertEqual(db_user.subscription_token, token_a)

    async def test_concurrent_rotate_token_consistency(self):
        async def worker_rotate():
            async with self.sessions.begin() as session:
                user = await session.get(User, self.user_id)
                return await SubscriptionTokenService.rotate_token(session, user)

        token_1, token_2 = await asyncio.gather(
            worker_rotate(),
            worker_rotate(),
        )

        self.assertIsNotNone(token_1)
        self.assertIsNotNone(token_2)

        # Verify DB contains the winner token
        async with self.sessions.begin() as session:
            db_user = await session.get(User, self.user_id)
            self.assertIn(db_user.subscription_token, [token_1, token_2])


if __name__ == "__main__":
    unittest.main()
