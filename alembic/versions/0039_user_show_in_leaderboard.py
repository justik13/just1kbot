"""Add show_in_leaderboard column to users.

Revision ID: 0039_user_show_in_leaderboard
Revises: 0038_rename_awg_to_standard
Create Date: 2026-10-19 23:55:00.000000
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "0039_user_show_in_leaderboard"
down_revision: str | None = "0038_rename_awg_to_standard"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column(
            "show_in_leaderboard",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )


def downgrade() -> None:
    op.drop_column("users", "show_in_leaderboard")
