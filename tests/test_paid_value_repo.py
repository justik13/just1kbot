import unittest
from decimal import Decimal
from unittest.mock import AsyncMock

from database.models import PaidValueLedgerEntry
from database.repositories.paid_value_repo import (
    _INDEX_WHERE_MAP,
    PaidValueLedgerConflictError,
    _insert_or_get,
    get_or_create_account_purchase_entry,
    get_or_create_conversion_entry,
)


class PaidValueRepoTests(unittest.IsolatedAsyncioTestCase):
    def test_index_where_map_matches_model_check_constraints(self):
        """Verify that _INDEX_WHERE_MAP exactly matches all entry types permitted
        by the PaidValueLedgerEntry ck_paid_value_ledger_entry_type CheckConstraint."""
        # Extract allowed entry_types from model check constraints
        found_constraint = None
        for constraint in PaidValueLedgerEntry.__table__.constraints:
            if getattr(constraint, "name", None) == "ck_paid_value_ledger_entry_type":
                found_constraint = constraint
                break

        self.assertIsNotNone(found_constraint)
        sqltext = str(found_constraint.sqltext)
        expected_types = {"account_purchase", "tariff_conversion", "manual_adjustment"}
        for t in expected_types:
            self.assertIn(t, sqltext)

        # Verify repo whitelist map contains all expected types
        self.assertEqual(set(_INDEX_WHERE_MAP.keys()), expected_types)

    async def test_invalid_entry_type_raises_value_error(self):
        """Verify that attempting to insert an unsupported entry_type raises ValueError."""
        mock_session = AsyncMock()
        session.begin_nested.return_value.__aenter__.return_value = session
        session.begin_nested.return_value.__aexit__.return_value = None
        values = {
            "entry_type": "unknown_legacy_type",
            "user_id": 1,
            "quote_id": 10,
        }
        with self.assertRaises(ValueError) as ctx:
            await _insert_or_get(mock_session, values, PaidValueLedgerEntry.quote_id)

        self.assertIn("Invalid entry_type: unknown_legacy_type", str(ctx.exception))

    async def test_get_or_create_account_purchase_entry_success(self):
        """Verify get_or_create_account_purchase_entry builds correct payload and returns verified entry."""
        entry = PaidValueLedgerEntry(
            id=101,
            user_id=42,
            source_type="quote",
            source_id="555",
            entry_type="account_purchase",
            paid_hours_delta=720,
            paid_value_rub_delta=Decimal("150.000000"),
            currency="RUB",
            tariff_version_id=1,
            quote_id=555,
            metadata_={},
        )

        mock_session = AsyncMock()
        session.begin_nested.return_value.__aenter__.return_value = session
        session.begin_nested.return_value.__aexit__.return_value = None
        mock_session.scalar.return_value = 101
        mock_session.get.return_value = entry

        res = await get_or_create_account_purchase_entry(
            mock_session,
            user_id=42,
            quote_id=555,
            tariff_version_id=1,
            paid_hours=720,
            paid_value_rub=Decimal("150.000000"),
        )
        self.assertEqual(res.id, 101)
        self.assertEqual(res.entry_type, "account_purchase")
        self.assertEqual(res.quote_id, 555)

    async def test_get_or_create_conversion_entry_success(self):
        """Verify get_or_create_conversion_entry builds correct payload and returns verified entry."""
        entry = PaidValueLedgerEntry(
            id=102,
            user_id=42,
            source_type="quote",
            source_id="666",
            entry_type="tariff_conversion",
            paid_hours_delta=360,
            paid_value_rub_delta=Decimal("75.000000"),
            currency="RUB",
            tariff_version_id=2,
            quote_id=666,
            metadata_={"old_tariff": 1},
        )

        mock_session = AsyncMock()
        session.begin_nested.return_value.__aenter__.return_value = session
        session.begin_nested.return_value.__aexit__.return_value = None
        mock_session.scalar.return_value = 102
        mock_session.get.return_value = entry

        res = await get_or_create_conversion_entry(
            mock_session,
            user_id=42,
            quote_id=666,
            tariff_version_id=2,
            paid_hours_delta=360,
            paid_value_rub_delta=Decimal("75.000000"),
            metadata={"old_tariff": 1},
        )
        self.assertEqual(res.id, 102)
        self.assertEqual(res.entry_type, "tariff_conversion")
        self.assertEqual(res.quote_id, 666)

    async def test_economic_mismatch_raises_conflict_error(self):
        """Verify that returning an existing entry with mismatched economic fields raises PaidValueLedgerConflictError."""
        existing_entry = PaidValueLedgerEntry(
            id=103,
            user_id=42,
            source_type="quote",
            source_id="777",
            entry_type="account_purchase",
            paid_hours_delta=720,
            paid_value_rub_delta=Decimal("200.000000"),  # Different amount
            currency="RUB",
            tariff_version_id=1,
            quote_id=777,
            metadata_={},
        )

        mock_session = AsyncMock()
        session.begin_nested.return_value.__aenter__.return_value = session
        session.begin_nested.return_value.__aexit__.return_value = None
        # Simulate on_conflict_do_nothing returning None (conflict exists)
        mock_session.scalar.side_effect = [None, existing_entry]

        with self.assertRaises(PaidValueLedgerConflictError) as ctx:
            await get_or_create_account_purchase_entry(
                mock_session,
                user_id=42,
                quote_id=777,
                tariff_version_id=1,
                paid_hours=720,
                paid_value_rub=Decimal("150.000000"),
            )

        self.assertIn("paid_value_ledger_conflict:paid_value_rub_delta", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
