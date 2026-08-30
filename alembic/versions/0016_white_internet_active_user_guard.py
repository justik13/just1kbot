"""Harden White Internet lifecycle invariants.

Revision ID: 0016_white_internet_active_user_guard
Revises: 0015_white_internet
Create Date: 2026-08-30 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0016_white_internet_active_user_guard"
down_revision: str | Sequence[str] | None = "0015_white_internet"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # There may be historical EXPIRED/DISABLED rows, but at most one live
    # lifecycle instance may exist for a user. This closes concurrent purchase
    # races at the database boundary instead of relying on application reads.
    op.create_index(
        "uq_white_internet_live_user",
        "white_internet_subscriptions",
        ["user_id"],
        unique=True,
        postgresql_where=sa.text(
            "status IN ('PENDING', 'ACTIVE', 'EXHAUSTED')"
        ),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_white_internet_live_user",
        table_name="white_internet_subscriptions",
    )
