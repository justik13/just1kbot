"""Add notified_3d, notified_1d, notified_2h, notified_expired columns and index to white_internet_subscriptions.

Revision ID: 0027_wi_notification_flags
Revises: 0026_wi_trial_semantics
Create Date: 2026-09-11 12:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0027_wi_notification_flags"
down_revision: str | None = "0026_wi_trial_semantics"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
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
