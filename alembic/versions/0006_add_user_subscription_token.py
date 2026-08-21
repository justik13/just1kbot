"""Add subscription_token to users.

Revision ID: 0006_add_user_subscription_token
Revises: 0005_payment_statuses_sync
Create Date: 2026-08-18 01:30:00.000000
"""

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0006_add_user_subscription_token"
down_revision: str = "0005_payment_statuses_sync"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add the column first
    op.add_column(
        "users",
        sa.Column("subscription_token", sa.String(length=64), nullable=True),
    )

    # Disable transaction to allow concurrent index creation
    op.execute("COMMIT")
    op.execute("CREATE UNIQUE INDEX CONCURRENTLY ix_users_subscription_token ON users (subscription_token)")

def downgrade() -> None:
    # Use autocommit_block to allow CONCURRENT index drop (requires no active transaction)
    bind = op.get_bind()
    if bind and bind.dialect.name == "postgresql":
        with op.get_context().autocommit_block():
            op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_users_subscription_token")
    else:
        op.execute("DROP INDEX IF EXISTS ix_users_subscription_token")
    op.drop_column("users", "subscription_token")

