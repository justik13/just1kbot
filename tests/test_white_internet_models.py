import unittest
from decimal import Decimal

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import BigInteger, Integer, String

from config.enums import ServerLifecycleStatus, ServiceType
from database.models import (
    Server,
    Tariff,
    TariffQuote,
    TariffVersion,
    WhiteInternetSubscription,
)


class WhiteInternetModelsTests(unittest.TestCase):
    def test_alembic_migration_0019_revision_chain(self):
        scripts = ScriptDirectory.from_config(Config("alembic.ini"))
        rev_0019 = scripts.get_revision("0019_wi_server_set_null")
        self.assertIsNotNone(rev_0019)
        self.assertEqual(rev_0019.down_revision, "0018_simplify_wi_traffic")
        rev_0018 = scripts.get_revision("0018_simplify_wi_traffic")
        self.assertIsNotNone(rev_0018)
        self.assertEqual(rev_0018.down_revision, "0017_white_internet_durations")
        rev_0017 = scripts.get_revision("0017_white_internet_durations")
        self.assertIsNotNone(rev_0017)
        self.assertEqual(rev_0017.down_revision, "0016_white_internet")
        self.assertEqual(scripts.get_heads(), ["0019_wi_server_set_null"])

    def test_server_lifecycle_status_field_and_constraints(self):
        self.assertEqual(ServerLifecycleStatus.ACTIVE, "ACTIVE")
        self.assertEqual(ServerLifecycleStatus.DECOMMISSIONING, "DECOMMISSIONING")
        self.assertEqual(ServerLifecycleStatus.DECOMMISSIONED, "DECOMMISSIONED")
        self.assertEqual(ServerLifecycleStatus.ARCHIVED, "ARCHIVED")

        table = Server.__table__
        self.assertIn("lifecycle_status", table.columns)
        self.assertEqual(table.columns["lifecycle_status"].type.length, 30)
        self.assertFalse(table.columns["lifecycle_status"].nullable)
        self.assertEqual(
            table.columns["lifecycle_status"].default.arg, ServerLifecycleStatus.ACTIVE
        )
        self.assertEqual(
            table.columns["lifecycle_status"].server_default.arg, ServerLifecycleStatus.ACTIVE
        )

        constraint_names = {c.name for c in table.constraints if c.name}
        self.assertIn("ck_servers_lifecycle_status", constraint_names)

        index_names = {idx.name: idx for idx in table.indexes}
        self.assertIn("ix_servers_lifecycle_status", index_names)

    def test_tariff_version_white_internet_fields_and_constraints(self):
        table = TariffVersion.__table__
        self.assertIn("service_type", table.columns)
        self.assertEqual(table.columns["service_type"].type.length, 30)
        self.assertFalse(table.columns["service_type"].nullable)
        self.assertEqual(table.columns["service_type"].default.arg, ServiceType.AWG)
        self.assertEqual(table.columns["service_type"].server_default.arg, "awg")

        self.assertIn("base_quota_bytes", table.columns)
        self.assertTrue(table.columns["base_quota_bytes"].nullable)
        self.assertIsInstance(table.columns["base_quota_bytes"].type, BigInteger)

        constraint_names = {c.name for c in table.constraints if c.name}
        self.assertIn("ck_tariff_versions_service_type", constraint_names)
        self.assertIn("ck_tariff_versions_base_quota_positive", constraint_names)

    def test_tariff_version_snapshot_and_duration_days(self):
        tv = TariffVersion(
            tariff_id=1,
            version_number=1,
            name_snapshot="Белый Интернет 50 ГБ",
            service_type=ServiceType.WHITE_INTERNET,
            duration_hours=720,
            device_limit=1,
            price_rub=Decimal("250.00"),
            currency="RUB",
            base_quota_bytes=53687091200,
        )
        self.assertEqual(tv.duration_days, 30)
        self.assertEqual(tv.service_type, "white_internet")
        self.assertEqual(tv.base_quota_bytes, 53687091200)

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

        self.assertIn("xray_instance_boot_id", table.columns)
        self.assertTrue(table.columns["xray_instance_boot_id"].nullable)

        self.assertIn("xray_instance_starttime", table.columns)
        self.assertTrue(table.columns["xray_instance_starttime"].nullable)

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
            "base_traffic_bytes": BigInteger,
            "extra_traffic_bytes": BigInteger,
            "traffic_used_bytes": BigInteger,
            "traffic_uplink_bytes": BigInteger,
            "traffic_downlink_bytes": BigInteger,
            "traffic_overage_bytes": BigInteger,
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
        self.assertEqual(fk_server.ondelete, "SET NULL")
        self.assertTrue(table.columns["origin_node_id"].nullable)

        # Constraints
        constraint_names = {c.name for c in table.constraints if c.name}
        self.assertIn("ck_white_internet_subscriptions_status", constraint_names)
        self.assertIn("ck_white_internet_subscriptions_provisioning_status", constraint_names)
        self.assertIn("ck_white_internet_subscriptions_traffic_nonnegative", constraint_names)

    def test_white_internet_subscription_hybrid_traffic_limit(self):
        sub = WhiteInternetSubscription(
            base_traffic_bytes=53_687_091_200,
            extra_traffic_bytes=10_737_418_240,
        )
        self.assertEqual(sub.traffic_limit_bytes, 64_424_509_440)
        # Test backward-compatible setter
        sub.traffic_limit_bytes = 100_000_000_000
        self.assertEqual(sub.base_traffic_bytes, 100_000_000_000)
        self.assertEqual(sub.extra_traffic_bytes, 0)

    def test_white_internet_subscription_traffic_constraint_expression(self):
        table = WhiteInternetSubscription.__table__
        traffic_ck = next(
            c for c in table.constraints
            if c.name == "ck_white_internet_subscriptions_traffic_nonnegative"
        )
        sql_text = str(traffic_ck.sqltext)
        self.assertIn("base_traffic_bytes >= 0", sql_text)
        self.assertIn("extra_traffic_bytes >= 0", sql_text)
        self.assertIn("traffic_used_bytes >= 0", sql_text)
        self.assertIn("traffic_uplink_bytes >= 0", sql_text)
        self.assertIn("traffic_downlink_bytes >= 0", sql_text)
        self.assertNotIn("last_uplink_snapshot", sql_text)
        self.assertNotIn("last_downlink_snapshot", sql_text)
        self.assertNotIn("traffic_overage_bytes", sql_text)


if __name__ == "__main__":
    unittest.main()
