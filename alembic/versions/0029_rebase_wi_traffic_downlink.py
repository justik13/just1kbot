"""Rebase White Internet used traffic to downlink bytes.

Revision ID: 0029_rebase_wi_traffic_downlink
Revises: 0028_wi_notifications
Create Date: 2026-09-17 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0029_rebase_wi_traffic_downlink"
down_revision: str | None = "0028_wi_notifications"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Bind table metadata for core updates
    wi_subs = sa.table(
        "white_internet_subscriptions",
        sa.column("id", sa.Integer()),
        sa.column("status", sa.String()),
        sa.column("status_reason", sa.String()),
        sa.column("expires_at", sa.DateTime(timezone=True)),
        sa.column("base_traffic_bytes", sa.BigInteger()),
        sa.column("extra_traffic_bytes", sa.BigInteger()),
        sa.column("traffic_used_bytes", sa.BigInteger()),
        sa.column("traffic_downlink_bytes", sa.BigInteger()),
        sa.column("traffic_overage_bytes", sa.BigInteger()),
        sa.column("desired_version", sa.Integer()),
        sa.column("provisioning_status", sa.String()),
        sa.column("notified_90p", sa.Boolean()),
    )

    quota = wi_subs.c.base_traffic_bytes + wi_subs.c.extra_traffic_bytes
    overage_calc = sa.case(
        (wi_subs.c.traffic_downlink_bytes > quota, wi_subs.c.traffic_downlink_bytes - quota),
        else_=0,
    )

    # 1. Rebase traffic_used_bytes and recalculate traffic_overage_bytes
    # to align with downlink-only metering (Egress CDN billing model).
    op.execute(
        wi_subs.update()
        .where(
            sa.or_(
                wi_subs.c.traffic_used_bytes > wi_subs.c.traffic_downlink_bytes,
                wi_subs.c.traffic_overage_bytes > overage_calc,
            )
        )
        .values(
            traffic_used_bytes=wi_subs.c.traffic_downlink_bytes,
            traffic_overage_bytes=overage_calc,
        )
    )

    # 2. Reactivate subscriptions prematurely marked EXHAUSTED solely due to
    # the old uplink accounting, provided their downlink is within quota and expires_at > now.
    op.execute(
        wi_subs.update()
        .where(
            sa.and_(
                wi_subs.c.status == "EXHAUSTED",
                wi_subs.c.expires_at > sa.func.now(),
                wi_subs.c.traffic_downlink_bytes < quota,
            )
        )
        .values(
            status="ACTIVE",
            status_reason=None,
            desired_version=wi_subs.c.desired_version + 1,
            provisioning_status="PENDING_UPDATE",
        )
    )

    # 3. Reset notified_90p flag for active subscriptions if pure downlink
    # usage rolled back below 90% threshold.
    op.execute(
        wi_subs.update()
        .where(
            sa.and_(
                wi_subs.c.status == "ACTIVE",
                wi_subs.c.notified_90p.is_(True),
                wi_subs.c.traffic_downlink_bytes < 0.90 * quota,
            )
        )
        .values(notified_90p=False)
    )


def downgrade() -> None:
    # Historical quota rebase is a one-way data alignment
    pass
