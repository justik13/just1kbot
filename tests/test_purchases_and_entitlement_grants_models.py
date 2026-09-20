import unittest

from sqlalchemy import BigInteger, CheckConstraint, Index, Integer, Numeric, String
from sqlalchemy.dialects.postgresql import JSONB

from database import models


class PurchasesAndEntitlementGrantsModelTests(unittest.TestCase):
    def test_purchase_table_structure_and_types(self):
        table = models.Purchase.__table__
        self.assertEqual(table.name, "purchases")

        # Columns
        col_id = table.c.id
        self.assertIsInstance(col_id.type, BigInteger)
        self.assertTrue(col_id.primary_key)

        col_user_id = table.c.user_id
        self.assertIsInstance(col_user_id.type, Integer)
        self.assertFalse(col_user_id.nullable)

        col_quote_id = table.c.quote_id
        self.assertIsInstance(col_quote_id.type, BigInteger)
        self.assertTrue(col_quote_id.nullable)

        col_idemp = table.c.idempotency_key
        self.assertIsInstance(col_idemp.type, String)
        self.assertEqual(col_idemp.type.length, 100)

        col_amount = table.c.amount_rub
        self.assertIsInstance(col_amount.type, Numeric)
        self.assertEqual(col_amount.type.precision, 12)
        self.assertEqual(col_amount.type.scale, 2)

        col_details = table.c.details
        self.assertIsInstance(col_details.type, JSONB)

    def test_purchase_constraints_and_indexes(self):
        table = models.Purchase.__table__
        constraints = {c.name: c for c in table.constraints if isinstance(c, CheckConstraint)}

        self.assertIn("ck_purchases_status", constraints)
        self.assertIn("ck_purchases_fulfillment_status", constraints)
        self.assertIn("ck_purchases_amount_rub_nonnegative", constraints)

        indexes = {idx.name: idx for idx in table.indexes if isinstance(idx, Index)}
        self.assertIn("ix_purchases_user_id", indexes)
        self.assertIn("uq_purchases_quote_id", indexes)
        self.assertTrue(indexes["uq_purchases_quote_id"].unique)
        self.assertIn("uq_purchases_idempotency_key", indexes)
        self.assertTrue(indexes["uq_purchases_idempotency_key"].unique)
        self.assertIn("ix_purchases_user_status", indexes)

    def test_entitlement_grant_table_structure_and_types(self):
        table = models.EntitlementGrant.__table__
        self.assertEqual(table.name, "entitlement_grants")

        col_id = table.c.id
        self.assertIsInstance(col_id.type, BigInteger)
        self.assertTrue(col_id.primary_key)

        col_purchase_id = table.c.purchase_id
        self.assertIsInstance(col_purchase_id.type, BigInteger)
        self.assertTrue(col_purchase_id.nullable)

        col_paid_value = table.c.paid_value_rub
        self.assertIsInstance(col_paid_value.type, Numeric)
        self.assertEqual(col_paid_value.type.precision, 18)
        self.assertEqual(col_paid_value.type.scale, 6)

        col_orig_dur = table.c.original_duration_hours
        self.assertIsInstance(col_orig_dur.type, Integer)
        self.assertFalse(col_orig_dur.nullable)

    def test_entitlement_grant_constraints_and_indexes(self):
        table = models.EntitlementGrant.__table__
        constraints = {c.name: c for c in table.constraints if isinstance(c, CheckConstraint)}

        self.assertIn("ck_entitlement_grants_status", constraints)
        self.assertIn("ck_entitlement_grants_grant_type", constraints)
        self.assertIn("ck_entitlement_grants_paid_value_rub_nonnegative", constraints)
        self.assertIn("ck_entitlement_grants_coverage_interval", constraints)
        self.assertIn("ck_entitlement_grants_original_duration_positive", constraints)

        indexes = {idx.name: idx for idx in table.indexes if isinstance(idx, Index)}
        self.assertIn("ix_entitlement_grants_user_active", indexes)
        self.assertIn("ix_entitlement_grants_purchase_id", indexes)
        self.assertIn("uq_entitlement_grants_source", indexes)
        self.assertTrue(indexes["uq_entitlement_grants_source"].unique)

    def test_account_ledger_purchase_id_column_and_indexes(self):
        table = models.AccountLedgerEntry.__table__
        self.assertIn("purchase_id", table.c)
        col_purchase_id = table.c.purchase_id
        self.assertIsInstance(col_purchase_id.type, BigInteger)
        self.assertTrue(col_purchase_id.nullable)

        indexes = {idx.name: idx for idx in table.indexes if isinstance(idx, Index)}
        self.assertIn("uq_account_ledger_purchase_debit_v2", indexes)
        self.assertTrue(indexes["uq_account_ledger_purchase_debit_v2"].unique)
        self.assertIn("ix_account_ledger_purchase_id", indexes)

        constraints = {c.name: c for c in table.constraints if isinstance(c, CheckConstraint)}
        self.assertIn("ck_account_ledger_entry_shape", constraints)
        shape_sql = str(constraints["ck_account_ledger_entry_shape"].sqltext)
        self.assertIn("purchase_id IS NOT NULL", shape_sql)
        self.assertIn("purchase_id IS NULL", shape_sql)
