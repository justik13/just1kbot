"""Drop users.is_banned column, ix_users_banned index, and simplify expiry index.

Revision ID: 0049_drop_user_ban
Revises: 0048_drop_ui_tables
Create Date: 2026-11-12 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0049_drop_user_ban"
down_revision: str | None = "0048_drop_ui_tables"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    index_names = {idx["name"] for idx in insp.get_indexes("users")}
    column_names = {col["name"] for col in insp.get_columns("users")}

    if "ix_users_banned" in index_names:
        op.drop_index("ix_users_banned", table_name="users")

    if "ix_users_expiring_subscription" in index_names:
        op.drop_index("ix_users_expiring_subscription", table_name="users")

    if "is_banned" in column_names:
        op.drop_column("users", "is_banned")

    op.create_index(
        "ix_users_expiring_subscription",
        "users",
        ["subscription_end", "telegram_id"],
        unique=False,
        postgresql_where=sa.text(
            "is_deleted = false AND is_bot_blocked = false "
            "AND subscription_end IS NOT NULL "
            "AND (notified_3d = false OR notified_1d = false OR notified_2h = false)"
        ),
    )


def downgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    index_names = {idx["name"] for idx in insp.get_indexes("users")}
    column_names = {col["name"] for col in insp.get_columns("users")}

    if "ix_users_expiring_subscription" in index_names:
        op.drop_index("ix_users_expiring_subscription", table_name="users")

    if "is_banned" not in column_names:
        op.add_column(
            "users",
            sa.Column(
                "is_banned",
                sa.Boolean(),
                nullable=False,
                server_default=sa.text("false"),
            ),
        )

    op.create_index(
        "ix_users_banned",
        "users",
        ["telegram_id"],
        unique=False,
        postgresql_where=sa.text("is_banned = true AND is_deleted = false"),
    )

    op.create_index(
        "ix_users_expiring_subscription",
        "users",
        ["subscription_end", "telegram_id"],
        unique=False,
        postgresql_where=sa.text(
            "is_deleted = false AND is_bot_blocked = false AND is_banned = false "
            "AND subscription_end IS NOT NULL "
            "AND (notified_3d = false OR notified_1d = false OR notified_2h = false)"
        ),
    )
