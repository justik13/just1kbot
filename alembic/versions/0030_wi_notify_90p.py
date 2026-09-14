"""Add notified_90p to white_internet_subscriptions.

Revision ID: 0030_wi_notify_90p
Revises: 0029_wi_notify_and_quote_opt
Create Date: 2026-09-14 06:20:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0030_wi_notify_90p"
down_revision: str | None = "0029_wi_notify_and_quote_opt"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. Add notified_90p column to white_internet_subscriptions
    op.add_column(
        "white_internet_subscriptions",
        sa.Column(
            "notified_90p",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )

    # 2. Backfill expired or exhausted subscriptions so they don't trigger retroactive warnings
    wi_subs = sa.table(
        "white_internet_subscriptions",
        sa.column("expires_at", sa.DateTime(timezone=True)),
        sa.column("status", sa.String()),
        sa.column("notified_90p", sa.Boolean()),
    )
    op.execute(
        wi_subs.update()
        .where(
            sa.or_(
                wi_subs.c.expires_at < sa.func.now(),
                wi_subs.c.status.in_(["EXPIRED", "EXHAUSTED", "DISABLED"]),
            )
        )
        .values(notified_90p=True)
    )


def downgrade() -> None:
    op.drop_column("white_internet_subscriptions", "notified_90p")
