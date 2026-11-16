"""Purge legacy active_hwids JSONB columns and phantom notified_grace_12h.

user_devices table is the authoritative single source of truth for device bindings.
The legacy active_hwids JSONB columns on vless_subscriptions and
white_internet_subscriptions are dropped. Phantom notified_grace_12h column
on users is dropped and ix_users_expired_grace_notify index is simplified.

Revision ID: 0053_purge_legacy_hwids_and_grace12h
Revises: 0052_grace12h_and_broadcast
Create Date: 2026-11-17 04:30:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0053_purge_legacy_hwids_and_grace12h"
down_revision: str | None = "0052_grace12h_and_broadcast"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)

    user_cols = {c["name"] for c in insp.get_columns("users")}
    if "notified_grace_12h" in user_cols:
        op.drop_column("users", "notified_grace_12h")

    user_indexes = {idx["name"] for idx in insp.get_indexes("users")}
    if "ix_users_expired_grace_notify" in user_indexes:
        op.drop_index("ix_users_expired_grace_notify", table_name="users")

    op.create_index(
        "ix_users_expired_grace_notify",
        "users",
        ["subscription_end", "telegram_id"],
        unique=False,
        postgresql_where=sa.text(
            "is_deleted = false AND is_bot_blocked = false "
            "AND subscription_end IS NOT NULL "
            "AND notified_expired = false"
        ),
    )

    tables = set(insp.get_table_names())

    if "white_internet_subscriptions" in tables:
        wi_indexes = {idx["name"] for idx in insp.get_indexes("white_internet_subscriptions")}
        if "ix_white_internet_subscriptions_active_hwids" in wi_indexes:
            op.drop_index(
                "ix_white_internet_subscriptions_active_hwids",
                table_name="white_internet_subscriptions",
            )
        wi_cols = {c["name"] for c in insp.get_columns("white_internet_subscriptions")}
        if "active_hwids" in wi_cols:
            op.drop_column("white_internet_subscriptions", "active_hwids")

    if "vless_subscriptions" in tables:
        vless_cols = {c["name"] for c in insp.get_columns("vless_subscriptions")}
        if "active_hwids" in vless_cols:
            op.drop_column("vless_subscriptions", "active_hwids")


def downgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)

    tables = set(insp.get_table_names())

    if "vless_subscriptions" in tables:
        vless_cols = {c["name"] for c in insp.get_columns("vless_subscriptions")}
        if "active_hwids" not in vless_cols:
            op.add_column(
                "vless_subscriptions",
                sa.Column(
                    "active_hwids",
                    postgresql.JSONB(astext_type=sa.Text()),
                    nullable=True,
                    server_default=sa.text("'{}'::jsonb"),
                ),
            )

    if "white_internet_subscriptions" in tables:
        wi_cols = {c["name"] for c in insp.get_columns("white_internet_subscriptions")}
        if "active_hwids" not in wi_cols:
            op.add_column(
                "white_internet_subscriptions",
                sa.Column(
                    "active_hwids",
                    postgresql.JSONB(astext_type=sa.Text()),
                    nullable=True,
                    server_default=sa.text("'{}'::jsonb"),
                ),
            )
        wi_indexes = {idx["name"] for idx in insp.get_indexes("white_internet_subscriptions")}
        if "ix_white_internet_subscriptions_active_hwids" not in wi_indexes:
            op.create_index(
                "ix_white_internet_subscriptions_active_hwids",
                "white_internet_subscriptions",
                ["active_hwids"],
                unique=False,
                postgresql_using="gin",
            )

    user_cols = {c["name"] for c in insp.get_columns("users")}
    if "notified_grace_12h" not in user_cols:
        op.add_column(
            "users",
            sa.Column(
                "notified_grace_12h",
                sa.Boolean(),
                nullable=False,
                server_default=sa.text("false"),
            ),
        )

    user_indexes = {idx["name"] for idx in insp.get_indexes("users")}
    if "ix_users_expired_grace_notify" in user_indexes:
        op.drop_index("ix_users_expired_grace_notify", table_name="users")

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
