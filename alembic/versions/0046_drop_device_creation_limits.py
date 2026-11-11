"""Drop device creation limits and archived device traffic columns from users table.

Revision ID: 0046_drop_device_creation_limits
Revises: 0045_drop_api_operations
Create Date: 2026-11-11 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0046_drop_device_creation_limits"
down_revision: str | None = "0045_drop_api_operations"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    columns = [c["name"] for c in insp.get_columns("users")]
    with op.batch_alter_table("users") as batch_op:
        if "device_creations_today" in columns:
            batch_op.drop_column("device_creations_today")
        if "last_creation_date" in columns:
            batch_op.drop_column("last_creation_date")
        if "archived_device_traffic" in columns:
            batch_op.drop_column("archived_device_traffic")


def downgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    columns = [c["name"] for c in insp.get_columns("users")]
    with op.batch_alter_table("users") as batch_op:
        if "device_creations_today" not in columns:
            batch_op.add_column(
                sa.Column(
                    "device_creations_today",
                    sa.Integer(),
                    nullable=False,
                    server_default=sa.text("0"),
                )
            )
        if "last_creation_date" not in columns:
            batch_op.add_column(
                sa.Column("last_creation_date", sa.Date(), nullable=True)
            )
        if "archived_device_traffic" not in columns:
            batch_op.add_column(
                sa.Column(
                    "archived_device_traffic",
                    postgresql.JSONB(astext_type=sa.Text())
                    if bind.dialect.name == "postgresql"
                    else sa.JSON(),
                    nullable=False,
                    server_default=sa.text("'{}'"),
                )
            )
