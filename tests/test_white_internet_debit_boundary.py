"""Focused coverage for the single White Internet debit boundary."""

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


def _quote(amount=Decimal("150.00")):
    quote = MagicMock()
    quote.id = 77
    quote.amount_due_rub = amount
    quote.status = "active"
    return quote


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


class DebitForQuoteTests(unittest.IsolatedAsyncioTestCase):
    async def test_success_returns_none_and_keeps_quote_active(self):
        session = _session()
        quote = _quote()
        with patch(
            "services.white_internet_service.create_purchase_debit", new=AsyncMock()
        ) as debit:
            result = await WhiteInternetService._debit_for_quote(
                session,
                user=_user(),
                quote=quote,
                price=Decimal("150.00"),
                insufficient_text="Недостаточно средств: {price} / {balance} / {shortage}",
            )

        self.assertIsNone(result)
        debit.assert_awaited_once()
        # On success the helper leaves the status to the caller (which marks it
        # consumed); it must not touch it here.
        self.assertEqual(quote.status, "active")
        session.add.assert_called_once_with(quote)

    async def test_insufficient_balance_cancels_quote_and_formats_shortage(self):
        session = _session()
        quote = _quote()
        snapshot = MagicMock(available=Decimal("40.00"))
        with patch(
            "services.white_internet_service.create_purchase_debit",
            new=AsyncMock(side_effect=InsufficientAccountBalanceError("nope")),
        ), patch(
            "services.white_internet_service.get_account_balance",
            new=AsyncMock(return_value=snapshot),
        ):
            result = await WhiteInternetService._debit_for_quote(
                session,
                user=_user(),
                quote=quote,
                price=Decimal("150.00"),
                insufficient_text="Недостаточно средств: {price} / {balance} / {shortage}",
            )

        self.assertIsNotNone(result)
        ok, message, subscription = result
        self.assertFalse(ok)
        self.assertIsNone(subscription)
        self.assertIn("150", message)
        self.assertIn("40", message)
        self.assertIn("110", message)
        self.assertEqual(quote.status, "cancelled")

    async def test_extra_message_kwargs_are_passed_through(self):
        """The top-up path formats the pack size alongside the money fields."""
        session = _session()
        quote = _quote(Decimal("200.00"))
        snapshot = MagicMock(available=Decimal("0.00"))
        template = "Не хватает {gb} ГБ: нужно {price}, есть {balance}, не хватает {shortage}"
        with patch(
            "services.white_internet_service.create_purchase_debit",
            new=AsyncMock(side_effect=InsufficientAccountBalanceError("nope")),
        ), patch(
            "services.white_internet_service.get_account_balance",
            new=AsyncMock(return_value=snapshot),
        ):
            _, message, _ = await WhiteInternetService._debit_for_quote(
                session,
                user=_user(),
                quote=quote,
                price=Decimal("200.00"),
                insufficient_text=template,
                gb=10,
            )

        self.assertIn("10", message)
        self.assertIn("200", message)
        self.assertIn("200", message)  # shortage equals the full price at zero balance

    async def test_ledger_error_cancels_quote_without_balance_lookup(self):
        session = _session()
        quote = _quote()
        with patch(
            "services.white_internet_service.create_purchase_debit",
            new=AsyncMock(side_effect=AccountLedgerError("generic ledger failure")),
        ), patch(
            "services.white_internet_service.get_account_balance",
            new=AsyncMock(),
        ) as balance:
            result = await WhiteInternetService._debit_for_quote(
                session,
                user=_user(),
                quote=quote,
                price=Decimal("150.00"),
                insufficient_text="unused {price} {balance} {shortage}",
            )

        ok, message, subscription = result
        self.assertFalse(ok)
        self.assertIsNone(subscription)
        self.assertIn("generic ledger failure", message)
        self.assertEqual(quote.status, "cancelled")
        balance.assert_not_awaited()

    async def test_invariant_error_cancels_quote_and_raises(self):
        session = _session()
        quote = _quote()
        with patch(
            "services.white_internet_service.create_purchase_debit",
            new=AsyncMock(side_effect=AccountLedgerInvariantError("invariant broken")),
        ):
            with self.assertRaises(AccountLedgerInvariantError):
                await WhiteInternetService._debit_for_quote(
                    session,
                    user=_user(),
                    quote=quote,
                    price=Decimal("150.00"),
                    insufficient_text="unused",
                )
        self.assertEqual(quote.status, "cancelled")
        self.assertEqual(session.flush.await_count, 2)

    async def test_conflict_error_cancels_quote_and_raises(self):
        session = _session()
        quote = _quote()
        with patch(
            "services.white_internet_service.create_purchase_debit",
            new=AsyncMock(side_effect=AccountLedgerConflictError("concurrent collision")),
        ):
            with self.assertRaises(AccountLedgerConflictError):
                await WhiteInternetService._debit_for_quote(
                    session,
                    user=_user(),
                    quote=quote,
                    price=Decimal("150.00"),
                    insufficient_text="unused",
                )
        self.assertEqual(quote.status, "cancelled")
        self.assertEqual(session.flush.await_count, 2)

    async def test_subscriber_module_has_single_debit_boundary(self):
        """Guard against the boundary being copied back into individual paths."""
        import inspect

        source = inspect.getsource(
            __import__(
                "services.white_internet_service", fromlist=["x"]
            )
        )
        self.assertEqual(
            source.count("await create_purchase_debit("),
            1,
            "create_purchase_debit must be called from exactly one place",
        )


if __name__ == "__main__":
    unittest.main()
