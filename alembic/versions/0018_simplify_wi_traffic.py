"""Simplify White Internet traffic accounting to 2 columns (base and extra).

Revision ID: 0018_simplify_wi_traffic
Revises: 0017_white_internet_durations
Create Date: 2026-09-03 14:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0018_simplify_wi_traffic"
down_revision: str | None = "0017_white_internet_durations"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. Add base_traffic_bytes and extra_traffic_bytes columns
    op.add_column(
        "white_internet_subscriptions",
        sa.Column(
            "base_traffic_bytes",
            sa.BigInteger(),
            nullable=False,
            server_default="53687091200",
        ),
    )
    op.add_column(
        "white_internet_subscriptions",
        sa.Column(
            "extra_traffic_bytes",
            sa.BigInteger(),
            nullable=False,
            server_default="0",
        ),
    )

    # 2. Backfill base_traffic_bytes from existing traffic_limit_bytes if any
    op.execute(
        "UPDATE white_internet_subscriptions SET base_traffic_bytes = traffic_limit_bytes WHERE traffic_limit_bytes IS NOT NULL"
    )

    # 3. Drop legacy single column traffic_limit_bytes
    op.drop_column("white_internet_subscriptions", "traffic_limit_bytes")

    # 4. Drop obsolete check constraint on traffic_limit_bytes and add new one
    op.execute(
        "ALTER TABLE white_internet_subscriptions DROP CONSTRAINT IF EXISTS ck_white_internet_subscriptions_traffic_nonnegative"
    )
    op.create_check_constraint(
        "ck_white_internet_subscriptions_traffic_nonnegative",
        "white_internet_subscriptions",
        "base_traffic_bytes >= 0 AND extra_traffic_bytes >= 0 AND traffic_used_bytes >= 0 AND traffic_uplink_bytes >= 0 AND traffic_downlink_bytes >= 0",
    )

    # 5. Drop obsolete ledger and event tables
    op.execute("DROP TABLE IF EXISTS white_internet_traffic_events CASCADE")
    op.execute("DROP TABLE IF EXISTS white_internet_quota_grants CASCADE")


def downgrade() -> None:
    # Re-create traffic_limit_bytes column
    op.add_column(
        "white_internet_subscriptions",
        sa.Column(
            "traffic_limit_bytes",
            sa.BigInteger(),
            nullable=False,
            server_default="53687091200",
        ),
    )
    op.execute(
        "UPDATE white_internet_subscriptions SET traffic_limit_bytes = base_traffic_bytes + extra_traffic_bytes"
    )
    op.execute(
        "ALTER TABLE white_internet_subscriptions DROP CONSTRAINT IF EXISTS ck_white_internet_subscriptions_traffic_nonnegative"
    )
    op.create_check_constraint(
        "ck_white_internet_subscriptions_traffic_nonnegative",
        "white_internet_subscriptions",
        "traffic_limit_bytes >= 0 AND traffic_used_bytes >= 0 AND traffic_uplink_bytes >= 0 AND traffic_downlink_bytes >= 0",
    )
    op.drop_column("white_internet_subscriptions", "extra_traffic_bytes")
    op.drop_column("white_internet_subscriptions", "base_traffic_bytes")
