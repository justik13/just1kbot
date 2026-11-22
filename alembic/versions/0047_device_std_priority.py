"""Add std_priority column to user_devices for independent Standard slot assignment.

Revision ID: 0047_device_std_priority
Revises: 0046_device_registration_events
Create Date: 2026-11-23 00:00:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0047_device_std_priority"
down_revision = "0046_device_registration_events"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "user_devices",
        sa.Column(
            "std_priority",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_user_devices_user_std_priority",
        "user_devices",
        ["user_id", "std_priority", "first_seen_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_user_devices_user_std_priority", table_name="user_devices")
    op.drop_column("user_devices", "std_priority")
