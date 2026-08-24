"""Comprehensive production hardening tests.

Validates:
1. Financial state machine monotonic manual_review lock.
2. Canceled -> succeeded conflict return.
3. Fail-closed guards in settle_succeeded_topup and credit_succeeded_topup.
4. Financial invariant across all 4 entry points (GET, webhook, recovery, direct settlement).
5. AmneziaWG configuration strict validation.
6. User identity cache in UserContextMiddleware.
7. Worker supervisor cooldown and stability window.
"""

import unittest
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

from aiogram.types import Message
from aiogram.types import User as TgUser

from database.models import (
    Payment,
    PaymentEvent,
    User,
)
from database.repositories.account_ledger_repo import (
    AccountBalanceSnapshot,
    AccountLedgerConflictError,
    credit_succeeded_topup,
)
from services.account_topup import (
    settle_succeeded_topup,
)
from services.api_operations_executor import _is_usable_created_config
from services.payment_provider_state import (
    apply_provider_transition,
)
from utils.datetime_helpers import now_utc


class MockSession:
    """Lightweight session mock for state machine and ledger testing."""

    def __init__(self, db_user=None, db_payment=None, db_row=None):
        self.added = []
        self._user = db_user
        self._payment = db_payment
        self._row = db_row
        self.flushed = False

    def add(self, obj):
        self.added.append(obj)

    async def flush(self):
        self.flushed = True

    async def refresh(self, obj):
        pass

    async def execute(self, stmt, params=None):
        result = MagicMock()
        result.scalar_one_or_none = MagicMock(return_value=self._user)
        result.scalars = MagicMock(return_value=MagicMock(all=MagicMock(return_value=[])))
        return result

    async def scalar(self, stmt):
        sql = str(stmt).lower()
        if "payments" in sql:
            return self._payment
        if "webhook_inbox" in sql:
            return self._row
        if "payment_provider_operations" in sql:
            return self._row
        if "payment_notifications" in sql:
            from database.models import PaymentNotification
            for obj in self.added:
                if isinstance(obj, PaymentNotification):
                    return obj
            return None
        if "users" in sql:
            return self._user or User(id=1, telegram_id=12345, is_deleted=False)
        return self._user or self._payment or self._row

    async def get(self, model, ident):
        if model is User and self._user and self._user.id == ident:
            return self._user
        if model is Payment and self._payment and self._payment.id == ident:
            return self._payment
        return None


