import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from aiogram.exceptions import TelegramForbiddenError
from bot.handlers.admin.users.mass_bonus import _run_mass_bonus_background
from database.models import User


class AdminMassBonusTests(unittest.IsolatedAsyncioTestCase):
    async def test_run_mass_bonus_background_batches_and_post_commit_dispatch(self):
        """Verify that mass bonus processes in batches of 50, commits ledger adjustments,
        and only dispatches Telegram messages after transaction commits."""
        mock_bot = AsyncMock()
        admin_id = 999999
        amount = 100
        reason = "Test bonus compensation <alert>"
        batch_id = 1700000000

        users = [(i, 1000 + i) for i in range(1, 121)]
        users[9] = (10, admin_id)

        session_commits = []
        created_adjustments = []
        sent_messages = []
        blocked_users_marked = []

        class FakeSession:
            def __init__(self):
                self._in_transaction = True

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc_val, exc_tb):
                if exc_type is None:
                    session_commits.append(len(created_adjustments))

            def begin_nested(self):
                class FakeSavepoint:
                    async def __aenter__(self):
                        return self
                    async def __aexit__(self, exc_type, exc_val, exc_tb):
                        pass
                return FakeSavepoint()

            async def execute(self, stmt, *args, **kwargs):
                mock_res = MagicMock()
                mock_res.all.return_value = users
                return mock_res

            async def get(self, model, ident):
                if model == User:
                    user_obj = MagicMock(spec=User)
                    user_obj.id = ident
                    user_obj.is_bot_blocked = False
                    blocked_users_marked.append(user_obj)
                    return user_obj
                return None

        async def fake_create_admin_adjustment(session, user_id, signed_amount, idempotency_key, metadata):
            if user_id == 20:
                raise RuntimeError("Simulated ledger error")
            created_adjustments.append({
                "user_id": user_id,
                "signed_amount": signed_amount,
                "idempotency_key": idempotency_key,
                "metadata": metadata,
            })
            return MagicMock(), True

        async def fake_send_message(chat_id, text, parse_mode="HTML"):
            current_committed = session_commits[-1] if session_commits else 0
            if chat_id == 1030:
                sent_messages.append((chat_id, text, current_committed))
                raise TelegramForbiddenError(method=MagicMock(), message="Forbidden: bot was blocked by the user")
            sent_messages.append((chat_id, text, current_committed))

        mock_bot.send_message.side_effect = fake_send_message

        with patch("database.connection.session_scope", side_effect=FakeSession), \
             patch("bot.handlers.admin.users.mass_bonus.create_admin_adjustment", side_effect=fake_create_admin_adjustment), \
             patch("services.audit_service.AuditService.log_action", new_callable=AsyncMock) as mock_audit, \
             patch("utils.rate_limiter.global_send_limiter.acquire", new_callable=AsyncMock), \
             patch("bot.handlers.admin.users.mass_bonus.render_hub", new_callable=AsyncMock) as mock_render:

            await _run_mass_bonus_background(
                bot=mock_bot,
                admin_id=admin_id,
                target_aud="all",
                amount=amount,
                reason=reason,
                batch_id=batch_id,
            )

            self.assertEqual(len(created_adjustments), 119)
            self.assertGreaterEqual(len(session_commits), 4)

            self.assertEqual(
                created_adjustments[0]["idempotency_key"],
                f"mass_bonus_{batch_id}_1_{amount}",
            )

            admin_msgs = [m for m in sent_messages if m[0] == admin_id]
            self.assertEqual(len(admin_msgs), 0)
            self.assertEqual(len(sent_messages), 118)

            for _chat_id, text, committed_count in sent_messages:
                self.assertGreater(committed_count, 0)
                self.assertIn("Вам начислен бонусный баланс: +100 ₽!", text)
                self.assertIn("&lt;alert&gt;", text)

            self.assertEqual(len(blocked_users_marked), 1)
            self.assertTrue(blocked_users_marked[0].is_bot_blocked)

            mock_audit.assert_called_once()
            self.assertIn("Granted +100 RUB bonus to 119 users", mock_audit.call_args[0][5])

            mock_render.assert_called_once()
            report_text = mock_render.call_args[0][2]
            self.assertIn("Зачислено: <b>119 чел.</b>", report_text)
            self.assertIn("Ошибок: <b>1</b>", report_text)
            self.assertIn("Заблокировали бота: <b>1</b>", report_text)

    async def test_mass_bonus_crash_retry_idempotency_recovery(self):
        """Verify that if a mass bonus background task crashes mid-way, running it again
        with the same batch_id safely skips already credited users and credits remaining users
        without duplicate ledger entries."""
        mock_bot = AsyncMock()
        admin_id = 999999
        amount = 50
        reason = "Crash recovery test"
        batch_id = 1700005555

        users = [(i, 2000 + i) for i in range(1, 11)]

        # Global ledger store simulating database unique constraint on idempotency_key
        ledger_entries_by_key = {}
        sent_messages = []

        class FakeSession:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                pass

            def begin_nested(self):
                class FakeSavepoint:
                    async def __aenter__(self):
                        return self
                    async def __aexit__(self, exc_type, exc_val, exc_tb):
                        pass
                return FakeSavepoint()

            async def execute(self, stmt, *args, **kwargs):
                mock_res = MagicMock()
                mock_res.all.return_value = users
                return mock_res

            async def get(self, model, ident):
                return None

        async def fake_create_admin_adjustment(session, user_id, signed_amount, idempotency_key, metadata):
            if idempotency_key in ledger_entries_by_key:
                # Simulates account_ledger_repo._insert_or_get_entry returning (existing_entry, False)
                return MagicMock(), False
            ledger_entries_by_key[idempotency_key] = {
                "user_id": user_id,
                "amount": signed_amount,
            }
            return MagicMock(), True

        async def fake_send_message(chat_id, text, parse_mode="HTML"):
            sent_messages.append((chat_id, text))

        mock_bot.send_message.side_effect = fake_send_message

        # Run 1: process 10 users, but crash on user 6 (e.g. simulate crash during notifications)
        # Manually populate ledger for users 1..5 as if Run 1 completed batch 1
        for u in range(1, 6):
            key = f"mass_bonus_{batch_id}_{u}_{amount}"
            ledger_entries_by_key[key] = {"user_id": u, "amount": amount}

        self.assertEqual(len(ledger_entries_by_key), 5)

        # Run 2: full rerun of the same batch_id
        with patch("database.connection.session_scope", side_effect=FakeSession), \
             patch("bot.handlers.admin.users.mass_bonus.create_admin_adjustment", side_effect=fake_create_admin_adjustment), \
             patch("services.audit_service.AuditService.log_action", new_callable=AsyncMock), \
             patch("utils.rate_limiter.global_send_limiter.acquire", new_callable=AsyncMock), \
             patch("bot.handlers.admin.users.mass_bonus.render_hub", new_callable=AsyncMock):

            await _run_mass_bonus_background(
                bot=mock_bot,
                admin_id=admin_id,
                target_aud="all",
                amount=amount,
                reason=reason,
                batch_id=batch_id,
            )

        # Total entries in database must be exactly 10 (5 prior + 5 newly credited)
        self.assertEqual(len(ledger_entries_by_key), 10)
        for u in range(1, 11):
            key = f"mass_bonus_{batch_id}_{u}_{amount}"
            self.assertIn(key, ledger_entries_by_key)

    async def test_mass_bonus_true_crash_and_restart_recovery(self):
        """Simulates an actual process crash / exception thrown mid-run during Telegram
        notification dispatch after committing batch 1, then triggers a full restart with the
        same batch_id, proving zero duplicate ledger entries and notification deduplication."""
        mock_bot = AsyncMock()
        admin_id = 999999
        amount = 50
        reason = "Real crash restart test"
        batch_id = 1700008888

        # 120 users (3 batches of 50: batch1=1..50, batch2=51..100, batch3=101..120)
        users = [(i, 3000 + i) for i in range(1, 121)]

        # Simulated persistent database ledger table with unique constraint on idempotency_key
        ledger_table = {}
        sent_messages = []

        class FakeSession:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                pass

            def begin_nested(self):
                class FakeSavepoint:
                    async def __aenter__(self):
                        return self
                    async def __aexit__(self, exc_type, exc_val, exc_tb):
                        pass
                return FakeSavepoint()

            async def execute(self, stmt, *args, **kwargs):
                mock_res = MagicMock()
                mock_res.all.return_value = users
                return mock_res

            async def get(self, model, ident):
                return None

        async def persistent_create_admin_adjustment(session, user_id, signed_amount, idempotency_key, metadata):
            if idempotency_key in ledger_table:
                # Simulates database returning (existing_entry, False)
                return MagicMock(), False
            ledger_table[idempotency_key] = {"user_id": user_id, "amount": signed_amount}
            return MagicMock(), True

        should_crash = True

        async def crashable_send_message(chat_id, text, parse_mode="HTML"):
            nonlocal should_crash
            sent_messages.append((chat_id, text))
            if should_crash and chat_id == 3025:  # crash on user 25 during batch 1 notification
                raise KeyboardInterrupt("Simulated server crash / process SIGKILL")

        mock_bot.send_message.side_effect = crashable_send_message

        # RUN 1: Starts processing batch 1 (users 1..50 committed to DB), but crashes at user 25 in Telegram dispatch
        with self.assertRaises(KeyboardInterrupt):
            with patch("database.connection.session_scope", side_effect=FakeSession), \
                 patch("bot.handlers.admin.users.mass_bonus.create_admin_adjustment", side_effect=persistent_create_admin_adjustment), \
                 patch("services.audit_service.AuditService.log_action", new_callable=AsyncMock), \
                 patch("utils.rate_limiter.global_send_limiter.acquire", new_callable=AsyncMock), \
                 patch("bot.handlers.admin.users.mass_bonus.render_hub", new_callable=AsyncMock):

                await _run_mass_bonus_background(
                    bot=mock_bot,
                    admin_id=admin_id,
                    target_aud="all",
                    amount=amount,
                    reason=reason,
                    batch_id=batch_id,
                )

        # Confirm that Batch 1 (50 users) was committed to the DB before the crash
        self.assertEqual(len(ledger_table), 50)
        self.assertEqual(len(sent_messages), 25)

        # RUN 2: Server restarts, operator triggers rerun of the same batch_id
        should_crash = False
        sent_messages.clear()

        with patch("database.connection.session_scope", side_effect=FakeSession), \
             patch("bot.handlers.admin.users.mass_bonus.create_admin_adjustment", side_effect=persistent_create_admin_adjustment), \
             patch("services.audit_service.AuditService.log_action", new_callable=AsyncMock) as mock_audit, \
             patch("utils.rate_limiter.global_send_limiter.acquire", new_callable=AsyncMock), \
             patch("bot.handlers.admin.users.mass_bonus.render_hub", new_callable=AsyncMock) as mock_render:

            await _run_mass_bonus_background(
                bot=mock_bot,
                admin_id=admin_id,
                target_aud="all",
                amount=amount,
                reason=reason,
                batch_id=batch_id,
            )

        # Verify that all 120 users are in the ledger and exactly 0 duplicate adjustments were created
        self.assertEqual(len(ledger_table), 120)
        for u in range(1, 121):
            key = f"mass_bonus_{batch_id}_{u}_{amount}"
            self.assertIn(key, ledger_table)

        # Batch 2 and 3 (70 users) were credited and notified
        self.assertEqual(len(sent_messages), 70)
        mock_audit.assert_called_once()
        mock_render.assert_called_once()


if __name__ == "__main__":
    unittest.main()
