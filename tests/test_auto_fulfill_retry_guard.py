import os
import unittest
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

os.environ.setdefault("BOT_TOKEN", "123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11")
os.environ.setdefault("ADMIN_IDS", "[100]")
os.environ.setdefault("SUPPORT_USERNAME", "test_support")
os.environ.setdefault("DB_ENCRYPTION_KEY", "MDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDA=")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("REDIS_PASSWORD", "testpass")
os.environ.setdefault("YOOKASSA_SHOP_ID", "12345")
os.environ.setdefault("YOOKASSA_SECRET_KEY", "test_key")
os.environ.setdefault("YOOKASSA_RETURN_URL", "https://t.me/test_bot?start={bot_username}")
os.environ.setdefault("YOOKASSA_WEBHOOK_PORT", "8080")
os.environ.setdefault("DOMAIN", "myrealdomain.com")
os.environ.setdefault("SSL_EMAIL", "admin@myrealdomain.com")
# NOTE: no DATABASE_URL setdefault here — this module must not flip the
# skipUnless live-database marker of tests/test_database_startup.py.

from services.workers.payments import _retry_auto_fulfillment


def _payment(topup_context):
    from database.models import Payment

    return Payment(
        id=17,
        user_id=1,
        amount=100,
        currency="RUB",
        public_order_id="order",
        provider_idempotency_key="key",
        provider_status="succeeded",
        fulfillment_status="succeeded",
        topup_context=topup_context,
    )


def _session():
    session = MagicMock()

    @asynccontextmanager
    async def _nested():
        yield MagicMock()

    session.begin_nested.side_effect = _nested
    return session


