import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

from config.enums import WhiteInternetStatus
from database.models import WhiteInternetSubscription
from services.workers.notifications import _send_white_internet_notifications, _wi_notification_cache


class TestWhiteInternetNotifications(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        _wi_notification_cache.clear()

    async def test_send_white_internet_notifications_success(self):
        bot = AsyncMock()
        now = datetime.now(timezone.utc)

        sub_3d = MagicMock(spec=WhiteInternetSubscription)
        sub_3d.id = 1
        sub_3d.expires_at = now + timedelta(days=2)
        sub_3d.status = WhiteInternetStatus.ACTIVE

        sub_expired = MagicMock(spec=WhiteInternetSubscription)
        sub_expired.id = 2
        sub_expired.expires_at = now - timedelta(minutes=10)
        sub_expired.status = WhiteInternetStatus.ACTIVE

        mock_rows = [
            (sub_3d, 111111, False),
            (sub_expired, 222222, False),
        ]

        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.all.return_value = mock_rows
        mock_session.execute.return_value = mock_result

        with (
            patch("services.workers.notifications.session_scope") as mock_scope,
            patch("services.workers.notifications.global_send_limiter.acquire", new_callable=AsyncMock),
        ):
            mock_scope.return_value.__aenter__.return_value = mock_session
            await _send_white_internet_notifications(bot, now)

        self.assertEqual(bot.send_message.call_count, 2)
        # Check that cache is populated
        self.assertIn((1, "3d"), _wi_notification_cache)
        self.assertIn((2, "expired"), _wi_notification_cache)

        # Calling again should not resend because of cache
        bot.send_message.reset_mock()
        with (
            patch("services.workers.notifications.session_scope") as mock_scope,
            patch("services.workers.notifications.global_send_limiter.acquire", new_callable=AsyncMock),
        ):
            mock_scope.return_value.__aenter__.return_value = mock_session
            await _send_white_internet_notifications(bot, now)

        self.assertEqual(bot.send_message.call_count, 0)
