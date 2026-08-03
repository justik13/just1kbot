import unittest
from decimal import Decimal
from pathlib import Path

from sqlalchemy import BigInteger

from database.models import (
    AccountBalanceReservation,
    AccountLedgerAllocation,
    AccountLedgerEntry,
    Payment,
)
from database.repositories.account_ledger_repo import whole_rubles


class WholeRubleTests(unittest.TestCase):
    def test_accepts_integer_like_values(self):
        self.assertEqual(whole_rubles(10), Decimal("10.00"))
        self.assertEqual(whole_rubles("499.00"), Decimal("499.00"))

    def test_rejects_fractional_negative_and_float_values(self):
        for value in ("10.01", "NaN", "Infinity", -1, 1.0):
            with self.subTest(value=value), self.assertRaises(ValueError):
                whole_rubles(value)

    def test_zero_is_explicit(self):
        with self.assertRaises(ValueError):
            whole_rubles(0)
        self.assertEqual(whole_rubles(0, allow_zero=True), Decimal("0.00"))


class AccountLedgerSchemaTests(unittest.TestCase):
    def test_money_history_uses_bigint_keys_and_restrict_sources(self):
        self.assertIsInstance(AccountLedgerEntry.__table__.c.id.type, BigInteger)
        self.assertIsInstance(AccountLedgerAllocation.__table__.c.id.type, BigInteger)
        for table in (
            AccountLedgerEntry.__table__,
            AccountLedgerAllocation.__table__,
            AccountBalanceReservation.__table__,
        ):
            for foreign_key in table.foreign_keys:
                self.assertEqual(foreign_key.ondelete, "RESTRICT")

    def test_payment_model_is_topup_only(self):
        self.assertNotIn("tariff_id", Payment.__table__.c)
        self.assertNotIn("tariff_quote_id", Payment.__table__.c)
        self.assertNotIn("payment_kind", Payment.__table__.c)
        constraints = {
            item.name for item in Payment.__table__.constraints if item.name
        }
        self.assertIn("ck_payments_topup_money", constraints)

    def test_exactly_once_indexes_are_present(self):
        indexes = {item.name for item in AccountLedgerEntry.__table__.indexes}
        self.assertIn("uq_account_ledger_payment_credit", indexes)
        self.assertIn("uq_account_ledger_purchase_debit", indexes)
        self.assertIn("uq_account_ledger_reversal", indexes)
        self.assertTrue(AccountLedgerEntry.__table__.c.idempotency_key.unique)
        self.assertTrue(AccountLedgerAllocation.__table__.c.idempotency_key.unique)

    def test_migration_installs_append_only_and_allocation_guards(self):
        migration = (
            Path(__file__).parents[1]
            / "alembic"
            / "versions"
            / "0001_clean_baseline.py"
        ).read_text(encoding="utf-8")
        for required in (
            "account_ledger_append_only",
            "account_allocations_append_only",
            "validate_account_allocation",
            "account_reservation_identity",
        ):
            self.assertIn(required, migration)


if __name__ == "__main__":
    unittest.main()
