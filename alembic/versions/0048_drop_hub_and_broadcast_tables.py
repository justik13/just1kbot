"""Drop legacy hub_messages and broadcast_progress UI tables.

Revision ID: 0048_drop_hub_and_broadcast_tables
Revises: 0047_drop_notified_grace_12h
Create Date: 2026-11-12 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0048_drop_hub_and_broadcast_tables"
down_revision: str | None = "0047_drop_notified_grace_12h"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    table_names = set(insp.get_table_names())

    if "hub_messages" in table_names:
        op.drop_table("hub_messages")

    if "broadcast_progress" in table_names:
        index_names = {idx["name"] for idx in insp.get_indexes("broadcast_progress")}
        for idx_name in (
            "ix_broadcast_in_progress",
            "ix_broadcast_progress_admin_id",
            "ix_broadcast_progress_status",
        ):
            if idx_name in index_names:
                op.drop_index(idx_name, table_name="broadcast_progress")
        op.drop_table("broadcast_progress")


def downgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    table_names = set(insp.get_table_names())

    if "broadcast_progress" not in table_names:
        op.create_table(
            "broadcast_progress",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("admin_id", sa.BigInteger(), nullable=False),
            sa.Column("total_count", sa.Integer(), nullable=False),
            sa.Column("success_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
            sa.Column("fail_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
            sa.Column("last_processed_id", sa.BigInteger(), nullable=False, server_default=sa.text("0")),
            sa.Column("target_audience", sa.String(length=20), nullable=False, server_default=sa.text("'all'")),
            sa.Column("broadcast_text", sa.Text(), nullable=False),
            sa.Column("media_id", sa.String(length=255), nullable=True),
            sa.Column("content_type", sa.String(length=50), nullable=False),
            sa.Column("label", sa.String(length=50), nullable=False),
            sa.Column("status", sa.String(length=20), nullable=False, server_default=sa.text("'in_progress'")),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
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

    if "hub_messages" not in table_names:
        op.create_table(
            "hub_messages",
            sa.Column("chat_id", sa.BigInteger(), nullable=False),
            sa.Column("message_id", sa.BigInteger(), nullable=False),
            sa.Column("is_effect_message", sa.Boolean(), nullable=False, server_default=sa.text("false")),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
            sa.PrimaryKeyConstraint("chat_id", "message_id"),
        )
