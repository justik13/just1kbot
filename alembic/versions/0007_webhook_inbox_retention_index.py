"""Add webhook_inbox retention index.

Revision ID: 0007_webhook_retention
Revises: 0006_add_user_subscription_token
Create Date: 2026-08-18 14:40:00.000000
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "0007_webhook_retention"
down_revision: str = "0006_add_user_subscription_token"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind and bind.dialect.name == "postgresql":
        with op.get_context().autocommit_block():
            op.create_index(
                "ix_webhook_inbox_retention",
                "webhook_inbox",
                ["received_at", "id"],
                postgresql_where=sa.text("status IN ('succeeded', 'dead')"),
                postgresql_concurrently=True,
                if_not_exists=True,
            )
    else:
        op.create_index(
            "ix_webhook_inbox_retention",
            "webhook_inbox",
            ["received_at", "id"],
            if_not_exists=True,
        )


def downgrade() -> None:
    bind = op.get_bind()
    if bind and bind.dialect.name == "postgresql":
        with op.get_context().autocommit_block():
            op.drop_index(
                "ix_webhook_inbox_retention",
                table_name="webhook_inbox",
                postgresql_concurrently=True,
                if_exists=True,
            )
    else:
        op.drop_index("ix_webhook_inbox_retention", table_name="webhook_inbox", if_exists=True)
