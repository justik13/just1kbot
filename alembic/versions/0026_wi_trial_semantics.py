"""Add is_trial column, update ck_tariff_quotes_operation, and safely backfill historical trials.

Revision ID: 0026_wi_trial_semantics
Revises: 0025_admin_qol_and_idempotency
Create Date: 2026-09-08 20:00:00.000000
"""

from collections.abc import Sequence
import logging

import sqlalchemy as sa
from alembic import op

logger = logging.getLogger("alembic.runtime.migration_0026")

# revision identifiers, used by Alembic.
revision: str = "0026_wi_trial_semantics"
down_revision: str | None = "0025_admin_qol_and_idempotency"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()

    # 1. Update CheckConstraint on tariff_quotes to allow 'trial'
    op.drop_constraint("ck_tariff_quotes_operation", "tariff_quotes", type_="check")
    op.create_check_constraint(
        "ck_tariff_quotes_operation",
        "tariff_quotes",
        "operation_type IN ('purchase', 'renew', 'change', 'trial')",
    )

    # 2. Scoped, idempotent backfill of historical trial quotes
    # The tariff_quotes_immutable trigger forbids updating economic fields including operation_type.
    # We temporarily disable the trigger during this one-off historical backfill.
    is_postgres = bool(bind and getattr(bind, "dialect", None) and bind.dialect.name == "postgresql")
    if is_postgres:
        op.execute(
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
    try:
        op.execute(
            """
            UPDATE tariff_quotes
            SET operation_type = 'trial'
            WHERE service_type = 'white_internet'
              AND operation_type = 'purchase'
              AND amount_due_rub = 0
              AND status = 'consumed'
              AND resulting_paid_hours <= 72
            """
        )
    finally:
        if is_postgres:
            op.execute(
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

    # 3. Add is_trial column to white_internet_subscriptions
    op.add_column(
        "white_internet_subscriptions",
        sa.Column("is_trial", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )

    # 4. Diagnostic Ambiguity Inspection: Safely log any unconfirmed subscriptions
    # 4a. Candidate sub-50GiB subscriptions lacking a historical trial quote
    ambiguous_no_quote = bind.execute(
        sa.text(
            """
            SELECT s.id, s.user_id, s.base_traffic_bytes
            FROM white_internet_subscriptions s
            WHERE s.base_traffic_bytes < 53687091200
              AND NOT EXISTS (
                  SELECT 1 FROM tariff_quotes q
                  WHERE q.user_id = s.user_id
                    AND q.service_type = 'white_internet'
                    AND q.operation_type = 'trial'
                    AND q.status = 'consumed'
              )
            """
        )
    ).fetchall()
    if ambiguous_no_quote:
        logger.warning(
            "Migration 0026: found candidate trial subscriptions lacking trial quote: %s. "
            "Safely leaving is_trial=false by default without aborting migration.",
            ambiguous_no_quote,
        )

    # 4b. Candidate trial subscriptions exceeding consumed trial quotes count (orphaned subscriptions)
    ambiguous_counts = bind.execute(
        sa.text(
            """
            WITH sub_counts AS (
                SELECT user_id, count(*) AS sub_cnt
                FROM white_internet_subscriptions
                WHERE base_traffic_bytes IN (5368709120, 10737418240)
                  AND device_limit = 1
                GROUP BY user_id
            ),
            quote_counts AS (
                SELECT user_id, count(*) AS quote_cnt
                FROM tariff_quotes
                WHERE service_type = 'white_internet'
                  AND operation_type = 'trial'
                  AND status = 'consumed'
                GROUP BY user_id
            )
            SELECT coalesce(s.user_id, q.user_id) AS user_id,
                   coalesce(s.sub_cnt, 0) AS sub_cnt,
                   coalesce(q.quote_cnt, 0) AS quote_cnt
            FROM sub_counts s
            FULL OUTER JOIN quote_counts q ON s.user_id = q.user_id
            WHERE coalesce(s.sub_cnt, 0) > coalesce(q.quote_cnt, 0)
            """
        )
    ).fetchall()
    if ambiguous_counts:
        logger.warning(
            "Migration 0026: found unconfirmed trial subscriptions exceeding trial quotes: %s. "
            "Safely leaving is_trial=false by default without aborting migration.",
            ambiguous_counts,
        )

    # 5. Positive-Identification Backfill for historical trial subscriptions (1:1 confirmed)
    op.execute(
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


def downgrade() -> None:
    # Fail-safe guard against corrupting historical trial data on downgrade
    bind = op.get_bind()
    has_trials = bind.execute(
        sa.text("SELECT 1 FROM tariff_quotes WHERE operation_type = 'trial' LIMIT 1")
    ).scalar() or bind.execute(
        sa.text("SELECT 1 FROM white_internet_subscriptions WHERE is_trial = true LIMIT 1")
    ).scalar()
    if has_trials:
        raise RuntimeError(
            "Cannot safely downgrade migration 0026: records with operation_type='trial' or is_trial=true exist. "
            "Manual data migration of historical trial records is required."
        )

    op.drop_constraint("ck_tariff_quotes_operation", "tariff_quotes", type_="check")
    op.create_check_constraint(
        "ck_tariff_quotes_operation",
        "tariff_quotes",
        "operation_type IN ('purchase', 'renew', 'change')",
    )
    op.drop_column("white_internet_subscriptions", "is_trial")
