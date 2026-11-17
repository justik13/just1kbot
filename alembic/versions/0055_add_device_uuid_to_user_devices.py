"""Add device_uuid to user_devices for 1-slot-1-UUID isolation.

Revision ID: 0055_add_device_uuid_to_user_devices
Revises: 0054_strict_server_role_hub_rename_and_drop_maintenance
Create Date: 2026-11-17 12:15:00.000000
"""

from collections.abc import Sequence
import uuid

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0055_add_device_uuid_to_user_devices"
down_revision: str | None = "0054_strict_server_role_hub_rename_and_drop_maintenance"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)

    cols = {c["name"] for c in insp.get_columns("user_devices")}
    if "device_uuid" not in cols:
        op.add_column(
            "user_devices",
            sa.Column("device_uuid", sa.String(36), nullable=True),
        )

        conn = op.get_bind()
        rows = conn.execute(sa.text("SELECT id FROM user_devices ORDER BY id")).fetchall()
        for r in rows:
            new_uuid = str(uuid.uuid4())
            conn.execute(
                sa.text("UPDATE user_devices SET device_uuid = :dev_uuid WHERE id = :dev_id"),
                {"dev_uuid": new_uuid, "dev_id": r[0]},
            )

        op.alter_column("user_devices", "device_uuid", nullable=False)
        op.create_index("ix_user_devices_device_uuid", "user_devices", ["device_uuid"], unique=True)


def downgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)

    indexes = {idx["name"] for idx in insp.get_indexes("user_devices")}
    if "ix_user_devices_device_uuid" in indexes:
        op.drop_index("ix_user_devices_device_uuid", table_name="user_devices")

    cols = {c["name"] for c in insp.get_columns("user_devices")}
    if "device_uuid" in cols:
        op.drop_column("user_devices", "device_uuid")
