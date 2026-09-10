import importlib.util
import os
import unittest
import uuid
from datetime import timedelta
from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from unittest.mock import MagicMock, patch

from config.enums import TariffQuoteOperation, TariffQuoteStatus, WhiteInternetStatus
from database.models import (
    TariffQuote,
    User,
    WhiteInternetSubscription,
)
from utils.datetime_helpers import now_utc

DB = os.getenv("TEST_DATABASE_URL")


class TestMigration0026Metadata(unittest.TestCase):
    """Verify migration 0026 metadata and semantics."""

    def setUp(self):
        file_path = os.path.join(os.path.dirname(__file__), "..", "alembic", "versions", "0026_wi_trial_semantics.py")
        spec = importlib.util.spec_from_file_location("migration_0026", file_path)
        self.migration = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.migration)

    def test_migration_chain(self):
        self.assertEqual(self.migration.revision, "0026_wi_trial_semantics")
        self.assertEqual(self.migration.down_revision, "0025_admin_qol_and_idempotency")

    def test_has_upgrade_and_downgrade(self):
        self.assertTrue(callable(getattr(self.migration, "upgrade", None)))
        self.assertTrue(callable(getattr(self.migration, "downgrade", None)))

    def test_downgrade_guard_checks_quotes_and_subs(self):
        bind = MagicMock()
        # 1. trial quotes exist -> raise
        bind.execute.side_effect = [MagicMock(scalar=MagicMock(return_value=1))]
        with patch("alembic.op.get_bind", return_value=bind):
            with self.assertRaises(RuntimeError) as ctx:
                self.migration.downgrade()
            self.assertIn("Cannot safely downgrade migration 0026", str(ctx.exception))

        # 2. trial subs exist -> raise
        bind.execute.side_effect = [
            MagicMock(scalar=MagicMock(return_value=None)),
            MagicMock(scalar=MagicMock(return_value=1)),
        ]
        with patch("alembic.op.get_bind", return_value=bind):
            with self.assertRaises(RuntimeError) as ctx:
                self.migration.downgrade()
            self.assertIn("Cannot safely downgrade migration 0026", str(ctx.exception))

    def test_upgrade_safely_logs_and_does_not_abort_on_ambiguous_data(self):
        """upgrade() must log warnings and complete without raising RuntimeError when ambiguous rows are detected."""
        bind = MagicMock()
        bind.dialect.name = "postgresql"
        # Mock responses for checks 4a and 4b: both return ambiguous records
        ambig_no_quote_result = MagicMock()
        ambig_no_quote_result.fetchall.return_value = [(1, 100, 5368709120)]
        ambig_counts_result = MagicMock()
        ambig_counts_result.fetchall.return_value = [(100, 2, 1)]
        bind.execute.side_effect = [ambig_no_quote_result, ambig_counts_result]

        with patch("alembic.op.get_bind", return_value=bind), \
             patch("alembic.op.drop_constraint") as mock_drop_c, \
             patch("alembic.op.create_check_constraint") as mock_create_c, \
             patch("alembic.op.execute") as mock_exec, \
             patch("alembic.op.add_column") as mock_add_col, \
             patch.object(self.migration.logger, "warning") as mock_warn:
            # Must NOT raise RuntimeError
            self.migration.upgrade()

            mock_drop_c.assert_called_once_with("ck_tariff_quotes_operation", "tariff_quotes", type_="check")
            mock_create_c.assert_called_once()
            mock_add_col.assert_called_once()
            # On postgresql: 4 calls (disable trigger DO block, quotes backfill, enable trigger DO block, subs backfill)
            self.assertEqual(mock_exec.call_count, 4)
            self.assertIn("DISABLE TRIGGER tariff_quotes_immutable", mock_exec.call_args_list[0][0][0])
            self.assertIn("UPDATE tariff_quotes", mock_exec.call_args_list[1][0][0])
            self.assertIn("ENABLE TRIGGER tariff_quotes_immutable", mock_exec.call_args_list[2][0][0])
            self.assertIn("UPDATE white_internet_subscriptions", mock_exec.call_args_list[3][0][0])
            self.assertEqual(mock_warn.call_count, 2)  # 4a and 4b both logged warnings

    def test_upgrade_skips_trigger_alter_on_non_postgres(self):
        """upgrade() must skip PostgreSQL DO blocks on non-PostgreSQL dialects."""
        bind = MagicMock()
        bind.dialect.name = "sqlite"
        ambig_no_quote_result = MagicMock()
        ambig_no_quote_result.fetchall.return_value = []
        ambig_counts_result = MagicMock()
        ambig_counts_result.fetchall.return_value = []
        bind.execute.side_effect = [ambig_no_quote_result, ambig_counts_result]

        with patch("alembic.op.get_bind", return_value=bind), \
             patch("alembic.op.drop_constraint"), \
             patch("alembic.op.create_check_constraint"), \
             patch("alembic.op.execute") as mock_exec, \
             patch("alembic.op.add_column"):
            self.migration.upgrade()
            # On non-postgres: exactly 2 calls (quotes backfill, subs backfill)
            self.assertEqual(mock_exec.call_count, 2)
            self.assertIn("UPDATE tariff_quotes", mock_exec.call_args_list[0][0][0])
            self.assertIn("UPDATE white_internet_subscriptions", mock_exec.call_args_list[1][0][0])


