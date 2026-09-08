"""Unit tests for reset_user_trial safety and B18 timestamp updates."""

import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from bot import texts
from config.enums import WhiteInternetProvisioningStatus, WhiteInternetStatus
from database.models import User, WhiteInternetSubscription
from services.white_internet_service import WhiteInternetService


class TestWhiteInternetTrialResetSafety(unittest.IsolatedAsyncioTestCase):
    """Test suite ensuring reset_user_trial does not affect paid subscriptions."""

    async def asyncSetUp(self):
        self.session = AsyncMock()
        self.user = User(
            id=101,
            telegram_id=11223344,
            last_trial_reset_at=None,
        )

    async def test_reset_user_trial_leaves_paid_subscription_untouched(self):
        """reset_user_trial must never deprovision or alter a paid subscription."""
        paid_sub = WhiteInternetSubscription(
            id=50,
            user_id=self.user.id,
            is_trial=False,
            status=WhiteInternetStatus.ACTIVE,
            provisioning_status=WhiteInternetProvisioningStatus.ACTIVE,
            base_traffic_bytes=50 * 1024**3,
        )

        self.session.execute.return_value = MagicMock(scalars=lambda: MagicMock(all=lambda: [paid_sub]))

        with patch("services.white_internet_service.lock_checkout_user", return_value=self.user):
            success, msg = await WhiteInternetService.reset_user_trial(
                self.session, self.user.id
            )

            self.assertTrue(success)
            self.assertEqual(msg, texts.ADMIN_WL_RESET_SUCCESS)
            self.assertIsNotNone(self.user.last_trial_reset_at)
            self.assertEqual(paid_sub.status, WhiteInternetStatus.ACTIVE)
            self.assertFalse(paid_sub.is_trial)

    async def test_reset_user_trial_deprovisions_trial_subscription_and_sets_timestamp(self):
        """reset_user_trial deprovisions trial subscription and updates last_trial_reset_at (B18)."""
        trial_sub = WhiteInternetSubscription(
            id=51,
            user_id=self.user.id,
            is_trial=True,
            status=WhiteInternetStatus.ACTIVE,
            provisioning_status=WhiteInternetProvisioningStatus.ACTIVE,
            base_traffic_bytes=5 * 1024**3,
            desired_version=1,
            uuid="uuid-trial",
        )

        self.session.execute.return_value = MagicMock(scalars=lambda: MagicMock(all=lambda: [trial_sub]))

        with patch("services.white_internet_service.lock_checkout_user", return_value=self.user), \
             patch("services.white_internet_service._dispatch_deprovision"):
            success, msg = await WhiteInternetService.reset_user_trial(
                self.session, self.user.id
            )

            self.assertTrue(success)
            self.assertEqual(msg, texts.ADMIN_WL_RESET_SUCCESS)
            self.assertIsNotNone(self.user.last_trial_reset_at)
            self.assertEqual(trial_sub.status, WhiteInternetStatus.DISABLED)
            self.assertEqual(trial_sub.provisioning_status, WhiteInternetProvisioningStatus.PENDING_DELETE)


if __name__ == "__main__":
    unittest.main()
