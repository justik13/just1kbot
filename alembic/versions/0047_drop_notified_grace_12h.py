"""Drop legacy notified_grace_12h column and simplify expiry index.

The 12-hour post-expiry AWG peer-deletion grace warning was removed; standard
VLESS subscriptions retain their subscription URL after expiry and only clear
active HWID bindings after the 24-hour retention period.

Revision ID: 0047_drop_notified_grace_12h
Revises: 0046_drop_device_creation_limits
Create Date: 2026-11-11 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0047_drop_notified_grace_12h"
down_revision: str | None = "0046_drop_device_creation_limits"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    index_names = {idx["name"] for idx in insp.get_indexes("users")}
    column_names = {col["name"] for col in insp.get_columns("users")}

    if "ix_users_expired_grace_notify" in index_names:
        op.drop_index("ix_users_expired_grace_notify", table_name="users")

    if "notified_grace_12h" in column_names:
        op.drop_column("users", "notified_grace_12h")

    op.create_index(
        "ix_users_expired_grace_notify",
        "users",
        ["subscription_end", "telegram_id"],
        unique=False,
        postgresql_where=sa.text(
            "is_deleted = false AND is_bot_blocked = false "
            "AND subscription_end IS NOT NULL AND notified_expired = false"
        ),
    )


def downgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    index_names = {idx["name"] for idx in insp.get_indexes("users")}
    column_names = {col["name"] for col in insp.get_columns("users")}

    if "ix_users_expired_grace_notify" in index_names:
        op.drop_index("ix_users_expired_grace_notify", table_name="users")

    if "notified_grace_12h" not in column_names:
        op.add_column(
            "users",
            sa.Column(
                "notified_grace_12h",
                sa.Boolean(),
                nullable=False,
                server_default=sa.text("false"),
            ),
        )

    op.create_index(
        "ix_users_expired_grace_notify",
        "users",
        ["subscription_end", "telegram_id"],
        unique=False,
        postgresql_where=sa.text(
            "is_deleted = false AND is_bot_blocked = false "
            "AND subscription_end IS NOT NULL "
            "AND (notified_expired = false OR notified_grace_12h = false)"
        ),
    )
