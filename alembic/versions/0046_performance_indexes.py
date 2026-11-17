"""Performance partial indexes for worker reconciliation queues.

Revision ID: 0046_performance_indexes
Revises: 0045_v2_core_and_devices
Create Date: 2026-11-17 14:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0046_performance_indexes"
down_revision: str | None = "0045_v2_core_and_devices"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    tables = set(insp.get_table_names())

    if "white_internet_orphan_cleanups" in tables:
        op.execute(
            sa.text(
                "CREATE INDEX IF NOT EXISTS ix_wi_orphan_cleanups_pending "
                "ON white_internet_orphan_cleanups (id) WHERE status = 'pending';"
            )
        )

    if "white_internet_subscriptions" in tables:
        op.execute(
            sa.text(
                "CREATE INDEX IF NOT EXISTS ix_wi_subscriptions_pending_hard_delete "
                "ON white_internet_subscriptions (id) WHERE pending_hard_delete IS TRUE;"
            )
        )

    if "vless_subscriptions" in tables:
        op.execute(
            sa.text(
                "CREATE INDEX IF NOT EXISTS ix_vless_subscriptions_pending_revoked "
                "ON vless_subscriptions (id) WHERE pending_revoked_uuids != '[]'::jsonb;"
            )
        )


def downgrade() -> None:
    op.execute(sa.text("DROP INDEX IF EXISTS ix_vless_subscriptions_pending_revoked;"))
    op.execute(sa.text("DROP INDEX IF EXISTS ix_wi_subscriptions_pending_hard_delete;"))
    op.execute(sa.text("DROP INDEX IF EXISTS ix_wi_orphan_cleanups_pending;"))