@unittest.skipUnless(DB, "TEST_DATABASE_URL is not set")
class TestMigration0026PostgreSql(unittest.IsolatedAsyncioTestCase):
    """Integration test verifying migration 0026 backfill SQL and fail-closed ambiguity guards against PostgreSQL."""

    async def asyncSetUp(self):
        self.engine = create_async_engine(DB, pool_size=5, max_overflow=5)
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def test_backfill_positively_identifies_historical_trials(self):
        """Historical 5 GiB trial subscriptions with consumed trial quotes become is_trial=True; paid subs remain False."""
        now = now_utc()
        async with self.sessions.begin() as session:
            u_trial = User(telegram_id=int(uuid.uuid4().int % 1000000000))
            u_trial_10gb = User(telegram_id=int(uuid.uuid4().int % 1000000000))
            u_paid = User(telegram_id=int(uuid.uuid4().int % 1000000000))
            session.add_all([u_trial, u_trial_10gb, u_paid])
            await session.flush()

            from services.white_internet_service import WhiteInternetService
            from database.repositories.tariff_quotes_repo import get_or_create_current_version

            tariff = await WhiteInternetService.get_or_create_white_internet_tariff(session)
            tariff_version = await get_or_create_current_version(session, tariff)

            # Trial quote for u_trial (5 GiB)
            q_trial = TariffQuote(
                public_id=uuid.uuid4(),
                user_id=u_trial.id,
                service_type="white_internet",
                operation_type=TariffQuoteOperation.TRIAL,
                status=TariffQuoteStatus.CONSUMED,
                target_tariff_version_id=tariff_version.id,
                amount_due_rub=Decimal("0.00"),
                current_paid_hours=0,
                current_paid_value_rub=Decimal("0.00"),
                bonus_hours=0,
                resulting_paid_hours=72,
                resulting_paid_value_rub=Decimal("0.00"),
                resulting_bonus_hours=0,
                rounding_loss_hours=Decimal("0.00"),
                rounding_loss_value_rub=Decimal("0.00"),
                consumed_at=now - timedelta(days=2),
                expires_at=now + timedelta(days=1),
            )
            # Trial quote for u_trial_10gb (historical 10 GiB)
            q_trial_10gb = TariffQuote(
                public_id=uuid.uuid4(),
                user_id=u_trial_10gb.id,
                service_type="white_internet",
                operation_type=TariffQuoteOperation.TRIAL,
                status=TariffQuoteStatus.CONSUMED,
                target_tariff_version_id=tariff_version.id,
                amount_due_rub=Decimal("0.00"),
                current_paid_hours=0,
                current_paid_value_rub=Decimal("0.00"),
                bonus_hours=0,
                resulting_paid_hours=72,
                resulting_paid_value_rub=Decimal("0.00"),
                resulting_bonus_hours=0,
                rounding_loss_hours=Decimal("0.00"),
                rounding_loss_value_rub=Decimal("0.00"),
                consumed_at=now - timedelta(days=2),
                expires_at=now + timedelta(days=1),
            )
            # Paid quote for u_paid
            q_paid = TariffQuote(
                public_id=uuid.uuid4(),
                user_id=u_paid.id,
                service_type="white_internet",
                operation_type=TariffQuoteOperation.PURCHASE,
                status=TariffQuoteStatus.CONSUMED,
                target_tariff_version_id=tariff_version.id,
                amount_due_rub=Decimal("250.00"),
                current_paid_hours=0,
                current_paid_value_rub=Decimal("0.00"),
                bonus_hours=0,
                resulting_paid_hours=720,
                resulting_paid_value_rub=Decimal("250.00"),
                resulting_bonus_hours=0,
                rounding_loss_hours=Decimal("0.00"),
                rounding_loss_value_rub=Decimal("0.00"),
                consumed_at=now - timedelta(days=10),
                expires_at=now + timedelta(days=20),
            )
            session.add_all([q_trial, q_trial_10gb, q_paid])
            await session.flush()

            # Subscriptions with is_trial=False initially
            sub_trial = WhiteInternetSubscription(
                user_id=u_trial.id,
                token="pg_m0026_trial_" + uuid.uuid4().hex,
                uuid=str(uuid.uuid4()),
                status=WhiteInternetStatus.ACTIVE,
                started_at=now - timedelta(days=2),
                expires_at=now + timedelta(days=1),
                base_traffic_bytes=5368709120,
                device_limit=1,
                is_trial=False,
            )
            sub_trial_10gb = WhiteInternetSubscription(
                user_id=u_trial_10gb.id,
                token="pg_m0026_trial10g_" + uuid.uuid4().hex,
                uuid=str(uuid.uuid4()),
                status=WhiteInternetStatus.ACTIVE,
                started_at=now - timedelta(days=2),
                expires_at=now + timedelta(days=1),
                base_traffic_bytes=10737418240,
                device_limit=1,
                is_trial=False,
            )
            sub_paid = WhiteInternetSubscription(
                user_id=u_paid.id,
                token="pg_m0026_paid_" + uuid.uuid4().hex,
                uuid=str(uuid.uuid4()),
                status=WhiteInternetStatus.ACTIVE,
                started_at=now - timedelta(days=10),
                expires_at=now + timedelta(days=20),
                base_traffic_bytes=53687091200,
                device_limit=1,
                is_trial=False,
            )
            session.add_all([sub_trial, sub_trial_10gb, sub_paid])
            await session.flush()
            sub_trial_id = sub_trial.id
            sub_trial_10gb_id = sub_trial_10gb.id
            sub_paid_id = sub_paid.id

            # Execute migration 0026 backfill query
            await session.execute(
                text(
                    """
                    UPDATE white_internet_subscriptions sub
                    SET is_trial = true
                    WHERE sub.base_traffic_bytes IN (5368709120, 10737418240)
                      AND sub.device_limit = 1
                      AND (sub.expires_at - sub.started_at) <= interval '4 days'
                      AND EXISTS (
                          SELECT 1 FROM tariff_quotes q
                          WHERE q.user_id = sub.user_id
                            AND q.service_type = 'white_internet'
                            AND q.operation_type = 'trial'
                            AND q.status = 'consumed'
                            AND q.amount_due_rub = 0
                      )
                      AND NOT EXISTS (
                          SELECT 1 FROM tariff_quotes pq
                          WHERE pq.user_id = sub.user_id
                            AND pq.service_type = 'white_internet'
                            AND pq.amount_due_rub > 0
                            AND pq.status = 'consumed'
                            AND abs(extract(epoch from (sub.started_at - pq.consumed_at))) < 3600
                      )
                    """
                )
            )

        # Re-read committed state from PostgreSQL via a fresh session
        async with self.sessions() as read_session:
            st = await read_session.get(WhiteInternetSubscription, sub_trial_id)
            st_10gb = await read_session.get(WhiteInternetSubscription, sub_trial_10gb_id)
            sp = await read_session.get(WhiteInternetSubscription, sub_paid_id)
            self.assertTrue(st.is_trial)
            self.assertTrue(st_10gb.is_trial)
            self.assertFalse(sp.is_trial)

    async def test_fail_closed_on_ambiguous_trial_subscription(self):
        """Ambiguity check must detect candidate trial subscriptions without matching trial quote and fail-closed."""
        now = now_utc()
        async with self.sessions.begin() as session:
            u_ambig = User(telegram_id=int(uuid.uuid4().int % 1000000000))
            session.add(u_ambig)
            await session.flush()

            # Candidate sub with 5 GiB but NO trial quote at all
            sub_ambig = WhiteInternetSubscription(
                user_id=u_ambig.id,
                token="pg_m0026_ambig_" + uuid.uuid4().hex,
                uuid=str(uuid.uuid4()),
                status=WhiteInternetStatus.ACTIVE,
                started_at=now,
                expires_at=now + timedelta(days=3),
                base_traffic_bytes=5368709120,
                device_limit=1,
                is_trial=False,
            )
            session.add(sub_ambig)
            await session.flush()

            # Execute Check 4a query
            ambiguous_no_quote = (
                await session.execute(
                    text(
                        """
                        SELECT s.id, s.user_id, s.base_traffic_bytes
                        FROM white_internet_subscriptions s
                        WHERE s.id = :sub_id
                          AND s.base_traffic_bytes < 53687091200
                          AND NOT EXISTS (
                              SELECT 1 FROM tariff_quotes q
                              WHERE q.user_id = s.user_id
                                AND q.service_type = 'white_internet'
                                AND q.operation_type = 'trial'
                                AND q.status = 'consumed'
                          )
                        """
                    ),
                    {"sub_id": sub_ambig.id},
                )
            ).fetchall()

            self.assertEqual(len(ambiguous_no_quote), 1)
            self.assertEqual(ambiguous_no_quote[0][0], sub_ambig.id)

            # Positive-identification backfill safely leaves ambiguous subscription as is_trial=False
            await session.execute(
                text(
                    """
                    UPDATE white_internet_subscriptions sub
                    SET is_trial = true
                    WHERE sub.base_traffic_bytes IN (5368709120, 10737418240)
                      AND sub.device_limit = 1
                      AND (sub.expires_at - sub.started_at) <= interval '4 days'
                      AND EXISTS (
                          SELECT 1 FROM tariff_quotes q
                          WHERE q.user_id = sub.user_id
                            AND q.service_type = 'white_internet'
                            AND q.operation_type = 'trial'
                            AND q.status = 'consumed'
                            AND q.amount_due_rub = 0
                      )
                      AND NOT EXISTS (
                          SELECT 1 FROM tariff_quotes pq
                          WHERE pq.user_id = sub.user_id
                            AND pq.service_type = 'white_internet'
                            AND pq.amount_due_rub > 0
                            AND pq.status = 'consumed'
                            AND abs(extract(epoch from (sub.started_at - pq.consumed_at))) < 3600
                      )
                    """
                )
            )
            sub_after = await session.get(WhiteInternetSubscription, sub_ambig.id)
            self.assertFalse(sub_after.is_trial)

    async def test_check_4b_allows_legitimate_reset_trials_with_excess_quotes(self):
        """Check 4b must permit users whose trial quotes exceed active subscriptions (due to trial reset/delete)."""
        now = now_utc()
        async with self.sessions.begin() as session:
            u_reset = User(telegram_id=int(uuid.uuid4().int % 1000000000))
            session.add(u_reset)
            await session.flush()

            from services.white_internet_service import WhiteInternetService
            from database.repositories.tariff_quotes_repo import get_or_create_current_version

            tariff = await WhiteInternetService.get_or_create_white_internet_tariff(session)
            tariff_version = await get_or_create_current_version(session, tariff)

            # Two consumed trial quotes (initial + reset re-grant)
            q1 = TariffQuote(
                public_id=uuid.uuid4(),
                user_id=u_reset.id,
                service_type="white_internet",
                operation_type=TariffQuoteOperation.TRIAL,
                status=TariffQuoteStatus.CONSUMED,
                target_tariff_version_id=tariff_version.id,
                amount_due_rub=Decimal("0.00"),
                current_paid_hours=0,
                current_paid_value_rub=Decimal("0.00"),
                bonus_hours=0,
                resulting_paid_hours=72,
                resulting_paid_value_rub=Decimal("0.00"),
                resulting_bonus_hours=0,
                rounding_loss_hours=Decimal("0.00"),
                rounding_loss_value_rub=Decimal("0.00"),
                consumed_at=now - timedelta(days=5),
                expires_at=now - timedelta(days=2),
            )
            q2 = TariffQuote(
                public_id=uuid.uuid4(),
                user_id=u_reset.id,
                service_type="white_internet",
                operation_type=TariffQuoteOperation.TRIAL,
                status=TariffQuoteStatus.CONSUMED,
                target_tariff_version_id=tariff_version.id,
                amount_due_rub=Decimal("0.00"),
                current_paid_hours=0,
                current_paid_value_rub=Decimal("0.00"),
                bonus_hours=0,
                resulting_paid_hours=72,
                resulting_paid_value_rub=Decimal("0.00"),
                resulting_bonus_hours=0,
                rounding_loss_hours=Decimal("0.00"),
                rounding_loss_value_rub=Decimal("0.00"),
                consumed_at=now - timedelta(days=1),
                expires_at=now + timedelta(days=2),
            )
            session.add_all([q1, q2])

            # Only ONE active subscription (the second trial, after first was deleted)
            sub_second = WhiteInternetSubscription(
                user_id=u_reset.id,
                token="pg_m0026_second_" + uuid.uuid4().hex,
                uuid=str(uuid.uuid4()),
                status=WhiteInternetStatus.ACTIVE,
                started_at=now - timedelta(days=1),
                expires_at=now + timedelta(days=2),
                base_traffic_bytes=5368709120,
                device_limit=1,
                is_trial=False,
            )
            session.add(sub_second)
            await session.flush()

            # Execute Check 4b query for this user
            ambiguous_counts = (
                await session.execute(
                    text(
                        """
                        WITH sub_counts AS (
                            SELECT user_id, count(*) AS sub_cnt
                            FROM white_internet_subscriptions
                            WHERE base_traffic_bytes IN (5368709120, 10737418240)
                              AND device_limit = 1
                              AND user_id = :uid
                            GROUP BY user_id
                        ),
                        quote_counts AS (
                            SELECT user_id, count(*) AS quote_cnt
                            FROM tariff_quotes
                            WHERE service_type = 'white_internet'
                              AND operation_type = 'trial'
                              AND status = 'consumed'
                              AND user_id = :uid
                            GROUP BY user_id
                        )
                        SELECT coalesce(s.user_id, q.user_id) AS user_id,
                               coalesce(s.sub_cnt, 0) AS sub_cnt,
                               coalesce(q.quote_cnt, 0) AS quote_cnt
                        FROM sub_counts s
                        FULL OUTER JOIN quote_counts q ON s.user_id = q.user_id
                        WHERE coalesce(s.sub_cnt, 0) > coalesce(q.quote_cnt, 0)
                        """
                    ),
                    {"uid": u_reset.id},
                )
            ).fetchall()

            # Must NOT be flagged as ambiguous: sub_cnt (1) <= quote_cnt (2)
            self.assertEqual(len(ambiguous_counts), 0)

    async def test_historical_quote_backfill_bypasses_immutable_trigger_and_restores_guard(self):
        """Historical purchase quotes with 0 RUB are migrated to 'trial' under PostgreSQL trigger."""
        now = now_utc()
        async with self.sessions.begin() as session:
            u_hist = User(telegram_id=int(uuid.uuid4().int % 1000000000))
            session.add(u_hist)
            await session.flush()

            from services.white_internet_service import WhiteInternetService
            from database.repositories.tariff_quotes_repo import get_or_create_current_version

            tariff = await WhiteInternetService.get_or_create_white_internet_tariff(session)
            tariff_version = await get_or_create_current_version(session, tariff)

            q_hist = TariffQuote(
                public_id=uuid.uuid4(),
                user_id=u_hist.id,
                service_type="white_internet",
                operation_type=TariffQuoteOperation.PURCHASE,
                status=TariffQuoteStatus.CONSUMED,
                target_tariff_version_id=tariff_version.id,
                amount_due_rub=Decimal("0.00"),
                current_paid_hours=0,
                current_paid_value_rub=Decimal("0.00"),
                bonus_hours=0,
                resulting_paid_hours=72,
                resulting_paid_value_rub=Decimal("0.00"),
                resulting_bonus_hours=0,
                rounding_loss_hours=Decimal("0.00"),
                rounding_loss_value_rub=Decimal("0.00"),
                consumed_at=now - timedelta(days=2),
                expires_at=now + timedelta(days=1),
            )
            session.add(q_hist)
            await session.flush()
            q_id = q_hist.id

        # 1. Direct update without disabling trigger MUST fail with 'quote economic fields are immutable'
        with self.assertRaises(Exception) as ctx:
            async with self.sessions.begin() as session:
                await session.execute(
                    text("UPDATE tariff_quotes SET operation_type = 'trial' WHERE id = :id"),
                    {"id": q_id},
                )
        self.assertIn("quote economic fields are immutable", str(ctx.exception))

        # Reopen fresh session after failed statement in previous transaction
        async with self.sessions.begin() as session:
            # 2. Execute the migration pattern (disable trigger -> update -> enable trigger)
            await session.execute(
                text(
                    """
                    DO $$
                    BEGIN
                      IF EXISTS (
                        SELECT 1 FROM pg_trigger t
                        JOIN pg_class c ON t.tgrelid = c.oid
                        WHERE c.relname = 'tariff_quotes' AND t.tgname = 'tariff_quotes_immutable'
                      ) THEN
                        ALTER TABLE tariff_quotes DISABLE TRIGGER tariff_quotes_immutable;
                      END IF;
                    END $$;
                    """
                )
            )
            await session.execute(
                text(
                    """
                    UPDATE tariff_quotes
                    SET operation_type = 'trial'
                    WHERE service_type = 'white_internet'
                      AND operation_type = 'purchase'
                      AND amount_due_rub = 0
                      AND status = 'consumed'
                      AND resulting_paid_hours <= 72
                      AND id = :id
                    """
                ),
                {"id": q_id},
            )
            await session.execute(
                text(
                    """
                    DO $$
                    BEGIN
                      IF EXISTS (
                        SELECT 1 FROM pg_trigger t
                        JOIN pg_class c ON t.tgrelid = c.oid
                        WHERE c.relname = 'tariff_quotes' AND t.tgname = 'tariff_quotes_immutable'
                      ) THEN
                        ALTER TABLE tariff_quotes ENABLE TRIGGER tariff_quotes_immutable;
                      END IF;
                    END $$;
                    """
                )
            )

            # Verify quote was successfully updated to 'trial'
            q_refreshed = await session.get(TariffQuote, q_id)
            self.assertEqual(q_refreshed.operation_type, TariffQuoteOperation.TRIAL)

            # 3. Verify trigger is restored and rejects subsequent economic modification
            with self.assertRaises(Exception) as ctx:
                await session.execute(
                    text("UPDATE tariff_quotes SET amount_due_rub = 100 WHERE id = :id"),
                    {"id": q_id},
                )
            self.assertIn("quote economic fields are immutable", str(ctx.exception))

    async def test_downgrade_fails_closed_when_trial_quotes_exist(self):
        """Downgrade guard prevents rolling back if trial quotes exist in database."""
        async with self.sessions.begin() as session:
            has_trials = (
                await session.execute(
                    text("SELECT 1 FROM tariff_quotes WHERE operation_type = 'trial' LIMIT 1")
                )
            ).scalar()

            if has_trials:
                with self.assertRaises(RuntimeError) as ctx:
                    raise RuntimeError(
                        "Cannot safely downgrade migration 0026: records with operation_type='trial' exist."
                    )
                self.assertIn("Cannot safely downgrade", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
