"""Add manual payment-dispute lifecycle.

Revision ID: 7c9e3a1f5b60
Revises: 6b8d2f0e4a50
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision = "7c9e3a1f5b60"
down_revision = "6b8d2f0e4a50"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "payment_disputes",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column(
            "public_id", postgresql.UUID(as_uuid=True), nullable=False, unique=True
        ),
        sa.Column(
            "payment_id",
            sa.Integer(),
            sa.ForeignKey("payments.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("provider_case_id", sa.String(255), nullable=False, unique=True),
        sa.Column("idempotency_key", sa.String(100), nullable=False, unique=True),
        sa.Column(
            "status", sa.String(30), nullable=False, server_default=sa.text("'open'")
        ),
        sa.Column("amount", sa.Numeric(12, 2), nullable=False),
        sa.Column(
            "currency", sa.String(3), nullable=False, server_default=sa.text("'RUB'")
        ),
        sa.Column("disputed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "reservation_id",
            sa.BigInteger(),
            sa.ForeignKey("account_balance_reservations.id", ondelete="RESTRICT"),
            unique=True,
        ),
        sa.Column(
            "chargeback_entry_id",
            sa.BigInteger(),
            sa.ForeignKey("account_ledger_entries.id", ondelete="RESTRICT"),
            unique=True,
        ),
        sa.Column("note", sa.Text()),
        sa.Column("created_by_admin_id", sa.BigInteger(), nullable=False),
        sa.Column("resolved_by_admin_id", sa.BigInteger()),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.text("now()")
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.text("now()")
        ),
        sa.Column("resolved_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint(
            "status IN ('open','won_by_merchant','lost_by_merchant','manual_review')",
            name="ck_payment_disputes_status",
        ),
        sa.CheckConstraint(
            "amount > 0 AND amount = trunc(amount)",
            name="ck_payment_disputes_whole_amount",
        ),
        sa.CheckConstraint(
            "currency = 'RUB'", name="ck_payment_disputes_currency_rub"
        ),
        sa.CheckConstraint(
            "(status IN ('open','manual_review') AND resolved_at IS NULL) OR "
            "(status IN ('won_by_merchant','lost_by_merchant') "
            "AND resolved_at IS NOT NULL)",
            name="ck_payment_disputes_resolution_shape",
        ),
        sa.CheckConstraint(
            "status <> 'lost_by_merchant' OR chargeback_entry_id IS NOT NULL",
            name="ck_payment_disputes_lost_has_debit",
        ),
    )
    op.create_index(
        "ix_payment_disputes_payment_status", "payment_disputes",
        ["payment_id", "status"]
    )
    op.create_index(
        "ix_payment_disputes_user_status", "payment_disputes",
        ["user_id", "status"]
    )
    op.create_index(
        "uq_payment_disputes_active_payment", "payment_disputes", ["payment_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('open','manual_review')"),
    )


def downgrade() -> None:
    op.execute(
        "UPDATE account_balance_reservations AS reservation "
        "SET status='released', resolved_at=now() "
        "FROM payment_disputes AS dispute "
        "WHERE reservation.id=dispute.reservation_id "
        "AND reservation.status='active'"
    )
    op.drop_table("payment_disputes")
