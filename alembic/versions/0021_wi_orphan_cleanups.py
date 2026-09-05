"""Durable outbox for White Internet orphan cleanups on former origin nodes.

Revision ID: 0021_wi_orphan_cleanups
Revises: 0020_wi_reset_hard_delete
Create Date: 2026-09-06 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0021_wi_orphan_cleanups"
down_revision: str | None = "0020_wi_reset_hard_delete"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "white_internet_orphan_cleanups",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("server_id", sa.Integer(), nullable=True),
        sa.Column("client_uuid", sa.String(length=36), nullable=False),
        sa.Column("desired_version", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column(
            "status",
            sa.String(length=20),
            nullable=False,
            server_default=sa.text("'pending'"),
        ),
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
        sa.CheckConstraint(
            "status IN ('pending', 'done')",
            name="ck_white_internet_orphan_cleanups_status",
        ),
        sa.ForeignKeyConstraint(
            ["server_id"],
            ["servers.id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_white_internet_orphan_cleanups_server_id",
        "white_internet_orphan_cleanups",
        ["server_id"],
    )
    op.create_index(
        "ix_white_internet_orphan_cleanups_client_uuid",
        "white_internet_orphan_cleanups",
        ["client_uuid"],
    )
    op.create_index(
        "ix_white_internet_orphan_cleanups_status",
        "white_internet_orphan_cleanups",
        ["status"],
    )


def downgrade() -> None:
    # Fail-closed: refuse to drop pending cleanups — the operator must let the
    # reconciliation worker drain them first, otherwise active credentials
    # would be orphaned on former origin nodes with no record left.
    conn = op.get_bind()
    pending = conn.execute(
        sa.text("SELECT count(*) FROM white_internet_orphan_cleanups WHERE status = 'pending'")
    ).scalar()
    if pending:
        raise RuntimeError(
            f"Refusing downgrade: {pending} pending orphan cleanups exist. "
            "Let the reconciliation worker drain them before downgrading."
        )
    op.drop_index(
        "ix_white_internet_orphan_cleanups_status",
        table_name="white_internet_orphan_cleanups",
    )
    op.drop_index(
        "ix_white_internet_orphan_cleanups_client_uuid",
        table_name="white_internet_orphan_cleanups",
    )
    op.drop_index(
        "ix_white_internet_orphan_cleanups_server_id",
        table_name="white_internet_orphan_cleanups",
    )
    op.drop_table("white_internet_orphan_cleanups")
