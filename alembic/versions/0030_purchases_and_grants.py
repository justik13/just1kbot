"""Add purchases and entitlement_grants tables, update account_ledger_entries with purchase_id.

Revision ID: 0030_purchases_and_grants
Revises: 0029_rebase_wi_traffic_downlink
Create Date: 2026-09-20 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0030_purchases_and_grants"
down_revision: str | None = "0029_rebase_wi_traffic_downlink"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. Create table `purchases`
    op.create_table(
        "purchases",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "quote_id",
            sa.BigInteger(),
            sa.ForeignKey("tariff_quotes.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("idempotency_key", sa.String(length=100), nullable=False),
        sa.Column("service_type", sa.String(length=32), nullable=False),
        sa.Column("operation_type", sa.String(length=32), nullable=False),
        sa.Column("amount_rub", sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column(
            "status",
            sa.String(length=20),
            nullable=False,
            server_default=sa.text("'pending'"),
        ),
        sa.Column(
            "fulfillment_status",
            sa.String(length=20),
            nullable=False,
            server_default=sa.text("'pending'"),
        ),
        sa.Column(
            "tariff_id",
            sa.Integer(),
            sa.ForeignKey("tariffs.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "tariff_version_id",
            sa.Integer(),
            sa.ForeignKey("tariff_versions.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("duration_days", sa.Integer(), nullable=True),
        sa.Column("device_limit", sa.Integer(), nullable=True),
        sa.Column("details", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("fulfilled_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('pending','completed','cancelled','refunded')",
            name="ck_purchases_status",
        ),
        sa.CheckConstraint(
            "fulfillment_status IN ('pending','fulfilled','failed','manual_review')",
            name="ck_purchases_fulfillment_status",
        ),
        sa.CheckConstraint(
            "amount_rub >= 0",
            name="ck_purchases_amount_rub_nonnegative",
        ),
    )
    op.create_index("ix_purchases_user_id", "purchases", ["user_id"])
    op.create_index(
        "uq_purchases_quote_id",
        "purchases",
        ["quote_id"],
        unique=True,
        postgresql_where=sa.text("quote_id IS NOT NULL"),
    )
    op.create_index(
        "uq_purchases_idempotency_key",
        "purchases",
        ["idempotency_key"],
        unique=True,
    )
    op.create_index(
        "ix_purchases_user_status",
        "purchases",
        ["user_id", "status", "created_at"],
    )

    # 2. Create table `entitlement_grants`
    op.create_table(
        "entitlement_grants",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "purchase_id",
            sa.BigInteger(),
            sa.ForeignKey("purchases.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "service_type",
            sa.String(length=32),
            nullable=False,
            server_default=sa.text("'awg'"),
        ),
        sa.Column("source_type", sa.String(length=32), nullable=False),
        sa.Column("source_id", sa.String(length=64), nullable=False),
        sa.Column("grant_type", sa.String(length=32), nullable=False),
        sa.Column("coverage_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("coverage_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("original_duration_hours", sa.Integer(), nullable=False),
        sa.Column(
            "paid_value_rub",
            sa.Numeric(precision=18, scale=6),
            nullable=False,
            server_default=sa.text("0.000000"),
        ),
        sa.Column(
            "tariff_version_id",
            sa.Integer(),
            sa.ForeignKey("tariff_versions.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "device_limit",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("1"),
        ),
        sa.Column(
            "status",
            sa.String(length=20),
            nullable=False,
            server_default=sa.text("'active'"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "status IN ('active','revoked')",
            name="ck_entitlement_grants_status",
        ),
        sa.CheckConstraint(
            "grant_type IN ('paid_purchase','paid_change','bonus_change','referral_bonus','admin_gift','compensation','legacy_backfill')",
            name="ck_entitlement_grants_grant_type",
        ),
        sa.CheckConstraint(
            "paid_value_rub >= 0",
            name="ck_entitlement_grants_paid_value_rub_nonnegative",
        ),
        sa.CheckConstraint(
            "coverage_start < coverage_end",
            name="ck_entitlement_grants_coverage_interval",
        ),
        sa.CheckConstraint(
            "original_duration_hours > 0",
            name="ck_entitlement_grants_original_duration_positive",
        ),
    )
    op.create_index(
        "ix_entitlement_grants_user_active",
        "entitlement_grants",
        ["user_id", "status", "coverage_end"],
    )
    op.create_index(
        "ix_entitlement_grants_purchase_id",
        "entitlement_grants",
        ["purchase_id"],
    )
    op.create_index(
        "uq_entitlement_grants_source",
        "entitlement_grants",
        ["user_id", "source_type", "source_id", "grant_type"],
        unique=True,
        postgresql_where=sa.text("status = 'active'"),
    )

    # 3. Modify `account_ledger_entries`: add purchase_id and dual-mode constraints/indexes
    op.add_column(
        "account_ledger_entries",
        sa.Column(
            "purchase_id",
            sa.BigInteger(),
            sa.ForeignKey("purchases.id", ondelete="RESTRICT"),
            nullable=True,
        ),
    )
    op.drop_constraint(
        "ck_account_ledger_entry_shape",
        "account_ledger_entries",
        type_="check",
    )
    op.create_check_constraint(
        "ck_account_ledger_entry_shape",
        "account_ledger_entries",
        "(entry_type = 'payment_credit' AND amount > 0 "
        "AND payment_id IS NOT NULL AND quote_id IS NULL AND purchase_id IS NULL "
        "AND reversal_of_id IS NULL) OR "
        "(entry_type = 'purchase_debit' AND amount < 0 "
        "AND payment_id IS NULL AND (quote_id IS NOT NULL OR purchase_id IS NOT NULL) "
        "AND reversal_of_id IS NULL) OR "
        "(entry_type = 'purchase_reversal' AND amount > 0 "
        "AND payment_id IS NULL AND (quote_id IS NOT NULL OR purchase_id IS NOT NULL) "
        "AND reversal_of_id IS NOT NULL) OR "
        "(entry_type IN ('refund_debit','chargeback_debit') "
        "AND amount < 0 AND payment_id IS NOT NULL "
        "AND quote_id IS NULL AND purchase_id IS NULL AND reversal_of_id IS NULL) OR "
        "(entry_type = 'admin_adjustment' AND payment_id IS NULL "
        "AND quote_id IS NULL AND purchase_id IS NULL AND reversal_of_id IS NULL)",
    )
    op.drop_index(
        "uq_account_ledger_purchase_debit",
        table_name="account_ledger_entries",
    )
    op.create_index(
        "uq_account_ledger_purchase_debit",
        "account_ledger_entries",
        ["quote_id"],
        unique=True,
        postgresql_where=sa.text("entry_type = 'purchase_debit' AND quote_id IS NOT NULL"),
    )
    op.create_index(
        "uq_account_ledger_purchase_debit_v2",
        "account_ledger_entries",
        ["purchase_id"],
        unique=True,
        postgresql_where=sa.text("entry_type = 'purchase_debit' AND purchase_id IS NOT NULL"),
    )
    op.create_index(
        "ix_account_ledger_purchase_id",
        "account_ledger_entries",
        ["purchase_id"],
        postgresql_where=sa.text("purchase_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index(
        "ix_account_ledger_purchase_id",
        table_name="account_ledger_entries",
    )
    op.drop_index(
        "uq_account_ledger_purchase_debit_v2",
        table_name="account_ledger_entries",
    )
    op.drop_index(
        "uq_account_ledger_purchase_debit",
        table_name="account_ledger_entries",
    )
    op.create_index(
        "uq_account_ledger_purchase_debit",
        "account_ledger_entries",
        ["quote_id"],
        unique=True,
        postgresql_where=sa.text("entry_type = 'purchase_debit'"),
    )
    op.drop_constraint(
        "ck_account_ledger_entry_shape",
        "account_ledger_entries",
        type_="check",
    )
    op.create_check_constraint(
        "ck_account_ledger_entry_shape",
        "account_ledger_entries",
        "(entry_type = 'payment_credit' AND amount > 0 "
        "AND payment_id IS NOT NULL AND quote_id IS NULL "
        "AND reversal_of_id IS NULL) OR "
        "(entry_type = 'purchase_debit' AND amount < 0 "
        "AND payment_id IS NULL AND quote_id IS NOT NULL "
        "AND reversal_of_id IS NULL) OR "
        "(entry_type = 'purchase_reversal' AND amount > 0 "
        "AND payment_id IS NULL AND quote_id IS NOT NULL "
        "AND reversal_of_id IS NOT NULL) OR "
        "(entry_type IN ('refund_debit','chargeback_debit') "
        "AND amount < 0 AND payment_id IS NOT NULL "
        "AND quote_id IS NULL AND reversal_of_id IS NULL) OR "
        "(entry_type = 'admin_adjustment' AND payment_id IS NULL "
        "AND quote_id IS NULL AND reversal_of_id IS NULL)",
    )
    op.drop_column("account_ledger_entries", "purchase_id")

    op.drop_table("entitlement_grants")
    op.drop_table("purchases")
