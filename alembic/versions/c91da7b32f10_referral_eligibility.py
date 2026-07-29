"""Add durable referral first-eligibility reservation.
Revision ID: c91da7b32f10
Revises: b62fe1349c20
"""
from alembic import op
import sqlalchemy as sa
revision="c91da7b32f10"; down_revision="b62fe1349c20"; branch_labels=depends_on=None
def upgrade():
 op.create_table("referral_eligibilities",sa.Column("referred_user_id",sa.Integer(),sa.ForeignKey("users.id",ondelete="RESTRICT"),primary_key=True),sa.Column("status",sa.String(20),nullable=False),sa.Column("source_payment_id",sa.Integer(),sa.ForeignKey("payments.id",ondelete="RESTRICT")),sa.Column("reason",sa.String(100)),sa.Column("created_at",sa.DateTime(timezone=True),nullable=False,server_default=sa.func.now()),sa.CheckConstraint("status IN ('claimed','blocked')",name="ck_referral_eligibilities_status"))
 op.execute("""INSERT INTO referral_eligibilities(referred_user_id,status,source_payment_id,reason)
 SELECT referred_user_id,'claimed',source_payment_id,'legacy_proven_first' FROM referral_rewards WHERE is_first=true ON CONFLICT DO NOTHING""")
 op.execute("""INSERT INTO referral_eligibilities(referred_user_id,status,reason)
 SELECT user_id,'blocked','legacy_first_conflict' FROM payments WHERE manual_review_reason='legacy_referral_first_conflict' GROUP BY user_id ON CONFLICT (referred_user_id) DO UPDATE SET status='blocked',source_payment_id=NULL,reason='legacy_first_conflict'""")
def downgrade():
 count=op.get_bind().execute(sa.text("SELECT count(*) FROM referral_eligibilities WHERE status='blocked'")).scalar_one()
 if count: raise RuntimeError("Cannot downgrade while blocked referral eligibility requires review")
 op.drop_table("referral_eligibilities")
