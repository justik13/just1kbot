"""Add option_type to tariff_quotes and notification flags to white_internet_subscriptions.

Revision ID: 0029_wi_notify_and_quote_opt
Revises: 0028_two_balance_system
Create Date: 2026-09-13 21:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0029_wi_notify_and_quote_opt"
down_revision: str | None = "0028_two_balance_system"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. Add option_type column to tariff_quotes
    op.add_column(
        "tariff_quotes",
        sa.Column("option_type", sa.String(length=20), nullable=True),
    )

    # 2. Add notification flags to white_internet_subscriptions
    op.add_column(
        "white_internet_subscriptions",
        sa.Column(
            "notified_3d",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.add_column(
        "white_internet_subscriptions",
        sa.Column(
            "notified_1d",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.add_column(
        "white_internet_subscriptions",
        sa.Column(
            "notified_2h",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.add_column(
        "white_internet_subscriptions",
        sa.Column(
            "notified_expired",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )

    # 3. Create partial index for WI expiration notification polling
    op.create_index(
        "ix_wi_subs_expiring_notify",
        "white_internet_subscriptions",
        ["expires_at", "user_id"],
        postgresql_where=sa.text(
            "status IN ('ACTIVE', 'EXHAUSTED') AND (notified_3d = false OR notified_1d = false OR notified_2h = false OR notified_expired = false)"
        ),
    )


def downgrade() -> None:
    op.drop_index("ix_wi_subs_expiring_notify", table_name="white_internet_subscriptions")
    op.drop_column("white_internet_subscriptions", "notified_expired")
    op.drop_column("white_internet_subscriptions", "notified_2h")
    op.drop_column("white_internet_subscriptions", "notified_1d")
    op.drop_column("white_internet_subscriptions", "notified_3d")
    op.drop_column("tariff_quotes", "option_type")
