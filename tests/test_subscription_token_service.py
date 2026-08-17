import unittest
from unittest.mock import AsyncMock, patch

from database.models import User
from services.subscription_token_service import SubscriptionTokenService


class SubscriptionTokenServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_generate_token_format(self):
        token = SubscriptionTokenService.generate_token()
        self.assertIsInstance(token, str)
        self.assertGreaterEqual(len(token), 32)

    async def test_get_or_create_token_when_none(self):
        user = User(id=1, telegram_id=12345, subscription_token=None)
        session = AsyncMock()

        token = await SubscriptionTokenService.get_or_create_token(session, user)
        self.assertIsNotNone(token)
        self.assertEqual(user.subscription_token, token)
        session.flush.assert_awaited_once()

    async def test_get_or_create_token_idempotent(self):
        user = User(id=1, telegram_id=12345, subscription_token="existing_token_xyz")
        session = AsyncMock()

        token = await SubscriptionTokenService.get_or_create_token(session, user)
        self.assertEqual(token, "existing_token_xyz")
        session.flush.assert_not_awaited()

    async def test_rotate_token(self):
        user = User(id=1, telegram_id=12345, subscription_token="old_token_123")
        session = AsyncMock()

        new_token = await SubscriptionTokenService.rotate_token(session, user)
        self.assertNotEqual(new_token, "old_token_123")
        self.assertEqual(user.subscription_token, new_token)
        session.flush.assert_awaited_once()

    async def test_get_user_by_token_validation(self):
        session = AsyncMock()
        res_empty = await SubscriptionTokenService.get_user_by_token(session, "")
        self.assertIsNone(res_empty)

        res_huge = await SubscriptionTokenService.get_user_by_token(session, "a" * 200)
        self.assertIsNone(res_huge)

    @patch("services.subscription_token_service.get_user_by_subscription_token")
    async def test_get_user_by_token_delegates(self, mock_get_repo):
        user = User(id=42, telegram_id=999, subscription_token="valid_token")
        mock_get_repo.return_value = user
        session = AsyncMock()

        found = await SubscriptionTokenService.get_user_by_token(session, "valid_token")
        self.assertEqual(found, user)
        mock_get_repo.assert_awaited_once_with(session, "valid_token")


if __name__ == "__main__":
    unittest.main()
