"""Create payment_notifications durable outbox table

Revision ID: d38a19451992
Revises: c20a97270920
Create Date: 2026-08-23 21:57:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d38a19451992"
down_revision: str | Sequence[str] | None = "c20a97270920"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "payment_notifications",
        sa.Column("id", sa.BigInteger(), nullable=False, primary_key=True),
        sa.Column("payment_id", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=40), nullable=False),
        sa.Column("state", sa.String(length=30), server_default=sa.text("'pending'"), nullable=False),
        sa.Column("claim_token", sa.String(length=64), nullable=True),
        sa.Column("claim_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("telegram_message_id", sa.BigInteger(), nullable=True),
        sa.Column("chat_id", sa.BigInteger(), nullable=False),
        sa.Column("payload_snapshot", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("attempts", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("max_attempts", sa.Integer(), server_default=sa.text("10"), nullable=False),
        sa.Column("last_error", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["payment_id"], ["payments.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("payment_id", "kind", name="uq_payment_notifications_payment_kind"),
        sa.CheckConstraint(
            "kind IN ('payment_url','balance_credit','referral_bonus','account_purchase')",
            name="ck_payment_notifications_kind",
        ),
        sa.CheckConstraint(
            "state IN ('pending','claimed','delivered','compensation_required','compensation_retryable','compensated','dead')",
            name="ck_payment_notifications_state",
        ),
    )
    op.create_index(
        "ix_payment_notifications_claim",
        "payment_notifications",
        ["claim_until", "id"],
        postgresql_where=sa.text("state IN ('pending', 'compensation_retryable')"),
    )
    op.create_index(
        "ix_payment_notifications_payment_id",
        "payment_notifications",
        ["payment_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_payment_notifications_payment_id", table_name="payment_notifications")
    op.drop_index("ix_payment_notifications_claim", table_name="payment_notifications")
    op.drop_table("payment_notifications")
