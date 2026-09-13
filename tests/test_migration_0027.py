"""Unit tests for Alembic migration 0027_backfill_entitlements."""

import importlib.util
import os
from pathlib import Path
import unittest

from alembic.config import Config
from alembic.script import ScriptDirectory
from database.models import EntitlementEntry


class Migration0027Tests(unittest.TestCase):
    """Test suite for migration 0027_backfill_entitlements structure and constraint compliance."""

    def setUp(self):
        file_path = os.path.join(
            os.path.dirname(__file__),
            "..",
            "alembic",
            "versions",
            "0027_backfill_entitlements.py",
        )
        spec = importlib.util.spec_from_file_location("migration_0027", file_path)
        self.migration = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.migration)

    def test_migration_0027_metadata_and_chain(self):
        scripts = ScriptDirectory.from_config(Config("alembic.ini"))
        rev = scripts.get_revision("0027_backfill_entitlements")
        self.assertIsNotNone(rev)
        self.assertEqual(rev.down_revision, "0026_wi_trial_semantics")
        self.assertEqual(scripts.get_heads(), ["0027_backfill_entitlements"])
        self.assertEqual(self.migration.revision, "0027_backfill_entitlements")
        self.assertEqual(self.migration.down_revision, "0026_wi_trial_semantics")

    def test_migration_0027_source_content(self):
        m27_path = Path("alembic/versions/0027_backfill_entitlements.py")
        self.assertTrue(m27_path.is_file())
        content = m27_path.read_text(encoding="utf-8")

        self.assertIn("def upgrade()", content)
        self.assertIn("def downgrade()", content)
        self.assertIn("ck_entitlement_entries_shape", content)
        self.assertIn("exact_hours", content)
        self.assertIn("legacy_0027_grant_", content)
        self.assertIn("legacy_active_subscription_backfill", content)

    def test_migration_0027_sql_constants(self):
        self.assertTrue(hasattr(self.migration, "UPGRADE_CONSTRAINT_SQL"))
        self.assertTrue(hasattr(self.migration, "BACKFILL_SQL"))
        self.assertTrue(hasattr(self.migration, "DISABLE_TRIGGER_SQL"))
        self.assertTrue(hasattr(self.migration, "ENABLE_TRIGGER_SQL"))
        self.assertTrue(hasattr(self.migration, "DOWNGRADE_CLEANUP_SQL"))
        self.assertTrue(hasattr(self.migration, "DOWNGRADE_FAIL_CLOSED_CHECK_SQL"))
        self.assertTrue(hasattr(self.migration, "DOWNGRADE_CONSTRAINT_SQL"))
        self.assertIn("ck_entitlement_entries_shape", self.migration.UPGRADE_CONSTRAINT_SQL)
        self.assertIn("legacy_0027_grant_", self.migration.BACKFILL_SQL)
        self.assertIn("legacy_0027_grant_", self.migration.DOWNGRADE_CLEANUP_SQL)
        self.assertIn("RAISE EXCEPTION", self.migration.DOWNGRADE_FAIL_CLOSED_CHECK_SQL)

    def test_entitlement_entry_model_shape_constraint(self):
        constraint = next(
            c for c in EntitlementEntry.__table__.constraints
            if c.name == "ck_entitlement_entries_shape"
        )
        sqltext = str(constraint.sqltext)
        self.assertIn("days_delta = 0 AND hours_delta > 0", sqltext)
        self.assertIn("hours_delta = days_delta * 24", sqltext)
        self.assertIn("manual_grant", sqltext)


