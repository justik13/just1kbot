"""Persistent AWG traffic counters and user bandwidth totals.

Revision ID: 0031_awg_persistent_traffic
Revises: 0030_simple_billing
Create Date: 2026-09-29 03:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0031_awg_persistent_traffic"
down_revision: str | None = "0030_simple_billing"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. Add raw snapshot counters to vpn_profiles to compute monotonic deltas across node reboots
    op.add_column(
        "vpn_profiles",
        sa.Column(
            "raw_last_down",
            sa.BigInteger(),
            nullable=False,
            server_default="0",
        ),
    )
    op.add_column(
        "vpn_profiles",
        sa.Column(
            "raw_last_up",
            sa.BigInteger(),
            nullable=False,
            server_default="0",
        ),
    )

    # 2. Add cumulative traffic total to users table (retains history even when devices are deleted)
    op.add_column(
        "users",
        sa.Column(
            "total_traffic_bytes",
            sa.BigInteger(),
            nullable=False,
            server_default="0",
        ),
    )

    # 3. Safe baseline backfill:
    # Initialize raw_last_down/up with current traffic_down/up to prevent false delta spikes on first cycle
    op.execute(
        """
        UPDATE vpn_profiles
        SET raw_last_down = COALESCE(traffic_down, 0),
            raw_last_up = COALESCE(traffic_up, 0)
        WHERE traffic_down > 0 OR traffic_up > 0;
        """
    )

    # Initialize users.total_traffic_bytes with existing profile traffic sums
    op.execute(
        """
        UPDATE users u
        SET total_traffic_bytes = sub.total
        FROM (
            SELECT user_id, SUM(COALESCE(traffic_down, 0) + COALESCE(traffic_up, 0)) AS total
            FROM vpn_profiles
            GROUP BY user_id
        ) sub
        WHERE u.id = sub.user_id AND sub.total > 0;
        """
    )


def downgrade() -> None:
    op.drop_column("users", "total_traffic_bytes")
    op.drop_column("vpn_profiles", "raw_last_up")
    op.drop_column("vpn_profiles", "raw_last_down")