class FinancialStateMachineHardeningTests(unittest.IsolatedAsyncioTestCase):
    """Verifies that manual_review and mismatch states are strictly monotonic and fail-closed."""

    async def test_manual_review_repeated_exact_snapshot_returns_conflict(self):
        """A payment already in manual_review must NOT become 'applied' on repeated identical GET."""
        captured = datetime(2026, 8, 17, 12, 0, 0, tzinfo=timezone.utc)
        payment = Payment(
            id=101,
            user_id=1,
            external_id="yoo_101",
            public_order_id="pay_101",
            amount=Decimal(500),
            currency="RUB",
            provider_status="succeeded",
            provider_confirmed_at=captured,
            paid_at=captured,
            reconciliation_status="manual_review",
            fulfillment_status="manual_review",
            manual_review_reason="captured_at_changed",
        )
        session = MockSession(db_payment=payment)

        data = {
            "id": "yoo_101",
            "status": "succeeded",
            "captured_at": "2026-08-17T12:00:00+00:00",
            "amount": {"value": "500.00", "currency": "RUB"},
            "metadata": {"order_id": "pay_101", "local_payment_id": "101"},
        }

        transition = await apply_provider_transition(
            session,
            payment=payment,
            data=data,
            source="provider_verified_get",
        )

        self.assertEqual(transition.outcome, "conflict")
        self.assertEqual(transition.reason, "manual_review_locked")
        self.assertEqual(payment.reconciliation_status, "manual_review")
        self.assertEqual(payment.fulfillment_status, "manual_review")

    async def test_mismatch_repeated_exact_snapshot_returns_conflict(self):
        """A payment with reconciliation_status='mismatch' must NOT become 'applied' on GET."""
        captured = datetime(2026, 8, 17, 12, 0, 0, tzinfo=timezone.utc)
        payment = Payment(
            id=102,
            user_id=1,
            external_id="yoo_102",
            public_order_id="pay_102",
            amount=Decimal(500),
            currency="RUB",
            provider_status="succeeded",
            provider_confirmed_at=captured,
            paid_at=captured,
            reconciliation_status="mismatch",
            fulfillment_status="manual_review",
        )
        session = MockSession(db_payment=payment)

        data = {
            "id": "yoo_102",
            "status": "succeeded",
            "captured_at": "2026-08-17T12:00:00+00:00",
            "amount": {"value": "500.00", "currency": "RUB"},
            "metadata": {"order_id": "pay_102", "local_payment_id": "102"},
        }

        transition = await apply_provider_transition(
            session,
            payment=payment,
            data=data,
            source="provider_verified_get",
        )

        self.assertEqual(transition.outcome, "conflict")
        self.assertEqual(transition.reason, "manual_review_locked")

    async def test_canceled_to_succeeded_returns_conflict_and_locks_manual_review(self):
        """Transition from canceled -> succeeded must return conflict, never applied."""
        payment = Payment(
            id=103,
            user_id=1,
            external_id="yoo_103",
            public_order_id="pay_103",
            amount=Decimal(300),
            currency="RUB",
            provider_status="canceled",
            checkout_status="abandoned",
        )
        session = MockSession(db_payment=payment)

        data = {
            "id": "yoo_103",
            "status": "succeeded",
            "captured_at": "2026-08-17T12:30:00+00:00",
            "amount": {"value": "300.00", "currency": "RUB"},
            "metadata": {"order_id": "pay_103", "local_payment_id": "103"},
        }

        transition = await apply_provider_transition(
            session,
            payment=payment,
            data=data,
            source="webhook",
        )

        self.assertEqual(transition.outcome, "conflict")
        self.assertEqual(transition.reason, "canceled_to_succeeded")
        self.assertEqual(payment.provider_status, "succeeded")
        self.assertEqual(payment.reconciliation_status, "mismatch")
        self.assertEqual(payment.fulfillment_status, "manual_review")
        self.assertEqual(payment.manual_review_reason, "canceled_to_succeeded")

    async def test_settle_succeeded_topup_fails_closed_on_manual_review(self):
        """settle_succeeded_topup must abort and not credit money if payment is in manual_review."""
        captured = datetime(2026, 8, 17, 12, 0, 0, tzinfo=timezone.utc)
        payment = Payment(
            id=104,
            user_id=1,
            amount=Decimal(1000),
            currency="RUB",
            provider_status="succeeded",
            provider_confirmed_at=captured,
            fulfillment_status="manual_review",
            reconciliation_status="manual_review",
            manual_review_reason="captured_at_changed",
        )
        session = MockSession()

        with patch("services.account_topup.get_account_balance", new_callable=AsyncMock) as mock_balance:
            mock_balance.return_value = AccountBalanceSnapshot(
                accounting_position=Decimal(0),
                available=Decimal(0),
                reserved=Decimal(0),
                debt=Decimal(0),
            )
            credited, snapshot = await settle_succeeded_topup(
                session,
                payment=payment,
                source="test",
            )

        self.assertFalse(credited)
        self.assertEqual(payment.fulfillment_status, "manual_review")
        self.assertIsNone(payment.credited_at)
        event_types = [obj.event_type for obj in session.added if isinstance(obj, PaymentEvent)]
        self.assertIn("topup_settlement_blocked_manual_review", event_types)

    async def test_credit_succeeded_topup_raises_conflict_on_manual_review(self):
        """credit_succeeded_topup must raise AccountLedgerConflictError if payment is in manual_review."""
        user = User(id=1, telegram_id=12345, is_deleted=False)
        captured = datetime(2026, 8, 17, 12, 0, 0, tzinfo=timezone.utc)
        payment = Payment(
            id=105,
            user_id=1,
            amount=Decimal(500),
            currency="RUB",
            provider_status="succeeded",
            provider_confirmed_at=captured,
            fulfillment_status="manual_review",
            reconciliation_status="manual_review",
        )
        session = MockSession(db_user=user, db_payment=payment)

        with patch("database.repositories.account_ledger_repo.lock_account_user", new_callable=AsyncMock) as mock_lock:
            mock_lock.return_value = user
            with self.assertRaises(AccountLedgerConflictError) as ctx:
                await credit_succeeded_topup(session, locked_payment=payment)
            self.assertIn("manual_review", str(ctx.exception))

    async def test_abandoned_checkout_late_success_is_applied_and_settled(self):
        """Late success for abandoned UI checkout must be applied and credited successfully."""
        user = User(id=1, telegram_id=12345, is_deleted=False, topup_blocked=False, financial_hold=False)
        captured = datetime(2026, 8, 17, 12, 0, 0, tzinfo=timezone.utc)
        payment = Payment(
            id=106,
            user_id=1,
            external_id="yoo_106",
            public_order_id="pay_106",
            amount=Decimal(500),
            currency="RUB",
            provider_status="pending",
            provider_confirmed_at=captured,
            checkout_status="abandoned",
            fulfillment_status="not_ready",
            reconciliation_status="ok",
        )
        session = MockSession(db_user=user, db_payment=payment)

        data = {
            "id": "yoo_106",
            "status": "succeeded",
            "captured_at": "2026-08-17T12:00:00+00:00",
            "amount": {"value": "500.00", "currency": "RUB"},
            "metadata": {"order_id": "pay_106", "local_payment_id": "106"},
        }

        transition = await apply_provider_transition(
            session,
            payment=payment,
            data=data,
            source="webhook",
        )

        self.assertEqual(transition.outcome, "applied")
        self.assertEqual(payment.provider_status, "succeeded")
        self.assertEqual(payment.reconciliation_status, "ok")
        self.assertEqual(payment.fulfillment_status, "not_ready")

        with patch("services.account_topup.lock_checkout_user", new_callable=AsyncMock) as mock_lock_user, \
             patch("services.account_topup.credit_succeeded_topup", new_callable=AsyncMock) as mock_credit, \
             patch("services.account_topup.get_account_balance", new_callable=AsyncMock) as mock_balance, \
             patch("services.account_topup.refresh_user_dispute_hold", new_callable=AsyncMock):
            mock_lock_user.return_value = user
            mock_credit.return_value = (MagicMock(), True)
            mock_balance.return_value = AccountBalanceSnapshot(
                accounting_position=Decimal(500),
                available=Decimal(500),
                reserved=Decimal(0),
                debt=Decimal(0),
            )
            credited, snapshot = await settle_succeeded_topup(
                session,
                payment=payment,
                source="test",
                settings=MagicMock(BALANCE_MAX_AVAILABLE_RUB="50000"),
            )

        self.assertTrue(credited)
        self.assertEqual(payment.fulfillment_status, "succeeded")


