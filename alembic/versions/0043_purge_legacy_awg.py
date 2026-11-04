"""Purge legacy AmneziaWG dead tables and user monthly AWG counters.

AmneziaWG protocol was decommissioned in production on 2026-10-27.
Standard access is served exclusively by VLESS TLS with device accounting via active_hwids.
This migration drops the legacy vpn_profiles table, decouples api_operations,
and drops the obsolete monthly_awg_bytes column from users.

Revision ID: 0043_purge_legacy_awg
Revises: 0042_servers_proto_vless
Create Date: 2026-11-04 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0043_purge_legacy_awg"
down_revision: str | None = "0042_servers_proto_vless"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. Drop foreign key and column from api_operations if present
    bind = op.get_bind()
    insp = sa.inspect(bind)
    api_cols = [c["name"] for c in insp.get_columns("api_operations")]
    if "profile_id" in api_cols:
        op.drop_column("api_operations", "profile_id")

    # 2. Drop legacy vpn_profiles table if present
    if "vpn_profiles" in insp.get_table_names():
        op.drop_table("vpn_profiles")

    # 3. Drop obsolete monthly_awg_bytes from users if present
    user_cols = [c["name"] for c in insp.get_columns("users")]
    if "monthly_awg_bytes" in user_cols:
        op.drop_column("users", "monthly_awg_bytes")


def downgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)

    # 1. Restore monthly_awg_bytes to users for symmetrical rollback down to 0035
    user_cols = [c["name"] for c in insp.get_columns("users")]
    if "monthly_awg_bytes" not in user_cols:
        op.add_column(
            "users",
            sa.Column(
                "monthly_awg_bytes",
                sa.BigInteger(),
                nullable=False,
                server_default="0",
            ),
        )

    # 2. Restore legacy vpn_profiles table for symmetrical rollback down to 0001
    if "vpn_profiles" not in insp.get_table_names():
        op.create_table(
            "vpn_profiles",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column(
                "user_id",
                sa.Integer(),
                sa.ForeignKey("users.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column(
                "server_id",
                sa.Integer(),
                sa.ForeignKey("servers.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("device_name", sa.String(length=255), nullable=False),
            sa.Column("peer_id", sa.String(length=255), nullable=True),
            sa.Column("client_name", sa.String(length=255), nullable=True),
            sa.Column("raw_config", sa.Text(), nullable=True),
            sa.Column("traffic_down", sa.BigInteger(), nullable=False, server_default="0"),
            sa.Column("traffic_up", sa.BigInteger(), nullable=False, server_default="0"),
            sa.Column("raw_last_down", sa.BigInteger(), nullable=False, server_default="0"),
            sa.Column("raw_last_up", sa.BigInteger(), nullable=False, server_default="0"),
            sa.Column("last_connected", sa.DateTime(timezone=True), nullable=True),
            sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
            sa.Column(
                "provisioning_status",
                sa.String(length=30),
                nullable=False,
                server_default="active",
            ),
            sa.Column("desired_is_active", sa.Boolean(), nullable=False, server_default="true"),
            sa.Column("actual_is_active", sa.Boolean(), nullable=True),
            sa.Column("desired_expires_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("actual_expires_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("desired_version", sa.Integer(), nullable=False, server_default="1"),
            sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("last_sync_error", sa.Text(), nullable=True),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            ),
        )

    # 3. Restore profile_id to api_operations
    api_cols = [c["name"] for c in insp.get_columns("api_operations")]
    if "profile_id" not in api_cols:
        op.add_column(
            "api_operations",
            sa.Column(
                "profile_id",
                sa.Integer(),
                sa.ForeignKey("vpn_profiles.id", ondelete="SET NULL"),
                nullable=True,
            ),
        )
