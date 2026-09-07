"""Add active_hwids JSONB column to white_internet_subscriptions table.

Revision ID: 0023_wi_active_hwids
Revises: 0022_servers_protocol_not_null
Create Date: 2026-09-07 05:15:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0023_wi_active_hwids"
down_revision: str | None = "0022_servers_protocol_not_null"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "white_internet_subscriptions",
        sa.Column(
            "active_hwids",
            JSONB,
            nullable=True,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )


def downgrade() -> None:
    op.drop_column("white_internet_subscriptions", "active_hwids")
