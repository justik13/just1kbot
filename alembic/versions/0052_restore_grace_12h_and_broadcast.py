"""Restore users.notified_grace_12h, grace notify index, and broadcast_progress table.

Revision ID: 0052_restore_grace_12h_and_broadcast
Revises: 0051_v2_billing_and_devices
Create Date: 2026-11-14 12:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0052_restore_grace_12h_and_broadcast"
down_revision: str | None = "0051_v2_billing_and_devices"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)

    user_cols = {c["name"] for c in insp.get_columns("users")}
    if "notified_grace_12h" not in user_cols:
        op.add_column(
            "users",
            sa.Column(
                "notified_grace_12h",
                sa.Boolean(),
                nullable=False,
                server_default=sa.text("false"),
            ),
        )

    user_indexes = {idx["name"] for idx in insp.get_indexes("users")}
    if "ix_users_expired_grace_notify" in user_indexes:
        op.drop_index("ix_users_expired_grace_notify", table_name="users")
    op.create_index(
        "ix_users_expired_grace_notify",
        "users",
        ["subscription_end", "telegram_id"],
        unique=False,
        postgresql_where=sa.text(
            "is_deleted = false AND is_bot_blocked = false "
            "AND subscription_end IS NOT NULL "
            "AND (notified_expired = false OR notified_grace_12h = false)"
        ),
    )

    tables = set(insp.get_table_names())
    if "broadcast_progress" not in tables:
        op.create_table(
            "broadcast_progress",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("admin_id", sa.BigInteger(), nullable=False),
            sa.Column("total_count", sa.Integer(), nullable=False),
            sa.Column("success_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
            sa.Column("fail_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
            sa.Column("last_processed_id", sa.BigInteger(), nullable=False, server_default=sa.text("0")),
            sa.Column("target_audience", sa.String(length=64), nullable=False, server_default=sa.text("'all'")),
            sa.Column("broadcast_text", sa.Text(), nullable=False),
            sa.Column("media_id", sa.String(length=500), nullable=True),
            sa.Column("content_type", sa.String(length=50), nullable=False),
            sa.Column("label", sa.String(length=50), nullable=False),
            sa.Column("status", sa.String(length=20), nullable=False, server_default=sa.text("'in_progress'")),
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
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_broadcast_progress_admin_id", "broadcast_progress", ["admin_id"], unique=False)
        op.create_index("ix_broadcast_progress_status", "broadcast_progress", ["status"], unique=False)
        op.create_index(
            "ix_broadcast_in_progress",
            "broadcast_progress",
            ["status", "created_at"],
            unique=False,
            postgresql_where=sa.text("status = 'in_progress'"),
        )


def downgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    tables = set(insp.get_table_names())

    if "broadcast_progress" in tables:
        op.drop_index("ix_broadcast_in_progress", table_name="broadcast_progress")
        op.drop_index("ix_broadcast_progress_status", table_name="broadcast_progress")
        op.drop_index("ix_broadcast_progress_admin_id", table_name="broadcast_progress")
        op.drop_table("broadcast_progress")

    user_indexes = {idx["name"] for idx in insp.get_indexes("users")}
    if "ix_users_expired_grace_notify" in user_indexes:
        op.drop_index("ix_users_expired_grace_notify", table_name="users")
    op.create_index(
        "ix_users_expired_grace_notify",
        "users",
        ["subscription_end", "telegram_id"],
        unique=False,
        postgresql_where=sa.text(
            "is_deleted = false AND is_bot_blocked = false "
            "AND subscription_end IS NOT NULL "
            "AND notified_expired = false"
        ),
    )

    user_cols = {c["name"] for c in insp.get_columns("users")}
    if "notified_grace_12h" in user_cols:
        op.drop_column("users", "notified_grace_12h")
