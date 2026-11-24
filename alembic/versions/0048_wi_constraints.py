"""Align White Internet subscription check constraints.

Revision ID: 0048_wi_constraints
Revises: 0047_device_std_priority
Create Date: 2026-11-24 00:00:00.000000
"""

from __future__ import annotations

from alembic import op

revision = "0048_wi_constraints"
down_revision = "0047_device_std_priority"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint(
        "ck_wi_subs_pending_device_limit",
        "white_internet_subscriptions",
        type_="check",
    )
    op.create_check_constraint(
        "ck_wi_subs_pending_device_limit",
        "white_internet_subscriptions",
        "pending_device_limit IS NULL OR (pending_device_limit >= 1 AND pending_device_limit <= 3)",
    )

    op.drop_constraint(
        "ck_white_internet_subscriptions_traffic_nonnegative",
        "white_internet_subscriptions",
        type_="check",
    )
    op.create_check_constraint(
        "ck_white_internet_subscriptions_traffic_nonnegative",
        "white_internet_subscriptions",
        "base_traffic_bytes >= 0 AND extra_traffic_bytes >= 0 AND traffic_used_bytes >= 0 "
        "AND traffic_uplink_bytes >= 0 AND traffic_downlink_bytes >= 0 "
        "AND traffic_overage_bytes >= 0",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_white_internet_subscriptions_traffic_nonnegative",
        "white_internet_subscriptions",
        type_="check",
    )
    op.create_check_constraint(
        "ck_white_internet_subscriptions_traffic_nonnegative",
        "white_internet_subscriptions",
        "base_traffic_bytes >= 0 AND extra_traffic_bytes >= 0 AND traffic_used_bytes >= 0 "
        "AND traffic_uplink_bytes >= 0 AND traffic_downlink_bytes >= 0",
    )

    op.drop_constraint(
        "ck_wi_subs_pending_device_limit",
        "white_internet_subscriptions",
        type_="check",
    )
    op.create_check_constraint(
        "ck_wi_subs_pending_device_limit",
        "white_internet_subscriptions",
        "pending_device_limit IS NULL OR (pending_device_limit >= 1 AND pending_device_limit <= 5)",
    )
