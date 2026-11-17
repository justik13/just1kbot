"""Strict server roles, rename hub_server_id, drop maintenance_mode and unify subscription fields.

1. Drop legacy maintenance_mode table and phantom users.last_trial_reset_at column.
2. Add explicit servers.role column (VARCHAR(30) NOT NULL with CHECK constraint on ru_hub, eu_hub, exit_node).
3. Rename white_internet_subscriptions.origin_node_id to hub_server_id.
4. Add expires_at, device_limit, auto_renew_enabled, notified_expired, notified_before_days to vless_subscriptions and backfill from users.

Revision ID: 0054_strict_server_role_hub_rename_and_drop_maintenance
Revises: 0053_purge_legacy_hwids_and_grace12h
Create Date: 2026-11-17 08:50:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0054_strict_server_role_hub_rename_and_drop_maintenance"
down_revision: str | None = "0053_purge_legacy_hwids_and_grace12h"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)

    # 1. Drop table maintenance_mode if present
    if insp.has_table("maintenance_mode"):
        op.drop_table("maintenance_mode")

    # 2. Drop column last_trial_reset_at from users if present
    user_cols = {c["name"] for c in insp.get_columns("users")}
    if "last_trial_reset_at" in user_cols:
        op.drop_column("users", "last_trial_reset_at")

    # 3. Add servers.role column and populate from extra_data/capabilities
    server_cols = {c["name"] for c in insp.get_columns("servers")}
    if "role" not in server_cols:
        op.add_column(
            "servers",
            sa.Column(
                "role",
                sa.String(30),
                nullable=False,
                server_default="ru_hub",
            ),
        )
        # Backfill existing servers based on extra_data or capabilities
        op.execute(
            """
            UPDATE servers
            SET role = CASE
                WHEN extra_data->>'role' IN ('ru_hub', 'eu_hub', 'exit_node') THEN extra_data->>'role'
                ELSE 'ru_hub'
            END;
            """
        )
        op.create_check_constraint(
            "ck_servers_role",
            "servers",
            "role IN ('ru_hub', 'eu_hub', 'exit_node')",
        )
        op.create_index("ix_servers_role", "servers", ["role"])

    # 4. Rename white_internet_subscriptions.origin_node_id to hub_server_id
    if insp.has_table("white_internet_subscriptions"):
        wi_cols = {c["name"] for c in insp.get_columns("white_internet_subscriptions")}
        if "origin_node_id" in wi_cols and "hub_server_id" not in wi_cols:
            op.alter_column(
                "white_internet_subscriptions",
                "origin_node_id",
                new_column_name="hub_server_id",
            )
            # Rename index if exists
            wi_indices = {i["name"] for i in insp.get_indexes("white_internet_subscriptions")}
            if "ix_white_internet_subscriptions_origin_node_id" in wi_indices:
                op.execute(
                    "ALTER INDEX ix_white_internet_subscriptions_origin_node_id "
                    "RENAME TO ix_white_internet_subscriptions_hub_server_id;"
                )
            elif "ix_white_internet_subscriptions_hub_server_id" not in wi_indices:
                op.create_index(
                    "ix_white_internet_subscriptions_hub_server_id",
                    "white_internet_subscriptions",
                    ["hub_server_id"],
                )

    # 5. Add subscription lifetime and device limit fields to vless_subscriptions
    if insp.has_table("vless_subscriptions"):
        vless_cols = {c["name"] for c in insp.get_columns("vless_subscriptions")}
        if "expires_at" not in vless_cols:
            op.add_column(
                "vless_subscriptions",
                sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
            )
            op.create_index("ix_vless_subscriptions_expires_at", "vless_subscriptions", ["expires_at"])
        if "device_limit" not in vless_cols:
            op.add_column(
                "vless_subscriptions",
                sa.Column("device_limit", sa.Integer(), nullable=False, server_default="2"),
            )
        if "auto_renew_enabled" not in vless_cols:
            op.add_column(
                "vless_subscriptions",
                sa.Column("auto_renew_enabled", sa.Boolean(), nullable=False, server_default=sa.text("false")),
            )
        if "notified_expired" not in vless_cols:
            op.add_column(
                "vless_subscriptions",
                sa.Column("notified_expired", sa.Boolean(), nullable=False, server_default=sa.text("false")),
            )
        if "notified_before_days" not in vless_cols:
            op.add_column(
                "vless_subscriptions",
                sa.Column("notified_before_days", sa.Integer(), nullable=True),
            )

        # Backfill vless_subscriptions from users
        op.execute(
            """
            UPDATE vless_subscriptions v
            SET expires_at = u.subscription_end,
                device_limit = COALESCE(u.device_limit, 2),
                auto_renew_enabled = COALESCE(u.auto_renew_enabled, false),
                notified_expired = COALESCE(u.notified_expired, false),
                notified_before_days = u.notified_before_days
            FROM users u
            WHERE v.user_id = u.id;
            """
        )


def downgrade() -> None:
    # Downgrade logic safely reverts new columns
    bind = op.get_bind()
    insp = sa.inspect(bind)

    if insp.has_table("vless_subscriptions"):
        vless_cols = {c["name"] for c in insp.get_columns("vless_subscriptions")}
        for col in ["notified_before_days", "notified_expired", "auto_renew_enabled", "device_limit", "expires_at"]:
            if col in vless_cols:
                op.drop_column("vless_subscriptions", col)

    if insp.has_table("white_internet_subscriptions"):
        wi_cols = {c["name"] for c in insp.get_columns("white_internet_subscriptions")}
        if "hub_server_id" in wi_cols and "origin_node_id" not in wi_cols:
            op.alter_column(
                "white_internet_subscriptions",
                "hub_server_id",
                new_column_name="origin_node_id",
            )

    server_cols = {c["name"] for c in insp.get_columns("servers")}
    if "role" in server_cols:
        op.drop_constraint("ck_servers_role", "servers", type_="check")
        op.drop_column("servers", "role")
