"""Add durable notification outbox.

Revision ID: 0002_add_outbox_notifications
Revises: 0001_clean_baseline
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0002_add_outbox_notifications"
down_revision = "0001_clean_baseline"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "outbox_notifications",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(length=50), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "payload",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.String(length=20),
            nullable=False,
            server_default=sa.text("'pending'"),
        ),
        sa.Column(
            "attempts",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "attempts >= 0",
            name="ck_outbox_notifications_attempts_nonnegative",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_outbox_notifications_user_id",
        "outbox_notifications",
        ["user_id"],
        unique=False,
    )
    op.create_index(
        "ix_outbox_notifications_status",
        "outbox_notifications",
        ["status"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_outbox_notifications_status",
        table_name="outbox_notifications",
    )
    op.drop_index(
        "ix_outbox_notifications_user_id",
        table_name="outbox_notifications",
    )
    op.drop_table("outbox_notifications")
