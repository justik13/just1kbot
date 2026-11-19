"""Simplify ledger to real/bonus buckets, drop allocations, drop tariffs and vless_subscriptions dupes.

Revision ID: 0048_bucket_ledger_drop_tariffs_vless_dupes
Revises: 0047_cleanup_legacy_schema
Create Date: 2026-11-19
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0048_bucket_ledger_drop_tariffs_vless_dupes"
down_revision = "0047_cleanup_legacy_schema"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. Add bucket column to account_ledger_entries and backfill exact split from allocations
    op.add_column(
        "account_ledger_entries",
        sa.Column("bucket", sa.String(length=10), nullable=False, server_default=sa.text("'real'")),
    )
    op.execute("DROP INDEX IF EXISTS uq_account_ledger_order_debit")

    op.execute(
        """
        DO $$
        DECLARE
            rec RECORD;
        BEGIN
            IF EXISTS (
                SELECT 1 FROM pg_trigger
                WHERE tgname = 'account_ledger_append_only'
                  AND tgrelid = 'account_ledger_entries'::regclass
            ) THEN
                ALTER TABLE account_ledger_entries DISABLE TRIGGER account_ledger_append_only;
            END IF;

            UPDATE account_ledger_entries
            SET bucket = 'bonus'
            WHERE entry_type = 'admin_adjustment';

            IF to_regclass('public.account_ledger_allocations') IS NOT NULL THEN
                FOR rec IN
                    SELECT
                        d.id AS debit_id,
                        d.user_id,
                        d.currency,
                        d.order_id,
                        d.idempotency_key,
                        d.metadata,
                        d.created_at,
                        abs(d.amount) AS total_amt,
                        COALESCE(SUM(CASE WHEN c.entry_type = 'admin_adjustment' THEN a.amount ELSE 0 END), 0) AS bonus_amt
                    FROM account_ledger_entries d
                    JOIN account_ledger_allocations a ON a.debit_entry_id = d.id
                    JOIN account_ledger_entries c ON c.id = a.credit_entry_id
                    WHERE d.entry_type = 'purchase_debit'
                    GROUP BY d.id, d.user_id, d.currency, d.order_id, d.idempotency_key, d.metadata, d.created_at, d.amount
                    HAVING COALESCE(SUM(CASE WHEN c.entry_type = 'admin_adjustment' THEN a.amount ELSE 0 END), 0) > 0
                LOOP
                    IF rec.bonus_amt >= rec.total_amt THEN
                        UPDATE account_ledger_entries
                        SET bucket = 'bonus'
                        WHERE id = rec.debit_id;
                    ELSE
                        UPDATE account_ledger_entries
                        SET amount = -(rec.total_amt - rec.bonus_amt),
                            bucket = 'real'
                        WHERE id = rec.debit_id;

                        INSERT INTO account_ledger_entries (
                            user_id, entry_type, bucket, amount, currency, order_id, idempotency_key, metadata, created_at
                        ) VALUES (
                            rec.user_id,
                            'purchase_debit',
                            'bonus',
                            -rec.bonus_amt,
                            rec.currency,
                            rec.order_id,
                            rec.idempotency_key || chr(58) || 'bonus',
                            rec.metadata,
                            rec.created_at
                        );
                    END IF;
                END LOOP;
            END IF;

            IF EXISTS (
                SELECT 1 FROM pg_trigger
                WHERE tgname = 'account_ledger_append_only'
                  AND tgrelid = 'account_ledger_entries'::regclass
            ) THEN
                ALTER TABLE account_ledger_entries ENABLE TRIGGER account_ledger_append_only;
            END IF;
        END $$;
        """
    )

    # 2. Drop account_ledger_allocations and its triggers/function
    op.execute("DROP TRIGGER IF EXISTS account_allocations_append_only ON account_ledger_allocations")
    op.execute("DROP TRIGGER IF EXISTS account_allocation_validate ON account_ledger_allocations")
    op.execute("DROP FUNCTION IF EXISTS public.validate_account_allocation()")
    op.execute("DROP TABLE IF EXISTS account_ledger_allocations CASCADE")

    # 3. Update account_ledger_entries constraints & indexes, drop reversal_of_id
    op.execute("ALTER TABLE account_ledger_entries DROP CONSTRAINT IF EXISTS ck_account_ledger_entry_shape")
    op.execute("ALTER TABLE account_ledger_entries DROP CONSTRAINT IF EXISTS ck_account_ledger_entry_type")
    op.execute("DROP INDEX IF EXISTS uq_account_ledger_order_debit")
    op.execute("DROP INDEX IF EXISTS uq_account_ledger_reversal")
    op.execute("ALTER TABLE account_ledger_entries DROP COLUMN IF EXISTS reversal_of_id")

    op.create_check_constraint(
        "ck_account_ledger_bucket",
        "account_ledger_entries",
        "bucket IN ('real', 'bonus')",
    )
    op.create_check_constraint(
        "ck_account_ledger_entry_type",
        "account_ledger_entries",
        "entry_type IN ('payment_credit', 'purchase_debit', 'refund_debit', 'admin_adjustment')",
    )
    op.create_check_constraint(
        "ck_account_ledger_entry_shape",
        "account_ledger_entries",
        "(entry_type = 'payment_credit' AND amount > 0) OR "
        "(entry_type = 'purchase_debit' AND amount < 0 AND order_id IS NOT NULL) OR "
        "(entry_type = 'refund_debit' AND amount < 0 AND order_id IS NOT NULL AND bucket = 'real') OR "
        "(entry_type = 'admin_adjustment' AND bucket = 'bonus')",
    )
    op.create_index(
        "ix_account_ledger_user_bucket",
        "account_ledger_entries",
        ["user_id", "bucket"],
    )
    op.create_index(
        "uq_account_ledger_order_debit",
        "account_ledger_entries",
        ["order_id", "bucket"],
        unique=True,
        postgresql_where=sa.text("entry_type='purchase_debit' AND order_id IS NOT NULL"),
    )

    # 4. Drop legacy tariffs table and references
    op.execute("ALTER TABLE users DROP CONSTRAINT IF EXISTS users_current_tariff_id_fkey")
    op.execute("ALTER TABLE users DROP COLUMN IF EXISTS current_tariff_id")
    op.execute("ALTER TABLE orders DROP CONSTRAINT IF EXISTS orders_tariff_id_fkey")
    op.execute("ALTER TABLE orders DROP COLUMN IF EXISTS tariff_id")
    op.execute("DROP TABLE IF EXISTS tariffs CASCADE")

    # 5. Drop unused duplicate business columns from vless_subscriptions (SSOT = users)
    op.execute("DROP INDEX IF EXISTS ix_vless_subscriptions_expires_at")
    op.execute("ALTER TABLE vless_subscriptions DROP COLUMN IF EXISTS expires_at")
    op.execute("ALTER TABLE vless_subscriptions DROP COLUMN IF EXISTS device_limit")
    op.execute("ALTER TABLE vless_subscriptions DROP COLUMN IF EXISTS auto_renew_enabled")
    op.execute("ALTER TABLE vless_subscriptions DROP COLUMN IF EXISTS notified_expired")
    op.execute("ALTER TABLE vless_subscriptions DROP COLUMN IF EXISTS notified_before_days")


def downgrade() -> None:
    # 5. Restore vless_subscriptions columns
    op.add_column("vless_subscriptions", sa.Column("notified_before_days", sa.Integer(), nullable=True))
    op.add_column(
        "vless_subscriptions",
        sa.Column("notified_expired", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )
    op.add_column(
        "vless_subscriptions",
        sa.Column("auto_renew_enabled", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )
    op.add_column(
        "vless_subscriptions",
        sa.Column("device_limit", sa.Integer(), nullable=False, server_default=sa.text("2")),
    )
    op.add_column(
        "vless_subscriptions",
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_vless_subscriptions_expires_at", "vless_subscriptions", ["expires_at"])

    # 4. Restore tariffs table and references
    op.create_table(
        "tariffs",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("service_type", sa.String(length=30), server_default="standard", nullable=False),
        sa.Column("duration_days", sa.Integer(), nullable=False),
        sa.Column("device_limit", sa.Integer(), nullable=False, server_default=sa.text("2")),
        sa.Column("price_rub", sa.Integer(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("service_type", "device_limit", "duration_days", name="uq_tariffs_service_device_duration"),
    )
    op.add_column(
        "orders",
        sa.Column("tariff_id", sa.Integer(), sa.ForeignKey("tariffs.id", ondelete="SET NULL"), nullable=True),
    )
    op.add_column(
        "users",
        sa.Column("current_tariff_id", sa.Integer(), sa.ForeignKey("tariffs.id", ondelete="SET NULL"), nullable=True),
    )

    # 3. Restore account_ledger_entries structure
    op.drop_index("uq_account_ledger_order_debit", table_name="account_ledger_entries")
    op.drop_index("ix_account_ledger_user_bucket", table_name="account_ledger_entries")
    op.drop_constraint("ck_account_ledger_entry_shape", "account_ledger_entries", type_="check")
    op.drop_constraint("ck_account_ledger_entry_type", "account_ledger_entries", type_="check")
    op.drop_constraint("ck_account_ledger_bucket", "account_ledger_entries", type_="check")

    op.add_column(
        "account_ledger_entries",
        sa.Column(
            "reversal_of_id",
            sa.BigInteger(),
            sa.ForeignKey("account_ledger_entries.id", ondelete="RESTRICT"),
            nullable=True,
        ),
    )
    op.create_index(
        "uq_account_ledger_reversal",
        "account_ledger_entries",
        ["reversal_of_id"],
        unique=True,
        postgresql_where=sa.text("entry_type='purchase_reversal'"),
    )
    op.create_index(
        "uq_account_ledger_order_debit",
        "account_ledger_entries",
        ["order_id"],
        unique=True,
        postgresql_where=sa.text("entry_type='purchase_debit' AND order_id IS NOT NULL"),
    )
    op.create_check_constraint(
        "ck_account_ledger_entry_type",
        "account_ledger_entries",
        "entry_type IN ('payment_credit', 'purchase_debit', 'purchase_reversal', 'refund_debit', 'chargeback_debit', 'admin_adjustment')",
    )
    op.create_check_constraint(
        "ck_account_ledger_entry_shape",
        "account_ledger_entries",
        "(entry_type = 'payment_credit' AND amount > 0 AND reversal_of_id IS NULL) OR "
        "(entry_type = 'purchase_debit' AND amount < 0 AND order_id IS NOT NULL AND reversal_of_id IS NULL) OR "
        "(entry_type = 'purchase_reversal' AND amount > 0 AND order_id IS NOT NULL AND reversal_of_id IS NOT NULL) OR "
        "(entry_type IN ('refund_debit','chargeback_debit') AND amount < 0 AND order_id IS NOT NULL AND reversal_of_id IS NULL) OR "
        "(entry_type = 'admin_adjustment' AND reversal_of_id IS NULL)",
    )
    op.drop_column("account_ledger_entries", "bucket")

    # 2. Recreate account_ledger_allocations table
    op.create_table(
        "account_ledger_allocations",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("credit_entry_id", sa.BigInteger(), nullable=False),
        sa.Column("debit_entry_id", sa.BigInteger(), nullable=False),
        sa.Column("amount", sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column("idempotency_key", sa.String(length=180), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["credit_entry_id"], ["account_ledger_entries.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["debit_entry_id"], ["account_ledger_entries.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("idempotency_key"),
        sa.UniqueConstraint("credit_entry_id", "debit_entry_id", name="uq_account_allocations_credit_debit"),
        sa.CheckConstraint("amount > 0 AND amount = trunc(amount)", name="ck_account_allocations_whole_positive_amount"),
    )
    op.create_index("ix_account_ledger_allocations_user_id", "account_ledger_allocations", ["user_id"])
    op.create_index("ix_account_allocations_credit", "account_ledger_allocations", ["credit_entry_id", "id"])
    op.create_index("ix_account_allocations_debit", "account_ledger_allocations", ["debit_entry_id", "id"])