class AutoFulfillRetryGuardTests(unittest.IsolatedAsyncioTestCase):
    async def test_poisoned_attempts_dead_letters_instead_of_crashing(self):
        """A poisoned durable attempts value must dead-letter the record, not
        raise before the error handling (poisoned recovery record loop)."""
        payment = _payment(
            {
                "auto_fulfill_action": "purchase",
                "quote_public_id": "00000000-0000-0000-0000-000000000001",
                "auto_fulfill_attempts": "abc",
                "auto_fulfill_status": "failed",
            }
        )
        session = _session()
        with patch(
            "services.account_purchase.settle_account_purchase", new_callable=AsyncMock
        ) as mock_settle:
            await _retry_auto_fulfillment(session, payment)

        mock_settle.assert_not_awaited()
        self.assertEqual(
            payment.topup_context.get("auto_fulfill_status"), "dead"
        )
        self.assertEqual(
            payment.topup_context.get("auto_fulfill_error"),
            "invalid_auto_fulfill_attempts",
        )

    async def test_object_attempts_value_dead_letters(self):
        """Any non-int durable attempts value (str/dict/bool) must dead-letter
        instead of crashing or being silently coerced by `or 0`."""
        for poisoned in ("abc", {"poisoned": 1}, ["x"], 1.5, True):
            with self.subTest(poisoned=poisoned):
                payment = _payment(
                    {
                        "auto_fulfill_action": "purchase",
                        "quote_public_id": "00000000-0000-0000-0000-000000000001",
                        "auto_fulfill_attempts": poisoned,
                    }
                )
                session = _session()
                with patch(
                    "services.account_purchase.settle_account_purchase",
                    new_callable=AsyncMock,
                ) as mock_settle:
                    await _retry_auto_fulfillment(session, payment)

                mock_settle.assert_not_awaited()
                self.assertEqual(
                    payment.topup_context.get("auto_fulfill_status"), "dead"
                )
                self.assertEqual(
                    payment.topup_context.get("auto_fulfill_error"),
                    "invalid_auto_fulfill_attempts",
                )

    async def test_missing_attempts_counter_proceeds_to_settlement(self):
        """A well-formed record without an attempts counter reaches settlement
        (the guard only fires on poisoned values)."""
        from services.account_purchase import AccountPurchaseError

        payment = _payment(
            {
                "auto_fulfill_action": "purchase",
                "quote_public_id": "00000000-0000-0000-0000-000000000001",
                "auto_fulfill_status": "failed",
            }
        )
        session = _session()
        with patch(
            "services.account_purchase.settle_account_purchase",
            new_callable=AsyncMock,
            side_effect=AccountPurchaseError("quote_expired"),
        ) as mock_settle:
            await _retry_auto_fulfillment(session, payment)

        mock_settle.assert_awaited_once()
        # quote_expired is a permanent code → dead, not failed.
        self.assertEqual(payment.topup_context.get("auto_fulfill_status"), "dead")
        self.assertEqual(payment.topup_context.get("auto_fulfill_error"), "quote_expired")
        self.assertEqual(payment.topup_context.get("auto_fulfill_attempts"), 1)

    async def test_attempts_at_cap_never_buys_an_extra_settlement(self):
        """A corrupted-but-int counter >= MAX must dead-letter BEFORE any
        settlement call (bounded-retry invariant)."""
        for capped in (5, 6, 100):
            with self.subTest(attempts=capped):
                payment = _payment(
                    {
                        "auto_fulfill_action": "purchase",
                        "quote_public_id": "00000000-0000-0000-0000-000000000001",
                        "auto_fulfill_attempts": capped,
                        "auto_fulfill_status": "failed",
                    }
                )
                session = _session()
                with patch(
                    "services.account_purchase.settle_account_purchase",
                    new_callable=AsyncMock,
                ) as mock_settle:
                    await _retry_auto_fulfillment(session, payment)

                mock_settle.assert_not_awaited()
                self.assertEqual(
                    payment.topup_context.get("auto_fulfill_status"), "dead"
                )
                self.assertEqual(
                    payment.topup_context.get("auto_fulfill_error"),
                    "auto_fulfill_attempts_exhausted",
                )

    async def test_negative_attempts_counter_dead_letters(self):
        """A negative counter must never disable the retry cap."""
        for negative in (-1, -100):
            with self.subTest(attempts=negative):
                payment = _payment(
                    {
                        "auto_fulfill_action": "purchase",
                        "quote_public_id": "00000000-0000-0000-0000-000000000001",
                        "auto_fulfill_attempts": negative,
                        "auto_fulfill_status": "failed",
                    }
                )
                session = _session()
                with patch(
                    "services.account_purchase.settle_account_purchase",
                    new_callable=AsyncMock,
                ) as mock_settle:
                    await _retry_auto_fulfillment(session, payment)

                mock_settle.assert_not_awaited()
                self.assertEqual(
                    payment.topup_context.get("auto_fulfill_status"), "dead"
                )
                self.assertEqual(
                    payment.topup_context.get("auto_fulfill_error"),
                    "invalid_auto_fulfill_attempts",
                )

    async def test_success_retry_clears_stale_telemetry(self):
        """A successful retry must not keep the old auto_fulfill_error."""
        payment = _payment(
            {
                "auto_fulfill_action": "purchase",
                "quote_public_id": "00000000-0000-0000-0000-000000000001",
                "auto_fulfill_attempts": 1,
                "auto_fulfill_status": "failed",
                "auto_fulfill_error": "TimeoutError",
            }
        )
        session = _session()
        with patch(
            "services.account_purchase.settle_account_purchase",
            new_callable=AsyncMock,
        ):
            await _retry_auto_fulfillment(session, payment)

        self.assertEqual(payment.topup_context.get("auto_fulfill_status"), "succeeded")
        self.assertNotIn("auto_fulfill_error", payment.topup_context)
        self.assertEqual(payment.topup_context.get("auto_fulfill_attempts"), 2)

    async def test_unknown_action_is_fail_closed(self):
        """An unknown durable action must never be executed as a purchase.
        Non-string poison (list/dict) must dead-letter too: a bare set
        membership test would raise TypeError (unhashable) outside the try
        block and poison the recovery lane forever."""
        for poisoned in (
            "banana",
            None,
            123,
            True,
            [],
            {},
            ["purchase"],
            {"action": "purchase"},
        ):
            with self.subTest(action=poisoned):
                payment = _payment(
                    {
                        "auto_fulfill_action": poisoned,
                        "quote_public_id": "00000000-0000-0000-0000-000000000001",
                    }
                )
                session = _session()
                with (
                    patch(
                        "services.account_purchase.settle_account_purchase",
                        new_callable=AsyncMock,
                    ) as mock_purchase,
                    patch(
                        "services.account_tariff_change.settle_account_tariff_change",
                        new_callable=AsyncMock,
                    ) as mock_change,
                ):
                    await _retry_auto_fulfillment(session, payment)

                mock_purchase.assert_not_awaited()
                mock_change.assert_not_awaited()
                self.assertEqual(
                    payment.topup_context.get("auto_fulfill_status"), "dead"
                )
                self.assertEqual(
                    payment.topup_context.get("auto_fulfill_error"),
                    "invalid_auto_fulfill_action",
                )


if __name__ == "__main__":
    unittest.main()
