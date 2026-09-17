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
    # Rebase traffic_used_bytes to traffic_downlink_bytes where used exceeds downlink
    # to align with Egress-only billing model in Yandex Cloud CDN.
    op.execute(
        sa.text(
            """
            UPDATE white_internet_subscriptions
            SET traffic_used_bytes = traffic_downlink_bytes
            WHERE traffic_used_bytes > traffic_downlink_bytes;
            """
        )
    )


def downgrade() -> None:
    # Historical quota rebase is a one-way data alignment
    pass
