"""Add subscription_token to users.

Revision ID: 0006_add_user_subscription_token
Revises: 0005_payment_statuses_sync
Create Date: 2026-08-18 01:30:00.000000
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "0006_add_user_subscription_token"
down_revision: str = "0005_payment_statuses_sync"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("subscription_token", sa.String(length=64), nullable=True),
    )
    op.create_index(
        "ix_users_subscription_token",
        "users",
        ["subscription_token"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("ix_users_subscription_token", table_name="users")
    op.drop_column("users", "subscription_token")
