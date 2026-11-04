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
    # Legacy AWG is permanently decommissioned and archived in git branch archive/amnezia-awg.
    pass
