"""Allow partially_refunded status in ck_orders_status constraint.

Revision ID: 0049_orders_partially_refunded
Revises: 0048_bucket_ledger_cleanup
Create Date: 2026-11-19
"""

from __future__ import annotations

from alembic import op

revision = "0049_orders_partially_refunded"
down_revision = "0048_bucket_ledger_cleanup"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE orders DROP CONSTRAINT IF EXISTS ck_orders_status")
    op.create_check_constraint(
        "ck_orders_status",
        "orders",
        "status IN ('pending', 'paid', 'partially_refunded', 'refunded', 'canceled')",
    )


def downgrade() -> None:
    op.execute("UPDATE orders SET status = 'refunded' WHERE status = 'partially_refunded'")
    op.execute("ALTER TABLE orders DROP CONSTRAINT IF EXISTS ck_orders_status")
    op.create_check_constraint(
        "ck_orders_status",
        "orders",
        "status IN ('pending', 'paid', 'refunded', 'canceled')",
    )
