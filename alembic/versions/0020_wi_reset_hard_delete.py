"""Add pending_hard_delete flag for two-phase White Internet trial reset.

Revision ID: 0020_wi_reset_hard_delete
Revises: 0019_wi_server_set_null
Create Date: 2026-09-06 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0020_wi_reset_hard_delete"
down_revision: str | None = "0019_wi_server_set_null"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "white_internet_subscriptions",
        sa.Column(
            "pending_hard_delete",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )


def downgrade() -> None:
    # Loses only the reset-intent marker; subscription rows themselves are kept.
    # Any reset that was awaiting node confirmation will simply never finalize
    # after this downgrade (fail-closed: rows stay DISABLED, no orphan created).
    op.drop_column("white_internet_subscriptions", "pending_hard_delete")
