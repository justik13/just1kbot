"""Add orders.expires_at for wallet checkout TTL.

Wallet checkouts (White Internet) carry a 15-minute expiry that mirrors
the retired quote TTL: checkout must settle while the order is unexpired.
NULL means no expiry (gateway orders).

Revision ID: 0033_wi_order_checkout
Revises: 0032_drop_banking_residue
Create Date: 2026-10-04 12:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0033_wi_order_checkout"
down_revision: str | None = "0032_drop_banking_residue"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "orders",
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("orders", "expires_at")
