"""Add durable top-up link presentation marker.

Revision ID: 3c8e4d1b7a20
Revises: 2f9c7a6e1b10
"""

import sqlalchemy as sa
from alembic import op


revision = "3c8e4d1b7a20"
down_revision = "2f9c7a6e1b10"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "payments",
        sa.Column("payment_url_notified_at", sa.DateTime(timezone=True)),
    )


def downgrade() -> None:
    op.drop_column("payments", "payment_url_notified_at")
