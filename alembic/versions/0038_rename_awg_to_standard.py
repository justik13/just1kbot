"""Rename legacy awg service_type to standard across tariffs and orders.

Revision ID: 0038_rename_awg_to_standard
Revises: 0037_vless_subscriptions
Create Date: 2026-10-11 08:30:00.000000
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "0038_rename_awg_to_standard"
down_revision: str | None = "0037_vless_subscriptions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint("ck_orders_service_type", "orders", type_="check")
    op.execute("UPDATE tariffs SET service_type = 'standard' WHERE TRIM(service_type) = 'awg'")
    op.execute("UPDATE orders SET service_type = 'standard' WHERE TRIM(service_type) = 'awg'")
    op.alter_column(
        "tariffs",
        "service_type",
        existing_type=sa.String(length=30),
        server_default="standard",
    )
    op.alter_column(
        "orders",
        "service_type",
        existing_type=sa.String(length=30),
        server_default=sa.text("'standard'"),
    )
    op.create_check_constraint(
        "ck_orders_service_type",
        "orders",
        "service_type IN ('standard', 'white_internet', 'topup', 'awg')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_orders_service_type", "orders", type_="check")
    op.execute("UPDATE tariffs SET service_type = 'awg' WHERE service_type = 'standard'")
    op.execute("UPDATE orders SET service_type = 'awg' WHERE service_type = 'standard'")
    op.alter_column(
        "tariffs",
        "service_type",
        existing_type=sa.String(length=30),
        server_default="awg",
    )
    op.alter_column(
        "orders",
        "service_type",
        existing_type=sa.String(length=30),
        server_default=sa.text("'awg'"),
    )
    op.create_check_constraint(
        "ck_orders_service_type",
        "orders",
        "service_type IN ('awg', 'white_internet', 'topup')",
    )
