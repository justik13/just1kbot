import unittest
from decimal import Decimal

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import BigInteger, Integer, Numeric, String

from database.models import (
    Server,
    Tariff,
    TariffQuote,
    WhiteInternetQuotaGrant,
    WhiteInternetSubscription,
)


class WhiteInternetModelsTests(unittest.TestCase):
    def test_alembic_migration_0015_revision_chain(self):
        scripts = ScriptDirectory.from_config(Config("alembic.ini"))
        rev_0015 = scripts.get_revision("0015_white_internet")
        self.assertIsNotNone(rev_0015)
        self.assertEqual(rev_0015.down_revision, "0014_drop_sub_token")
        self.assertEqual(scripts.get_heads(), ["0015_white_internet"])



    def test_tariff_model_white_internet_fields_and_constraints(self):
        table = Tariff.__table__
        self.assertIn("service_type", table.columns)
        self.assertEqual(table.columns["service_type"].type.length, 30)
        self.assertFalse(table.columns["service_type"].nullable)
        self.assertEqual(table.columns["service_type"].default.arg, "awg")
        self.assertEqual(table.columns["service_type"].server_default.arg, "awg")

        constraint_names = {c.name for c in table.constraints if c.name}
        self.assertIn("uq_tariffs_service_device_duration", constraint_names)
        self.assertNotIn("uq_tariffs_device_limit_duration_days", constraint_names)

        uq = next(c for c in table.constraints if c.name == "uq_tariffs_service_device_duration")
        col_names = [col.name for col in uq.columns]
        self.assertEqual(col_names, ["service_type", "device_limit", "duration_days"])

    def test_tariff_quote_model_white_internet_fields_and_constraints(self):
        table = TariffQuote.__table__
        self.assertIn("service_type", table.columns)
        self.assertEqual(table.columns["service_type"].type.length, 30)
        self.assertFalse(table.columns["service_type"].nullable)
        self.assertEqual(table.columns["service_type"].default.arg, "awg")
        self.assertEqual(table.columns["service_type"].server_default.arg, "awg")

        constraint_names = {c.name for c in table.constraints if c.name}
        self.assertIn("ck_tariff_quotes_service_type", constraint_names)

        index_names = {idx.name: idx for idx in table.indexes}
        self.assertIn("uq_tariff_quotes_active_checkout", index_names)
        idx = index_names["uq_tariff_quotes_active_checkout"]
        self.assertTrue(idx.unique)
        col_names = [col.name for col in idx.columns]
        self.assertEqual(col_names, ["user_id", "service_type", "target_tariff_version_id"])

    def test_server_model_white_internet_capabilities_and_epoch(self):
        table = Server.__table__
        self.assertIn("capabilities", table.columns)
        self.assertFalse(table.columns["capabilities"].nullable)
        self.assertEqual(table.columns["capabilities"].server_default.arg, "[]")

        self.assertIn("xray_instance_epoch", table.columns)
        self.assertTrue(table.columns["xray_instance_epoch"].nullable)
        self.assertEqual(table.columns["xray_instance_epoch"].type.length, 64)

    def test_white_internet_subscription_table_structure(self):
        table = WhiteInternetSubscription.__table__
        self.assertEqual(table.name, "white_internet_subscriptions")

        expected_columns = {
            "id": Integer,
            "user_id": Integer,
            "origin_node_id": Integer,
            "token": String,
            "uuid": String,
            "status": String,
            "status_reason": String,
            "started_at": None,
            "expires_at": None,
            "traffic_limit_bytes": BigInteger,
            "traffic_used_bytes": BigInteger,
            "last_uplink_snapshot": BigInteger,
            "last_downlink_snapshot": BigInteger,
            "traffic_stats_epoch": String,
            "provisioning_status": String,
            "desired_version": Integer,
            "actual_version": Integer,
            "last_reconciled_node_epoch": String,
            "last_synced_at": None,
            "last_sync_error": None,
            "created_at": None,
            "updated_at": None,
        }
        for col_name in expected_columns:
            self.assertIn(col_name, table.columns, f"Missing column {col_name}")

        self.assertTrue(table.columns["id"].primary_key)
        self.assertTrue(table.columns["token"].unique)
        self.assertTrue(table.columns["token"].index)
        self.assertTrue(table.columns["uuid"].unique)
        self.assertFalse(table.columns["uuid"].nullable)
        self.assertTrue(table.columns["user_id"].index)
        self.assertTrue(table.columns["origin_node_id"].index)
        self.assertTrue(table.columns["status"].index)
        self.assertTrue(table.columns["expires_at"].index)

        # Foreign keys
        fk_user = next(fk for fk in table.foreign_keys if fk.column.table.name == "users")
        self.assertEqual(fk_user.ondelete, "CASCADE")
        fk_server = next(fk for fk in table.foreign_keys if fk.column.table.name == "servers")
        self.assertEqual(fk_server.ondelete, "RESTRICT")

        # Constraints
        constraint_names = {c.name for c in table.constraints if c.name}
        self.assertIn("ck_white_internet_subscriptions_status", constraint_names)
        self.assertIn("ck_white_internet_subscriptions_provisioning_status", constraint_names)
        self.assertIn("ck_white_internet_subscriptions_traffic_nonnegative", constraint_names)

    def test_white_internet_quota_grant_table_structure(self):
        table = WhiteInternetQuotaGrant.__table__
        self.assertEqual(table.name, "white_internet_quota_grants")

        self.assertIsInstance(table.columns["id"].type, BigInteger)
        self.assertTrue(table.columns["id"].primary_key)

        self.assertIsInstance(table.columns["subscription_id"].type, Integer)
        self.assertTrue(table.columns["subscription_id"].index)

        self.assertIsInstance(table.columns["grant_type"].type, String)
        self.assertIsInstance(table.columns["bytes_granted"].type, BigInteger)
        self.assertIsInstance(table.columns["bytes_remaining"].type, BigInteger)
        self.assertIsInstance(table.columns["price_rub"].type, Numeric)
        self.assertEqual(table.columns["price_rub"].default.arg, Decimal("0.00"))

        self.assertIsInstance(table.columns["quote_id"].type, BigInteger)
        self.assertTrue(table.columns["expires_at"].index)

        # Foreign keys
        fk_sub = next(
            fk for fk in table.foreign_keys if fk.column.table.name == "white_internet_subscriptions"
        )
        self.assertEqual(fk_sub.ondelete, "CASCADE")
        fk_quote = next(fk for fk in table.foreign_keys if fk.column.table.name == "tariff_quotes")
        self.assertEqual(fk_quote.ondelete, "RESTRICT")

        # Constraints
        constraint_names = {c.name for c in table.constraints if c.name}
        self.assertIn("ck_white_internet_quota_grants_grant_type", constraint_names)
        self.assertIn("ck_white_internet_quota_grants_bytes_granted_positive", constraint_names)
        self.assertIn("ck_white_internet_quota_grants_bytes_remaining_nonnegative", constraint_names)
        self.assertIn("ck_white_internet_quota_grants_bytes_remaining_le_granted", constraint_names)
        self.assertIn("ck_white_internet_quota_grants_price_nonnegative", constraint_names)
        self.assertIn("uq_white_internet_quota_grants_sub_quote_type", constraint_names)


if __name__ == "__main__":
    unittest.main()
