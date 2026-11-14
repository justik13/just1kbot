"""Add v2 billing fields (Standard downgrade cooldown, WI pending downgrade, auto_renew stub) and user_devices table.

Revision ID: 0050_v2_billing_and_devices
Revises: 0049_drop_user_ban
Create Date: 2026-11-14 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0050_v2_billing_and_devices"
down_revision: str | None = "0049_drop_user_ban"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)

    user_cols = {c["name"] for c in insp.get_columns("users")}
    if "standard_downgrade_cooldown_until" not in user_cols:
        op.add_column(
            "users",
            sa.Column(
                "standard_downgrade_cooldown_until",
                sa.DateTime(timezone=True),
                nullable=True,
            ),
        )
    if "auto_renew_enabled" not in user_cols:
        op.add_column(
            "users",
            sa.Column(
                "auto_renew_enabled",
                sa.Boolean(),
                nullable=False,
                server_default=sa.text("false"),
            ),
        )

    wi_cols = {c["name"] for c in insp.get_columns("white_internet_subscriptions")}
    if "pending_device_limit" not in wi_cols:
        op.add_column(
            "white_internet_subscriptions",
            sa.Column("pending_device_limit", sa.Integer(), nullable=True),
        )
    if "pending_from" not in wi_cols:
        op.add_column(
            "white_internet_subscriptions",
            sa.Column("pending_from", sa.DateTime(timezone=True), nullable=True),
        )
    if "auto_renew_enabled" not in wi_cols:
        op.add_column(
            "white_internet_subscriptions",
            sa.Column(
                "auto_renew_enabled",
                sa.Boolean(),
                nullable=False,
                server_default=sa.text("false"),
            ),
        )

    wi_checks = {c["name"] for c in insp.get_check_constraints("white_internet_subscriptions")}
    if "ck_wi_subs_pending_device_limit" not in wi_checks:
        op.create_check_constraint(
            "ck_wi_subs_pending_device_limit",
            "white_internet_subscriptions",
            "pending_device_limit IS NULL OR (pending_device_limit >= 1 AND pending_device_limit <= 3)",
        )

    tables = set(insp.get_table_names())
    if "user_devices" not in tables:
        op.create_table(
            "user_devices",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("user_id", sa.Integer(), nullable=False),
            sa.Column("hwid", sa.String(length=128), nullable=False),
            sa.Column("device_name", sa.String(length=128), nullable=True),
            sa.Column("platform", sa.String(length=64), nullable=True),
            sa.Column("os_version", sa.String(length=64), nullable=True),
            sa.Column("app_version", sa.String(length=64), nullable=True),
            sa.Column(
                "bi_priority",
                sa.Integer(),
                nullable=False,
                server_default=sa.text("0"),
            ),
            sa.Column(
                "first_seen_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.text("now()"),
            ),
            sa.Column(
                "last_seen_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.text("now()"),
            ),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.text("now()"),
            ),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("user_id", "hwid", name="uq_user_devices_user_hwid"),
        )
        op.create_index("ix_user_devices_user_id", "user_devices", ["user_id"], unique=False)
        op.create_index(
            "ix_user_devices_user_priority",
            "user_devices",
            ["user_id", "bi_priority", "first_seen_at"],
            unique=False,
        )


def downgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    tables = set(insp.get_table_names())
    if "user_devices" in tables:
        op.drop_index("ix_user_devices_user_priority", table_name="user_devices")
        op.drop_index("ix_user_devices_user_id", table_name="user_devices")
        op.drop_table("user_devices")

    wi_checks = {c["name"] for c in insp.get_check_constraints("white_internet_subscriptions")}
    if "ck_wi_subs_pending_device_limit" in wi_checks:
        op.drop_constraint(
            "ck_wi_subs_pending_device_limit",
            "white_internet_subscriptions",
            type_="check",
        )

    wi_cols = {c["name"] for c in insp.get_columns("white_internet_subscriptions")}
    for col in ("auto_renew_enabled", "pending_from", "pending_device_limit"):
        if col in wi_cols:
            op.drop_column("white_internet_subscriptions", col)

    user_cols = {c["name"] for c in insp.get_columns("users")}
    for col in ("auto_renew_enabled", "standard_downgrade_cooldown_until"):
        if col in user_cols:
            op.drop_column("users", col)
