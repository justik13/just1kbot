"""users_subscription_token_index

Revision ID: bac83372da22
Revises: 0007_webhook_retention
Create Date: 2026-08-21 16:10:50.407966

"""
from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'bac83372da22'
down_revision: str | Sequence[str] | None = '0007_webhook_retention'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    bind = op.get_bind()
    if bind and bind.dialect.name == "postgresql":
        with op.get_context().autocommit_block():
            op.create_index(
                "ix_users_subscription_token",
                "users",
                ["subscription_token"],
                unique=True,
                postgresql_concurrently=True,
                if_not_exists=True,
            )
    else:
        op.create_index(
            "ix_users_subscription_token",
            "users",
            ["subscription_token"],
            unique=True,
        )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(
        "ix_users_subscription_token",
        table_name="users",
        if_exists=True
    )
