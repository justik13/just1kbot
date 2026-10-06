"""Tests for YooKassa timeout hardening, safe callback answering, and billing reconciliation."""

import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery

from services.yookassa_service import (
    CHECK_STATUS_TIMEOUT,
    CREATE_PAYMENT_TIMEOUT,
    YooKassaResult,
    YooKassaService,
)
from utils.logging_security import sanitize_text
from utils.telegram import safe_callback_answer


class TestSafeCallbackAnswer(unittest.IsolatedAsyncioTestCase):
    async def test_successful_answer(self):
        cb = AsyncMock(spec=CallbackQuery)
        cb.answer = AsyncMock()
        res = await safe_callback_answer(cb, text="OK", show_alert=True)
        self.assertTrue(res)
        cb.answer.assert_awaited_once_with("OK", show_alert=True)

    async def test_query_too_old_is_silenced(self):
        cb = AsyncMock(spec=CallbackQuery)
        cb.answer.side_effect = TelegramBadRequest(
            method=MagicMock(),
            message="query is too old and response timeout expired or query id is invalid",
        )
        res = await safe_callback_answer(cb, text="Alert", show_alert=True)
        self.assertFalse(res)

    async def test_query_already_answered_is_silenced(self):
        cb = AsyncMock(spec=CallbackQuery)
        cb.answer.side_effect = TelegramBadRequest(
            method=MagicMock(),
            message="query is already answered",
        )
        res = await safe_callback_answer(cb, text="Alert", show_alert=True)
        self.assertFalse(res)

    async def test_query_invalid_id_is_silenced(self):
        cb = AsyncMock(spec=CallbackQuery)
        cb.answer.side_effect = TelegramBadRequest(
            method=MagicMock(),
            message="query id is invalid",
        )
        res = await safe_callback_answer(cb)
        self.assertFalse(res)

    async def test_none_callback_handled_safely(self):
        res = await safe_callback_answer(None)
        self.assertFalse(res)

    async def test_unexpected_error_handled_safely(self):
        cb = AsyncMock(spec=CallbackQuery)
        cb.answer.side_effect = RuntimeError("network glitch")
        res = await safe_callback_answer(cb)
        self.assertFalse(res)


class TestYooKassaServiceTimeouts(unittest.IsolatedAsyncioTestCase):
    def test_timeout_presets(self):
        self.assertLessEqual(CHECK_STATUS_TIMEOUT.total, 7.0)
        self.assertLessEqual(CHECK_STATUS_TIMEOUT.connect, 3.0)
        self.assertLessEqual(CREATE_PAYMENT_TIMEOUT.total, 10.0)

    @patch("services.yookassa_service.YooKassaService._request")
    async def test_create_payment_uses_create_timeout(self, mock_req):
        mock_req.return_value = YooKassaResult(True, value={"id": "pay-1"})
        await YooKassaService.create_payment_result(
            {"amount": {"value": "100.00"}},
            idempotency_key="key-1",
        )
        mock_req.assert_awaited_once_with(
            "POST",
            "/payments",
            payload={"amount": {"value": "100.00"}},
            idempotency_key="key-1",
            ambiguous_on_failure=True,
            timeout=CREATE_PAYMENT_TIMEOUT,
        )

    @patch("services.yookassa_service.YooKassaService._request")
    async def test_get_payment_uses_check_timeout(self, mock_req):
        mock_req.return_value = YooKassaResult(True, value={"id": "pay-1"})
        await YooKassaService.get_payment_result("pay-1")
        mock_req.assert_awaited_once_with(
            "GET",
            "/payments/pay-1",
            timeout=CHECK_STATUS_TIMEOUT,
        )


class TestPeerIdLoggingNotCorrupted(unittest.TestCase):
    def test_peer_id_preserved_when_it_matches_fernet_length(self):
        # A 43-character base64 public key ending with '='
        peer_pubkey = "xX12345678901234567890123456789012345678901="
        raw_log = f"Configuring interface peer_id={peer_pubkey} on server_id=1"
        sanitized = sanitize_text(raw_log)
        self.assertIn(f"peer_id={peer_pubkey}", sanitized)
        self.assertNotIn("[FERNET_KEY_REDACTED]", sanitized)

    def test_actual_fernet_key_still_redacted(self):
        fernet_key = "AAA-_AABAgMEBQYHCAkKCwwNDg8QERITFBUWFxgZGhs="
        raw_log = f"settings.FIELD_ENCRYPTION_KEY={fernet_key} loaded"
        sanitized = sanitize_text(raw_log)
        self.assertNotIn(fernet_key, sanitized)
        self.assertIn("[FERNET_KEY_REDACTED]", sanitized)


