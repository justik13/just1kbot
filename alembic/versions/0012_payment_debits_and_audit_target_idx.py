"""payment_debit_and_audit_target_indexes

Revision ID: 0012_ledger_audit_idx
Revises: 0011_hub_effect_flag
Create Date: 2026-08-26 00:00:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = '0012_ledger_audit_idx'
down_revision: str | Sequence[str] | None = '0011_hub_effect_flag'
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
            _drop_invalid_index_concurrently(bind, 'ix_account_ledger_payment_debits')
            _drop_invalid_index_concurrently(bind, 'ix_audit_logs_target')

            op.execute(
                "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_account_ledger_payment_debits "
                "ON account_ledger_entries (payment_id) "
                "WHERE entry_type IN ('refund_debit', 'chargeback_debit');"
            )
            op.execute(
                "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_audit_logs_target "
                "ON audit_logs (lower(target_type), target_id, created_at DESC);"
            )


def downgrade() -> None:
    """Downgrade schema."""
    bind = op.get_bind()
    if bind and bind.dialect.name == "postgresql":
        with op.get_context().autocommit_block():
            op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_audit_logs_target")
            op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_account_ledger_payment_debits")
