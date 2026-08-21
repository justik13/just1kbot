"""payments_referral_bonus_idx

Revision ID: c20a97270920
Revises: bac83372da22
Create Date: 2026-08-21 16:12:12.338575

"""
from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'c20a97270920'
down_revision: str | Sequence[str] | None = 'bac83372da22'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    bind = op.get_bind()
    if bind and bind.dialect.name == "postgresql":
        with op.get_context().autocommit_block():
            op.execute("""
            DO $$
            BEGIN
                IF EXISTS (
                    SELECT 1 FROM pg_index i
                    JOIN pg_class c ON i.indexrelid = c.oid
                    WHERE c.relname = 'ix_payments_referral_bonus_unprocessed' AND i.indisvalid = false
                ) THEN
                    DROP INDEX ix_payments_referral_bonus_unprocessed;
                END IF;
            END $$;
            """)
            op.execute(
                "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_payments_referral_bonus_unprocessed "
                "ON payments (created_at) "
                "WHERE provider_status = 'succeeded' "
                "AND fulfillment_status = 'succeeded' "
                "AND NOT (COALESCE(topup_context, '{}'::jsonb) @> '{\"referral_bonus_processed\": true}'::jsonb);"
            )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(
        "ix_payments_referral_bonus_unprocessed",
        table_name="payments",
        if_exists=True
    )
