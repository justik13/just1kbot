"""Add subscription_token and active_sub_devices to users, device_type and sub_device_hash to vpn_profiles.

Revision ID: 0027_awg_subscription_system
Revises: 0026_wi_trial_semantics
Create Date: 2026-09-12 07:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0027_awg_subscription_system"
down_revision: str | None = "0026_wi_trial_semantics"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. Update 'users' table
    op.add_column("users", sa.Column("subscription_token", sa.String(length=64), nullable=True))
    op.create_index("ix_users_subscription_token", "users", ["subscription_token"], unique=True)
    op.add_column(
        "users",
        sa.Column(
            "active_sub_devices",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=True,
        ),
    )

    # 2. Update 'vpn_profiles' table
    op.add_column(
        "vpn_profiles",
        sa.Column(
            "device_type",
            sa.String(length=20),
            server_default=sa.text("'manual'"),
            nullable=False,
        ),
    )
    op.add_column("vpn_profiles", sa.Column("sub_device_hash", sa.String(length=64), nullable=True))
    op.create_index("ix_vpn_profiles_sub_device_hash", "vpn_profiles", ["sub_device_hash"], unique=False)


def downgrade() -> None:
    # 1. Revert 'vpn_profiles' table
    op.drop_index("ix_vpn_profiles_sub_device_hash", table_name="vpn_profiles")
    op.drop_column("vpn_profiles", "sub_device_hash")
    op.drop_column("vpn_profiles", "device_type")

    # 2. Revert 'users' table
    op.drop_column("users", "active_sub_devices")
    op.drop_index("ix_users_subscription_token", table_name="users")
    op.drop_column("users", "subscription_token")
