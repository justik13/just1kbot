"""Drop legacy payments table and dead columns/indexes from users, ledger, wi_subs, webhook_inbox.

Revision ID: 0047_cleanup_legacy_schema
Revises: 0046_performance_indexes
Create Date: 2026-11-19
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0047_cleanup_legacy_schema"
down_revision = "0046_performance_indexes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. account_ledger_entries: drop old shape check + payment_id/quote_id indexes & columns, recreate shape check
    op.drop_constraint("ck_account_ledger_entry_shape", "account_ledger_entries", type_="check")
    op.execute("DROP INDEX IF EXISTS uq_account_ledger_payment_credit")
    op.execute("DROP INDEX IF EXISTS uq_account_ledger_purchase_debit")
    op.execute("DROP INDEX IF EXISTS ix_account_ledger_payment_debits")
    op.drop_column("account_ledger_entries", "payment_id")
    op.drop_column("account_ledger_entries", "quote_id")
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM pg_trigger
                WHERE tgname = 'account_ledger_append_only'
                  AND tgrelid = 'account_ledger_entries'::regclass
            ) THEN
                ALTER TABLE account_ledger_entries DISABLE TRIGGER account_ledger_append_only;
            END IF;

            UPDATE account_ledger_entries
            SET entry_type = 'admin_adjustment',
                metadata = COALESCE(metadata, '{}'::jsonb) || jsonb_build_object('legacy_entry_type', entry_type)
            WHERE entry_type IN ('refund_debit', 'chargeback_debit') AND order_id IS NULL;

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
    op.create_check_constraint(
        "ck_account_ledger_entry_shape",
        "account_ledger_entries",
        "(entry_type = 'payment_credit' AND amount > 0 AND reversal_of_id IS NULL) OR "
        "(entry_type = 'purchase_debit' AND amount < 0 AND order_id IS NOT NULL AND reversal_of_id IS NULL) OR "
        "(entry_type = 'purchase_reversal' AND amount > 0 AND order_id IS NOT NULL AND reversal_of_id IS NOT NULL) OR "
        "(entry_type IN ('refund_debit','chargeback_debit') AND amount < 0 AND order_id IS NOT NULL AND reversal_of_id IS NULL) OR "
        "(entry_type = 'admin_adjustment' AND reversal_of_id IS NULL)",
    )

    # 2. Drop dead payments table
    op.execute("DROP TABLE IF EXISTS payments CASCADE")

    # 3. users: drop dead show_in_leaderboard
    op.drop_column("users", "show_in_leaderboard")

    # 4. white_internet_subscriptions: drop dead index and columns
    op.execute("DROP INDEX IF EXISTS ix_wi_subscriptions_pending_hard_delete")
    op.drop_column("white_internet_subscriptions", "pending_hard_delete")
    op.drop_column("white_internet_subscriptions", "is_trial")
    op.drop_column("white_internet_subscriptions", "last_device_reset_at")

    # 5. webhook_inbox: drop dead queue/lease indexes and columns
    op.execute("DROP INDEX IF EXISTS ix_webhook_inbox_claim")
    op.execute("DROP INDEX IF EXISTS ix_webhook_inbox_lease")
    op.drop_column("webhook_inbox", "attempts")
    op.drop_column("webhook_inbox", "max_attempts")
    op.drop_column("webhook_inbox", "next_attempt_at")
    op.drop_column("webhook_inbox", "locked_at")
    op.drop_column("webhook_inbox", "locked_by")
    op.drop_column("webhook_inbox", "last_error_code")
    op.drop_column("webhook_inbox", "last_error")
    op.drop_column("webhook_inbox", "processed_at")


def downgrade() -> None:
    # 5. webhook_inbox
    op.add_column("webhook_inbox", sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("webhook_inbox", sa.Column("last_error", sa.Text(), nullable=True))
    op.add_column("webhook_inbox", sa.Column("last_error_code", sa.String(length=100), nullable=True))
    op.add_column("webhook_inbox", sa.Column("locked_by", sa.String(length=100), nullable=True))
    op.add_column("webhook_inbox", sa.Column("locked_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column(
        "webhook_inbox",
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.add_column(
        "webhook_inbox",
        sa.Column("max_attempts", sa.Integer(), nullable=False, server_default=sa.text("30")),
    )
    op.add_column(
        "webhook_inbox",
        sa.Column("attempts", sa.Integer(), nullable=False, server_default=sa.text("0")),
    )
    op.create_index(
        "ix_webhook_inbox_lease",
        "webhook_inbox",
        ["locked_at"],
        postgresql_where=sa.text("status = 'processing'"),
    )
    op.create_index(
        "ix_webhook_inbox_claim",
        "webhook_inbox",
        ["next_attempt_at", "id"],
        postgresql_where=sa.text("status IN ('pending','retry')"),
    )

    # 4. white_internet_subscriptions
    op.add_column(
        "white_internet_subscriptions",
        sa.Column("last_device_reset_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "white_internet_subscriptions",
        sa.Column("is_trial", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )
    op.add_column(
        "white_internet_subscriptions",
        sa.Column("pending_hard_delete", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )
    op.create_index(
        "ix_wi_subscriptions_pending_hard_delete",
        "white_internet_subscriptions",
        ["id"],
        postgresql_where=sa.text("pending_hard_delete IS TRUE"),
    )

    # 3. users
    op.add_column(
        "users",
        sa.Column("show_in_leaderboard", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )

    # 2. payments table
    op.create_table(
        "payments",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("amount", sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column("currency", sa.String(length=3), server_default=sa.text("'RUB'"), nullable=False),
        sa.Column("public_order_id", sa.String(length=64), nullable=False),
        sa.Column("provider_idempotency_key", sa.String(length=64), nullable=False),
        sa.Column("provider_status", sa.String(length=30), server_default=sa.text("'creating'"), nullable=False),
        sa.Column("fulfillment_status", sa.String(length=30), server_default=sa.text("'not_ready'"), nullable=False),
        sa.Column("reconciliation_status", sa.String(length=30), server_default=sa.text("'ok'"), nullable=False),
        sa.Column("checkout_status", sa.String(length=20), server_default=sa.text("'active'"), nullable=False),
        sa.Column("ui_visible", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("user_cancel_requested_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("manual_review_reason", sa.String(length=255), nullable=True),
        sa.Column("topup_context", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("paid_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("provider_confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("credited_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("credit_notified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("payment_url_notified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("fulfilled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reversed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_reconciled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("provider_last_error_code", sa.String(length=100), nullable=True),
        sa.Column("provider_last_error", sa.Text(), nullable=True),
        sa.Column("fulfillment_last_error_code", sa.String(length=100), nullable=True),
        sa.Column("fulfillment_last_error", sa.Text(), nullable=True),
        sa.Column("external_id", sa.String(length=255), nullable=True),
        sa.Column("payment_url", sa.String(length=1000), nullable=True),
        sa.Column("payment_method", sa.String(length=50), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint("currency = 'RUB' AND amount > 0 AND amount = trunc(amount)", name="ck_payments_topup_money"),
    )
    op.create_index("ix_payments_user_id", "payments", ["user_id"])
    op.create_index("ix_payments_external_id", "payments", ["external_id"])
    op.create_index("ix_payments_user_created", "payments", ["user_id", "created_at"])

    # 1. account_ledger_entries
    op.drop_constraint("ck_account_ledger_entry_shape", "account_ledger_entries", type_="check")
    op.add_column("account_ledger_entries", sa.Column("quote_id", sa.Integer(), nullable=True))
    op.add_column(
        "account_ledger_entries",
        sa.Column("payment_id", sa.Integer(), sa.ForeignKey("payments.id", ondelete="RESTRICT"), nullable=True),
    )
    op.create_index(
        "uq_account_ledger_payment_credit",
        "account_ledger_entries",
        ["payment_id"],
        unique=True,
        postgresql_where=sa.text("entry_type='payment_credit' AND payment_id IS NOT NULL"),
    )
    op.create_index(
        "uq_account_ledger_purchase_debit",
        "account_ledger_entries",
        ["quote_id"],
        unique=True,
        postgresql_where=sa.text("entry_type='purchase_debit' AND quote_id IS NOT NULL"),
    )
    op.create_index(
        "ix_account_ledger_payment_debits",
        "account_ledger_entries",
        ["payment_id"],
        postgresql_where=sa.text("entry_type IN ('refund_debit','chargeback_debit')"),
    )
    op.create_check_constraint(
        "ck_account_ledger_entry_shape",
        "account_ledger_entries",
        "(entry_type = 'payment_credit' AND amount > 0 "
        "AND (payment_id IS NOT NULL OR order_id IS NOT NULL) AND quote_id IS NULL "
        "AND reversal_of_id IS NULL) OR "
        "(entry_type = 'purchase_debit' AND amount < 0 "
        "AND payment_id IS NULL AND (quote_id IS NOT NULL OR order_id IS NOT NULL) "
        "AND reversal_of_id IS NULL) OR "
        "(entry_type = 'purchase_reversal' AND amount > 0 "
        "AND payment_id IS NULL AND (quote_id IS NOT NULL OR order_id IS NOT NULL) "
        "AND reversal_of_id IS NOT NULL) OR "
        "(entry_type IN ('refund_debit','chargeback_debit') "
        "AND amount < 0 AND (payment_id IS NOT NULL OR order_id IS NOT NULL) "
        "AND quote_id IS NULL AND reversal_of_id IS NULL) OR "
        "(entry_type = 'admin_adjustment' AND payment_id IS NULL "
        "AND quote_id IS NULL AND reversal_of_id IS NULL)",
    )
