"""Unit tests for durable White Internet subscription expiration notifications."""

import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

from aiogram.exceptions import TelegramForbiddenError
from config.enums import WhiteInternetStatus
from database.models import User, WhiteInternetSubscription
from services.workers.notifications import _send_white_internet_notifications


class TestWhiteInternetNotifications(unittest.IsolatedAsyncioTestCase):
    async def test_send_white_internet_notifications_durable_flags(self):
        bot = AsyncMock()
        now = datetime.now(timezone.utc)

        # 1. Sub with 2 days left -> 3d tier
        sub_3d = MagicMock(spec=WhiteInternetSubscription)
        sub_3d.id = 1
        sub_3d.user_id = 101
        sub_3d.expires_at = now + timedelta(days=2)
        sub_3d.status = WhiteInternetStatus.ACTIVE
        sub_3d.notified_3d = False
        sub_3d.notified_1d = False
        sub_3d.notified_2h = False
        sub_3d.notified_expired = False

        user_3d = MagicMock(spec=User)
        user_3d.id = 101
        user_3d.telegram_id = 111111
        user_3d.is_bot_blocked = False
        user_3d.is_banned = False
        user_3d.is_deleted = False

        # 2. Sub expired 10 minutes ago -> expired tier
        sub_exp = MagicMock(spec=WhiteInternetSubscription)
        sub_exp.id = 2
        sub_exp.user_id = 102
        sub_exp.expires_at = now - timedelta(minutes=10)
        sub_exp.status = WhiteInternetStatus.EXHAUSTED
        sub_exp.notified_3d = True
        sub_exp.notified_1d = True
        sub_exp.notified_2h = True
        sub_exp.notified_expired = False

        user_exp = MagicMock(spec=User)
        user_exp.id = 102
        user_exp.telegram_id = 222222
        user_exp.is_bot_blocked = False
        user_exp.is_banned = False
        user_exp.is_deleted = False

        # Mock sessions
        # 1st query: select(WhiteInternetSubscription.id) -> returns [(1,), (2,)]
        # subsequent queries: select(WhiteInternetSubscription) -> returns sub, select(User) -> returns user
        mock_id_result = MagicMock()
        mock_id_result.all.return_value = [(1,), (2,)]

        mock_session_query = AsyncMock()
        mock_session_query.execute.return_value = mock_id_result

        mock_session_sub1 = AsyncMock()
        mock_session_sub1.scalar.side_effect = [sub_3d, user_3d]

        mock_session_sub2 = AsyncMock()
        mock_session_sub2.scalar.side_effect = [sub_exp, user_exp]

        sessions = [mock_session_query, mock_session_sub1, mock_session_sub2]

        def get_session():
            ctx = AsyncMock()
            ctx.__aenter__.return_value = sessions.pop(0)
            return ctx

        with (
            patch("services.workers.notifications.session_scope", side_effect=get_session),
            patch("services.workers.notifications.global_send_limiter.acquire", new_callable=AsyncMock),
        ):
            await _send_white_internet_notifications(bot, now)

        self.assertEqual(bot.send_message.call_count, 2)
        # Check that durable DB flags were flipped
        self.assertTrue(sub_3d.notified_3d)
        self.assertFalse(sub_3d.notified_1d)
        self.assertTrue(sub_exp.notified_expired)
        mock_session_sub1.flush.assert_awaited()
        mock_session_sub2.flush.assert_awaited()

    async def test_telegram_forbidden_marks_blocked_and_sets_flags(self):
        bot = AsyncMock()
        bot.send_message.side_effect = TelegramForbiddenError(method=MagicMock(), message="Forbidden")
        now = datetime.now(timezone.utc)

        sub = MagicMock(spec=WhiteInternetSubscription)
        sub.id = 10
        sub.user_id = 200
        sub.expires_at = now + timedelta(hours=1)
        sub.status = WhiteInternetStatus.ACTIVE
        sub.notified_3d = False
        sub.notified_1d = False
        sub.notified_2h = False
        sub.notified_expired = False

        user = MagicMock(spec=User)
        user.id = 200
        user.telegram_id = 333333
        user.is_bot_blocked = False
        user.is_banned = False
        user.is_deleted = False

        mock_id_result = MagicMock()
        mock_id_result.all.return_value = [(10,)]

        mock_session_query = AsyncMock()
        mock_session_query.execute.return_value = mock_id_result

        mock_session_sub = AsyncMock()
        mock_session_sub.scalar.side_effect = [sub, user]

        sessions = [mock_session_query, mock_session_sub]

        def get_session():
            ctx = AsyncMock()
            ctx.__aenter__.return_value = sessions.pop(0)
            return ctx

        with (
            patch("services.workers.notifications.session_scope", side_effect=get_session),
            patch("services.workers.notifications.global_send_limiter.acquire", new_callable=AsyncMock),
        ):
            await _send_white_internet_notifications(bot, now)

        self.assertTrue(user.is_bot_blocked)
        self.assertTrue(sub.notified_expired)
        self.assertTrue(sub.notified_2h)
        self.assertTrue(sub.notified_1d)
        self.assertTrue(sub.notified_3d)
        mock_session_sub.flush.assert_awaited()
