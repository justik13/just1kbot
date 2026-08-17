import asyncio
import os
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from database.models import User
from database.repositories import users_repo
from services.subscription_token_service import (
    MAX_SUBSCRIPTION_TOKEN_LENGTH,
    SubscriptionTokenService,
)

DB = os.getenv("TEST_DATABASE_URL")


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

    @patch("services.subscription_token_service.get_user_by_subscription_token")
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
                "DATABASE_URL": DB,
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
            created_user = await users_repo.create_user(session, telegram_id=999888777)
            self.user_id = created_user.id

    async def asyncTearDown(self):
        from config.settings import get_settings
        get_settings.cache_clear()
        self.env_patcher.stop()
        await self.engine.dispose()

    async def test_concurrent_get_or_create_token_returns_identical_token(self):
        async def worker_get_token():
            async with self.sessions.begin() as session:
                user = await users_repo.get_user_by_id(session, self.user_id)
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
            db_user = await users_repo.get_user_by_id(session, self.user_id)
            self.assertEqual(db_user.subscription_token, token_a)

    async def test_concurrent_rotate_token_consistency(self):
        async def worker_rotate():
            async with self.sessions.begin() as session:
                user = await users_repo.get_user_by_id(session, self.user_id)
                return await SubscriptionTokenService.rotate_token(session, user)

        token_1, token_2 = await asyncio.gather(
            worker_rotate(),
            worker_rotate(),
        )

        self.assertIsNotNone(token_1)
        self.assertIsNotNone(token_2)

        # Verify DB contains the winner token
        async with self.sessions.begin() as session:
            db_user = await users_repo.get_user_by_id(session, self.user_id)
            self.assertIn(db_user.subscription_token, [token_1, token_2])


if __name__ == "__main__":
    unittest.main()