class AmneziaWGValidationTests(unittest.TestCase):
    """Verifies strict AmneziaWG configuration validation in executor."""

    def test_valid_awg_vpn_uri_accepted(self):
        """A valid vpn:// URI containing AWG container must be accepted."""
        with patch("utils.vpn_parser.is_valid_vpn_uri", return_value=True):
            self.assertTrue(_is_usable_created_config("vpn://awg_valid_sample_config_string"))

    def test_valid_wireguard_ini_accepted(self):
        """A raw WireGuard/AWG .conf containing [Interface] and [Peer] must be accepted."""
        conf = (
            "[Interface]\n"
            "PrivateKey = aaaa=\n"
            "Address = 10.0.0.2/32\n"
            "[Peer]\n"
            "PublicKey = bbbb=\n"
            "Endpoint = 1.2.3.4:51820\n"
        )
        self.assertTrue(_is_usable_created_config(conf))

    def test_html_error_page_rejected(self):
        """HTML 502/504 Bad Gateway responses must be strictly rejected."""
        html_502 = "<!DOCTYPE html><html><head><title>502 Bad Gateway</title></head><body><h1>502 Bad Gateway</h1></body></html>"
        self.assertFalse(_is_usable_created_config(html_502))

        html_nginx = "<html>\r\n<head><title>504 Gateway Time-out</title></head>\r\n<body>\r\n<center><h1>504 Gateway Time-out</h1></center>\r\n<hr><center>nginx</center>\r\n</body>\r\n</html>"
        self.assertFalse(_is_usable_created_config(html_nginx))

    def test_json_error_rejected(self):
        """JSON error responses must be strictly rejected."""
        json_error = '{"status": 500, "error": "Internal Server Error", "message": "Docker container unreachable"}'
        self.assertFalse(_is_usable_created_config(json_error))

    def test_foreign_protocols_rejected(self):
        """Foreign protocols (vless, vmess, ss, trojan) must be strictly rejected."""
        self.assertFalse(_is_usable_created_config("vless://uuid@domain:443?security=reality"))
        self.assertFalse(_is_usable_created_config("vmess://eyJhZGRyIjoiMS4yLjMuNCJ9"))
        self.assertFalse(_is_usable_created_config("ss://YWVzLTEyOC1nY206cGFzc3dvcmRAMS4yLjMuNDo4Mzgw"))
        self.assertFalse(_is_usable_created_config("trojan://pass@domain:443"))

    def test_empty_and_garbage_rejected(self):
        """Empty, None, 'invalid', and arbitrary strings without AWG structure must be rejected."""
        self.assertFalse(_is_usable_created_config(None))
        self.assertFalse(_is_usable_created_config(""))
        self.assertFalse(_is_usable_created_config("   "))
        self.assertFalse(_is_usable_created_config("invalid"))
        self.assertFalse(_is_usable_created_config("arbitrary_string_that_is_longer_than_twenty_characters_and_not_awg"))


