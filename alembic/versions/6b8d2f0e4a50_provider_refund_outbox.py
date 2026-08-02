"""Add durable provider refund outbox.

Revision ID: 6b8d2f0e4a50
Revises: 5a7c1e9d3f40
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision = "6b8d2f0e4a50"
down_revision = "5a7c1e9d3f40"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "provider_refund_operations",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column(
            "operation_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
            unique=True,
        ),
        sa.Column(
            "payment_id",
            sa.Integer(),
            sa.ForeignKey("payments.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "reservation_id",
            sa.BigInteger(),
            sa.ForeignKey("account_balance_reservations.id", ondelete="RESTRICT"),
            nullable=False,
            unique=True,
        ),
        sa.Column("idempotency_key", sa.String(100), nullable=False, unique=True),
        sa.Column("amount", sa.Numeric(12, 2), nullable=False),
        sa.Column(
            "currency",
            sa.String(3),
            nullable=False,
            server_default=sa.text("'RUB'"),
        ),
        sa.Column("provider_payment_id", sa.String(255), nullable=False),
        sa.Column("provider_refund_id", sa.String(255), unique=True),
        sa.Column("provider_status", sa.String(20)),
        sa.Column(
            "status",
            sa.String(20),
            nullable=False,
            server_default=sa.text("'pending'"),
        ),
        sa.Column(
            "attempts", sa.Integer(), nullable=False, server_default=sa.text("0")
        ),
        sa.Column(
            "max_attempts", sa.Integer(), nullable=False, server_default=sa.text("12")
        ),
        sa.Column(
            "next_attempt_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("locked_at", sa.DateTime(timezone=True)),
        sa.Column("locked_by", sa.String(100)),
        sa.Column("last_error_code", sa.String(100)),
        sa.Column("last_error", sa.Text()),
        sa.Column("requested_by_admin_id", sa.BigInteger()),
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
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint(
            "status IN ('pending','processing','retry','completed','failed')",
            name="ck_provider_refund_operations_status",
        ),
        sa.CheckConstraint(
            "provider_status IS NULL OR "
            "provider_status IN ('pending','succeeded','canceled')",
            name="ck_provider_refund_operations_provider_status",
        ),
        sa.CheckConstraint(
            "amount > 0 AND amount = trunc(amount)",
            name="ck_provider_refund_operations_whole_amount",
        ),
        sa.CheckConstraint(
            "currency = 'RUB'",
            name="ck_provider_refund_operations_currency_rub",
        ),
        sa.CheckConstraint(
            "attempts >= 0 AND max_attempts > 0",
            name="ck_provider_refund_operations_attempts",
        ),
    )
    op.create_index(
        "ix_provider_refund_operations_payment",
        "provider_refund_operations",
        ["payment_id"],
    )
    op.create_index(
        "ix_provider_refund_operations_claim",
        "provider_refund_operations",
        ["next_attempt_at", "id"],
        postgresql_where=sa.text("status IN ('pending','retry')"),
    )
    op.create_index(
        "ix_provider_refund_operations_lease",
        "provider_refund_operations",
        ["locked_at"],
        postgresql_where=sa.text("status='processing'"),
    )
    op.create_index(
        "uq_provider_refund_operations_active_payment",
        "provider_refund_operations",
        ["payment_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('pending','processing','retry')"),
    )


def downgrade() -> None:
    op.execute(
        "UPDATE account_balance_reservations AS reservation "
        "SET status='released', resolved_at=now() "
        "FROM provider_refund_operations AS operation "
        "WHERE reservation.id=operation.reservation_id "
        "AND reservation.status='active'"
    )
    op.drop_table("provider_refund_operations")
