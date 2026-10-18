"""Concurrency tests for White Internet purchase and conversion execution."""

import unittest
from unittest.mock import AsyncMock, patch

from bot import texts
from config.constants import XRAY_PROTOCOL
from config.enums import WhiteInternetStatus
from database.models import Server, User, WhiteInternetSubscription
from services.white_internet_service import WhiteInternetService


class TestWhiteInternetConcurrencyExecute(unittest.IsolatedAsyncioTestCase):
    """Test concurrent purchase and conversion execution."""

    async def asyncSetUp(self):
        self.session = AsyncMock()
        self.user = User(id=1, telegram_id=12345678)
        self.server = Server(
            id=10,
            name="Origin-NL",
            protocol=XRAY_PROTOCOL,
            api_url="http://node.local:8080",
            api_key="secret",
            is_active=True,
        )

    async def test_purchase_subscription_returns_already_active_if_active_exists(self):
        """purchase_subscription must fail gracefully with WL_ALREADY_ACTIVE if user already has an active subscription."""
        from datetime import timedelta
        from utils.datetime_helpers import now_utc

        active_sub = WhiteInternetSubscription(
            id=1,
            user_id=self.user.id,
            is_trial=False,
            status=WhiteInternetStatus.ACTIVE,
            expires_at=now_utc() + timedelta(days=30),
        )

        with patch("services.white_internet_service.lock_checkout_user", return_value=self.user):
            with patch("database.repositories.white_internet_repo.get_subscription_by_user_id", return_value=active_sub):
                ok, msg, sub = await WhiteInternetService.purchase_subscription(self.session, self.user.id)
                self.assertFalse(ok)
                self.assertEqual(msg, texts.WL_ALREADY_ACTIVE)
                self.assertEqual(sub, active_sub)

    async def test_convert_trial_to_paid_concurrency_guard(self):
        """If subscription is already paid (not trial), convert_trial_to_paid returns (False, WL_ALREADY_ACTIVE, sub)."""
        already_converted_sub = WhiteInternetSubscription(
            id=1,
            user_id=self.user.id,
            is_trial=False,
            status=WhiteInternetStatus.ACTIVE,
        )

        with patch("services.white_internet_service.lock_checkout_user", return_value=self.user):
            with patch("database.repositories.white_internet_repo.get_subscription_by_user_id", return_value=already_converted_sub):
                ok, msg, sub = await WhiteInternetService.convert_trial_to_paid(self.session, self.user.id)
                self.assertFalse(ok)
                self.assertEqual(msg, texts.WL_ALREADY_ACTIVE)
                self.assertEqual(sub, already_converted_sub)


if __name__ == "__main__":
    unittest.main()
