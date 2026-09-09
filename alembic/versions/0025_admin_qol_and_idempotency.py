"""Admin QoL and idempotency tables and indexes.

Revision ID: 0025_admin_qol_and_idempotency
Revises: 0024_wi_device_limit
Create Date: 2026-09-08 12:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0025_admin_qol_and_idempotency"
down_revision: str | None = "0024_wi_device_limit"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. admin_operation_idempotency table
    op.create_table(
        "admin_operation_idempotency",
        sa.Column("op_key", sa.String(length=64), primary_key=True, nullable=False),
        sa.Column("admin_id", sa.BigInteger(), nullable=False),
        sa.Column("target_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index(
        "ix_admin_operation_idempotency_created_at",
        "admin_operation_idempotency",
        ["created_at"],
    )

    # 2. users.last_trial_reset_at column
    op.add_column(
        "users",
        sa.Column(
            "last_trial_reset_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )

    # 3. Partial B-tree index on lower(username) for exact case-insensitive username lookup
    op.create_index(
        "ix_users_username_lower",
        "users",
        [sa.text("lower(username)")],
        postgresql_where=sa.text("username IS NOT NULL AND is_deleted = false"),
    )

    # 4. GIN index on active_hwids for fast HWID lookup
    op.create_index(
        "ix_white_internet_subscriptions_active_hwids",
        "white_internet_subscriptions",
        ["active_hwids"],
        postgresql_using="gin",
    )


def downgrade() -> None:
    op.drop_index(
        "ix_white_internet_subscriptions_active_hwids",
        table_name="white_internet_subscriptions",
        postgresql_using="gin",
    )
    op.drop_index(
        "ix_users_username_lower",
        table_name="users",
    )
    op.drop_column("users", "last_trial_reset_at")
    op.drop_index(
        "ix_admin_operation_idempotency_created_at",
        table_name="admin_operation_idempotency",
    )
    op.drop_table("admin_operation_idempotency")
