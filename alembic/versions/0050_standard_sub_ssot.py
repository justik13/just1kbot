"""Move Standard subscription fields from users to vless_subscriptions and add order idempotency_key.

Revision ID: 0050_standard_sub_ssot
Revises: 0049_orders_partially_refunded
Create Date: 2026-11-19
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0050_standard_sub_ssot"
down_revision = "0049_orders_partially_refunded"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. Order idempotency_key for wallet/balance checkout
    op.add_column("orders", sa.Column("idempotency_key", sa.String(length=64), nullable=True))
    op.create_index(
        "uq_orders_user_idempotency_key",
        "orders",
        ["user_id", "idempotency_key"],
        unique=True,
        postgresql_where=sa.text("idempotency_key IS NOT NULL"),
    )

    # 2. Add Standard subscription SSOT columns to vless_subscriptions
    op.add_column("vless_subscriptions", sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column(
        "vless_subscriptions",
        sa.Column("device_limit", sa.Integer(), nullable=False, server_default=sa.text("0")),
    )
    op.add_column(
        "vless_subscriptions",
        sa.Column("downgrade_cooldown_until", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "vless_subscriptions",
        sa.Column("auto_renew_enabled", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )
    op.add_column(
        "vless_subscriptions",
        sa.Column("notified_3d", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )
    op.add_column(
        "vless_subscriptions",
        sa.Column("notified_1d", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )
    op.add_column(
        "vless_subscriptions",
        sa.Column("notified_2h", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )
    op.add_column(
        "vless_subscriptions",
        sa.Column("notified_expired", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )
    op.create_index(
        "ix_vless_subscriptions_expires_at",
        "vless_subscriptions",
        ["expires_at"],
        postgresql_where=sa.text("expires_at IS NOT NULL"),
    )

    # 3. Ensure every user with Standard subscription data has a vless_subscriptions row, then backfill
    op.execute(
        """
        INSERT INTO vless_subscriptions (
            user_id, token, uuid, is_active,
            expires_at, device_limit, downgrade_cooldown_until, auto_renew_enabled,
            notified_3d, notified_1d, notified_2h, notified_expired
        )
        SELECT
            u.id,
            substr(md5(u.id::text || clock_timestamp()::text || random()::text), 1, 32),
            (
                substr(h, 1, 8) || '-' || substr(h, 9, 4) || '-4' || substr(h, 14, 3) ||
                '-a' || substr(h, 18, 3) || '-' || substr(h, 21, 12)
            ),
            (u.subscription_end IS NOT NULL AND u.subscription_end > now()),
            u.subscription_end,
            COALESCE(u.device_limit, 0),
            u.standard_downgrade_cooldown_until,
            COALESCE(u.auto_renew_enabled, false),
            COALESCE(u.notified_3d, false),
            COALESCE(u.notified_1d, false),
            COALESCE(u.notified_2h, false),
            COALESCE(u.notified_expired, false)
        FROM (
            SELECT *, md5(id::text || random()::text || clock_timestamp()::text) AS h
            FROM users
            WHERE subscription_end IS NOT NULL
               OR COALESCE(device_limit, 0) > 0
               OR COALESCE(auto_renew_enabled, false) = true
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
            downgrade_cooldown_until = u.standard_downgrade_cooldown_until,
            auto_renew_enabled = COALESCE(u.auto_renew_enabled, false),
            notified_3d = COALESCE(u.notified_3d, false),
            notified_1d = COALESCE(u.notified_1d, false),
            notified_2h = COALESCE(u.notified_2h, false),
            notified_expired = COALESCE(u.notified_expired, false)
        FROM users u
        WHERE vs.user_id = u.id
        """
    )

    # 4. Drop legacy Standard subscription indexes & columns from users
    op.execute("DROP INDEX IF EXISTS ix_users_active_subscription")
    op.execute("DROP INDEX IF EXISTS ix_users_expiring_subscription")
    op.execute("DROP INDEX IF EXISTS ix_users_expired_grace_notify")
    op.drop_column("users", "subscription_end")
    op.drop_column("users", "device_limit")
    op.drop_column("users", "standard_downgrade_cooldown_until")
    op.drop_column("users", "auto_renew_enabled")
    op.drop_column("users", "notified_3d")
    op.drop_column("users", "notified_1d")
    op.drop_column("users", "notified_2h")
    op.drop_column("users", "notified_expired")


def downgrade() -> None:
    op.add_column("users", sa.Column("notified_expired", sa.Boolean(), nullable=False, server_default=sa.text("false")))
    op.add_column("users", sa.Column("notified_2h", sa.Boolean(), nullable=False, server_default=sa.text("false")))
    op.add_column("users", sa.Column("notified_1d", sa.Boolean(), nullable=False, server_default=sa.text("false")))
    op.add_column("users", sa.Column("notified_3d", sa.Boolean(), nullable=False, server_default=sa.text("false")))
    op.add_column("users", sa.Column("auto_renew_enabled", sa.Boolean(), nullable=False, server_default=sa.text("false")))
    op.add_column("users", sa.Column("standard_downgrade_cooldown_until", sa.DateTime(timezone=True), nullable=True))
    op.add_column("users", sa.Column("device_limit", sa.Integer(), nullable=False, server_default=sa.text("0")))
    op.add_column("users", sa.Column("subscription_end", sa.DateTime(timezone=True), nullable=True))

    op.execute(
        """
        UPDATE users u
        SET
            subscription_end = vs.expires_at,
            device_limit = vs.device_limit,
            standard_downgrade_cooldown_until = vs.downgrade_cooldown_until,
            auto_renew_enabled = vs.auto_renew_enabled,
            notified_3d = vs.notified_3d,
            notified_1d = vs.notified_1d,
            notified_2h = vs.notified_2h,
            notified_expired = vs.notified_expired
        FROM vless_subscriptions vs
        WHERE vs.user_id = u.id
        """
    )

    op.create_index(
        "ix_users_active_subscription",
        "users",
        ["subscription_end"],
        postgresql_where=sa.text("is_deleted = false AND subscription_end IS NOT NULL"),
    )
    op.create_index(
        "ix_users_expiring_subscription",
        "users",
        ["subscription_end", "telegram_id"],
        postgresql_where=sa.text(
            "is_deleted = false AND is_bot_blocked = false AND subscription_end IS NOT NULL "
            "AND (notified_3d = false OR notified_1d = false OR notified_2h = false)"
        ),
    )
    op.create_index(
        "ix_users_expired_grace_notify",
        "users",
        ["subscription_end", "telegram_id"],
        postgresql_where=sa.text(
            "is_deleted = false AND is_bot_blocked = false AND subscription_end IS NOT NULL "
            "AND notified_expired = false"
        ),
    )

    op.execute("DROP INDEX IF EXISTS ix_vless_subscriptions_expires_at")
    op.drop_column("vless_subscriptions", "notified_expired")
    op.drop_column("vless_subscriptions", "notified_2h")
    op.drop_column("vless_subscriptions", "notified_1d")
    op.drop_column("vless_subscriptions", "notified_3d")
    op.drop_column("vless_subscriptions", "auto_renew_enabled")
    op.drop_column("vless_subscriptions", "downgrade_cooldown_until")
    op.drop_column("vless_subscriptions", "device_limit")
    op.drop_column("vless_subscriptions", "expires_at")

    op.execute("DROP INDEX IF EXISTS uq_orders_user_idempotency_key")
    op.drop_column("orders", "idempotency_key")
