"""Add is_trial column, update ck_tariff_quotes_operation, and safely backfill historical trials.

Revision ID: 0026_wi_trial_semantics
Revises: 0025_admin_qol_and_idempotency
Create Date: 2026-09-08 20:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

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

    # 3. Add is_trial column to white_internet_subscriptions
    op.add_column(
        "white_internet_subscriptions",
        sa.Column("is_trial", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )

    # 4. Fail-Closed Ambiguity Check: Ensure no paid subscriptions could be misclassified
    ambiguous = bind.execute(
        sa.text(
            """
            SELECT s.id, s.user_id, s.base_traffic_bytes
            FROM white_internet_subscriptions s
            WHERE s.base_traffic_bytes < 53687091200
              AND s.user_id NOT IN (
                  SELECT user_id FROM tariff_quotes
                  WHERE service_type = 'white_internet'
                    AND operation_type = 'trial'
              )
            """
        )
    ).fetchall()
    if ambiguous:
        raise RuntimeError(
            f"Migration 0026 aborted: found ambiguous subscriptions with sub-50GiB traffic "
            f"lacking historical trial quote: {ambiguous}. Manual DBA resolution required."
        )

    # 5. Positive-Identification Backfill for historical trial subscriptions
    op.execute(
        """
        UPDATE white_internet_subscriptions sub
        SET is_trial = true
        WHERE sub.base_traffic_bytes = 5368709120
          AND sub.device_limit = 1
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
                AND pq.consumed_at >= sub.started_at
          )
        """
    )


def downgrade() -> None:
    # Fail-safe guard against corrupting historical trial data on downgrade
    bind = op.get_bind()
    has_trials = bind.execute(
        sa.text("SELECT 1 FROM tariff_quotes WHERE operation_type = 'trial' LIMIT 1")
    ).scalar()
    if has_trials:
        raise RuntimeError(
            "Cannot safely downgrade migration 0026: records with operation_type='trial' exist. "
            "Manual data migration of historical trial records is required."
        )

    op.drop_constraint("ck_tariff_quotes_operation", "tariff_quotes", type_="check")
    op.create_check_constraint(
        "ck_tariff_quotes_operation",
        "tariff_quotes",
        "operation_type IN ('purchase', 'renew', 'change')",
    )
    op.drop_column("white_internet_subscriptions", "is_trial")
