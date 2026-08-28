"""Drop subscription_token from users table (INCY cleanup).

Revision ID: 0014_drop_sub_token
Revises: 0012_ledger_audit_idx
Create Date: 2026-08-28 19:18:00.000000
"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0014_drop_sub_token"
down_revision: str | Sequence[str] | None = "0012_ledger_audit_idx"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema - remove legacy INCY subscription_token column and index."""
    bind = op.get_bind()
    if bind and bind.dialect.name == "postgresql":
        with op.get_context().autocommit_block():
            op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_users_subscription_token")
    else:
        op.execute("DROP INDEX IF EXISTS ix_users_subscription_token")

    has_col = False
    if bind:
        insp = sa.inspect(bind)
        has_col = any(c["name"] == "subscription_token" for c in insp.get_columns("users"))
    if has_col:
        op.drop_column("users", "subscription_token")


def downgrade() -> None:
    """Downgrade schema - restore subscription_token column and index."""
    bind = op.get_bind()
    has_col = False
    if bind:
        insp = sa.inspect(bind)
        has_col = any(c["name"] == "subscription_token" for c in insp.get_columns("users"))
    if not has_col:
        op.add_column(
            "users",
            sa.Column("subscription_token", sa.String(length=64), nullable=True),
        )

    if bind and bind.dialect.name == "postgresql":
        with op.get_context().autocommit_block():
            op.execute(
                "CREATE UNIQUE INDEX CONCURRENTLY IF NOT EXISTS ix_users_subscription_token "
                "ON users (subscription_token)"
            )
    else:
        op.create_index(
            "ix_users_subscription_token",
            "users",
            ["subscription_token"],
            unique=True,
            if_not_exists=True,
        )