@unittest.skipUnless(os.getenv("TEST_DATABASE_URL"), "TEST_DATABASE_URL is not set")
class Migration0027IntegrationTests(unittest.IsolatedAsyncioTestCase):
    """Integration tests running migration 0027 upgrade and downgrade against PostgreSQL."""

    def setUp(self):
        file_path = os.path.join(
            os.path.dirname(__file__),
            "..",
            "alembic",
            "versions",
            "0027_backfill_entitlements.py",
        )
        spec = importlib.util.spec_from_file_location("migration_0027_integration", file_path)
        self.migration = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.migration)

    async def asyncSetUp(self) -> None:
        db_url = os.getenv("TEST_DATABASE_URL")
        from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
        self.engine = create_async_engine(db_url, pool_pre_ping=True)
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)
        self.created_user_ids: list[int] = []

    async def asyncTearDown(self) -> None:
        # Clean up any test rows by temporarily disabling append-only trigger
        if self.created_user_ids:
            async with self.engine.connect() as conn:
                def _cleanup_test_rows(sync_conn):
                    sync_conn.exec_driver_sql(
                        """
                        DO $$
                        BEGIN
                          IF EXISTS (
                            SELECT 1 FROM pg_trigger t
                            JOIN pg_class c ON t.tgrelid = c.oid
                            WHERE c.relname = 'entitlement_entries' AND t.tgname = 'entitlement_entries_append_only'
                          ) THEN
                            ALTER TABLE entitlement_entries DISABLE TRIGGER entitlement_entries_append_only;
                          END IF;
                        END $$;
                        """
                    )
                    try:
                        user_ids_str = ",".join(str(uid) for uid in self.created_user_ids)
                        sync_conn.exec_driver_sql(f"DELETE FROM entitlement_entries WHERE beneficiary_user_id IN ({user_ids_str})")
                        sync_conn.exec_driver_sql(f"DELETE FROM users WHERE id IN ({user_ids_str})")
                    finally:
                        sync_conn.exec_driver_sql(
                            """
                            DO $$
                            BEGIN
                              IF EXISTS (
                                SELECT 1 FROM pg_trigger t
                                JOIN pg_class c ON t.tgrelid = c.oid
                                WHERE c.relname = 'entitlement_entries' AND t.tgname = 'entitlement_entries_append_only'
                              ) THEN
                                ALTER TABLE entitlement_entries ENABLE TRIGGER entitlement_entries_append_only;
                              END IF;
                            END $$;
                            """
                        )
                await conn.run_sync(_cleanup_test_rows)
                await conn.commit()

        # Ensure DB is left at upgraded head state
        async with self.engine.connect() as conn:
            def _ensure_upgrade(sync_conn):
                from alembic.migration import MigrationContext
                from alembic.operations import Operations
                import alembic.op as op
                ctx = MigrationContext.configure(sync_conn)
                op._proxy = Operations(ctx)
                self.migration.upgrade()
            await conn.run_sync(_ensure_upgrade)
            await conn.commit()
        await self.engine.dispose()

    async def test_downgrade_clean_succeeds(self) -> None:
        import uuid
        from datetime import timedelta
        from sqlalchemy import select
        from database.models import User
        from utils.datetime_helpers import now_utc
        now = now_utc().replace(microsecond=0)
        tg_id = int(uuid.uuid4().int % 1000000000)

        # 1. Ensure upgrade has run
        async with self.engine.connect() as conn:
            def _run_up(sync_conn):
                from alembic.migration import MigrationContext
                from alembic.operations import Operations
                import alembic.op as op
                ctx = MigrationContext.configure(sync_conn)
                op._proxy = Operations(ctx)
                self.migration.upgrade()
            await conn.run_sync(_run_up)
            await conn.commit()

        # 2. Create user and run upgrade so user receives legacy_0027_grant_
        async with self.sessions.begin() as session:
            user = User(
                telegram_id=tg_id,
                username=f"clean_down_{tg_id}",
                device_limit=1,
                subscription_end=now + timedelta(hours=5),
            )
            session.add(user)
            await session.flush()
            user_id = user.id
            self.created_user_ids.append(user_id)

        async with self.engine.connect() as conn:
            def _run_up2(sync_conn):
                from alembic.migration import MigrationContext
                from alembic.operations import Operations
                import alembic.op as op
                ctx = MigrationContext.configure(sync_conn)
                op._proxy = Operations(ctx)
                self.migration.upgrade()
            await conn.run_sync(_run_up2)
            await conn.commit()

        # 3. Execute downgrade() - MUST succeed and remove only migration-owned rows
        async with self.engine.connect() as conn:
            def _run_down(sync_conn):
                from alembic.migration import MigrationContext
                from alembic.operations import Operations
                import alembic.op as op
                ctx = MigrationContext.configure(sync_conn)
                op._proxy = Operations(ctx)
                self.migration.downgrade()
            await conn.run_sync(_run_down)
            await conn.commit()

        # 4. Verify that legacy grant was removed
        async with self.sessions() as session:
            grant = await session.scalar(
                select(EntitlementEntry).where(
                    EntitlementEntry.beneficiary_user_id == user_id,
                    EntitlementEntry.source_id == f"legacy_0027_grant_{user_id}",
                )
            )
            self.assertIsNone(grant)

    async def test_downgrade_with_unowned_subday_entries_fails_closed(self) -> None:
        import uuid
        from datetime import timedelta
        from database.models import User
        from utils.datetime_helpers import now_utc
        now = now_utc().replace(microsecond=0)
        tg_id = int(uuid.uuid4().int % 1000000000)

        # 1. Ensure upgrade has run
        async with self.engine.connect() as conn:
            def _run_up(sync_conn):
                from alembic.migration import MigrationContext
                from alembic.operations import Operations
                import alembic.op as op
                ctx = MigrationContext.configure(sync_conn)
                op._proxy = Operations(ctx)
                self.migration.upgrade()
            await conn.run_sync(_run_up)
            await conn.commit()

        # 2. Create user and insert an unowned sub-day grant (days_delta = 0, hours_delta = 5)
        async with self.sessions.begin() as session:
            user = User(
                telegram_id=tg_id,
                username=f"subday_failclosed_{tg_id}",
                device_limit=1,
                subscription_end=now + timedelta(hours=5),
            )
            session.add(user)
            await session.flush()
            user_id = user.id
            self.created_user_ids.append(user_id)

            subday_entry = EntitlementEntry(
                beneficiary_user_id=user_id,
                source_type="admin",
                source_id="unowned_subday_grant",
                entry_type="manual_grant",
                days_delta=0,
                hours_delta=5,
                device_limit_snapshot=1,
                tariff_id_snapshot=None,
            )
            session.add(subday_entry)
            await session.flush()

        # 3. Execute downgrade() - MUST FAIL with fail-closed exception!
        async with self.engine.connect() as conn:
            def _run_down(sync_conn):
                from alembic.migration import MigrationContext
                from alembic.operations import Operations
                import alembic.op as op
                ctx = MigrationContext.configure(sync_conn)
                op._proxy = Operations(ctx)
                self.migration.downgrade()

            with self.assertRaises(Exception) as ctx:
                await conn.run_sync(_run_down)
            self.assertIn("Cannot downgrade migration 0027: sub-day entitlement entries", str(ctx.exception))

    async def test_upgrade_backfills_missing_users_honestly(self) -> None:
        import uuid
        from datetime import timedelta
        from sqlalchemy import select
        from database.models import User
        from utils.datetime_helpers import now_utc
        now = now_utc().replace(microsecond=0)
        tg_id = int(uuid.uuid4().int % 1000000000)

        # 1. Create active user without any entitlements
        async with self.sessions.begin() as session:
            user = User(
                telegram_id=tg_id,
                username=f"upgrade_backfill_{tg_id}",
                device_limit=2,
                subscription_end=now + timedelta(hours=5),
            )
            session.add(user)
            await session.flush()
            user_id = user.id
            self.created_user_ids.append(user_id)

        # 2. Run upgrade()
        async with self.engine.connect() as conn:
            def _run_up(sync_conn):
                from alembic.migration import MigrationContext
                from alembic.operations import Operations
                import alembic.op as op
                ctx = MigrationContext.configure(sync_conn)
                op._proxy = Operations(ctx)
                self.migration.upgrade()
            await conn.run_sync(_run_up)
            await conn.commit()

        # 3. Verify user received legacy_0027_grant_
        async with self.sessions() as session:
            grant = await session.scalar(
                select(EntitlementEntry).where(
                    EntitlementEntry.beneficiary_user_id == user_id,
                    EntitlementEntry.source_id == f"legacy_0027_grant_{user_id}",
                )
            )
            self.assertIsNotNone(grant)
            self.assertEqual(grant.source_type, "admin")
            self.assertEqual(grant.entry_type, "manual_grant")
            self.assertEqual(grant.hours_delta, 5)
            self.assertEqual(grant.days_delta, 0)
            self.assertEqual(grant.metadata_["reason"], "legacy_active_subscription_backfill")
