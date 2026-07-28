"""Add durable API operations table.

Revision ID: 6f8c2d31a9b4
Revises: a293fba90064
Create Date: 2026-07-28

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from utils.encryption import EncryptedString


revision: str = "6f8c2d31a9b4"
down_revision: Union[str, Sequence[str], None] = "a293fba90064"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "api_operations",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("operation_type", sa.String(length=32), nullable=False),
        sa.Column(
            "status",
            sa.String(length=20),
            server_default=sa.text("'pending'"),
            nullable=False,
        ),
        sa.Column("idempotency_key", sa.String(length=255), nullable=False),
        sa.Column("server_id", sa.Integer(), nullable=True),
        sa.Column("profile_id", sa.Integer(), nullable=True),
        sa.Column("server_name_snapshot", sa.String(length=255), nullable=True),
        sa.Column("api_url_snapshot", sa.String(length=500), nullable=True),
        sa.Column("api_key_snapshot", EncryptedString(critical=True), nullable=True),
        sa.Column("peer_id", sa.String(length=255), nullable=True),
        sa.Column("client_name", sa.String(length=255), nullable=True),
        sa.Column(
            "payload",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("attempts", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column(
            "max_attempts", sa.Integer(), server_default=sa.text("10"), nullable=False
        ),
        sa.Column(
            "next_attempt_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("locked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("locked_by", sa.String(length=100), nullable=True),
        sa.Column("last_error_code", sa.String(length=100), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "attempts >= 0", name="ck_api_operations_attempts_nonnegative"
        ),
        sa.CheckConstraint(
            "max_attempts > 0", name="ck_api_operations_max_attempts_positive"
        ),
        sa.CheckConstraint(
            "operation_type IN ('create_peer', 'update_peer', 'delete_peer')",
            name="ck_api_operations_operation_type",
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'processing', 'retry', 'succeeded', "
            "'dead', 'cancelled')",
            name="ck_api_operations_status",
        ),
        sa.ForeignKeyConstraint(
            ["profile_id"], ["vpn_profiles.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(["server_id"], ["servers.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "idempotency_key", name="uq_api_operations_idempotency_key"
        ),
    )
    op.create_index(
        "ix_api_operations_claim",
        "api_operations",
        ["status", "next_attempt_at", "created_at"],
        unique=False,
        postgresql_where=sa.text("status IN ('pending', 'retry')"),
    )
    op.create_index(
        "ix_api_operations_processing_lock",
        "api_operations",
        ["locked_at"],
        unique=False,
        postgresql_where=sa.text("status = 'processing'"),
    )
    op.create_index(
        "ix_api_operations_server_id",
        "api_operations",
        ["server_id"],
        unique=False,
    )
    op.create_index(
        "ix_api_operations_profile_id",
        "api_operations",
        ["profile_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_api_operations_profile_id", table_name="api_operations")
    op.drop_index("ix_api_operations_server_id", table_name="api_operations")
    op.drop_index("ix_api_operations_processing_lock", table_name="api_operations")
    op.drop_index("ix_api_operations_claim", table_name="api_operations")
    op.drop_table("api_operations")
