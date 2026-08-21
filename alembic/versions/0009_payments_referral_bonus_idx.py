"""payments_referral_bonus_idx

Revision ID: c20a97270920
Revises: 0007_webhook_retention
Create Date: 2026-08-21 16:12:12.338575

"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'c20a97270920'
down_revision: str | Sequence[str] | None = '0007_webhook_retention'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

def _drop_invalid_index_concurrently(conn, index_name: str):
    res = conn.execute(
        sa.text(
            f"SELECT 1 FROM pg_index i JOIN pg_class c ON i.indexrelid = c.oid "
            f"WHERE c.relname = '{index_name}' AND i.indisvalid = false"
        )
    ).scalar()
    if res:
        conn.execute(sa.text(f"DROP INDEX CONCURRENTLY IF EXISTS {index_name}"))


def upgrade() -> None:
    """Upgrade schema."""
    bind = op.get_bind()
    if bind and bind.dialect.name == "postgresql":
        with op.get_context().autocommit_block():
            _drop_invalid_index_concurrently(bind, 'ix_payments_referral_bonus_unprocessed')
            _drop_invalid_index_concurrently(bind, 'ix_payments_recovery_pending')
            _drop_invalid_index_concurrently(bind, 'ix_payments_recovery_unfulfilled')

            op.execute(
                "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_payments_referral_bonus_unprocessed "
                "ON payments (created_at) "
                "WHERE provider_status = 'succeeded' "
                "AND fulfillment_status = 'succeeded' "
                "AND NOT (COALESCE(topup_context, '{}'::jsonb) @> '{\"referral_bonus_processed\": true}'::jsonb);"
            )
            op.execute(
                "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_payments_recovery_pending "
                "ON payments (created_at) "
                "WHERE external_id IS NOT NULL AND provider_status IN ('creating', 'pending', 'waiting_for_capture', 'unknown');"
            )
            op.execute(
                "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_payments_recovery_unfulfilled "
                "ON payments (created_at) "
                "WHERE provider_status = 'succeeded' AND provider_confirmed_at IS NOT NULL AND fulfillment_status NOT IN ('succeeded', 'reversed', 'manual_review');"
            )

def downgrade() -> None:
    """Downgrade schema."""
    bind = op.get_bind()
    if bind and bind.dialect.name == "postgresql":
        with op.get_context().autocommit_block():
            op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_payments_referral_bonus_unprocessed")
            op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_payments_recovery_pending")
            op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_payments_recovery_unfulfilled")

