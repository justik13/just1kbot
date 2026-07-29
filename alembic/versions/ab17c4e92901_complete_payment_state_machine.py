"""Complete checkout and referral reward state machine.
Revision ID: ab17c4e92901
Revises: f2c9d7124a01
"""
from alembic import op
import sqlalchemy as sa
revision="ab17c4e92901"; down_revision="f2c9d7124a01"; branch_labels=depends_on=None
def upgrade():
 op.drop_constraint("ck_payments_provider_status","payments",type_="check")
 op.create_check_constraint("ck_payments_provider_status","payments","provider_status IN ('not_created','creating','pending','waiting_for_capture','succeeded','canceled','refunded','unknown','manual_review')")
 op.add_column("payments",sa.Column("checkout_status",sa.String(20),nullable=False,server_default="active")); op.add_column("payments",sa.Column("user_cancel_requested_at",sa.DateTime(timezone=True)))
 op.create_check_constraint("ck_payments_checkout_status","payments","checkout_status IN ('active','abandoned')")
 op.create_table("referral_rewards",sa.Column("id",sa.BigInteger(),primary_key=True),sa.Column("referred_user_id",sa.Integer(),sa.ForeignKey("users.id",ondelete="RESTRICT"),nullable=False,unique=True),sa.Column("source_payment_id",sa.Integer(),sa.ForeignKey("payments.id",ondelete="RESTRICT"),nullable=False,unique=True),sa.Column("referrer_user_id",sa.Integer(),sa.ForeignKey("users.id",ondelete="RESTRICT"),nullable=False),sa.Column("created_at",sa.DateTime(timezone=True),nullable=False,server_default=sa.func.now()),sa.Column("reversed_at",sa.DateTime(timezone=True)))
def downgrade():
 op.execute("UPDATE payments SET provider_status='pending' WHERE provider_status='waiting_for_capture'")
 op.drop_table("referral_rewards"); op.drop_constraint("ck_payments_checkout_status","payments",type_="check"); op.drop_column("payments","user_cancel_requested_at"); op.drop_column("payments","checkout_status"); op.drop_constraint("ck_payments_provider_status","payments",type_="check"); op.create_check_constraint("ck_payments_provider_status","payments","provider_status IN ('not_created','creating','pending','succeeded','canceled','refunded','unknown','manual_review')")
