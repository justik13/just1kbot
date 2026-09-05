"""Enforce NOT NULL constraint and backfill legacy NULL protocols on servers table.

Revision ID: 0022_servers_protocol_not_null
Revises: 0021_wi_orphan_cleanups
Create Date: 2026-09-06 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0022_servers_protocol_not_null"
down_revision: str | None = "0021_wi_orphan_cleanups"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. Backfill legacy servers where protocol is NULL and capabilities has 'xray_origin'
    op.execute(
        """
        UPDATE servers
        SET protocol = 'xray'
        WHERE protocol IS NULL
          AND capabilities @> '["xray_origin"]'::jsonb
        """
    )

    # 2. Backfill any remaining servers where protocol is NULL to 'amneziawg2'
    op.execute(
        """
        UPDATE servers
        SET protocol = 'amneziawg2'
        WHERE protocol IS NULL
        """
    )

    # 3. Alter column to NOT NULL with default 'amneziawg2'
    op.alter_column(
        "servers",
        "protocol",
        existing_type=sa.String(length=50),
        nullable=False,
        server_default="amneziawg2",
    )


def downgrade() -> None:
    op.alter_column(
        "servers",
        "protocol",
        existing_type=sa.String(length=50),
        nullable=True,
        server_default=None,
    )
