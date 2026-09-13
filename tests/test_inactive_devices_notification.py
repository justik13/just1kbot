"""Unit tests for inactive sub devices worker notifications."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from database.models import User
from services.workers.notifications import _send_inactive_sub_device_notifications


class TestInactiveDevicesNotification(unittest.IsolatedAsyncioTestCase):
    async def test_no_notification_if_below_device_limit(self):
        now = datetime(2026, 9, 12, 12, 0, 0, tzinfo=timezone.utc)
        user = User(
            id=1,
            telegram_id=111,
            subscription_end=now + timedelta(days=30),
            device_limit=2,
            active_sub_devices={
                "hwid_1": {
                    "device_index": 1,
                    "label": "Old iPad",
                    "last_seen": (now - timedelta(days=10)).isoformat(),
                }
            },
        )
        mock_bot = MagicMock()
        mock_bot.send_message = AsyncMock()

        mock_session = AsyncMock()
        # users query
        mock_session.execute.side_effect = [
            MagicMock(scalars=lambda: MagicMock(all=lambda: [user])),
            MagicMock(scalar_one=lambda: 0),  # manual_count = 0 -> total_active = 1 < limit (2)
        ]

        with (
            patch("services.workers.notifications.session_scope") as mock_scope,
            patch("services.workers.notifications.SubscriptionService.get_effective_device_limit", new_callable=AsyncMock) as mock_limit,
        ):
            mock_scope.return_value.__aenter__.return_value = mock_session
            mock_limit.return_value = 2

            await _send_inactive_sub_device_notifications(mock_bot, now)
            mock_bot.send_message.assert_not_called()

    async def test_sends_notification_if_at_limit_and_inactive(self):
        now = datetime(2026, 9, 12, 12, 0, 0, tzinfo=timezone.utc)
        user = User(
            id=1,
            telegram_id=222,
            subscription_end=now + timedelta(days=30),
            device_limit=2,
            active_sub_devices={
                "hwid_1": {
                    "device_index": 1,
                    "label": "Old iPad",
                    "last_seen": (now - timedelta(days=10)).isoformat(),
                },
                "hwid_2": {
                    "device_index": 2,
                    "label": "iPhone 15",
                    "last_seen": (now - timedelta(hours=2)).isoformat(),
                },
            },
        )
        mock_bot = MagicMock()
        mock_bot.send_message = AsyncMock()

        mock_session = AsyncMock()
        mock_session.execute.side_effect = [
            MagicMock(scalars=lambda: MagicMock(all=lambda: [user])),
            MagicMock(scalar_one=lambda: 0),  # manual_count = 0 -> total_active = 2 == limit (2)
        ]

        with (
            patch("services.workers.notifications.session_scope") as mock_scope,
            patch("services.workers.notifications.SubscriptionService.get_effective_device_limit", new_callable=AsyncMock) as mock_limit,
            patch("services.workers.notifications.global_send_limiter.acquire", new_callable=AsyncMock),
        ):
            mock_scope.return_value.__aenter__.return_value = mock_session
            mock_limit.return_value = 2

            await _send_inactive_sub_device_notifications(mock_bot, now)

            # Old iPad should trigger notification!
            self.assertEqual(mock_bot.send_message.call_count, 1)
            call_args = mock_bot.send_message.call_args
            self.assertEqual(call_args[0][0], 222)
            self.assertIn("Old iPad", call_args[0][1])

            # Should update notified_inactive_at
            self.assertIsNotNone(user.active_sub_devices["hwid_1"].get("notified_inactive_at"))
            mock_session.flush.assert_awaited()

    async def test_skips_if_already_notified_recently(self):
        now = datetime(2026, 9, 12, 12, 0, 0, tzinfo=timezone.utc)
        user = User(
            id=1,
            telegram_id=333,
            subscription_end=now + timedelta(days=30),
            device_limit=1,
            active_sub_devices={
                "hwid_1": {
                    "device_index": 1,
                    "label": "Old iPad",
                    "last_seen": (now - timedelta(days=12)).isoformat(),
                    "notified_inactive_at": (now - timedelta(days=2)).isoformat(),  # Notified 2 days ago (< 7d)
                },
            },
        )
        mock_bot = MagicMock()
        mock_bot.send_message = AsyncMock()

        mock_session = AsyncMock()
        mock_session.execute.side_effect = [
            MagicMock(scalars=lambda: MagicMock(all=lambda: [user])),
            MagicMock(scalar_one=lambda: 0),  # total_active = 1 == limit (1)
        ]

        with (
            patch("services.workers.notifications.session_scope") as mock_scope,
            patch("services.workers.notifications.SubscriptionService.get_effective_device_limit", new_callable=AsyncMock) as mock_limit,
        ):
            mock_scope.return_value.__aenter__.return_value = mock_session
            mock_limit.return_value = 1

            await _send_inactive_sub_device_notifications(mock_bot, now)
            mock_bot.send_message.assert_not_called()


if __name__ == "__main__":
    unittest.main()
