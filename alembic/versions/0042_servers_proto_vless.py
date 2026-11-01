"""Switch servers.protocol server default from amneziawg2 to vless.

AWG-серверы выведены из эксплуатации, остался только VLESS/Xray.
Существующие строки не меняются, меняется только значение по умолчанию.

Revision ID: 0042_servers_proto_vless
Revises: 0041_reset_notification_flags
Create Date: 2026-11-01 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0042_servers_proto_vless"
down_revision: str | None = "0041_reset_notification_flags"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column(
        "servers",
        "protocol",
        existing_type=sa.String(length=50),
        existing_nullable=False,
        server_default="vless",
    )


def downgrade() -> None:
    op.alter_column(
        "servers",
        "protocol",
        existing_type=sa.String(length=50),
        existing_nullable=False,
        server_default="amneziawg2",
    )