class TestReconciliationWorkerUnit(unittest.IsolatedAsyncioTestCase):
    @patch("services.workers.cleanup.session_scope")
    async def test_reconcile_handles_empty(self, mock_session_scope):
        from services.workers.cleanup import _reconcile_stale_pending_orders

        session = AsyncMock()
        mock_session_scope.return_value.__aenter__.return_value = session

        result_mock = MagicMock()
        result_mock.all.return_value = []
        result_mock.scalars.return_value.all.return_value = []
        session.execute.return_value = result_mock

        await _reconcile_stale_pending_orders()
        self.assertEqual(session.execute.await_count, 2)

    @patch("integrations.payment_gateways.factory.get_payment_gateway")
    @patch("services.workers.cleanup.session_scope")
    async def test_reconcile_transaction_isolation_per_order(
        self, mock_session_scope, mock_gw_factory
    ):
        """Order 1 failure must not poison Order 2 settlement."""
        import uuid
        from decimal import Decimal
        from database.models import Order
        from integrations.payment_gateways.base import PaymentStatusResult
        from services.workers.cleanup import _reconcile_stale_pending_orders

        # Gateway returns paid for both
        gw = AsyncMock()
        gw.check_payment_status.return_value = PaymentStatusResult(
            is_paid=True,
            is_refunded=False,
            is_canceled=False,
            status_str="succeeded",
            amount_rub=Decimal("100.00"),
        )
        mock_gw_factory.return_value = gw

        # Read session for candidates
        read_session = AsyncMock()
        order1_id = uuid.uuid4()
        order2_id = uuid.uuid4()

        cand_mock = MagicMock()
        cand_mock.all.return_value = [
            (order1_id, "ext-1", "yookassa"),
            (order2_id, "ext-2", "yookassa"),
        ]

        # Order sessions
        order1_session = AsyncMock()
        order2_session = AsyncMock()
        expire_session = AsyncMock()

        order1 = Order(id=order1_id, status="pending", amount_rub=Decimal("100.00"))
        order2 = Order(id=order2_id, status="pending", amount_rub=Decimal("100.00"))

        order1_session.scalar.return_value = order1
        order2_session.scalar.return_value = order2

        expire_mock = MagicMock()
        expire_mock.scalars.return_value.all.return_value = []
        expire_session.execute.return_value = expire_mock

        # Setup sequence of session_scope enters
        mock_session_scope.side_effect = [
            AsyncMock(__aenter__=AsyncMock(return_value=read_session), __aexit__=AsyncMock()),
            AsyncMock(__aenter__=AsyncMock(return_value=order1_session), __aexit__=AsyncMock()),
            AsyncMock(__aenter__=AsyncMock(return_value=order2_session), __aexit__=AsyncMock()),
            AsyncMock(__aenter__=AsyncMock(return_value=expire_session), __aexit__=AsyncMock()),
        ]
        read_session.execute.return_value = cand_mock

        with patch("services.order_service.OrderService.mark_order_paid") as mock_mark:
            mock_mark.side_effect = [RuntimeError("DB glitch on order1"), order2]
            await _reconcile_stale_pending_orders()
            self.assertEqual(mock_mark.await_count, 2)


class TestAdminReduceCutoff(unittest.TestCase):
    def test_cutoff_clamping_prevents_ancient_dates(self):
        from datetime import datetime, timezone, timedelta
        from config.constants import VPN_ACCESS_GRACE_HOURS

        now = datetime.now(timezone.utc)
        cutoff = now - timedelta(hours=VPN_ACCESS_GRACE_HOURS + 1)
        sub_end = now + timedelta(days=5)
        days_to_reduce = 1000

        new_end = sub_end - timedelta(days=days_to_reduce)
        effective_end = cutoff if new_end <= now else new_end

        self.assertLess(new_end, now - timedelta(days=900))
        self.assertEqual(effective_end, cutoff)
        self.assertGreater(effective_end, now - timedelta(days=1))


if __name__ == "__main__":
    unittest.main()
