"""Add user traffic breakdown (AWG and Xray), monthly cycles, and device traffic retention.

Revision ID: 0035_traffic_device_retention
Revises: 0034_drop_tariff_quotes
Create Date: 2026-10-05 02:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0035_traffic_device_retention"
down_revision: str | None = "0034_drop_tariff_quotes"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. Total White Internet (Xray) traffic accumulated for the user across lifetime
    op.add_column(
        "users",
        sa.Column(
            "total_wi_traffic_bytes",
            sa.BigInteger(),
            nullable=False,
            server_default="0",
        ),
    )
    # Best-effort backfill of available White Internet traffic from existing active subscription records.
    # Note: Prior to PR #354, subscription renewals reset traffic_used_bytes to 0 on each cycle, so
    # pre-PR historical cycles cannot be reconstructed without a prior ledger. Monotonic lifetime
    # accumulation begins with this migration.
    op.execute(
        sa.text(
            """
            UPDATE users u
            SET total_wi_traffic_bytes = sub.total_bytes
            FROM (
                SELECT user_id, sum(traffic_used_bytes) AS total_bytes
                FROM white_internet_subscriptions
                GROUP BY user_id
            ) sub
            WHERE u.id = sub.user_id AND sub.total_bytes > 0
            """
        )
    )

    # 2. Monthly traffic counters for current billing cycle (AWG and White Internet)
    op.add_column(
        "users",
        sa.Column(
            "monthly_awg_bytes",
            sa.BigInteger(),
            nullable=False,
            server_default="0",
        ),
    )
    op.add_column(
        "users",
        sa.Column(
            "monthly_wi_bytes",
            sa.BigInteger(),
            nullable=False,
            server_default="0",
        ),
    )
    op.add_column(
        "users",
        sa.Column(
            "traffic_cycle",
            sa.String(length=7),
            nullable=True,
        ),
    )

    # 3. Retained traffic for deleted/recreated device slots to prevent stats resetting to 0
    op.add_column(
        "users",
        sa.Column(
            "archived_device_traffic",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default="{}",
        ),
    )


def downgrade() -> None:
    op.drop_column("users", "archived_device_traffic")
    op.drop_column("users", "traffic_cycle")
    op.drop_column("users", "monthly_wi_bytes")
    op.drop_column("users", "monthly_awg_bytes")
    op.drop_column("users", "total_wi_traffic_bytes")
