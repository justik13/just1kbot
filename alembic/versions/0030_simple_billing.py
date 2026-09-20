"""Simple billing orders and ledger integration.

Revision ID: 0030_simple_billing
Revises: 0029_rebase_wi_traffic_downlink
Create Date: 2026-09-20 15:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0030_simple_billing"
down_revision: str | None = "0029_rebase_wi_traffic_downlink"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. Create orders table
    op.create_table(
        "orders",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "service_type",
            sa.String(30),
            nullable=False,
            server_default="awg",
        ),
        sa.Column(
            "tariff_id",
            sa.Integer(),
            sa.ForeignKey("tariffs.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("amount_rub", sa.Numeric(10, 2), nullable=False),
        sa.Column(
            "duration_days",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "traffic_bytes",
            sa.BigInteger(),
            nullable=False,
            server_default="0",
        ),
        sa.Column("device_limit", sa.Integer(), nullable=True),
        sa.Column(
            "payment_method",
            sa.String(30),
            nullable=False,
            server_default="yookassa",
        ),
        sa.Column("external_id", sa.String(100), nullable=True),
        sa.Column("payment_url", sa.String(1000), nullable=True),
        sa.Column(
            "status",
            sa.String(20),
            nullable=False,
            server_default="pending",
        ),
        sa.Column("description", sa.String(255), nullable=True),
        sa.Column(
            "metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("paid_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("refunded_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "service_type IN ('awg', 'white_internet', 'topup')",
            name="ck_orders_service_type",
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'paid', 'refunded', 'canceled')",
            name="ck_orders_status",
        ),
        sa.CheckConstraint(
            "amount_rub >= 0 AND amount_rub = trunc(amount_rub)",
            name="ck_orders_amount_rub",
        ),
    )

    op.create_index(
        "ix_orders_user_created",
        "orders",
        ["user_id", "created_at"],
    )
    op.create_index(
        "ix_orders_external_id",
        "orders",
        ["external_id"],
        postgresql_where=sa.text("external_id IS NOT NULL"),
    )
    op.create_index("ix_orders_status", "orders", ["status"])

    # 2. Add order_id to account_ledger_entries
    op.add_column(
        "account_ledger_entries",
        sa.Column(
            "order_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("orders.id", ondelete="RESTRICT"),
            nullable=True,
        ),
    )

    # 3. Update check constraint on account_ledger_entries
    op.execute(
        "ALTER TABLE account_ledger_entries DROP CONSTRAINT IF EXISTS ck_account_ledger_entry_shape"
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

    # 4. Partial indices on account_ledger_entries
    op.drop_index(
        "uq_account_ledger_payment_credit",
        table_name="account_ledger_entries",
    )
    op.create_index(
        "uq_account_ledger_payment_credit",
        "account_ledger_entries",
        ["payment_id"],
        unique=True,
        postgresql_where=sa.text(
            "entry_type='payment_credit' AND payment_id IS NOT NULL"
        ),
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
        postgresql_where=sa.text(
            "entry_type='purchase_debit' AND quote_id IS NOT NULL"
        ),
    )
    op.create_index(
        "ix_account_ledger_order_id",
        "account_ledger_entries",
        ["order_id"],
        postgresql_where=sa.text("order_id IS NOT NULL"),
    )
    op.create_index(
        "uq_account_ledger_order_debit",
        "account_ledger_entries",
        ["order_id"],
        unique=True,
        postgresql_where=sa.text(
            "entry_type='purchase_debit' AND order_id IS NOT NULL"
        ),
    )
    op.create_index(
        "uq_account_ledger_order_credit",
        "account_ledger_entries",
        ["order_id"],
        unique=True,
        postgresql_where=sa.text(
            "entry_type='payment_credit' AND order_id IS NOT NULL"
        ),
    )


def downgrade() -> None:
    conn = op.get_bind()
    order_entries_count = conn.execute(
        sa.text("SELECT count(*) FROM account_ledger_entries WHERE order_id IS NOT NULL")
    ).scalar()
    if order_entries_count:
        raise RuntimeError(
            f"Cannot downgrade migration 0030: {order_entries_count} ledger entries are linked to orders. "
            "Downgrading would cause loss of financial records."
        )

    paid_orders_count = conn.execute(
        sa.text("SELECT count(*) FROM orders WHERE status = 'paid'")
    ).scalar()
    if paid_orders_count:
        raise RuntimeError(
            f"Cannot downgrade migration 0030: {paid_orders_count} paid orders exist in orders table. "
            "Downgrading would cause loss of order history."
        )

    op.drop_index(
        "uq_account_ledger_order_credit",
        table_name="account_ledger_entries",
    )
    op.drop_index(
        "uq_account_ledger_order_debit",
        table_name="account_ledger_entries",
    )
    op.drop_index(
        "ix_account_ledger_order_id",
        table_name="account_ledger_entries",
    )
    op.drop_index(
        "uq_account_ledger_payment_credit",
        table_name="account_ledger_entries",
    )
    op.create_index(
        "uq_account_ledger_payment_credit",
        "account_ledger_entries",
        ["payment_id"],
        unique=True,
        postgresql_where=sa.text("entry_type='payment_credit'"),
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
        postgresql_where=sa.text("entry_type='purchase_debit'"),
    )

    op.execute(
        "ALTER TABLE account_ledger_entries DROP CONSTRAINT IF EXISTS ck_account_ledger_entry_shape"
    )
    op.drop_column("account_ledger_entries", "order_id")
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

    op.drop_index("ix_orders_status", table_name="orders")
    op.drop_index("ix_orders_external_id", table_name="orders")
    op.drop_index("ix_orders_user_created", table_name="orders")
    op.drop_table("orders")
