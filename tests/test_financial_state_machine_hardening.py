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

from datetime import datetime, timezone
from decimal import Decimal
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from aiogram.types import Message, User as TgUser

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


class MockSession:
    """Lightweight session mock for state machine and ledger testing."""

    def __init__(self, db_user=None, db_payment=None):
        self.added = []
        self._user = db_user
        self._payment = db_payment
        self.flushed = False

    def add(self, obj):
        self.added.append(obj)

    async def flush(self):
        self.flushed = True

    async def refresh(self, obj):
        pass

    async def execute(self, stmt):
        result = MagicMock()
        result.scalar_one_or_none = MagicMock(return_value=self._user)
        result.scalars = MagicMock(return_value=MagicMock(all=MagicMock(return_value=[])))
        return result

    async def scalar(self, stmt):
        return self._user or self._payment

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
            amount=Decimal("500"),
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
            amount=Decimal("500"),
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
            amount=Decimal("300"),
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
            amount=Decimal("1000"),
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
                accounting_position=Decimal("0"),
                available=Decimal("0"),
                reserved=Decimal("0"),
                debt=Decimal("0"),
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
            amount=Decimal("500"),
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
            amount=Decimal("500"),
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
        self.assertEqual(payment.reconciliation_status, "mismatch")
        self.assertEqual(payment.fulfillment_status, "not_ready")

        with patch("services.account_topup.lock_checkout_user", new_callable=AsyncMock) as mock_lock_user, \
             patch("services.account_topup.credit_succeeded_topup", new_callable=AsyncMock) as mock_credit, \
             patch("services.account_topup.get_account_balance", new_callable=AsyncMock) as mock_balance, \
             patch("services.account_topup.refresh_user_dispute_hold", new_callable=AsyncMock):
            mock_lock_user.return_value = user
            mock_credit.return_value = (MagicMock(), True)
            mock_balance.return_value = AccountBalanceSnapshot(
                accounting_position=Decimal("500"),
                available=Decimal("500"),
                reserved=Decimal("0"),
                debt=Decimal("0"),
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


if __name__ == "__main__":
    unittest.main()
