"""Add durable refund ledger.
Revision ID: f2c9d7124a01
Revises: e9b1c7a42d10
"""
from alembic import op
import sqlalchemy as sa
revision="f2c9d7124a01"; down_revision="e9b1c7a42d10"; branch_labels=depends_on=None
def upgrade():
 op.create_table("payment_refunds",sa.Column("id",sa.BigInteger(),primary_key=True),sa.Column("payment_id",sa.Integer(),sa.ForeignKey("payments.id",ondelete="RESTRICT"),nullable=False),sa.Column("provider_refund_id",sa.String(255),nullable=False,unique=True),sa.Column("amount",sa.Numeric(12,2),nullable=False),sa.Column("currency",sa.String(20),nullable=False),sa.Column("provider_status",sa.String(20),nullable=False),sa.Column("event_key",sa.String(64),nullable=False),sa.Column("created_at",sa.DateTime(timezone=True),nullable=False,server_default=sa.func.now()),sa.Column("processed_at",sa.DateTime(timezone=True)),sa.CheckConstraint("provider_status IN ('pending','succeeded','canceled')",name="ck_payment_refunds_provider_status"))
 op.create_index("ix_payment_refunds_payment_id","payment_refunds",["payment_id"])
def downgrade(): op.drop_table("payment_refunds")
