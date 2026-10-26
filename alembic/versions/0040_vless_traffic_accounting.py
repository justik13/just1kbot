"""Add traffic accounting columns for VLESS subscriptions and users.

Revision ID: 0040_vless_traffic_accounting
Revises: 0039_user_show_in_leaderboard
Create Date: 2026-10-26 12:00:00.000000
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

# revision identifiers, used by Alembic.
revision: str = "0040_vless_traffic_accounting"
down_revision: str | None = "0039_user_show_in_leaderboard"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "vless_subscriptions",
        sa.Column(
            "traffic_down",
            sa.BigInteger(),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )
    op.add_column(
        "vless_subscriptions",
        sa.Column(
            "traffic_up",
            sa.BigInteger(),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )
    op.add_column(
        "vless_subscriptions",
        sa.Column(
            "node_traffic_snapshots",
            JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )
    op.add_column(
        "users",
        sa.Column(
            "monthly_vless_bytes",
            sa.BigInteger(),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )


def downgrade() -> None:
    op.drop_column("users", "monthly_vless_bytes")
    op.drop_column("vless_subscriptions", "node_traffic_snapshots")
    op.drop_column("vless_subscriptions", "traffic_up")
    op.drop_column("vless_subscriptions", "traffic_down")
