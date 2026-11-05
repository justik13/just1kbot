"""Clean legacy AUTO_DISABLED server health states and AUTO_UNAVAILABLE reason.

Revision ID: 0044_clean_auto_disabled_servers
Revises: 0043_purge_legacy_awg
Create Date: 2026-11-05 12:00:00.000000
"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0044_clean_auto_disabled_servers"
down_revision: str | None = "0043_purge_legacy_awg"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        UPDATE servers
        SET is_active = true,
            disabled_reason = NULL,
            disabled_at = NULL,
            health_state = 'ONLINE',
            consecutive_fails = 0,
            consecutive_successes = 0,
            problem_started_at = NULL,
            next_check_at = NULL
        WHERE disabled_reason = 'AUTO_UNAVAILABLE'
           OR health_state = 'AUTO_DISABLED';
        """
    )


def downgrade() -> None:
    pass
