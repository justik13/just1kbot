"""Backfill legacy entitlements and support recurring referral rewards.
Revision ID: b62fe1349c20
Revises: ab17c4e92901
"""
from alembic import op
import sqlalchemy as sa
revision="b62fe1349c20"; down_revision="ab17c4e92901"; branch_labels=depends_on=None
def upgrade():
 op.drop_constraint("referral_rewards_referred_user_id_key","referral_rewards",type_="unique")
 op.add_column("referral_rewards",sa.Column("is_first",sa.Boolean(),nullable=False,server_default=sa.false()))
 op.create_index("uq_referral_rewards_first_user","referral_rewards",["referred_user_id"],unique=True,postgresql_where=sa.text("is_first = true"))
 # Markers only: access was already granted by legacy code, so never extend here.
 op.execute("""INSERT INTO entitlement_entries(beneficiary_user_id,source_type,source_id,entry_type,days_delta,device_limit_snapshot,tariff_id_snapshot,metadata,created_at)
 SELECT user_id,'payment',id::text,'payment_grant',snapshot_duration_days,snapshot_device_limit,tariff_id,jsonb_build_object('legacy_backfill', true),COALESCE(paid_at,created_at)
 FROM payments WHERE status='completed' AND fulfillment_status='succeeded' AND snapshot_duration_days>0 AND snapshot_device_limit>0
 ON CONFLICT ON CONSTRAINT uq_entitlement_entries_source DO NOTHING""")
 # Backfill known invited-user bonuses without applying them again.
 op.execute("""INSERT INTO entitlement_entries(beneficiary_user_id,source_type,source_id,entry_type,days_delta,device_limit_snapshot,tariff_id_snapshot,metadata,created_at)
 SELECT p.user_id,'payment',p.id::text,'referral_user_bonus',p.referral_user_bonus_days,p.snapshot_device_limit,p.tariff_id,jsonb_build_object('legacy_backfill',true),COALESCE(p.paid_at,p.created_at)
 FROM payments p WHERE p.status='completed' AND p.referral_user_bonus_days>0
 ON CONFLICT ON CONSTRAINT uq_entitlement_entries_source DO NOTHING""")
 # Referrer is unambiguous only when referred_by resolves to an extant user.
 op.execute("""INSERT INTO entitlement_entries(beneficiary_user_id,source_type,source_id,entry_type,days_delta,device_limit_snapshot,tariff_id_snapshot,metadata,created_at)
 SELECT ref.id,'payment',p.id::text,'referral_referrer_bonus',p.referral_referrer_bonus_days,NULL,p.tariff_id,jsonb_build_object('legacy_backfill',true),COALESCE(p.paid_at,p.created_at)
 FROM payments p JOIN users invited ON invited.id=p.user_id JOIN users ref ON ref.telegram_id=invited.referred_by
 WHERE p.status='completed' AND p.referral_referrer_bonus_days>0
 ON CONFLICT ON CONSTRAINT uq_entitlement_entries_source DO NOTHING""")
 op.execute("""UPDATE payments p SET reconciliation_status='manual_review',fulfillment_status='manual_review',status='requires_manual_review',manual_review_reason='legacy_referrer_unresolved'
 WHERE p.status='completed' AND p.referral_referrer_bonus_days>0 AND NOT EXISTS (SELECT 1 FROM users invited JOIN users ref ON ref.telegram_id=invited.referred_by WHERE invited.id=p.user_id)""")
 op.execute("""UPDATE payments SET reconciliation_status='manual_review',fulfillment_status='manual_review',status='requires_manual_review',manual_review_reason='legacy_entitlement_snapshot_missing'
 WHERE status='completed' AND (snapshot_duration_days IS NULL OR snapshot_duration_days<=0 OR snapshot_device_limit IS NULL OR snapshot_device_limit<=0)""")
 # Legacy referral_days means first-payment eligibility is ambiguous: reserve it without awarding again.
 op.execute("""INSERT INTO referral_rewards(referred_user_id,source_payment_id,referrer_user_id,is_first,created_at)
 SELECT DISTINCT ON (p.user_id) p.user_id,p.id,ref.id,true,COALESCE(p.paid_at,p.created_at)
 FROM payments p JOIN users u ON u.id=p.user_id JOIN users ref ON ref.telegram_id=u.referred_by
 WHERE p.status='completed' ORDER BY p.user_id,p.paid_at NULLS LAST,p.id
 ON CONFLICT DO NOTHING""")
def downgrade():
 count=op.get_bind().execute(sa.text("SELECT count(*) FROM referral_rewards WHERE is_first=false")).scalar_one()
 if count: raise RuntimeError("Cannot downgrade: recurring referral rewards exist")
 op.execute("DELETE FROM entitlement_entries WHERE metadata->>'legacy_backfill'='true'")
 op.drop_index("uq_referral_rewards_first_user",table_name="referral_rewards"); op.drop_column("referral_rewards","is_first"); op.create_unique_constraint("referral_rewards_referred_user_id_key","referral_rewards",["referred_user_id"])
