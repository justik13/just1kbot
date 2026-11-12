"""Drop servers.max_clients column.

Revision ID: 0050_drop_server_max_clients
Revises: 0049_drop_user_ban
Create Date: 2026-11-13 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0050_drop_server_max_clients"
down_revision: str | None = "0049_drop_user_ban"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    column_names = {col["name"] for col in insp.get_columns("servers")}

    if "max_clients" in column_names:
        op.drop_column("servers", "max_clients")


def downgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    column_names = {col["name"] for col in insp.get_columns("servers")}

    if "max_clients" not in column_names:
        op.add_column(
            "servers",
            sa.Column("max_clients", sa.Integer(), nullable=False, server_default="1000"),
        )
