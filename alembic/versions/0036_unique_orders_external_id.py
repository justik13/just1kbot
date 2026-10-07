"""Make ix_orders_external_id unique for idempotent payment deduplication.

Revision ID: 0036_unique_orders_external_id
Revises: 0035_traffic_device_retention
Create Date: 2026-10-07 13:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0036_unique_orders_external_id"
down_revision: str | None = "0035_traffic_device_retention"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_index("ix_orders_external_id", table_name="orders", if_exists=True)
    op.create_index(
        "ix_orders_external_id",
        "orders",
        ["external_id"],
        unique=True,
        postgresql_where=sa.text("external_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_orders_external_id", table_name="orders", if_exists=True)
    op.create_index(
        "ix_orders_external_id",
        "orders",
        ["external_id"],
        unique=False,
        postgresql_where=sa.text("external_id IS NOT NULL"),
    )
