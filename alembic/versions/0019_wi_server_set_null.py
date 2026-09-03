"""Make white_internet_subscriptions.origin_node_id nullable with ON DELETE SET NULL.

Revision ID: 0019_wi_server_set_null
Revises: 0018_simplify_wi_traffic
Create Date: 2026-09-03 22:15:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0019_wi_server_set_null"
down_revision: str | None = "0018_simplify_wi_traffic"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint(
        "white_internet_subscriptions_origin_node_id_fkey",
        "white_internet_subscriptions",
        type_="foreignkey",
    )
    op.alter_column(
        "white_internet_subscriptions",
        "origin_node_id",
        existing_type=sa.Integer(),
        nullable=True,
    )
    op.create_foreign_key(
        "white_internet_subscriptions_origin_node_id_fkey",
        "white_internet_subscriptions",
        "servers",
        ["origin_node_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint(
        "white_internet_subscriptions_origin_node_id_fkey",
        "white_internet_subscriptions",
        type_="foreignkey",
    )
    op.alter_column(
        "white_internet_subscriptions",
        "origin_node_id",
        existing_type=sa.Integer(),
        nullable=False,
    )
    op.create_foreign_key(
        "white_internet_subscriptions_origin_node_id_fkey",
        "white_internet_subscriptions",
        "servers",
        ["origin_node_id"],
        ["id"],
        ondelete="RESTRICT",
    )
