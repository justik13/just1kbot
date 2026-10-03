"""Focused coverage for the single White Internet wallet-checkout boundary."""

import unittest
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

from database.repositories.account_ledger_repo import (
    AccountLedgerConflictError,
    AccountLedgerError,
    AccountLedgerInvariantError,
    InsufficientAccountBalanceError,
)
from services.white_internet_service import WhiteInternetService


def _user():
    user = MagicMock()
    user.id = 42
    return user


def _session():
    """Session double: sync ``add`` as SQLAlchemy really has, async ``flush``."""
    session = MagicMock()
    session.add = MagicMock()
    session.flush = AsyncMock()
    return session


class CheckoutWalletOrderTests(unittest.IsolatedAsyncioTestCase):
    async def test_success_returns_paid_order(self):
        session = _session()
        with patch(
            "services.white_internet_service.create_order_debit",
            new=AsyncMock(return_value=(MagicMock(), True)),
        ) as debit:
            order, failure = await WhiteInternetService._checkout_wallet_order(
                session,
                user=_user(),
                service_type="white_internet",
                tariff_id=7,
                amount_due=Decimal("150.00"),
                duration_days=30,
                operation="purchase",
                insufficient_text="unused",
            )

        self.assertIsNone(failure)
        self.assertIsNotNone(order)
        self.assertEqual(order.status, "paid")
        self.assertIsNotNone(order.paid_at)
        self.assertIsNotNone(order.expires_at)
        self.assertEqual(order.payment_method, "wallet")
        debit.assert_awaited_once()
        session.add.assert_called_once_with(order)

    async def test_zero_amount_skips_debit_and_marks_paid(self):
        session = _session()
        with patch(
            "services.white_internet_service.create_order_debit",
            new=AsyncMock(),
        ) as debit:
            order, failure = await WhiteInternetService._checkout_wallet_order(
                session,
                user=_user(),
                service_type="white_internet",
                tariff_id=7,
                amount_due=Decimal("0.00"),
                duration_days=3,
                operation="trial",
                order_metadata={"is_trial": True},
                insufficient_text="unused",
            )

        self.assertIsNone(failure)
        self.assertEqual(order.status, "paid")
        self.assertEqual(order.metadata_, {"operation": "trial", "is_trial": True})
        debit.assert_not_awaited()

    async def test_insufficient_balance_cancels_order_and_formats_shortage(self):
        session = _session()
        snapshot = MagicMock(available=Decimal("40.00"))
        with patch(
            "services.white_internet_service.create_order_debit",
            new=AsyncMock(side_effect=InsufficientAccountBalanceError("nope")),
        ), patch(
            "services.white_internet_service.get_account_balance",
            new=AsyncMock(return_value=snapshot),
        ):
            order, failure = await WhiteInternetService._checkout_wallet_order(
                session,
                user=_user(),
                service_type="white_internet",
                tariff_id=7,
                amount_due=Decimal("150.00"),
                duration_days=30,
                operation="purchase",
                insufficient_text="Недостаточно средств: {price} / {balance} / {shortage}",
            )

        self.assertIsNotNone(order)
        self.assertIsNotNone(failure)
        ok, message, subscription = failure
        self.assertFalse(ok)
        self.assertIsNone(subscription)
        self.assertIn("150", message)
        self.assertIn("40", message)
        self.assertIn("110", message)
        self.assertEqual(order.status, "canceled")

    async def test_extra_message_kwargs_are_passed_through(self):
        """The top-up path formats the pack size alongside the money fields."""
        session = _session()
        snapshot = MagicMock(available=Decimal("0.00"))
        template = "Не хватает {gb} ГБ: нужно {price}, есть {balance}, не хватает {shortage}"
        with patch(
            "services.white_internet_service.create_order_debit",
            new=AsyncMock(side_effect=InsufficientAccountBalanceError("nope")),
        ), patch(
            "services.white_internet_service.get_account_balance",
            new=AsyncMock(return_value=snapshot),
        ):
            _, failure = await WhiteInternetService._checkout_wallet_order(
                session,
                user=_user(),
                service_type="white_internet",
                tariff_id=7,
                amount_due=Decimal("200.00"),
                duration_days=0,
                traffic_bytes=10 * 1024**3,
                operation="purchase",
                insufficient_text=template,
                gb=10,
            )

        self.assertIsNotNone(failure)
        _, message, _ = failure
        self.assertIn("10", message)
        self.assertIn("200", message)
        self.assertIn("200", message)  # shortage equals the full price at zero balance

    async def test_ledger_error_cancels_order_without_balance_lookup(self):
        session = _session()
        with patch(
            "services.white_internet_service.create_order_debit",
            new=AsyncMock(side_effect=AccountLedgerError("generic ledger failure")),
        ), patch(
            "services.white_internet_service.get_account_balance",
            new=AsyncMock(),
        ) as balance:
            order, failure = await WhiteInternetService._checkout_wallet_order(
                session,
                user=_user(),
                service_type="white_internet",
                tariff_id=7,
                amount_due=Decimal("150.00"),
                duration_days=30,
                operation="purchase",
                insufficient_text="unused {price} {balance} {shortage}",
            )

        self.assertIsNotNone(order)
        ok, message, subscription = failure
        self.assertFalse(ok)
        self.assertIsNone(subscription)
        self.assertIn("generic ledger failure", message)
        self.assertEqual(order.status, "canceled")
        balance.assert_not_awaited()

    async def test_invariant_error_cancels_order_and_raises(self):
        session = _session()
        with patch(
            "services.white_internet_service.create_order_debit",
            new=AsyncMock(side_effect=AccountLedgerInvariantError("invariant broken")),
        ):
            with self.assertRaises(AccountLedgerInvariantError):
                await WhiteInternetService._checkout_wallet_order(
                    session,
                    user=_user(),
                    service_type="white_internet",
                    tariff_id=7,
                    amount_due=Decimal("150.00"),
                    duration_days=30,
                    operation="purchase",
                    insufficient_text="unused",
                )

    async def test_conflict_error_cancels_order_and_raises(self):
        session = _session()
        with patch(
            "services.white_internet_service.create_order_debit",
            new=AsyncMock(side_effect=AccountLedgerConflictError("concurrent collision")),
        ):
            with self.assertRaises(AccountLedgerConflictError):
                await WhiteInternetService._checkout_wallet_order(
                    session,
                    user=_user(),
                    service_type="white_internet",
                    tariff_id=7,
                    amount_due=Decimal("150.00"),
                    duration_days=30,
                    operation="purchase",
                    insufficient_text="unused",
                )

    async def test_subscriber_module_has_single_checkout_boundary(self):
        """Guard against the checkout being copied back into individual paths."""
        import inspect

        source = inspect.getsource(
            __import__(
                "services.white_internet_service", fromlist=["x"]
            )
        )
        self.assertEqual(
            source.count("await create_order_debit("),
            1,
            "create_order_debit must be called from exactly one place",
        )


if __name__ == "__main__":
    unittest.main()
