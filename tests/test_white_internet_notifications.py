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
        sub_3d.is_trial = False
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

        # 2. Sub expired 10 minutes ago -> expired tier (status transitioned to EXPIRED by reconciliation worker)
        sub_exp = MagicMock(spec=WhiteInternetSubscription)
        sub_exp.id = 2
        sub_exp.user_id = 102
        sub_exp.expires_at = now - timedelta(minutes=10)
        sub_exp.status = WhiteInternetStatus.EXPIRED
        sub_exp.is_trial = False
        sub_exp.notified_3d = False
        sub_exp.notified_1d = False
        sub_exp.notified_2h = False
        sub_exp.notified_expired = False

        user_exp = MagicMock(spec=User)
        user_exp.id = 102
        user_exp.telegram_id = 222222
        user_exp.is_bot_blocked = False
        user_exp.is_banned = False
        user_exp.is_deleted = False

        # Mock sessions
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
        # Check that expired notification cascades and closes out all earlier flags
        self.assertTrue(sub_exp.notified_expired)
        self.assertTrue(sub_exp.notified_2h)
        self.assertTrue(sub_exp.notified_1d)
        self.assertTrue(sub_exp.notified_3d)
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

    async def test_already_expired_closes_earlier_flags_without_resending(self):
        bot = AsyncMock()
        now = datetime.now(timezone.utc)

        # Sub expired, notified_expired is already True, but notified_3d was left False
        sub = MagicMock(spec=WhiteInternetSubscription)
        sub.id = 55
        sub.user_id = 300
        sub.expires_at = now - timedelta(hours=5)
        sub.status = WhiteInternetStatus.EXPIRED
        sub.notified_3d = False
        sub.notified_1d = False
        sub.notified_2h = False
        sub.notified_expired = True

        user = MagicMock(spec=User)
        user.id = 300
        user.telegram_id = 444444
        user.is_bot_blocked = False
        user.is_banned = False
        user.is_deleted = False

        mock_id_result = MagicMock()
        mock_id_result.all.return_value = [(55,)]

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

        # No message should be sent since already notified_expired
        bot.send_message.assert_not_awaited()
        # But earlier flags should be closed out to prevent recurring query polling
        self.assertTrue(sub.notified_3d)
        self.assertTrue(sub.notified_1d)
        self.assertTrue(sub.notified_2h)
        self.assertTrue(sub.notified_expired)
        mock_session_sub.flush.assert_awaited()

    async def test_notifications_text_grammar_and_msk_date(self):
        bot = AsyncMock()
        now = datetime(2026, 9, 13, 20, 0, 0, tzinfo=timezone.utc)  # 23:00 MSK

        # 1. 3d sub -> should format in MSK (+3 hours = 23:00 MSK)
        sub_3d = MagicMock(spec=WhiteInternetSubscription)
        sub_3d.id = 1
        sub_3d.user_id = 101
        sub_3d.expires_at = now + timedelta(days=2)  # 15.09.2026 23:00 MSK
        sub_3d.status = WhiteInternetStatus.ACTIVE
        sub_3d.is_trial = False
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

        # 2. 1d sub -> should contain "через"
        sub_1d = MagicMock(spec=WhiteInternetSubscription)
        sub_1d.id = 2
        sub_1d.user_id = 102
        sub_1d.expires_at = now + timedelta(hours=20)
        sub_1d.status = WhiteInternetStatus.ACTIVE
        sub_1d.is_trial = False
        sub_1d.notified_3d = True
        sub_1d.notified_1d = False
        sub_1d.notified_2h = False
        sub_1d.notified_expired = False

        user_1d = MagicMock(spec=User)
        user_1d.id = 102
        user_1d.telegram_id = 222222
        user_1d.is_bot_blocked = False
        user_1d.is_banned = False
        user_1d.is_deleted = False

        mock_id_result = MagicMock()
        mock_id_result.all.return_value = [(1,), (2,)]

        mock_session_query = AsyncMock()
        mock_session_query.execute.return_value = mock_id_result

        mock_session_sub1 = AsyncMock()
        mock_session_sub1.scalar.side_effect = [sub_3d, user_3d]

        mock_session_sub2 = AsyncMock()
        mock_session_sub2.scalar.side_effect = [sub_1d, user_1d]

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
        call1_args = bot.send_message.await_args_list[0]
        call2_args = bot.send_message.await_args_list[1]

        # Verify tg-time formatting in 3d notification: 20:00 UTC -> 23:00 MSK fallback
        self.assertIn('<tg-time unix="1789502400" format="dt">15.09.2026 23:00</tg-time>', call1_args[0][1])
        # Verify Russian grammar 'через' in 1d notification
        self.assertIn("истекает через", call2_args[0][1])

    async def test_trial_subscription_closes_notified_3d_without_sending(self):
        """Trial subscription with 2 days left closes notified_3d = True and sends no 3d message."""
        bot = AsyncMock()
        now = datetime.now(timezone.utc)

        sub_trial = MagicMock(spec=WhiteInternetSubscription)
        sub_trial.id = 99
        sub_trial.user_id = 999
        sub_trial.expires_at = now + timedelta(days=2)
        sub_trial.status = WhiteInternetStatus.ACTIVE
        sub_trial.is_trial = True
        sub_trial.notified_3d = False
        sub_trial.notified_1d = False
        sub_trial.notified_2h = False
        sub_trial.notified_expired = False

        user_trial = MagicMock(spec=User)
        user_trial.id = 999
        user_trial.telegram_id = 999999
        user_trial.is_bot_blocked = False
        user_trial.is_banned = False
        user_trial.is_deleted = False

        mock_id_result = MagicMock()
        mock_id_result.all.return_value = [(99,)]

        mock_session_query = AsyncMock()
        mock_session_query.execute.return_value = mock_id_result

        mock_session_sub = AsyncMock()
        mock_session_sub.scalar.side_effect = [sub_trial, user_trial]

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

        bot.send_message.assert_not_awaited()
        self.assertTrue(sub_trial.notified_3d)
        mock_session_sub.flush.assert_awaited()

    async def test_convert_trial_to_paid_resets_notification_flags_and_allows_3d_notify(self):
        """Converting trial -> paid resets all 5 notification flags to False and allows 3d reminder."""
        from decimal import Decimal
        from config.enums import ServerHealthState, ServerLifecycleStatus
        from database.models import Tariff, TariffVersion, Server
        from services.white_internet_service import WhiteInternetService

        now = datetime.now(timezone.utc)
        sub = WhiteInternetSubscription(
            id=50,
            user_id=500,
            origin_node_id=1,
            uuid="test-uuid",
            is_trial=True,
            status=WhiteInternetStatus.ACTIVE,
            device_limit=1,
            expires_at=now + timedelta(days=1),
            notified_3d=True,
            notified_1d=True,
            notified_2h=True,
            notified_expired=True,
            notified_90p=True,
            desired_version=1,
            actual_version=1,
        )
        user = MagicMock(spec=User)
        user.id = 500
        user.telegram_id = 555555
        user.is_bot_blocked = False
        user.is_banned = False
        user.is_deleted = False

        server = MagicMock(spec=Server)
        server.id = 1
        server.protocol = "xray"
        server.is_active = True
        server.health_state = ServerHealthState.ONLINE
        server.lifecycle_status = ServerLifecycleStatus.ACTIVE
        server.extra_data = {"relays": ["r1"]}

        tariff = MagicMock(spec=Tariff)
        tariff.duration_days = 30
        tariff_ver = MagicMock(spec=TariffVersion)
        tariff_ver.id = 1
        tariff_ver.base_quota_bytes = 50 * 1024 * 1024 * 1024
        tariff_ver.price_rub = Decimal("250")
        tariff_ver.duration_hours = 720

        mock_session = AsyncMock()
        mock_session.add = MagicMock()
        mock_session.scalar.return_value = server

        with (
            patch("services.white_internet_service.lock_checkout_user", return_value=user),
            patch("database.repositories.white_internet_repo.get_subscription_by_user_id", return_value=sub),
            patch("database.repositories.white_internet_repo.get_subscription_with_lock", return_value=sub),
            patch.object(WhiteInternetService, "get_or_create_white_internet_tariff", return_value=tariff),
            patch("services.white_internet_service.get_or_create_current_version", return_value=tariff_ver),
            patch("services.white_internet_service.create_purchase_debit", new_callable=AsyncMock),
            patch.object(WhiteInternetService, "_try_inline_sync", new_callable=AsyncMock),
        ):
            ok, msg, converted_sub = await WhiteInternetService.convert_trial_to_paid(mock_session, 500)

        self.assertTrue(ok)
        self.assertFalse(sub.is_trial)
        # All 5 flags must be reset to False
        self.assertFalse(sub.notified_3d)
        self.assertFalse(sub.notified_1d)
        self.assertFalse(sub.notified_2h)
        self.assertFalse(sub.notified_expired)
        self.assertFalse(sub.notified_90p)

        # Now verify that when 3-day notification threshold is reached, 3d message is successfully sent
        bot = AsyncMock()
        check_now = sub.expires_at - timedelta(days=2)  # 2 days left
        mock_id_result = MagicMock()
        mock_id_result.all.return_value = [(50,)]

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
            await _send_white_internet_notifications(bot, check_now)

        bot.send_message.assert_awaited_once()
        self.assertTrue(sub.notified_3d)


if __name__ == "__main__":
    unittest.main()