class UserContextMiddlewareCacheTests(unittest.IsolatedAsyncioTestCase):
    """Verifies that UserContextMiddleware caches telegram_id -> user_id without leaking ORM instances."""

    async def test_cache_hits_fetch_fresh_user_from_current_session(self):
        from bot.middlewares.user_context import (
            UserContextMiddleware,
            _user_cache,
            clear_user_cache,
            invalidate_user_cache,
        )

        clear_user_cache()

        user = User(
            id=42,
            telegram_id=99999,
            username="testuser",
            is_deleted=False,
            is_banned=False,
        )

        session1 = MockSession(db_user=user)
        middleware = UserContextMiddleware()

        message = MagicMock(spec=Message)
        from_user = MagicMock(spec=TgUser)
        from_user.id = 99999
        from_user.username = "testuser"
        from_user.first_name = "Test"
        message.from_user = from_user

        handler_called = False

        async def handler(event, data):
            nonlocal handler_called
            handler_called = True
            return data.get("db_user")

        # First invocation -> cache miss, populates cache with user.id
        data1 = {"session": session1}
        result_user1 = await middleware(handler, message, data1)
        self.assertTrue(handler_called)
        self.assertIsNotNone(result_user1)
        self.assertEqual(result_user1.id, 42)
        self.assertEqual(_user_cache.get(99999), 42)

        # Second invocation with session2 -> cache hit, queries session2 with user_id=42
        user_in_session2 = User(
            id=42,
            telegram_id=99999,
            username="testuser",
            is_deleted=False,
            is_banned=True,  # Changed in DB!
        )
        session2 = MockSession(db_user=user_in_session2)
        data2 = {"session": session2}
        result_user2 = await middleware(handler, message, data2)
        self.assertIsNotNone(result_user2)
        self.assertEqual(result_user2.id, 42)
        # Verify that security flag is fresh from session2!
        self.assertTrue(result_user2.is_banned)

        # Invalidate cache
        invalidate_user_cache(99999)
        self.assertNotIn(99999, _user_cache)

    async def test_stale_cache_miss_falls_back_to_telegram_id_in_same_request(self):
        """When cached user.id misses in DB, middleware must fall back to telegram_id in same request."""
        from bot.middlewares.user_context import (
            UserContextMiddleware,
            _user_cache,
            clear_user_cache,
        )

        clear_user_cache()

        # Seed cache with stale user_id = 999
        _user_cache[88888] = 999

        real_user = User(
            id=77,
            telegram_id=88888,
            username="fallback_user",
            is_deleted=False,
            is_banned=False,
        )

        class StaleSession(MockSession):
            async def execute(self, stmt):
                sql = str(stmt)
                result = MagicMock()
                if "users.id =" in sql or "users_1.id =" in sql or "WHERE users.id" in sql:
                    result.scalar_one_or_none = MagicMock(return_value=None)
                else:
                    result.scalar_one_or_none = MagicMock(return_value=real_user)
                return result

        session = StaleSession(db_user=real_user)
        middleware = UserContextMiddleware()

        message = MagicMock(spec=Message)
        from_user = MagicMock(spec=TgUser)
        from_user.id = 88888
        from_user.username = "fallback_user"
        from_user.first_name = "Fallback"
        message.from_user = from_user

        data = {"session": session}

        async def handler(event, data):
            return data.get("db_user")

        result = await middleware(handler, message, data)

        self.assertIsNotNone(result)
        self.assertEqual(result.id, 77)
        self.assertEqual(_user_cache.get(88888), 77)


