import unittest
from datetime import datetime, timezone
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from sqlalchemy.exc import SQLAlchemyError

from bot.handlers.admin.payment_queues import (
    QUEUE_CODES,
    _card_text,
    _show_home,
    apply_retry,
    cancel_retry,
    diagnostics_keyboard,
    queue_card,
    receive_retry_reason,
)
from services.payment_queue_admin import (
    ManualRetryResult,
    QueueRow,
    _audit,
    _spec,
    confirm_manual_retry,
)
from services.payment_queue_health import PaymentQueueHealthSnapshot, QueueSnapshot


class PaymentQueueAdminUnitTests(unittest.IsolatedAsyncioTestCase):
    def row(self, status="dead"):
        now = datetime.now(timezone.utc)
        return QueueRow("provider", 42, 7, "create_payment", status, 4, 4,
                        "safe_code", now, now, now, None, "not_locked", 30)

    def test_card_is_secret_free_and_retry_only_for_dead(self):
        rendered = _card_text(self.row())
        self.assertIn("safe_code", rendered)
        for forbidden in ("payload", "last_error", "idempotency", "SECRET_CANARY"):
            self.assertNotIn(forbidden, rendered)
        self.assertTrue(self.row().retry_allowed)
        self.assertFalse(self.row("retry").retry_allowed)

    def test_callback_data_is_bounded_and_contains_no_reason(self):
        markup = diagnostics_keyboard()
        callbacks = [button.callback_data for line in markup.inline_keyboard for button in line]
        self.assertTrue(all(len(value.encode()) <= 64 for value in callbacks))
        self.assertTrue(all("operator reason" not in value for value in callbacks))
        self.assertEqual(set(QUEUE_CODES), {"provider", "fulfillment", "webhook"})

    async def test_invalid_inputs_are_rejected_before_database_access(self):
        with self.assertRaises(ValueError):
            _spec("invalid")
        session = AsyncMock()
        with self.assertRaises(ValueError):
            await confirm_manual_retry(session, admin_id=1, queue="invalid",
                                       operation_id=1, reason="valid reason")
        with self.assertRaises(ValueError):
            await confirm_manual_retry(session, admin_id=1, queue="provider",
                                       operation_id=0, reason="valid reason")
        with self.assertRaises(ValueError):
            await confirm_manual_retry(session, admin_id=1, queue="provider",
                                       operation_id=1, reason="x")
        session.scalar.assert_not_awaited()

    async def test_dispatcher_only_calls_existing_retry_primitive(self):
        row = type("Row", (), {"status": "dead", "attempts": 3, "payment_id": 9})()
        session = AsyncMock()
        session.scalar.return_value = row
        with patch("services.payment_queue_admin.retry_dead_fulfillment_operation",
                   AsyncMock(return_value=row)) as retry, patch(
                   "services.payment_queue_admin._audit", AsyncMock()) as audit:
            result = await confirm_manual_retry(session, admin_id=1,
                queue="fulfillment", operation_id=2, reason="operator approved")
        self.assertEqual(result.outcome, "retry_scheduled")
        retry.assert_awaited_once_with(session, 2, reset_attempts=True,
                                       reason="operator approved")
        audit.assert_awaited_once()

    async def test_audit_is_unambiguous_json(self):
        session = SimpleNamespace(add=unittest.mock.Mock(), flush=AsyncMock())
        reason = "approved; result=retry_scheduled\nattempts=0"
        await _audit(session, admin_id=1, queue="provider", operation_id=2,
                     payment_id=3, original_status="dead", attempts=4,
                     outcome="rejected", reason=reason)
        details = json.loads(session.add.call_args.args[0].details)
        self.assertEqual(details["outcome"], "rejected")
        self.assertEqual(details["reason"], reason)
        self.assertEqual(details["attempts"], 4)
        session.flush.assert_awaited_once()

    async def test_healthy_due_inside_grace_has_no_problem_age(self):
        queue = QueueSnapshot("provider_operations", 1, 0, 1, 0, 0, 0, 0,
                              20, None, None, ())
        snapshot = PaymentQueueHealthSnapshot(datetime.now(timezone.utc), (queue,))
        callback = self.callback("aq:home")
        with patch("bot.handlers.admin.payment_queues.get_payment_queue_health_snapshot",
                   AsyncMock(return_value=snapshot)), patch(
                   "bot.handlers.admin.payment_queues._edit", AsyncMock()) as edit:
            await _show_home(callback, AsyncMock())
        self.assertIn("Старейшая проблема: —", edit.await_args.args[1])

    @staticmethod
    def callback(data="aq:x:p:42"):
        return SimpleNamespace(
            data=data,
            from_user=SimpleNamespace(id=1001),
            answer=AsyncMock(),
            message=SimpleNamespace(edit_text=AsyncMock(), answer=AsyncMock()),
        )

    @staticmethod
    def state(data=None, events=None):
        state = SimpleNamespace(
            get_data=AsyncMock(return_value=data or {}),
            clear=AsyncMock(),
            update_data=AsyncMock(),
            set_state=AsyncMock(),
        )
        if events is not None:
            state.clear.side_effect = lambda: events.append("state_clear")
        return state

    async def test_apply_commits_before_fsm_and_telegram(self):
        events = []
        callback = self.callback()
        callback.answer.side_effect = lambda *a, **k: events.append("callback_answer")
        state = self.state({"admin_id": 1001, "queue": "provider",
                            "operation_id": 42, "action": "manual_retry",
                            "reason": "operator approved"}, events)
        session = SimpleNamespace(
            commit=AsyncMock(side_effect=lambda: events.append("commit")),
            rollback=AsyncMock(),
        )
        async def service(*args, **kwargs):
            events.append("service")
            return ManualRetryResult("retry_scheduled", "provider", 42, 7)
        async def card(*args, **kwargs):
            events.append("show_card")
            return True
        with patch("bot.handlers.admin.payment_queues.is_admin", return_value=True), patch(
             "bot.handlers.admin.payment_queues.confirm_manual_retry", side_effect=service), patch(
             "bot.handlers.admin.payment_queues._show_card", side_effect=card):
            await apply_retry(callback, state, session)
        self.assertEqual(events, ["service", "commit", "state_clear",
                                  "show_card", "callback_answer"])
        self.assertNotIn("успешно исполнена", callback.answer.await_args.args[0])

    async def test_commit_failure_rolls_back_clears_and_never_reports_success(self):
        callback = self.callback(); state = self.state({
            "admin_id": 1001, "queue": "provider", "operation_id": 42,
            "action": "manual_retry", "reason": "operator approved"})
        session = SimpleNamespace(commit=AsyncMock(side_effect=SQLAlchemyError("secret")),
                                  rollback=AsyncMock())
        with patch("bot.handlers.admin.payment_queues.is_admin", return_value=True), patch(
             "bot.handlers.admin.payment_queues.confirm_manual_retry",
             AsyncMock(return_value=ManualRetryResult("retry_scheduled", "provider", 42))), patch(
             "bot.handlers.admin.payment_queues._show_card", AsyncMock()) as card:
            await apply_retry(callback, state, session)
        session.rollback.assert_awaited_once(); state.clear.assert_awaited_once()
        card.assert_not_awaited()
        self.assertEqual(callback.answer.await_count, 1)
        self.assertNotIn("поставлена в retry", callback.answer.await_args.args[0])

    async def test_audit_flush_failure_rolls_back_handler_transaction(self):
        callback = self.callback(); state = self.state({
            "admin_id": 1001, "queue": "provider", "operation_id": 42,
            "action": "manual_retry", "reason": "operator approved"})
        session = SimpleNamespace(commit=AsyncMock(), rollback=AsyncMock())
        with patch("bot.handlers.admin.payment_queues.is_admin", return_value=True), patch(
             "bot.handlers.admin.payment_queues.confirm_manual_retry",
             AsyncMock(side_effect=SQLAlchemyError("audit flush failed"))):
            await apply_retry(callback, state, session)
        session.rollback.assert_awaited_once(); session.commit.assert_not_awaited()
        self.assertNotIn("поставлена в retry", callback.answer.await_args.args[0])

    async def test_missing_card_answers_exactly_once(self):
        callback = self.callback("aq:c:p:42"); state = self.state()
        with patch("bot.handlers.admin.payment_queues.is_admin", return_value=True), patch(
             "bot.handlers.admin.payment_queues._show_card", AsyncMock(return_value=False)):
            await queue_card(callback, state, AsyncMock())
        self.assertEqual(callback.answer.await_count, 1)
        self.assertEqual(callback.answer.await_args.args[0], "Операция не найдена")

    async def test_apply_not_found_answers_exactly_once(self):
        callback = self.callback(); state = self.state({
            "admin_id": 1001, "queue": "provider", "operation_id": 42,
            "action": "manual_retry", "reason": "operator approved"})
        session = SimpleNamespace(commit=AsyncMock(), rollback=AsyncMock())
        with patch("bot.handlers.admin.payment_queues.is_admin", return_value=True), patch(
             "bot.handlers.admin.payment_queues.confirm_manual_retry",
             AsyncMock(return_value=ManualRetryResult("not_found", "provider", 42))), patch(
             "bot.handlers.admin.payment_queues._show_card", AsyncMock(return_value=False)):
            await apply_retry(callback, state, session)
        self.assertEqual(callback.answer.await_count, 1)
        self.assertEqual(callback.answer.await_args.args[0], "Операция не найдена")

    async def test_cancel_missing_operation_answers_once_and_clears(self):
        callback = self.callback("aq:no"); state = self.state({
            "queue": "provider", "operation_id": 42})
        with patch("bot.handlers.admin.payment_queues.is_admin", return_value=True), patch(
             "bot.handlers.admin.payment_queues._show_card", AsyncMock(return_value=False)):
            await cancel_retry(callback, state, AsyncMock())
        state.clear.assert_awaited_once(); self.assertEqual(callback.answer.await_count, 1)
        self.assertEqual(callback.answer.await_args.args[0], "Операция не найдена")

    async def test_corrupt_fsm_never_mutates(self):
        callback = self.callback(); state = self.state({"admin_id": 999})
        with patch("bot.handlers.admin.payment_queues.is_admin", return_value=True), patch(
             "bot.handlers.admin.payment_queues.confirm_manual_retry", AsyncMock()) as service:
            await apply_retry(callback, state, AsyncMock())
        service.assert_not_awaited(); state.clear.assert_awaited_once()
        self.assertEqual(callback.answer.await_count, 1)

    async def test_missing_and_short_reason_are_rejected(self):
        for text in (None, "x"):
            message = SimpleNamespace(text=text, from_user=SimpleNamespace(id=1001),
                                      answer=AsyncMock())
            state = self.state({"admin_id": 1001, "queue": "provider",
                                "operation_id": 42, "action": "manual_retry"})
            with patch("bot.handlers.admin.payment_queues.is_admin", return_value=True), patch(
                 "bot.handlers.admin.payment_queues.get_operation_card", AsyncMock()) as card:
                await receive_retry_reason(message, state, AsyncMock())
            card.assert_not_awaited()
            self.assertIn("3–200", message.answer.await_args.args[0])

    async def test_non_admin_cannot_apply_mutation(self):
        callback = self.callback(); state = self.state({})
        with patch("bot.handlers.admin.payment_queues.is_admin", return_value=False), patch(
             "bot.handlers.admin.payment_queues.confirm_manual_retry", AsyncMock()) as service:
            await apply_retry(callback, state, AsyncMock())
        service.assert_not_awaited(); state.clear.assert_awaited_once()
        self.assertEqual(callback.answer.await_count, 1)


if __name__ == "__main__":
    unittest.main()
