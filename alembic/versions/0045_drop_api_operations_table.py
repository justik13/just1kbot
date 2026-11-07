"""Drop legacy api_operations table and constraints.

Revision ID: 0045_drop_api_operations_table
Revises: 0044_clean_auto_disabled_servers
Create Date: 2026-11-08 04:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0045_drop_api_operations_table"
down_revision: str | None = "0044_clean_auto_disabled_servers"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Safely drop table if exists (PostgreSQL cascades indexes and table constraints)
    op.execute("DROP TABLE IF EXISTS api_operations CASCADE;")


def downgrade() -> None:
    op.create_table(
        "api_operations",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("operation_type", sa.String(length=50), nullable=False),
        sa.Column("server_id", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(length=20), server_default="pending", nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("api_key_snapshot", sa.Text(), nullable=True),
        sa.Column("attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column("max_attempts", sa.Integer(), server_default="3", nullable=False),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("processing_token", sa.String(length=64), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["server_id"], ["servers.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("idempotency_key", name="uq_api_operations_idempotency_key"),
        sa.CheckConstraint("attempts >= 0", name="ck_api_operations_attempts_nonnegative"),
        sa.CheckConstraint("max_attempts > 0", name="ck_api_operations_max_attempts_positive"),
    )
