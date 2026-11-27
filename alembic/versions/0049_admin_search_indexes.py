"""Add targeted admin search, attention orders, webhook, and audit log indexes.

Revision ID: 0049_admin_search_indexes
Revises: 0048_wi_constraints
Create Date: 2026-11-26 02:15:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0049_admin_search_indexes"
down_revision: str | Sequence[str] | None = "0048_wi_constraints"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _drop_invalid_index_concurrently(conn, index_name: str) -> None:
    res = conn.execute(
        sa.text(
            "SELECT 1 FROM pg_index i JOIN pg_class c ON i.indexrelid = c.oid "
            "WHERE c.relname = :index_name AND i.indisvalid = false"
        ),
        {"index_name": index_name},
    ).scalar()
    if res:
        conn.execute(sa.text(f"DROP INDEX CONCURRENTLY IF EXISTS {index_name}"))


_PG_INDEXES: tuple[tuple[str, str, str], ...] = (
    (
        "ix_audit_logs_admin_id_id_desc",
        "audit_logs",
        "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_audit_logs_admin_id_id_desc "
        "ON audit_logs (admin_id, id DESC);",
    ),
    (
        "ix_audit_logs_action_id_desc",
        "audit_logs",
        "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_audit_logs_action_id_desc "
        "ON audit_logs (action, id DESC);",
    ),
    (
        "ix_orders_attention_flag",
        "orders",
        "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_orders_attention_flag "
        "ON orders (created_at DESC, id DESC) "
        "WHERE (metadata ->> 'requires_manual_review') = 'true' "
        "OR (metadata ->> 'settlement_held') = 'true';",
    ),
    (
        "ix_orders_user_settlement_held",
        "orders",
        "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_orders_user_settlement_held "
        "ON orders (user_id) "
        "WHERE status = 'paid' AND (metadata ->> 'settlement_held') = 'true';",
    ),
    (
        "ix_webhook_inbox_attention_recent",
        "webhook_inbox",
        "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_webhook_inbox_attention_recent "
        "ON webhook_inbox (received_at DESC, id DESC) "
        "WHERE status IN ('pending', 'retry', 'dead');",
    ),
)


def upgrade() -> None:
    bind = op.get_bind()
    if bind and bind.dialect.name == "postgresql":
        with op.get_context().autocommit_block():
            for idx_name, _table, ddl in _PG_INDEXES:
                _drop_invalid_index_concurrently(bind, idx_name)
                op.execute(ddl)
    else:
        op.create_index(
            "ix_audit_logs_admin_id_id_desc",
            "audit_logs",
            ["admin_id", "id"],
            if_not_exists=True,
        )
        op.create_index(
            "ix_audit_logs_action_id_desc",
            "audit_logs",
            ["action", "id"],
            if_not_exists=True,
        )
        op.create_index(
            "ix_webhook_inbox_attention_recent",
            "webhook_inbox",
            ["received_at", "id"],
            if_not_exists=True,
        )


def downgrade() -> None:
    bind = op.get_bind()
    if bind and bind.dialect.name == "postgresql":
        with op.get_context().autocommit_block():
            for idx_name, _table, _ddl in reversed(_PG_INDEXES):
                op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {idx_name};")
    else:
        op.drop_index("ix_webhook_inbox_attention_recent", table_name="webhook_inbox", if_exists=True)
        op.drop_index("ix_audit_logs_action_id_desc", table_name="audit_logs", if_exists=True)
        op.drop_index("ix_audit_logs_admin_id_id_desc", table_name="audit_logs", if_exists=True)
