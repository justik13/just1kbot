"""payments_auto_fulfill_retry_idx

Partial index for the auto-fulfillment retry lane of the stale topup
recovery worker. Without it the fourth OR branch in ``_needs_recovery()``
is unindexable and the planner drops the BitmapOr over the other partial
indexes, forcing a sequential scan on every recovery cycle.

Revision ID: 0015_auto_fulfill_retry_idx
Revises: 0014_drop_sub_token
Create Date: 2026-08-30 10:00:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0015_auto_fulfill_retry_idx"
down_revision: str | Sequence[str] | None = "0014_drop_sub_token"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

INDEX_NAME = "ix_payments_auto_fulfill_retry"


def _drop_invalid_index_concurrently(conn, index_name: str):
    res = conn.execute(
        sa.text(
            f"SELECT 1 FROM pg_index i JOIN pg_class c ON i.indexrelid = c.oid "
            f"WHERE c.relname = '{index_name}' AND i.indisvalid = false"
        )
    )
    # Offline (--sql) mode emits statements instead of executing them and
    # returns None here; there is nothing to inspect in that mode.
    if res is None:
        return
    if res.scalar():
        conn.execute(sa.text(f"DROP INDEX CONCURRENTLY IF EXISTS {index_name}"))


def upgrade() -> None:
    """Upgrade schema."""
    bind = op.get_bind()
    if bind and bind.dialect.name == "postgresql":
        with op.get_context().autocommit_block():
            _drop_invalid_index_concurrently(bind, INDEX_NAME)
            op.execute(
                f"CREATE INDEX CONCURRENTLY IF NOT EXISTS {INDEX_NAME} "
                "ON payments (created_at) "
                "WHERE provider_status = 'succeeded' "
                "AND provider_confirmed_at IS NOT NULL "
                "AND fulfillment_status = 'succeeded' "
                "AND topup_context ? 'auto_fulfill_action' "
                "AND topup_context->>'auto_fulfill_status' = 'failed';"
            )


def downgrade() -> None:
    """Downgrade schema."""
    bind = op.get_bind()
    if bind and bind.dialect.name == "postgresql":
        with op.get_context().autocommit_block():
            op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {INDEX_NAME}")
