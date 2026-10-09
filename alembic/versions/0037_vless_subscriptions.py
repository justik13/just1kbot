"""Create vless_subscriptions table for standard VLESS access and INCY HWID quota tracking.

Revision ID: 0037_vless_subscriptions
Revises: 0036_unique_orders_external_id
Create Date: 2026-10-08 06:00:00.000000
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0037_vless_subscriptions"
down_revision: str | None = "0036_unique_orders_external_id"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "vless_subscriptions",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("token", sa.String(length=64), nullable=False),
        sa.Column("uuid", sa.String(length=36), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column(
            "active_hwids",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=True,
        ),
        sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_vless_subscriptions_user_id",
        "vless_subscriptions",
        ["user_id"],
        unique=True,
    )
    op.create_index(
        "ix_vless_subscriptions_token",
        "vless_subscriptions",
        ["token"],
        unique=True,
    )
    op.create_index(
        "ix_vless_subscriptions_uuid",
        "vless_subscriptions",
        ["uuid"],
        unique=True,
    )
    op.create_index(
        "ix_vless_subscriptions_active_hwids",
        "vless_subscriptions",
        ["active_hwids"],
        postgresql_using="gin",
    )


def downgrade() -> None:
    op.drop_index("ix_vless_subscriptions_active_hwids", table_name="vless_subscriptions")
    op.drop_index("ix_vless_subscriptions_uuid", table_name="vless_subscriptions")
    op.drop_index("ix_vless_subscriptions_token", table_name="vless_subscriptions")
    op.drop_index("ix_vless_subscriptions_user_id", table_name="vless_subscriptions")
    op.drop_table("vless_subscriptions")
