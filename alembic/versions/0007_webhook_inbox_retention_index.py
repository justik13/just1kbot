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
    op.create_index(
        "ix_webhook_inbox_retention",
        "webhook_inbox",
        ["received_at", "id"],
        postgresql_where=sa.text("status IN ('succeeded', 'dead')"),
    )


def downgrade() -> None:
    op.drop_index("ix_webhook_inbox_retention", table_name="webhook_inbox")
