"""Unit tests for Alembic migration 0029_rebase_wi_traffic_downlink."""

import importlib.util
import os
from pathlib import Path
import unittest
from unittest.mock import patch

from alembic.config import Config
from alembic.script import ScriptDirectory


class Migration0029Tests(unittest.TestCase):
    """Test suite for migration 0029_rebase_wi_traffic_downlink."""

    def setUp(self):
        file_path = os.path.join(
            os.path.dirname(__file__),
            "..",
            "alembic",
            "versions",
            "0029_rebase_wi_traffic_downlink.py",
        )
        spec = importlib.util.spec_from_file_location("migration_0029", file_path)
        self.migration = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.migration)

    def test_migration_0029_metadata_and_chain(self):
        scripts = ScriptDirectory.from_config(Config("alembic.ini"))
        rev = scripts.get_revision("0029_rebase_wi_traffic_downlink")
        self.assertIsNotNone(rev)
        self.assertEqual(rev.down_revision, "0028_wi_notifications")
        self.assertEqual(scripts.get_heads(), ["0029_rebase_wi_traffic_downlink"])
        self.assertEqual(self.migration.revision, "0029_rebase_wi_traffic_downlink")
        self.assertEqual(self.migration.down_revision, "0028_wi_notifications")

    def test_migration_0029_source_content(self):
        m29_path = Path("alembic/versions/0029_rebase_wi_traffic_downlink.py")
        self.assertTrue(m29_path.is_file())
        content = m29_path.read_text(encoding="utf-8")

        self.assertIn("def upgrade()", content)
        self.assertIn("def downgrade()", content)
        self.assertIn("traffic_used_bytes=wi_subs.c.traffic_downlink_bytes", content)
        self.assertIn("traffic_overage_bytes=overage_calc", content)
        self.assertIn("status=\"ACTIVE\"", content)
        self.assertIn("provisioning_status=\"PENDING_UPDATE\"", content)
        self.assertIn("notified_90p=False", content)

    def test_migration_0029_upgrade_execution_calls(self):
        with patch("alembic.op.execute") as mock_execute:
            self.migration.upgrade()
            self.assertEqual(mock_execute.call_count, 3)

    def test_migration_0029_downgrade_noop(self):
        with patch("alembic.op.execute") as mock_execute:
            self.migration.downgrade()
            mock_execute.assert_not_called()

    def test_migration_0029_database_transformation_matrix(self):
        import sqlalchemy as sa
        from datetime import datetime, timezone, timedelta

        engine = sa.create_engine("sqlite:///:memory:")
        metadata = sa.MetaData()
        table = sa.Table(
            "white_internet_subscriptions",
            metadata,
            sa.Column("id", sa.Integer, primary_key=True),
            sa.Column("status", sa.String),
            sa.Column("status_reason", sa.String, nullable=True),
            sa.Column("expires_at", sa.DateTime(timezone=True)),
            sa.Column("base_traffic_bytes", sa.BigInteger),
            sa.Column("extra_traffic_bytes", sa.BigInteger),
            sa.Column("traffic_used_bytes", sa.BigInteger),
            sa.Column("traffic_downlink_bytes", sa.BigInteger),
            sa.Column("traffic_overage_bytes", sa.BigInteger),
            sa.Column("desired_version", sa.Integer),
            sa.Column("provisioning_status", sa.String),
            sa.Column("notified_90p", sa.Boolean),
        )
        metadata.create_all(engine)
        now = datetime.now(timezone.utc)

        # 1. Uplink overage: was EXHAUSTED by uplink (60 GB > 50 GB), but down=40 GB.
        # Should become ACTIVE with overage=0, desired_version=2, PENDING_UPDATE, notified_90p=False.
        row1 = {
            "id": 1,
            "status": "EXHAUSTED",
            "status_reason": "quota_exhausted",
            "expires_at": now + timedelta(days=5),
            "base_traffic_bytes": 50 * 1024**3,
            "extra_traffic_bytes": 0,
            "traffic_used_bytes": 60 * 1024**3,
            "traffic_downlink_bytes": 40 * 1024**3,
            "traffic_overage_bytes": 10 * 1024**3,
            "desired_version": 1,
            "provisioning_status": "ACTIVE",
            "notified_90p": True,
        }

        # 2. Downlink overage: down=55 GB > 50 GB.
        # Must stay EXHAUSTED with overage=5 GB, desired_version=1.
        row2 = {
            "id": 2,
            "status": "EXHAUSTED",
            "status_reason": "quota_exhausted",
            "expires_at": now + timedelta(days=5),
            "base_traffic_bytes": 50 * 1024**3,
            "extra_traffic_bytes": 0,
            "traffic_used_bytes": 65 * 1024**3,
            "traffic_downlink_bytes": 55 * 1024**3,
            "traffic_overage_bytes": 15 * 1024**3,
            "desired_version": 1,
            "provisioning_status": "ACTIVE",
            "notified_90p": True,
        }

        # 3. Time expired: EXHAUSTED, down=40 GB, but expires_at is past.
        # Must NOT be reactivated.
        row3 = {
            "id": 3,
            "status": "EXHAUSTED",
            "status_reason": "quota_exhausted",
            "expires_at": now - timedelta(days=1),
            "base_traffic_bytes": 50 * 1024**3,
            "extra_traffic_bytes": 0,
            "traffic_used_bytes": 60 * 1024**3,
            "traffic_downlink_bytes": 40 * 1024**3,
            "traffic_overage_bytes": 10 * 1024**3,
            "desired_version": 1,
            "provisioning_status": "ACTIVE",
            "notified_90p": True,
        }

        # 4. Admin disabled: DISABLED, down=15 GB.
        # Must NOT be reactivated.
        row4 = {
            "id": 4,
            "status": "DISABLED",
            "status_reason": "manual_admin",
            "expires_at": now + timedelta(days=5),
            "base_traffic_bytes": 50 * 1024**3,
            "extra_traffic_bytes": 0,
            "traffic_used_bytes": 20 * 1024**3,
            "traffic_downlink_bytes": 15 * 1024**3,
            "traffic_overage_bytes": 0,
            "desired_version": 1,
            "provisioning_status": "SYNCED_INACTIVE",
            "notified_90p": False,
        }

        # 5. Active 90% threshold rollback: was 46 GB (92%), down=40 GB (80%).
        # Must stay ACTIVE, used=40 GB, notified_90p reset to False.
        row5 = {
            "id": 5,
            "status": "ACTIVE",
            "status_reason": None,
            "expires_at": now + timedelta(days=5),
            "base_traffic_bytes": 50 * 1024**3,
            "extra_traffic_bytes": 0,
            "traffic_used_bytes": 46 * 1024**3,
            "traffic_downlink_bytes": 40 * 1024**3,
            "traffic_overage_bytes": 0,
            "desired_version": 1,
            "provisioning_status": "ACTIVE",
            "notified_90p": True,
        }

        # 6. Real production EXPIRED subscription (e.g. trial user 4):
        # Was 2.74 GB (uplink + downlink), downlink is 2.10 GB.
        # Must stay EXPIRED, used rebased to 2.10 GB, overage=0, effective usage preserved at 2.10 GB.
        row6 = {
            "id": 6,
            "status": "EXPIRED",
            "status_reason": "period_expired",
            "expires_at": now - timedelta(days=2),
            "base_traffic_bytes": 5 * 1024**3,
            "extra_traffic_bytes": 0,
            "traffic_used_bytes": int(2.74 * 1024**3),
            "traffic_downlink_bytes": int(2.10 * 1024**3),
            "traffic_overage_bytes": 0,
            "desired_version": 2,
            "provisioning_status": "SYNCED_INACTIVE",
            "notified_90p": False,
        }

        with engine.begin() as conn:
            conn.execute(table.insert(), [row1, row2, row3, row4, row5, row6])

        with engine.begin() as conn:
            with patch("alembic.op.execute", side_effect=conn.execute):
                self.migration.upgrade()

        with engine.connect() as conn:
            rows = {r.id: dict(r._mapping) for r in conn.execute(sa.select(table)).fetchall()}

        # Assert Row 1 (Reactivated)
        self.assertEqual(rows[1]["status"], "ACTIVE")
        self.assertIsNone(rows[1]["status_reason"])
        self.assertEqual(rows[1]["desired_version"], 2)
        self.assertEqual(rows[1]["provisioning_status"], "PENDING_UPDATE")
        self.assertEqual(rows[1]["traffic_used_bytes"], 40 * 1024**3)
        self.assertEqual(rows[1]["traffic_overage_bytes"], 0)
        self.assertFalse(rows[1]["notified_90p"])

        # Assert Row 2 (Still Exhausted)
        self.assertEqual(rows[2]["status"], "EXHAUSTED")
        self.assertEqual(rows[2]["status_reason"], "quota_exhausted")
        self.assertEqual(rows[2]["desired_version"], 1)
        self.assertEqual(rows[2]["traffic_used_bytes"], 55 * 1024**3)
        self.assertEqual(rows[2]["traffic_overage_bytes"], 5 * 1024**3)
        self.assertTrue(rows[2]["notified_90p"])

        # Assert Row 3 (Expired by time not reactivated)
        self.assertEqual(rows[3]["status"], "EXHAUSTED")
        self.assertEqual(rows[3]["desired_version"], 1)
        self.assertEqual(rows[3]["traffic_used_bytes"], 40 * 1024**3)
        self.assertEqual(rows[3]["traffic_overage_bytes"], 0)
        self.assertTrue(rows[3]["notified_90p"])

        # Assert Row 4 (Admin disabled not reactivated)
        self.assertEqual(rows[4]["status"], "DISABLED")
        self.assertEqual(rows[4]["desired_version"], 1)
        self.assertEqual(rows[4]["traffic_used_bytes"], 15 * 1024**3)
        self.assertEqual(rows[4]["traffic_overage_bytes"], 0)

        # Assert Row 5 (Active 90% flag reset)
        self.assertEqual(rows[5]["status"], "ACTIVE")
        self.assertEqual(rows[5]["traffic_used_bytes"], 40 * 1024**3)
        self.assertEqual(rows[5]["traffic_overage_bytes"], 0)
        self.assertFalse(rows[5]["notified_90p"])

        # Assert Row 6 (Real production EXPIRED preserved without zeroing effective usage)
        self.assertEqual(rows[6]["status"], "EXPIRED")
        self.assertEqual(rows[6]["status_reason"], "period_expired")
        self.assertEqual(rows[6]["desired_version"], 2)
        self.assertEqual(rows[6]["provisioning_status"], "SYNCED_INACTIVE")
        self.assertEqual(rows[6]["traffic_used_bytes"], int(2.10 * 1024**3))
        self.assertEqual(rows[6]["traffic_overage_bytes"], 0)
        self.assertEqual(rows[6]["traffic_used_bytes"] - rows[6]["traffic_overage_bytes"], int(2.10 * 1024**3))


if __name__ == "__main__":
    unittest.main()
