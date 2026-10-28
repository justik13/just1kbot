"""Tests for White Internet trial reset safety and invariant protections."""

from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from sqlalchemy.ext.asyncio import AsyncSession

from bot import texts
from config.enums import (
    WhiteInternetProvisioningStatus,
    WhiteInternetStatus,
)
from database.models import Server, User, WhiteInternetSubscription
from database.repositories import white_internet_repo
from services.white_internet_service import WhiteInternetService


class TestWhiteInternetTrialProtectionAndReset(unittest.IsolatedAsyncioTestCase):
    """Test suite for WhiteInternetService trial reset and ban protection."""

    async def asyncSetUp(self):
        self.session = AsyncMock(spec=AsyncSession)

    async def test_banned_user_cannot_create_trial(self):
        """create_trial_subscription rejects banned user."""
        banned_user = User(id=1, telegram_id=555, is_banned=True, is_deleted=False)

        with patch("services.white_internet_service.lock_checkout_user", new=AsyncMock(return_value=banned_user)):
            ok, msg, sub = await WhiteInternetService.create_trial_subscription(self.session, banned_user.id)
            self.assertFalse(ok)
            self.assertIsNone(sub)

    async def test_reset_user_trial_marks_two_phase_delete_and_deprovisions(self):
        """reset_user_trial marks DISABLED+PENDING_DELETE+pending_hard_delete instead of deleting rows."""
        from config.constants import XRAY_PROTOCOL

        user = User(id=1, telegram_id=555, is_banned=False, is_deleted=False)
        server = Server(
            id=2,
            name="Origin",
            protocol=XRAY_PROTOCOL,
            api_url="https://node.example.test:8444",
            api_key="test-key",
        )
        sub = WhiteInternetSubscription(
            id=10,
            user_id=user.id,
            origin_node_id=2,
            uuid="client-uuid-123",
            status=WhiteInternetStatus.ACTIVE,
            desired_version=1,
            is_trial=True,
        )

        mock_exec = MagicMock()
        mock_exec.scalars.return_value.all.return_value = [sub]
        self.session.execute = AsyncMock(return_value=mock_exec)
        self.session.get = AsyncMock(return_value=server)

        with patch("services.white_internet_service.lock_checkout_user", new=AsyncMock(return_value=user)), \
             patch("services.white_internet_service._dispatch_deprovision") as mock_dispatch:

            ok, msg = await WhiteInternetService.reset_user_trial(self.session, user.id)
            self.assertTrue(ok)
            self.assertEqual(msg, texts.ADMIN_WL_RESET_SUCCESS)
            self.assertEqual(sub.status, WhiteInternetStatus.DISABLED)
            self.assertEqual(sub.provisioning_status, WhiteInternetProvisioningStatus.PENDING_DELETE)
            self.assertTrue(sub.pending_hard_delete)
            self.assertEqual(sub.desired_version, 2)
            self.session.delete.assert_not_called()
            self.session.flush.assert_awaited_once()
            mock_dispatch.assert_called_once()

    async def test_reset_user_trial_leaves_paid_subscription_untouched(self):
        """reset_user_trial must never deprovision or alter a paid subscription."""
        user = User(id=101, telegram_id=11223344, last_trial_reset_at=None)
        paid_sub = WhiteInternetSubscription(
            id=50,
            user_id=user.id,
            is_trial=False,
            status=WhiteInternetStatus.ACTIVE,
            provisioning_status=WhiteInternetProvisioningStatus.ACTIVE,
            base_traffic_bytes=50 * 1024**3,
        )

        mock_exec = MagicMock()
        mock_exec.scalars.return_value.all.return_value = [paid_sub]
        self.session.execute = AsyncMock(return_value=mock_exec)

        with patch("services.white_internet_service.lock_checkout_user", new=AsyncMock(return_value=user)):
            success, msg = await WhiteInternetService.reset_user_trial(self.session, user.id)

            self.assertTrue(success)
            self.assertEqual(msg, texts.ADMIN_WL_RESET_SUCCESS)
            self.assertIsNotNone(user.last_trial_reset_at)
            self.assertEqual(paid_sub.status, WhiteInternetStatus.ACTIVE)
            self.assertFalse(paid_sub.is_trial)

    async def test_finalize_hard_delete_only_after_node_confirmed_disable(self):
        """finalize_hard_delete_subscription deletes only confirmed-disabled rows, keeps pending ones."""
        confirmed = WhiteInternetSubscription(
            id=11,
            user_id=1,
            origin_node_id=2,
            status=WhiteInternetStatus.DISABLED,
            provisioning_status=WhiteInternetProvisioningStatus.SYNCED_INACTIVE,
            desired_version=3,
            actual_version=3,
            pending_hard_delete=True,
        )
        unconfirmed = WhiteInternetSubscription(
            id=12,
            user_id=1,
            origin_node_id=2,
            status=WhiteInternetStatus.DISABLED,
            provisioning_status=WhiteInternetProvisioningStatus.PENDING_DELETE,
            desired_version=3,
            actual_version=1,
            pending_hard_delete=True,
        )
        real_session = AsyncMock(spec=AsyncSession)

        with patch.object(
            white_internet_repo,
            "get_subscription_with_lock",
            new=AsyncMock(side_effect=[confirmed, unconfirmed]),
        ):
            self.assertTrue(
                await white_internet_repo.finalize_hard_delete_subscription(real_session, 11)
            )
            real_session.delete.assert_awaited_once_with(confirmed)
            real_session.reset_mock()
            self.assertFalse(
                await white_internet_repo.finalize_hard_delete_subscription(real_session, 12)
            )
            real_session.delete.assert_not_called()

    async def test_reset_user_trial_returns_error_when_no_subscriptions(self):
        """reset_user_trial returns WL_SUB_NOT_FOUND when user has no subscriptions."""
        user = User(id=1, telegram_id=555, is_banned=False, is_deleted=False)
        mock_exec = MagicMock()
        mock_exec.scalars.return_value.all.return_value = []
        self.session.execute = AsyncMock(return_value=mock_exec)

        with patch("services.white_internet_service.lock_checkout_user", new=AsyncMock(return_value=user)):
            ok, msg = await WhiteInternetService.reset_user_trial(self.session, user.id)
            self.assertFalse(ok)
            self.assertEqual(msg, texts.WL_SUB_NOT_FOUND)

    def test_audit_actions_contains_white_internet_actions(self):
        """AUDIT_ACTIONS dict in dashboard texts must contain White Internet audit labels."""
        from bot.texts.admin.dashboard import AUDIT_ACTIONS
        self.assertIn("WHITE_INTERNET_RESET_TRIAL", AUDIT_ACTIONS)
        self.assertIn("WHITE_INTERNET_GRANT_TRIAL", AUDIT_ACTIONS)
        self.assertIn("Сброс триала", AUDIT_ACTIONS["WHITE_INTERNET_RESET_TRIAL"])
        self.assertIn("Выдача триала", AUDIT_ACTIONS["WHITE_INTERNET_GRANT_TRIAL"])


if __name__ == "__main__":
    unittest.main()