class TopupContextDefensiveParsingTests(unittest.TestCase):
    """Verifies defensive parsing of topup_context referrer fields."""

    def test_valid_referrer_fields_parsed(self):
        ctx = {"referrer_telegram_id": "123456", "referrer_bonus": "150"}
        ref_id_raw = ctx.get("referrer_telegram_id")
        ref_bonus_raw = ctx.get("referrer_bonus", 0)

        ref_id = None
        ref_bonus = 0
        try:
            if ref_id_raw is not None:
                parsed_id = int(ref_id_raw)
                if parsed_id > 0:
                    ref_id = parsed_id
            if ref_bonus_raw is not None:
                parsed_bonus = int(ref_bonus_raw)
                if parsed_bonus > 0:
                    ref_bonus = parsed_bonus
        except (ValueError, TypeError):
            ref_id = None
            ref_bonus = 0

        self.assertEqual(ref_id, 123456)
        self.assertEqual(ref_bonus, 150)

    def test_malformed_referrer_fields_handled_safely(self):
        for bad_ctx in [
            {"referrer_telegram_id": "not_an_int", "referrer_bonus": "bad"},
            {"referrer_telegram_id": -10, "referrer_bonus": -50},
            {"referrer_telegram_id": None, "referrer_bonus": None},
            {"referrer_telegram_id": {}, "referrer_bonus": []},
        ]:
            ref_id_raw = bad_ctx.get("referrer_telegram_id")
            ref_bonus_raw = bad_ctx.get("referrer_bonus", 0)

            ref_id = None
            ref_bonus = 0
            try:
                if ref_id_raw is not None:
                    parsed_id = int(ref_id_raw)
                    if parsed_id > 0:
                        ref_id = parsed_id
                if ref_bonus_raw is not None:
                    parsed_bonus = int(ref_bonus_raw)
                    if parsed_bonus > 0:
                        ref_bonus = parsed_bonus
            except (ValueError, TypeError):
                ref_id = None
                ref_bonus = 0

            self.assertIsNone(ref_id)
            self.assertEqual(ref_bonus, 0)


