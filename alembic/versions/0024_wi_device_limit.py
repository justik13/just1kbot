"""Add device_limit and last_device_reset_at to white_internet_subscriptions.

Revision ID: 0024_wi_device_limit
Revises: 0023_wi_active_hwids
Create Date: 2026-09-07 23:18:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0024_wi_device_limit"
down_revision: str | None = "0023_wi_active_hwids"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "white_internet_subscriptions",
        sa.Column(
            "device_limit",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("1"),
        ),
    )
    op.add_column(
        "white_internet_subscriptions",
        sa.Column(
            "last_device_reset_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )
    op.create_check_constraint(
        "ck_white_internet_subscriptions_device_limit",
        "white_internet_subscriptions",
        "device_limit >= 1 AND device_limit <= 3",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_white_internet_subscriptions_device_limit",
        "white_internet_subscriptions",
        type_="check",
    )
    op.drop_column("white_internet_subscriptions", "last_device_reset_at")
    op.drop_column("white_internet_subscriptions", "device_limit")
