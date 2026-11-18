"""Consolidated v2 core schema, cleanup of AmneziaWG legacy, server roles, and 1-slot-1-UUID user devices.

Squashes micro-migrations 0045 through 0055 into a single atomic, idempotent upgrade from 0044.

Revision ID: 0045_v2_core_and_devices
Revises: 0044_clean_auto_disabled_servers
Create Date: 2026-11-17 13:00:00.000000
"""

from collections.abc import Sequence
import uuid

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0045_v2_core_and_devices"
down_revision: str | None = "0044_clean_auto_disabled_servers"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    tables = set(insp.get_table_names())

    # 1. Drop obsolete AmneziaWG queue and legacy UI tables
    for tbl in ["api_operations", "hub_messages", "admin_operation_idempotency", "maintenance_mode"]:
        if tbl in tables:
            op.drop_table(tbl)

    # 2. Update orders: clean legacy 'awg' service_type and restrict check constraint
    if "orders" in tables:
        op.execute(
            "UPDATE orders SET service_type = 'standard' "
            "WHERE LOWER(TRIM(service_type)) = 'awg'"
        )
        if bind.dialect.name == "postgresql":
            op.drop_constraint("ck_orders_service_type", "orders", type_="check")
            op.create_check_constraint(
                "ck_orders_service_type",
                "orders",
                "service_type IN ('standard', 'white_internet', 'topup')",
            )
        else:
            with op.batch_alter_table("orders") as batch_op:
                batch_op.drop_constraint("ck_orders_service_type", type_="check")
                batch_op.create_check_constraint(
                    "ck_orders_service_type",
                    "service_type IN ('standard', 'white_internet', 'topup')",
                )

    # 3. Clean and configure servers: drop max_clients, add role enum
    if "servers" in tables:
        server_cols = {c["name"] for c in insp.get_columns("servers")}
        if "max_clients" in server_cols:
            op.drop_column("servers", "max_clients")

        if "role" not in server_cols:
            op.add_column(
                "servers",
                sa.Column(
                    "role",
                    sa.String(length=30),
                    nullable=False,
                    server_default=sa.text("'ru_hub'"),
                ),
            )
            op.create_check_constraint(
                "ck_servers_role",
                "servers",
                "role IN ('ru_hub', 'eu_hub', 'exit_node')",
            )
            op.create_index("ix_servers_role", "servers", ["role"], unique=False)

    # 4. Clean users table: drop legacy WireGuard columns/indexes, add cooldown & auto_renew
    if "users" in tables:
        user_cols = {c["name"] for c in insp.get_columns("users")}
        user_indexes = {i["name"] for i in insp.get_indexes("users")}

        # Drop legacy indexes
        for idx in [
            "ix_users_banned",
            "ix_users_grace_12h",
            "ix_users_expiring_subscription",
            "ix_users_expired_grace_notify",
        ]:
            if idx in user_indexes:
                op.drop_index(idx, table_name="users")

        # Drop legacy columns
        for col in [
            "device_creations_today",
            "last_creation_date",
            "archived_device_traffic",
            "last_trial_reset_at",
            "notified_grace_12h",
            "is_banned",
        ]:
            if col in user_cols:
                op.drop_column("users", col)

        # Add new lifecycle/billing columns
        if "standard_downgrade_cooldown_until" not in user_cols:
            op.add_column(
                "users",
                sa.Column(
                    "standard_downgrade_cooldown_until",
                    sa.DateTime(timezone=True),
                    nullable=True,
                ),
            )
        if "auto_renew_enabled" not in user_cols:
            op.add_column(
                "users",
                sa.Column(
                    "auto_renew_enabled",
                    sa.Boolean(),
                    nullable=False,
                    server_default=sa.text("false"),
                ),
            )

        # Recreate clean partial indexes on users
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

    # 5. Update white_internet_subscriptions: drop active_hwids, rename origin_node_id -> hub_server_id, add limits
    if "white_internet_subscriptions" in tables:
        wi_cols = {c["name"] for c in insp.get_columns("white_internet_subscriptions")}
        wi_indexes = {i["name"] for i in insp.get_indexes("white_internet_subscriptions")}
        wi_checks = {c["name"] for c in insp.get_check_constraints("white_internet_subscriptions")}

        if "active_hwids" in wi_cols:
            op.drop_column("white_internet_subscriptions", "active_hwids")

        if "origin_node_id" in wi_cols and "hub_server_id" not in wi_cols:
            op.alter_column(
                "white_internet_subscriptions",
                "origin_node_id",
                new_column_name="hub_server_id",
            )
            if "ix_white_internet_subscriptions_origin_node_id" in wi_indexes:
                op.drop_index(
                    "ix_white_internet_subscriptions_origin_node_id",
                    table_name="white_internet_subscriptions",
                )
            if "ix_white_internet_subscriptions_hub_server_id" not in wi_indexes:
                op.create_index(
                    "ix_white_internet_subscriptions_hub_server_id",
                    "white_internet_subscriptions",
                    ["hub_server_id"],
                    unique=False,
                )

        if "pending_device_limit" not in wi_cols:
            op.add_column(
                "white_internet_subscriptions",
                sa.Column("pending_device_limit", sa.Integer(), nullable=True),
            )
        if "pending_from" not in wi_cols:
            op.add_column(
                "white_internet_subscriptions",
                sa.Column("pending_from", sa.DateTime(timezone=True), nullable=True),
            )
        if "auto_renew_enabled" not in wi_cols:
            op.add_column(
                "white_internet_subscriptions",
                sa.Column(
                    "auto_renew_enabled",
                    sa.Boolean(),
                    nullable=False,
                    server_default=sa.text("false"),
                ),
            )

        if "ck_wi_subs_pending_device_limit" not in wi_checks:
            op.create_check_constraint(
                "ck_wi_subs_pending_device_limit",
                "white_internet_subscriptions",
                "pending_device_limit IS NULL OR (pending_device_limit >= 1 AND pending_device_limit <= 3)",
            )

    # 6. Update vless_subscriptions: drop active_hwids, add lifecycle fields and backfill from users
    if "vless_subscriptions" in tables:
        vless_cols = {c["name"] for c in insp.get_columns("vless_subscriptions")}
        vless_indexes = {i["name"] for i in insp.get_indexes("vless_subscriptions")}

        if "active_hwids" in vless_cols:
            op.drop_column("vless_subscriptions", "active_hwids")

        if "expires_at" not in vless_cols:
            op.add_column(
                "vless_subscriptions",
                sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
            )
        if "ix_vless_subscriptions_expires_at" not in vless_indexes:
            op.create_index(
                "ix_vless_subscriptions_expires_at",
                "vless_subscriptions",
                ["expires_at"],
                unique=False,
            )

        if "device_limit" not in vless_cols:
            op.add_column(
                "vless_subscriptions",
                sa.Column("device_limit", sa.Integer(), nullable=False, server_default="2"),
            )
        if "auto_renew_enabled" not in vless_cols:
            op.add_column(
                "vless_subscriptions",
                sa.Column(
                    "auto_renew_enabled",
                    sa.Boolean(),
                    nullable=False,
                    server_default=sa.text("false"),
                ),
            )
        if "notified_expired" not in vless_cols:
            op.add_column(
                "vless_subscriptions",
                sa.Column(
                    "notified_expired",
                    sa.Boolean(),
                    nullable=False,
                    server_default=sa.text("false"),
                ),
            )
        if "notified_before_days" not in vless_cols:
            op.add_column(
                "vless_subscriptions",
                sa.Column("notified_before_days", sa.Integer(), nullable=True),
            )

        # Backfill vless_subscriptions lifecycle from users
        op.execute(
            """
            UPDATE vless_subscriptions v
            SET expires_at = u.subscription_end,
                device_limit = COALESCE(u.device_limit, 2),
                auto_renew_enabled = COALESCE(u.auto_renew_enabled, false),
                notified_expired = COALESCE(u.notified_expired, false),
                notified_before_days = (
                    CASE
                        WHEN u.notified_1d THEN 1
                        WHEN u.notified_3d THEN 3
                        ELSE NULL
                    END
                )
            FROM users u
            WHERE v.user_id = u.id;
            """
        )

    # 7. Create or update user_devices (1 slot = 1 UUID)
    if "user_devices" not in tables:
        op.create_table(
            "user_devices",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("user_id", sa.Integer(), nullable=False),
            sa.Column("hwid", sa.String(length=128), nullable=False),
            sa.Column("device_uuid", sa.String(length=36), nullable=False),
            sa.Column("device_name", sa.String(length=128), nullable=True),
            sa.Column("platform", sa.String(length=64), nullable=True),
            sa.Column("os_version", sa.String(length=64), nullable=True),
            sa.Column("app_version", sa.String(length=64), nullable=True),
            sa.Column(
                "bi_priority",
                sa.Integer(),
                nullable=False,
                server_default=sa.text("0"),
            ),
            sa.Column(
                "first_seen_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.text("now()"),
            ),
            sa.Column(
                "last_seen_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.text("now()"),
            ),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.text("now()"),
            ),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("user_id", "hwid", name="uq_user_devices_user_hwid"),
        )
        op.create_index("ix_user_devices_user_id", "user_devices", ["user_id"], unique=False)
        op.create_index("ix_user_devices_device_uuid", "user_devices", ["device_uuid"], unique=True)
        op.create_index(
            "ix_user_devices_user_priority",
            "user_devices",
            ["user_id", "bi_priority", "first_seen_at"],
            unique=False,
        )
    else:
        dev_cols = {c["name"] for c in insp.get_columns("user_devices")}
        dev_indexes = {i["name"] for i in insp.get_indexes("user_devices")}
        if "device_uuid" not in dev_cols:
            op.add_column(
                "user_devices",
                sa.Column("device_uuid", sa.String(length=36), nullable=True),
            )
            conn = op.get_bind()
            rows = conn.execute(sa.text("SELECT id FROM user_devices ORDER BY id")).fetchall()
            for r in rows:
                conn.execute(
                    sa.text("UPDATE user_devices SET device_uuid = :dev_uuid WHERE id = :dev_id"),
                    {"dev_uuid": str(uuid.uuid4()), "dev_id": r[0]},
                )
            op.alter_column("user_devices", "device_uuid", nullable=False)
        if "ix_user_devices_device_uuid" not in dev_indexes:
            op.create_index("ix_user_devices_device_uuid", "user_devices", ["device_uuid"], unique=True)

    # 8. Ensure broadcast_progress matches Mini App broadcast schema
    if "broadcast_progress" not in tables:
        op.create_table(
            "broadcast_progress",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("admin_id", sa.BigInteger(), nullable=False),
            sa.Column("total_count", sa.Integer(), nullable=False),
            sa.Column(
                "success_count",
                sa.Integer(),
                nullable=False,
                server_default=sa.text("0"),
            ),
            sa.Column(
                "fail_count",
                sa.Integer(),
                nullable=False,
                server_default=sa.text("0"),
            ),
            sa.Column(
                "last_processed_id",
                sa.Integer(),
                nullable=False,
                server_default=sa.text("0"),
            ),
            sa.Column("target_audience", sa.String(length=64), nullable=True),
            sa.Column("broadcast_text", sa.Text(), nullable=False),
            sa.Column("media_id", sa.String(length=500), nullable=True),
            sa.Column(
                "content_type",
                sa.String(length=32),
                nullable=False,
                server_default=sa.text("'text'"),
            ),
            sa.Column("label", sa.String(length=255), nullable=True),
            sa.Column(
                "status",
                sa.String(length=20),
                nullable=False,
                server_default=sa.text("'in_progress'"),
            ),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.text("now()"),
            ),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.text("now()"),
            ),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_broadcast_progress_admin_id", "broadcast_progress", ["admin_id"], unique=False)
        op.create_index("ix_broadcast_progress_status", "broadcast_progress", ["status"], unique=False)
        op.create_index(
            "ix_broadcast_in_progress",
            "broadcast_progress",
            ["status", "created_at"],
            unique=False,
            postgresql_where=sa.text("status = 'in_progress'"),
        )
    else:
        bp_cols = {c["name"] for c in insp.get_columns("broadcast_progress")}
        bp_indexes = {i["name"] for i in insp.get_indexes("broadcast_progress")}
        if "target_audience" in bp_cols:
            op.alter_column("broadcast_progress", "target_audience", type_=sa.String(length=64), nullable=True)
        if "media_id" in bp_cols:
            op.alter_column("broadcast_progress", "media_id", type_=sa.String(length=500), nullable=True)
        if "label" in bp_cols:
            op.alter_column("broadcast_progress", "label", type_=sa.String(length=255), nullable=True)
        if "ix_broadcast_in_progress" not in bp_indexes:
            op.create_index(
                "ix_broadcast_in_progress",
                "broadcast_progress",
                ["status", "created_at"],
                unique=False,
                postgresql_where=sa.text("status = 'in_progress'"),
            )


def downgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    tables = set(insp.get_table_names())
    json_type = postgresql.JSONB(astext_type=sa.Text()) if bind.dialect.name == "postgresql" else sa.JSON()

    # Revert user_devices
    if "user_devices" in tables:
        op.drop_table("user_devices")

    # Revert vless_subscriptions
    if "vless_subscriptions" in tables:
        vless_cols = {c["name"] for c in insp.get_columns("vless_subscriptions")}
        for col in ["notified_before_days", "notified_expired", "auto_renew_enabled", "device_limit", "expires_at"]:
            if col in vless_cols:
                op.drop_column("vless_subscriptions", col)
        if "active_hwids" not in vless_cols:
            op.add_column(
                "vless_subscriptions",
                sa.Column("active_hwids", json_type, nullable=True, server_default=sa.text("'{}'")),
            )

    # Revert white_internet_subscriptions
    if "white_internet_subscriptions" in tables:
        wi_cols = {c["name"] for c in insp.get_columns("white_internet_subscriptions")}
        wi_checks = {c["name"] for c in insp.get_check_constraints("white_internet_subscriptions")}
        wi_indexes = {i["name"] for i in insp.get_indexes("white_internet_subscriptions")}
        if "ck_wi_subs_pending_device_limit" in wi_checks:
            op.drop_constraint("ck_wi_subs_pending_device_limit", "white_internet_subscriptions", type_="check")
        for col in ["pending_device_limit", "pending_from", "auto_renew_enabled"]:
            if col in wi_cols:
                op.drop_column("white_internet_subscriptions", col)
        if "hub_server_id" in wi_cols and "origin_node_id" not in wi_cols:
            if "ix_white_internet_subscriptions_hub_server_id" in wi_indexes:
                op.drop_index(
                    "ix_white_internet_subscriptions_hub_server_id",
                    table_name="white_internet_subscriptions",
                )
            op.alter_column(
                "white_internet_subscriptions",
                "hub_server_id",
                new_column_name="origin_node_id",
            )
            if "ix_white_internet_subscriptions_origin_node_id" not in wi_indexes:
                op.create_index(
                    "ix_white_internet_subscriptions_origin_node_id",
                    "white_internet_subscriptions",
                    ["origin_node_id"],
                    unique=False,
                )
        if "active_hwids" not in wi_cols:
            op.add_column(
                "white_internet_subscriptions",
                sa.Column("active_hwids", json_type, nullable=True, server_default=sa.text("'{}'")),
            )
            if bind.dialect.name == "postgresql" and "ix_white_internet_subscriptions_active_hwids" not in wi_indexes:
                op.create_index(
                    "ix_white_internet_subscriptions_active_hwids",
                    "white_internet_subscriptions",
                    ["active_hwids"],
                    postgresql_using="gin",
                )

    # Revert users
    if "users" in tables:
        user_cols = {c["name"] for c in insp.get_columns("users")}
        user_indexes = {i["name"] for i in insp.get_indexes("users")}
        for col in ["standard_downgrade_cooldown_until", "auto_renew_enabled"]:
            if col in user_cols:
                op.drop_column("users", col)
        if "device_creations_today" not in user_cols:
            op.add_column("users", sa.Column("device_creations_today", sa.Integer(), nullable=False, server_default="0"))
        if "last_creation_date" not in user_cols:
            op.add_column("users", sa.Column("last_creation_date", sa.Date(), nullable=True))
        if "archived_device_traffic" not in user_cols:
            op.add_column("users", sa.Column("archived_device_traffic", json_type, nullable=False, server_default=sa.text("'{}'")))
        if "last_trial_reset_at" not in user_cols:
            op.add_column("users", sa.Column("last_trial_reset_at", sa.DateTime(timezone=True), nullable=True))
        if "notified_grace_12h" not in user_cols:
            op.add_column("users", sa.Column("notified_grace_12h", sa.Boolean(), nullable=False, server_default=sa.text("false")))
        if "is_banned" not in user_cols:
            op.add_column("users", sa.Column("is_banned", sa.Boolean(), nullable=False, server_default=sa.text("false")))
        if "ix_users_banned" not in user_indexes:
            op.create_index(
                "ix_users_banned",
                "users",
                ["telegram_id"],
                unique=False,
                postgresql_where=sa.text("(is_banned = true) AND (is_deleted = false)"),
            )

    # Revert servers
    if "servers" in tables:
        server_cols = {c["name"] for c in insp.get_columns("servers")}
        server_checks = {c["name"] for c in insp.get_check_constraints("servers")}
        if "ck_servers_role" in server_checks:
            op.drop_constraint("ck_servers_role", "servers", type_="check")
        if "role" in server_cols:
            op.drop_column("servers", "role")
        if "max_clients" not in server_cols:
            op.add_column(
                "servers",
                sa.Column("max_clients", sa.Integer(), nullable=False, server_default="1000"),
            )

    # Revert orders
    if "orders" in tables:
        if bind.dialect.name == "postgresql":
            op.drop_constraint("ck_orders_service_type", "orders", type_="check")
            op.create_check_constraint(
                "ck_orders_service_type",
                "orders",
                "service_type IN ('standard', 'white_internet', 'topup', 'awg')",
            )
        else:
            with op.batch_alter_table("orders") as batch_op:
                batch_op.drop_constraint("ck_orders_service_type", type_="check")
                batch_op.create_check_constraint(
                    "ck_orders_service_type",
                    "service_type IN ('standard', 'white_internet', 'topup', 'awg')",
                )

    # Restore dropped legacy tables for full downgrade symmetry
    if "api_operations" not in tables:
        op.create_table(
            "api_operations",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("operation_type", sa.String(length=32), nullable=False),
            sa.Column("status", sa.String(length=20), nullable=False, server_default="pending"),
            sa.Column("idempotency_key", sa.String(length=255), nullable=False, unique=True),
            sa.Column("server_id", sa.Integer(), sa.ForeignKey("servers.id", ondelete="SET NULL"), nullable=True),
            sa.Column("server_name_snapshot", sa.String(length=255), nullable=True),
            sa.Column("api_url_snapshot", sa.String(length=500), nullable=True),
            sa.Column("api_key_snapshot", sa.Text(), nullable=True),
            sa.Column("peer_id", sa.String(length=255), nullable=True),
            sa.Column("client_name", sa.String(length=255), nullable=True),
            sa.Column("payload", json_type, nullable=False, server_default=sa.text("'{}'")),
            sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="10"),
            sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
            sa.Column("locked_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("locked_by", sa.String(length=100), nullable=True),
            sa.Column("last_error_code", sa.String(length=100), nullable=True),
            sa.Column("last_error", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
            sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        )

    if "hub_messages" not in tables:
        op.create_table(
            "hub_messages",
            sa.Column("chat_id", sa.BigInteger(), nullable=False),
            sa.Column("message_id", sa.BigInteger(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
            sa.Column("is_effect_message", sa.Boolean(), nullable=False, server_default=sa.text("false")),
            sa.PrimaryKeyConstraint("chat_id", "message_id"),
        )
        op.create_index("ix_hub_messages_chat_id", "hub_messages", ["chat_id"])

    if "admin_operation_idempotency" not in tables:
        op.create_table(
            "admin_operation_idempotency",
            sa.Column("op_key", sa.String(length=64), primary_key=True, nullable=False),
            sa.Column("admin_id", sa.BigInteger(), nullable=False),
            sa.Column("target_id", sa.BigInteger(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        )
        op.create_index("ix_admin_operation_idempotency_created_at", "admin_operation_idempotency", ["created_at"])

    if "maintenance_mode" not in tables:
        op.create_table(
            "maintenance_mode",
            sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
            sa.Column("is_enabled", sa.Boolean(), nullable=False, server_default=sa.text("false")),
            sa.Column("message", sa.Text(), nullable=True),
            sa.Column("updated_by", sa.BigInteger(), nullable=True),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        )