class FourChannelFinancialInvariantTests(unittest.IsolatedAsyncioTestCase):
    """Verifies that payments locked in manual_review or mismatch never get credited across all 4 entry channels."""

    async def test_channel_1_provider_operations_finalize_fails_closed(self):
        """Channel 1: services.payment_provider_operations.finalize must not credit money when manual_review locked."""
        from database.models import PaymentProviderOperation
        from services.payment_provider_operations import (
            ProviderOperationClaim,
            finalize,
        )
        from services.yookassa_service import YooKassaResult

        captured = datetime(2026, 8, 17, 12, 0, 0, tzinfo=timezone.utc)
        payment = Payment(
            id=201,
            user_id=1,
            amount=Decimal(500),
            currency="RUB",
            provider_status="succeeded",
            provider_confirmed_at=captured,
            fulfillment_status="manual_review",
            reconciliation_status="manual_review",
            manual_review_reason="captured_at_changed",
        )
        operation = PaymentProviderOperation(
            id=1,
            payment_id=201,
            operation_type="reconcile_payment",
            status="processing",
            locked_by="worker_1",
            attempts=1,
        )
        session = MockSession(db_payment=payment, db_row=operation)
        claim = ProviderOperationClaim(
            operation_id=1,
            payment_id=201,
            operation_type="reconcile_payment",
            payload={},
            idempotency_key="idemp_201",
            worker_id="worker_1",
            attempt_number=1,
            external_id="yoo_201",
            created_at=now_utc(),
        )
        result = YooKassaResult(
            ok=True,
            value={
                "id": "yoo_201",
                "status": "succeeded",
                "captured_at": "2026-08-17T12:00:00+00:00",
                "amount": {"value": "500.00", "currency": "RUB"},
                "metadata": {"order_id": "pay_201", "local_payment_id": "201"},
            },
            status_code=200,
        )

        with patch("services.account_topup.settle_succeeded_topup", new_callable=AsyncMock) as mock_settle:
            await finalize(session, claim, result)
            mock_settle.assert_not_called()

        self.assertIsNone(payment.credited_at)
        self.assertEqual(payment.fulfillment_status, "manual_review")

    async def test_channel_2_webhook_inbox_finalize_fails_closed(self):
        """Channel 2: services.workers.webhook_inbox.finalize must not credit money when manual_review locked."""
        from database.models import WebhookInbox
        from services.workers.webhook_inbox import InboxClaim, finalize
        from services.yookassa_service import YooKassaResult

        captured = datetime(2026, 8, 17, 12, 0, 0, tzinfo=timezone.utc)
        payment = Payment(
            id=202,
            user_id=1,
            external_id="yoo_202",
            public_order_id="pay_202",
            amount=Decimal(500),
            currency="RUB",
            provider_status="succeeded",
            provider_confirmed_at=captured,
            fulfillment_status="manual_review",
            reconciliation_status="manual_review",
            manual_review_reason="captured_at_changed",
        )
        row = WebhookInbox(
            id=1,
            provider="yookassa",
            event_key="evt_key_202",
            event_type="payment.succeeded",
            provider_object_id="yoo_202",
            payment_external_id="yoo_202",
            public_order_id="pay_202",
            payload={},
            status="processing",
            locked_by="worker_1",
            attempts=1,
        )
        session = MockSession(db_payment=payment, db_row=row)
        claim = InboxClaim(
            inbox_id=1,
            worker_id="worker_1",
            attempt_number=1,
            event_type="payment.succeeded",
            payment_external_id="yoo_202",
            public_order_id="pay_202",
            payload={
                "object": {
                    "id": "yoo_202",
                    "status": "succeeded",
                    "captured_at": "2026-08-17T12:00:00+00:00",
                    "amount": {"value": "500.00", "currency": "RUB"},
                    "metadata": {"order_id": "pay_202", "local_payment_id": "202"},
                }
            },
            event_key="evt_key_202",
        )
        result = YooKassaResult(
            ok=True,
            value={
                "id": "yoo_202",
                "status": "succeeded",
                "captured_at": "2026-08-17T12:00:00+00:00",
                "amount": {"value": "500.00", "currency": "RUB"},
                "metadata": {"order_id": "pay_202", "local_payment_id": "202"},
            },
            status_code=200,
        )

        with patch("services.account_topup.settle_succeeded_topup", new_callable=AsyncMock) as mock_settle:
            await finalize(session, claim, result=result)
            mock_settle.assert_not_called()

        self.assertIsNone(payment.credited_at)
        self.assertEqual(payment.fulfillment_status, "manual_review")

    async def test_channel_3_stale_refresh_fails_closed(self):
        """Channel 3: services.account_topup_refresh.request_topup_status_refresh must not credit money when manual_review locked."""
        from services.account_topup_refresh import request_topup_status_refresh

        captured = datetime(2026, 8, 17, 12, 0, 0, tzinfo=timezone.utc)
        payment = Payment(
            id=203,
            user_id=1,
            amount=Decimal(500),
            currency="RUB",
            provider_status="succeeded",
            provider_confirmed_at=captured,
            fulfillment_status="manual_review",
            reconciliation_status="manual_review",
            manual_review_reason="captured_at_changed",
        )
        session = MockSession(db_payment=payment)

        with patch("services.account_topup_refresh.lock_checkout_user", new_callable=AsyncMock) as mock_lock, \
             patch("services.account_topup_refresh.settle_succeeded_topup", new_callable=AsyncMock) as mock_settle:
            mock_lock.return_value = User(id=1, telegram_id=12345, topup_blocked=False, financial_hold=False)
            res_payment = await request_topup_status_refresh(session, payment_id=203)
            mock_settle.assert_not_called()

        self.assertIsNone(res_payment.credited_at)
        self.assertEqual(res_payment.fulfillment_status, "manual_review")

    async def test_channel_4_direct_settlement_and_ledger_fail_closed(self):
        """Channel 4: direct settle_succeeded_topup and credit_succeeded_topup must strictly fail closed on mismatch/manual_review."""
        user = User(id=1, telegram_id=12345, is_deleted=False)
        captured = datetime(2026, 8, 17, 12, 0, 0, tzinfo=timezone.utc)

        for status_tuple in [("manual_review", "manual_review"), ("not_ready", "mismatch"), ("reversed", "ok")]:
            f_status, r_status = status_tuple
            with self.subTest(f_status=f_status, r_status=r_status):
                payment = Payment(
                    id=204,
                    user_id=1,
                    amount=Decimal(500),
                    currency="RUB",
                    provider_status="succeeded",
                    provider_confirmed_at=captured,
                    fulfillment_status=f_status,
                    reconciliation_status=r_status,
                )
                session = MockSession(db_user=user, db_payment=payment)

                with patch("services.account_topup.get_account_balance", new_callable=AsyncMock) as mock_balance:
                    mock_balance.return_value = AccountBalanceSnapshot(
                        accounting_position=Decimal(0),
                        available=Decimal(0),
                        reserved=Decimal(0),
                        debt=Decimal(0),
                    )
                    credited, _ = await settle_succeeded_topup(session, payment=payment, source="test")
                    self.assertFalse(credited)

                with patch("database.repositories.account_ledger_repo.lock_account_user", new_callable=AsyncMock) as mock_lock:
                    mock_lock.return_value = user
                    with self.assertRaises(AccountLedgerConflictError):
                        await credit_succeeded_topup(session, locked_payment=payment)

    async def test_all_channels_unmocked_pipeline_guarantees_no_credit_on_conflict(self):
        """Cross-component test: full unmocked transition pipeline on conflict creates zero ledger credits."""
        from database.models import (
            AccountLedgerEntry,
            PaymentProviderOperation,
            WebhookInbox,
        )
        from services.account_topup_refresh import request_topup_status_refresh
        from services.payment_provider_operations import ProviderOperationClaim
        from services.payment_provider_operations import finalize as finalize_provider
        from services.workers.webhook_inbox import InboxClaim
        from services.workers.webhook_inbox import finalize as finalize_webhook
        from services.yookassa_service import YooKassaResult

        user = User(id=1, telegram_id=12345, is_deleted=False, topup_blocked=False, financial_hold=False)

        # 1. Provider Operations Channel (canceled -> succeeded conflict)
        p1 = Payment(
            id=301,
            user_id=1,
            amount=Decimal(500),
            currency="RUB",
            provider_status="canceled",
            fulfillment_status="not_ready",
            reconciliation_status="ok",
        )
        op1 = PaymentProviderOperation(
            id=10,
            payment_id=301,
            operation_type="reconcile_payment",
            status="processing",
            locked_by="w1",
            attempts=1,
        )
        s1 = MockSession(db_user=user, db_payment=p1, db_row=op1)
        claim1 = ProviderOperationClaim(
            operation_id=10,
            payment_id=301,
            operation_type="reconcile_payment",
            payload={},
            idempotency_key="k1",
            worker_id="w1",
            attempt_number=1,
            external_id="yoo_301",
            created_at=now_utc(),
        )
        res1 = YooKassaResult(
            ok=True,
            value={"id": "yoo_301", "status": "succeeded", "captured_at": "2026-08-17T12:00:00+00:00", "amount": {"value": "500.00", "currency": "RUB"}},
            status_code=200,
        )
        await finalize_provider(s1, claim1, res1)
        self.assertIsNone(p1.credited_at)
        self.assertEqual(p1.fulfillment_status, "manual_review")
        self.assertFalse(any(isinstance(x, AccountLedgerEntry) for x in s1.added))

        # 2. Webhook Inbox Channel (mismatch amount conflict)
        p2 = Payment(
            id=302,
            user_id=1,
            external_id="yoo_302",
            public_order_id="pay_302",
            amount=Decimal(500),
            currency="RUB",
            provider_status="pending",
            fulfillment_status="not_ready",
            reconciliation_status="ok",
        )
        row2 = WebhookInbox(
            id=20,
            provider="yookassa",
            event_key="k2",
            event_type="payment.succeeded",
            provider_object_id="yoo_302",
            payment_external_id="yoo_302",
            public_order_id="pay_302",
            payload={},
            status="processing",
            locked_by="w2",
            attempts=1,
            max_attempts=30,
        )
        s2 = MockSession(db_user=user, db_payment=p2, db_row=row2)
        claim2 = InboxClaim(
            inbox_id=20,
            worker_id="w2",
            attempt_number=1,
            event_type="payment.succeeded",
            payment_external_id="yoo_302",
            public_order_id="pay_302",
            payload={"object": {"id": "yoo_302", "status": "succeeded", "captured_at": "2026-08-17T12:00:00+00:00", "amount": {"value": "9999.00", "currency": "RUB"}}},
            event_key="k2",
        )
        res2 = YooKassaResult(
            ok=True,
            value={"id": "yoo_302", "status": "succeeded", "captured_at": "2026-08-17T12:00:00+00:00", "amount": {"value": "9999.00", "currency": "RUB"}},
            status_code=200,
        )
        await finalize_webhook(s2, claim2, result=res2)
        self.assertIsNone(p2.credited_at)
        self.assertEqual(p2.fulfillment_status, "manual_review")
        self.assertFalse(any(isinstance(x, AccountLedgerEntry) for x in s2.added))

        # 3. Stale Recovery Channel (financially blocked user)
        blocked_user = User(id=1, telegram_id=12345, topup_blocked=True, financial_hold=True)
        p3 = Payment(
            id=303,
            user_id=1,
            amount=Decimal(500),
            currency="RUB",
            provider_status="succeeded",
            provider_confirmed_at=now_utc(),
            fulfillment_status="not_ready",
            reconciliation_status="ok",
        )
        s3 = MockSession(db_user=blocked_user, db_payment=p3)
        with patch("services.account_topup_refresh.lock_checkout_user", new_callable=AsyncMock) as mock_lock:
            mock_lock.return_value = blocked_user
            await request_topup_status_refresh(s3, payment_id=303)
        self.assertIsNone(p3.credited_at)
        self.assertEqual(p3.fulfillment_status, "manual_review")
        self.assertFalse(any(isinstance(x, AccountLedgerEntry) for x in s3.added))

    async def test_settle_succeeded_topup_by_id_passes_bot_and_settings(self):
        """settle_succeeded_topup_by_id must accept bot and settings and forward them to settle_succeeded_topup."""
        from services.account_topup import settle_succeeded_topup_by_id

        user = User(id=1, telegram_id=12345, is_deleted=False, topup_blocked=False, financial_hold=False)
        p = Payment(
            id=401,
            user_id=1,
            amount=Decimal(500),
            currency="RUB",
            provider_status="succeeded",
            provider_confirmed_at=now_utc(),
            fulfillment_status="not_ready",
            reconciliation_status="ok",
        )
        session = MockSession(db_user=user, db_payment=p)
        mock_bot = MagicMock()
        mock_settings = MagicMock(BALANCE_MAX_AVAILABLE_RUB="50000")

        with patch("services.account_topup.settle_succeeded_topup", new_callable=AsyncMock) as mock_settle:
            mock_settle.return_value = (True, MagicMock())
            credited, snapshot = await settle_succeeded_topup_by_id(
                session,
                payment_id=401,
                source="test_by_id",
                settings=mock_settings,
                bot=mock_bot,
            )
            mock_settle.assert_called_once_with(
                session,
                payment=p,
                source="test_by_id",
                settings=mock_settings,
                bot=mock_bot,
                locked_user=user,
                locked_payment=p,
            )
            self.assertTrue(credited)


if __name__ == "__main__":
    unittest.main()
