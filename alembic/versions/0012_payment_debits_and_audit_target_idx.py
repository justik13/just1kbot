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
            _drop_invalid_index_concurrently(bind, 'ix_users_username_trgm')
            _drop_invalid_index_concurrently(bind, 'ix_tariff_quotes_consumed_journal')

            op.execute(
                "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_account_ledger_payment_debits "
                "ON account_ledger_entries (payment_id) "
                "WHERE entry_type IN ('refund_debit', 'chargeback_debit');"
            )
            op.execute(
                "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_audit_logs_target "
                "ON audit_logs (lower(target_type), target_id, created_at DESC);"
            )

            op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm;")
            op.execute(
                "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_users_username_trgm "
                "ON users USING gin (username gin_trgm_ops);"
            )
            op.execute(
                "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_tariff_quotes_consumed_journal "
                "ON tariff_quotes (consumed_at DESC NULLS LAST, created_at DESC) "
                "WHERE status = 'consumed';"
            )

        # Finish the NOT VALID constraints introduced online by 0004/0005:
        # validating takes only a brief SHARE UPDATE EXCLUSIVE lock.
        for table, constraint in (
            ("entitlement_entries", "ck_entitlement_entries_type"),
            ("entitlement_entries", "ck_entitlement_entries_shape"),
            ("payments", "ck_payments_provider_status"),
            ("payments", "ck_payments_fulfillment_status"),
        ):
            op.execute(
                f"DO $$ BEGIN "
                f"IF EXISTS (SELECT 1 FROM pg_constraint c JOIN pg_class t ON t.oid = c.conrelid "
                f"WHERE c.conname = '{constraint}' AND t.relname = '{table}' AND c.convalidated = false) "
                f"THEN ALTER TABLE {table} VALIDATE CONSTRAINT {constraint}; END IF; "
                f"END $$;"
            )

        # Drop indexes made redundant by stronger existing coverage.
        with op.get_context().autocommit_block():
            op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_audit_logs_created_at")
            op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_payment_events_payment_id")
            op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_hub_messages_chat_id")


def downgrade() -> None:
    """Downgrade schema."""
    bind = op.get_bind()
    if bind and bind.dialect.name == "postgresql":
        with op.get_context().autocommit_block():
            op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_tariff_quotes_consumed_journal")
            op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_users_username_trgm")
            op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_audit_logs_target")
            op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_account_ledger_payment_debits")

            # Restore the redundant indexes removed by the upgrade so the
            # downgrade matches the pre-0012 schema exactly.
            op.execute("CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_audit_logs_created_at ON audit_logs (created_at)")
            op.execute("CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_payment_events_payment_id ON payment_events (payment_id)")
            op.execute("CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_hub_messages_chat_id ON hub_messages (chat_id)")
