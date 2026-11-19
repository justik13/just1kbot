"""Squashed v2 migration from 0044: unified ledger buckets, Standard SSOT, multi-hub WI, UserDevice.

Revision ID: 0045_v2_clean_unified
Revises: 0044_clean_auto_disabled_servers
Create Date: 2026-11-20 00:30:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0045_v2_clean_unified"
down_revision = "0044_clean_auto_disabled_servers"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    tables = set(insp.get_table_names())

    # 1. Drop legacy tables superseded by v2 architecture
    op.execute("DROP TRIGGER IF EXISTS account_allocations_append_only ON account_ledger_allocations")
    op.execute("DROP TRIGGER IF EXISTS account_allocation_validate ON account_ledger_allocations")
    op.execute("DROP FUNCTION IF EXISTS validate_account_allocation() CASCADE")
    for legacy_tbl in (
        "account_ledger_allocations",
        "white_internet_orphan_cleanups",
        "api_operations",
        "hub_messages",
        "admin_operation_idempotency",
        "maintenance_mode",
    ):
        if legacy_tbl in tables:
            op.execute(f"DROP TABLE IF EXISTS {legacy_tbl} CASCADE")

    # 2. Orders: awg -> standard, drop tariff_id, add idempotency_key & partially_refunded status
    if "orders" in tables:
        order_cols = {c["name"] for c in insp.get_columns("orders")}
        order_fks = {fk["name"] for fk in insp.get_foreign_keys("orders") if fk.get("name")}
        order_checks = {c["name"] for c in insp.get_check_constraints("orders")}
        order_indexes = {i["name"] for i in insp.get_indexes("orders")}

        op.execute("UPDATE orders SET service_type = 'standard' WHERE service_type = 'awg'")

        for fk_name in list(order_fks):
            if "tariff" in fk_name:
                op.drop_constraint(fk_name, "orders", type_="foreignkey")
        if "tariff_id" in order_cols:
            op.drop_column("orders", "tariff_id")

        if "idempotency_key" not in order_cols:
            op.add_column("orders", sa.Column("idempotency_key", sa.String(128), nullable=True))
        if "uq_orders_user_idempotency_key" not in order_indexes:
            op.create_index(
                "uq_orders_user_idempotency_key",
                "orders",
                ["user_id", "idempotency_key"],
                unique=True,
                postgresql_where=sa.text("idempotency_key IS NOT NULL"),
            )

        if "ck_orders_service_type" in order_checks:
            op.drop_constraint("ck_orders_service_type", "orders", type_="check")
        op.create_check_constraint(
            "ck_orders_service_type",
            "orders",
            "service_type IN ('standard', 'white_internet', 'topup')",
        )

        if "ck_orders_status" in order_checks:
            op.drop_constraint("ck_orders_status", "orders", type_="check")
        op.create_check_constraint(
            "ck_orders_status",
            "orders",
            "status IN ('pending', 'paid', 'canceled', 'refunded', 'partially_refunded', 'expired')",
        )

    # 3. Account ledger entries: real/bonus buckets without payments/allocations tables
    if "account_ledger_entries" in tables:
        ledger_cols = {c["name"] for c in insp.get_columns("account_ledger_entries")}
        ledger_fks = {fk["name"] for fk in insp.get_foreign_keys("account_ledger_entries") if fk.get("name")}
        ledger_checks = {c["name"] for c in insp.get_check_constraints("account_ledger_entries")}
        ledger_indexes = {i["name"] for i in insp.get_indexes("account_ledger_entries")}

        for fk_name in list(ledger_fks):
            if any(k in fk_name for k in ("payment", "quote", "reversal")):
                op.drop_constraint(fk_name, "account_ledger_entries", type_="foreignkey")
        for idx_name in ("uq_account_ledger_order_debit", "ix_account_ledger_entries_payment_id"):
            if idx_name in ledger_indexes:
                op.drop_index(idx_name, table_name="account_ledger_entries")

        if "bucket" not in ledger_cols:
            op.add_column(
                "account_ledger_entries",
                sa.Column("bucket", sa.String(10), nullable=True),
            )

        # Normalize legacy entry types
        op.execute(
            """
            UPDATE account_ledger_entries
            SET entry_type = 'admin_adjustment'
            WHERE entry_type IN ('refund_debit', 'chargeback_debit') AND order_id IS NULL
            """
        )
        op.execute(
            """
            UPDATE account_ledger_entries
            SET entry_type = 'refund_debit'
            WHERE entry_type = 'chargeback_debit' AND order_id IS NOT NULL
            """
        )
        op.execute(
            """
            UPDATE account_ledger_entries
            SET entry_type = 'admin_adjustment'
            WHERE entry_type IN ('referral_bonus', 'purchase_reversal')
            """
        )

        # Assign buckets cleanly
        op.execute(
            """
            UPDATE account_ledger_entries
            SET bucket = CASE
                WHEN entry_type IN ('payment_credit', 'refund_debit') THEN 'real'
                WHEN entry_type = 'admin_adjustment' THEN 'bonus'
                ELSE 'real'
            END
            WHERE bucket IS NULL
            """
        )

        op.alter_column("account_ledger_entries", "bucket", nullable=False, server_default=sa.text("'real'"))

        for col in ("payment_id", "quote_id", "reversal_of_id"):
            if col in ledger_cols:
                op.drop_column("account_ledger_entries", col)

        for ck_name in ("ck_account_ledger_bucket", "ck_account_ledger_entry_shape"):
            if ck_name in ledger_checks:
                op.drop_constraint(ck_name, "account_ledger_entries", type_="check")

        op.create_check_constraint(
            "ck_account_ledger_bucket",
            "account_ledger_entries",
            "bucket IN ('real', 'bonus')",
        )
        op.create_check_constraint(
            "ck_account_ledger_entry_shape",
            "account_ledger_entries",
            "(entry_type = 'payment_credit' AND bucket = 'real' AND amount > 0) OR "
            "(entry_type = 'purchase_debit' AND amount < 0 AND order_id IS NOT NULL) OR "
            "(entry_type = 'refund_debit' AND amount < 0 AND order_id IS NOT NULL AND bucket = 'real') OR "
            "(entry_type = 'admin_adjustment' AND bucket = 'bonus')",
        )
        if "ix_account_ledger_user_bucket" not in ledger_indexes:
            op.create_index(
                "ix_account_ledger_user_bucket",
                "account_ledger_entries",
                ["user_id", "bucket"],
            )

    for legacy_tbl in ("payments", "tariffs"):
        if legacy_tbl in tables:
            op.execute(f"DROP TABLE IF EXISTS {legacy_tbl} CASCADE")

    # 4. Servers: drop max_clients, add role
    if "servers" in tables:
        srv_cols = {c["name"] for c in insp.get_columns("servers")}
        srv_indexes = {i["name"] for i in insp.get_indexes("servers")}
        if "max_clients" in srv_cols:
            op.drop_column("servers", "max_clients")
        if "role" not in srv_cols:
            op.add_column(
                "servers",
                sa.Column("role", sa.String(20), nullable=False, server_default=sa.text("'ru_hub'")),
            )
        if "ix_servers_role" not in srv_indexes:
            op.create_index("ix_servers_role", "servers", ["role"])

    # 5. VlessSubscriptions SSOT + backfill from users (preserving 4h grace)
    if "vless_subscriptions" in tables:
        vs_cols = {c["name"] for c in insp.get_columns("vless_subscriptions")}
        vs_indexes = {i["name"] for i in insp.get_indexes("vless_subscriptions")}

        if "active_hwids" in vs_cols:
            op.drop_column("vless_subscriptions", "active_hwids")

        add_vs_cols = [
            ("expires_at", sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True)),
            ("device_limit", sa.Column("device_limit", sa.Integer(), nullable=False, server_default=sa.text("0"))),
            ("downgrade_cooldown_until", sa.Column("downgrade_cooldown_until", sa.DateTime(timezone=True), nullable=True)),
            ("auto_renew_enabled", sa.Column("auto_renew_enabled", sa.Boolean(), nullable=False, server_default=sa.text("false"))),
            ("notified_3d", sa.Column("notified_3d", sa.Boolean(), nullable=False, server_default=sa.text("false"))),
            ("notified_1d", sa.Column("notified_1d", sa.Boolean(), nullable=False, server_default=sa.text("false"))),
            ("notified_2h", sa.Column("notified_2h", sa.Boolean(), nullable=False, server_default=sa.text("false"))),
            ("notified_expired", sa.Column("notified_expired", sa.Boolean(), nullable=False, server_default=sa.text("false"))),
        ]
        for col_name, col_def in add_vs_cols:
            if col_name not in vs_cols:
                op.add_column("vless_subscriptions", col_def)

        if "ix_vless_subscriptions_pending_revoked" not in vs_indexes:
            op.create_index(
                "ix_vless_subscriptions_pending_revoked",
                "vless_subscriptions",
                ["id"],
                postgresql_where=sa.text("pending_revoked_uuids != '[]'::jsonb"),
            )
        if "ix_vless_subscriptions_expires_at" not in vs_indexes:
            op.create_index(
                "ix_vless_subscriptions_expires_at",
                "vless_subscriptions",
                ["expires_at"],
                postgresql_where=sa.text("expires_at IS NOT NULL"),
            )

        op.alter_column("vless_subscriptions", "is_active", server_default=sa.text("false"))

        if "users" in tables:
            user_cols = {c["name"] for c in insp.get_columns("users")}
            if "subscription_end" in user_cols:
                op.execute(
                    """
                    INSERT INTO vless_subscriptions (
                        user_id, token, uuid, is_active,
                        expires_at, device_limit, auto_renew_enabled,
                        notified_3d, notified_1d, notified_2h, notified_expired
                    )
                    SELECT
                        u.id,
                        substr(md5(u.id::text || clock_timestamp()::text || random()::text), 1, 32),
                        (
                            substr(h, 1, 8) || '-' || substr(h, 9, 4) || '-4' || substr(h, 14, 3) ||
                            '-a' || substr(h, 18, 3) || '-' || substr(h, 21, 12)
                        ),
                        (u.subscription_end IS NOT NULL AND u.subscription_end > now() - interval '4 hours'),
                        u.subscription_end,
                        COALESCE(u.device_limit, 0),
                        false,
                        COALESCE(u.notified_3d, false),
                        COALESCE(u.notified_1d, false),
                        COALESCE(u.notified_2h, false),
                        COALESCE(u.notified_expired, false)
                    FROM (
                        SELECT *, md5(id::text || random()::text || clock_timestamp()::text) AS h
                        FROM users
                        WHERE subscription_end IS NOT NULL
                           OR COALESCE(device_limit, 0) > 0
                    ) u
                    WHERE NOT EXISTS (
                        SELECT 1 FROM vless_subscriptions vs WHERE vs.user_id = u.id
                    )
                    """
                )
                op.execute(
                    """
                    UPDATE vless_subscriptions vs
                    SET
                        expires_at = u.subscription_end,
                        device_limit = COALESCE(u.device_limit, 0),
                        is_active = (u.subscription_end IS NOT NULL AND u.subscription_end > now() - interval '4 hours'),
                        notified_3d = COALESCE(u.notified_3d, false),
                        notified_1d = COALESCE(u.notified_1d, false),
                        notified_2h = COALESCE(u.notified_2h, false),
                        notified_expired = COALESCE(u.notified_expired, false)
                    FROM users u
                    WHERE vs.user_id = u.id
                    """
                )

    # 6. Drop legacy & Standard columns from users
    if "users" in tables:
        user_cols = {c["name"] for c in insp.get_columns("users")}
        for idx_name in (
            "ix_users_active_subscription",
            "ix_users_expiring_subscription",
            "ix_users_expired_grace_notify",
            "ix_users_current_tariff_id",
        ):
            op.execute(f"DROP INDEX IF EXISTS {idx_name}")
        for col in (
            "subscription_end",
            "device_limit",
            "current_tariff_id",
            "last_trial_reset_at",
            "show_in_leaderboard",
            "notified_3d",
            "notified_1d",
            "notified_2h",
            "notified_expired",
            "notified_grace_12h",
            "device_creations_today",
            "last_creation_date",
            "archived_device_traffic",
            "is_banned",
        ):
            if col in user_cols:
                op.drop_column("users", col)

    # 7. White Internet subscriptions: remove server pinning, add multi-hub snapshots & device revocation
    if "white_internet_subscriptions" in tables:
        wi_cols = {c["name"] for c in insp.get_columns("white_internet_subscriptions")}
        wi_fks = {fk["name"] for fk in insp.get_foreign_keys("white_internet_subscriptions") if fk.get("name")}
        for fk_name in list(wi_fks):
            if "origin_node" in fk_name or "hub_server" in fk_name:
                op.drop_constraint(fk_name, "white_internet_subscriptions", type_="foreignkey")
        for idx_name in (
            "ix_white_internet_subscriptions_origin_node_id",
            "ix_white_internet_subscriptions_hub_server_id",
            "ix_white_internet_subscriptions_active_hwids",
            "ix_wi_subscriptions_pending_hard_delete",
        ):
            op.execute(f"DROP INDEX IF EXISTS {idx_name}")
        for col in (
            "origin_node_id",
            "hub_server_id",
            "active_hwids",
            "last_device_reset_at",
            "is_trial",
            "pending_hard_delete",
            "last_reconciled_node_epoch",
            "last_uplink_snapshot",
            "last_downlink_snapshot",
            "traffic_stats_epoch",
        ):
            if col in wi_cols:
                op.drop_column("white_internet_subscriptions", col)

        add_wi_cols = [
            ("node_traffic_snapshots", sa.Column("node_traffic_snapshots", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb"))),
            ("pending_device_limit", sa.Column("pending_device_limit", sa.Integer(), nullable=True)),
            ("pending_from", sa.Column("pending_from", sa.DateTime(timezone=True), nullable=True)),
            ("auto_renew_enabled", sa.Column("auto_renew_enabled", sa.Boolean(), nullable=False, server_default=sa.text("false"))),
            ("node_sync_state", sa.Column("node_sync_state", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb"))),
            ("pending_revoked_uuids", sa.Column("pending_revoked_uuids", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'[]'::jsonb"))),
        ]
        for col_name, col_def in add_wi_cols:
            if col_name not in wi_cols:
                op.add_column("white_internet_subscriptions", col_def)

    # 8. Create user_devices
    if "user_devices" not in tables:
        op.create_table(
            "user_devices",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("user_id", sa.Integer(), nullable=False),
            sa.Column("hwid", sa.String(128), nullable=False),
            sa.Column("device_uuid", sa.String(36), nullable=False),
            sa.Column("device_name", sa.String(255), nullable=True),
            sa.Column("platform", sa.String(64), nullable=True),
            sa.Column("client_app", sa.String(64), nullable=True),
            sa.Column("bi_priority", sa.Integer(), nullable=False, server_default=sa.text("0")),
            sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("user_id", "hwid", name="uq_user_devices_user_hwid"),
            sa.UniqueConstraint("device_uuid", name="uq_user_devices_device_uuid"),
        )
        op.create_index("ix_user_devices_user_id", "user_devices", ["user_id"])

    # 9. Webhook inbox legacy queue columns
    if "webhook_inbox" in tables:
        wi_box_cols = {c["name"] for c in insp.get_columns("webhook_inbox")}
        for col in (
            "attempts",
            "max_attempts",
            "next_attempt_at",
            "locked_at",
            "locked_by",
            "last_error_code",
            "last_error",
        ):
            if col in wi_box_cols:
                op.drop_column("webhook_inbox", col)

    # 10. BroadcastProgress: keep NOT NULL constraints, widen target_audience to 64
    if "broadcast_progress" in tables:
        bp_cols = {c["name"]: c for c in insp.get_columns("broadcast_progress")}
        if "target_audience" in bp_cols:
            op.alter_column(
                "broadcast_progress",
                "target_audience",
                type_=sa.String(64),
                existing_nullable=False,
                nullable=False,
            )


def downgrade() -> None:
    raise NotImplementedError(
        "Downgrade from 0045_v2_clean_unified is not supported; restore from pg_dump backup."
    )
