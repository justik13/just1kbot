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

    # Re-create white_internet_quota_grants table and indexes
    op.create_table(
        "white_internet_quota_grants",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("subscription_id", sa.Integer(), nullable=False),
        sa.Column("grant_type", sa.String(length=20), nullable=False),
        sa.Column("bytes_granted", sa.BigInteger(), nullable=False),
        sa.Column("bytes_remaining", sa.BigInteger(), nullable=False),
        sa.Column(
            "price_rub",
            sa.Numeric(precision=12, scale=2),
            nullable=False,
            server_default=sa.text("0.00"),
        ),
        sa.Column("quote_id", sa.BigInteger(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(
            ["quote_id"],
            ["tariff_quotes.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["subscription_id"],
            ["white_internet_subscriptions.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_white_internet_quota_grants_subscription_id",
        "white_internet_quota_grants",
        ["subscription_id"],
    )
    op.create_index(
        "ix_white_internet_quota_grants_quote_id",
        "white_internet_quota_grants",
        ["quote_id"],
    )
    op.create_index(
        "ix_white_internet_quota_grants_expires_at",
        "white_internet_quota_grants",
        ["expires_at"],
    )

    # Re-create white_internet_traffic_events table and indexes
    op.create_table(
        "white_internet_traffic_events",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("subscription_id", sa.Integer(), nullable=False),
        sa.Column("node_epoch", sa.String(length=64), nullable=False),
        sa.Column("node_boot_id", sa.String(length=64), nullable=True),
        sa.Column("node_starttime", sa.BigInteger(), nullable=True),
        sa.Column("snapshot_uplink_before", sa.BigInteger(), nullable=False),
        sa.Column("snapshot_uplink_after", sa.BigInteger(), nullable=False),
        sa.Column("snapshot_downlink_before", sa.BigInteger(), nullable=False),
        sa.Column("snapshot_downlink_after", sa.BigInteger(), nullable=False),
        sa.Column("delta_uplink", sa.BigInteger(), nullable=False),
        sa.Column("delta_downlink", sa.BigInteger(), nullable=False),
        sa.Column("allocated_bytes", sa.BigInteger(), nullable=False),
        sa.Column("overage_bytes", sa.BigInteger(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(
            ["subscription_id"],
            ["white_internet_subscriptions.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_white_internet_traffic_events_created_at",
        "white_internet_traffic_events",
        ["created_at"],
    )
    op.create_index(
        "ix_white_internet_traffic_events_subscription_id",
        "white_internet_traffic_events",
        ["subscription_id"],
    )
