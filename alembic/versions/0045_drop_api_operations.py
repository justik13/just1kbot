"""Drop obsolete api_operations durable queue table.

The api_operations table was originally designed for asynchronous AmneziaWG
worker commands, which were decommissioned and removed. All server and subscription
operations now use direct atomic repositories or Redis queues.

Revision ID: 0045_drop_api_operations
Revises: 0044_clean_auto_disabled_servers
Create Date: 2026-11-10 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

# revision identifiers, used by Alembic.
revision: str = "0045_drop_api_operations"
down_revision: str | None = "0044_clean_auto_disabled_servers"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if "api_operations" in insp.get_table_names():
        op.drop_table("api_operations")


def downgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if "api_operations" not in insp.get_table_names():
        op.create_table(
            "api_operations",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("operation_type", sa.String(length=32), nullable=False),
            sa.Column(
                "status",
                sa.String(length=20),
                nullable=False,
                server_default=sa.text("'pending'"),
            ),
            sa.Column("idempotency_key", sa.String(length=255), nullable=False),
            sa.Column(
                "server_id",
                sa.Integer(),
                sa.ForeignKey("servers.id", ondelete="SET NULL"),
                nullable=True,
            ),
            sa.Column("server_name_snapshot", sa.String(length=255), nullable=True),
            sa.Column("api_url_snapshot", sa.String(length=500), nullable=True),
            sa.Column("api_key_snapshot", sa.Text(), nullable=True),
            sa.Column("peer_id", sa.String(length=255), nullable=True),
            sa.Column("client_name", sa.String(length=255), nullable=True),
            sa.Column(
                "payload",
                JSONB,
                nullable=False,
                server_default=sa.text("'{}'::jsonb"),
            ),
            sa.Column(
                "attempts",
                sa.Integer(),
                nullable=False,
                server_default=sa.text("0"),
            ),
            sa.Column(
                "max_attempts",
                sa.Integer(),
                nullable=False,
                server_default=sa.text("10"),
            ),
            sa.Column(
                "next_attempt_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.text("now()"),
            ),
            sa.Column("locked_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("locked_by", sa.String(length=100), nullable=True),
            sa.Column("last_error_code", sa.String(length=100), nullable=True),
            sa.Column("last_error", sa.Text(), nullable=True),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.text("now()"),
            ),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.text("now()"),
            ),
            sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
            sa.CheckConstraint(
                "operation_type IN ('create_peer', 'update_peer', 'delete_peer')",
                name="ck_api_operations_operation_type",
            ),
            sa.CheckConstraint(
                "status IN ('pending', 'processing', 'retry', 'succeeded', 'dead', 'cancelled')",
                name="ck_api_operations_status",
            ),
            sa.CheckConstraint(
                "attempts >= 0",
                name="ck_api_operations_attempts_nonnegative",
            ),
            sa.CheckConstraint(
                "max_attempts > 0",
                name="ck_api_operations_max_attempts_positive",
            ),
            sa.UniqueConstraint(
                "idempotency_key",
                name="uq_api_operations_idempotency_key",
            ),
        )
        op.create_index(
            "ix_api_operations_claim",
            "api_operations",
            ["status", "next_attempt_at", "created_at"],
            postgresql_where=sa.text("status IN ('pending', 'retry')"),
        )
        op.create_index(
            "ix_api_operations_processing_lock",
            "api_operations",
            ["locked_at"],
            postgresql_where=sa.text("status = 'processing'"),
        )
        op.create_index(
            "ix_api_operations_server_id",
            "api_operations",
            ["server_id"],
        )
