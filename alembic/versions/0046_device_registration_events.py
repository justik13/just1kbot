"""Add device_registration_events table for durable 24h HWID churn tracking.

Revision ID: 0046_device_registration_events
Revises: 0045_v2_clean_unified
Create Date: 2026-11-22 00:00:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0046_device_registration_events"
down_revision = "0045_v2_clean_unified"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "device_registration_events",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("hwid", sa.String(length=128), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_device_reg_events_user_created",
        "device_registration_events",
        ["user_id", "created_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_device_reg_events_user_created", table_name="device_registration_events")
    op.drop_table("device_registration_events")
