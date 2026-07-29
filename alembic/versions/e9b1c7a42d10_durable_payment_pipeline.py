"""Add durable payment provider, webhook and fulfillment pipeline.

Revision ID: e9b1c7a42d10
Revises: d74b3e921c10
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
revision = "e9b1c7a42d10"
down_revision = "d74b3e921c10"
branch_labels = depends_on = None

QUEUE = "status IN ('pending','processing','retry','succeeded','dead','cancelled')"

def _queue(name, types):
    op.create_table(name,
      sa.Column('id',sa.BigInteger(),primary_key=True), sa.Column('payment_id',sa.Integer(),sa.ForeignKey('payments.id',ondelete='RESTRICT'),nullable=False),
      sa.Column('operation_type',sa.String(30),nullable=False), sa.Column('status',sa.String(20),nullable=False,server_default='pending'),
      sa.Column('idempotency_key',sa.String(100),nullable=False,unique=True), sa.Column('payload',postgresql.JSONB(),nullable=False),
      sa.Column('attempts',sa.Integer(),nullable=False,server_default='0'),sa.Column('max_attempts',sa.Integer(),nullable=False,server_default='12'),
      sa.Column('next_attempt_at',sa.DateTime(timezone=True),nullable=False,server_default=sa.func.now()),sa.Column('locked_at',sa.DateTime(timezone=True)),sa.Column('locked_by',sa.String(100)),
      sa.Column('last_error_code',sa.String(100)),sa.Column('last_error',sa.Text()),sa.Column('created_at',sa.DateTime(timezone=True),nullable=False,server_default=sa.func.now()),
      sa.Column('updated_at',sa.DateTime(timezone=True),nullable=False,server_default=sa.func.now()),sa.Column('completed_at',sa.DateTime(timezone=True)),
      sa.CheckConstraint(f"operation_type IN ({types})",name=f'ck_{name}_type'),sa.CheckConstraint(QUEUE,name=f'ck_{name}_status'))
    op.create_index(f'ix_{name}_payment_id',name,['payment_id'])
    op.create_index(f'ix_{name}_claim',name,['next_attempt_at','id'],postgresql_where=sa.text("status IN ('pending','retry')"))
    op.create_index(f'ix_{name}_lease',name,['locked_at'],postgresql_where=sa.text("status='processing'"))

def upgrade():
    for n,t in [('public_order_id',sa.String(64)),('provider_idempotency_key',sa.String(64)),('provider_status',sa.String(30)),('fulfillment_status',sa.String(30)),('reconciliation_status',sa.String(30)),('provider_confirmed_at',sa.DateTime(timezone=True)),('fulfilled_at',sa.DateTime(timezone=True)),('reversed_at',sa.DateTime(timezone=True)),('last_reconciled_at',sa.DateTime(timezone=True)),('provider_last_error_code',sa.String(100)),('provider_last_error',sa.Text()),('fulfillment_last_error_code',sa.String(100)),('fulfillment_last_error',sa.Text())]: op.add_column('payments',sa.Column(n,t))
    op.execute("UPDATE payments SET provider_status=CASE status WHEN 'completed' THEN 'succeeded' WHEN 'cancelled' THEN 'canceled' WHEN 'refunded' THEN 'refunded' WHEN 'pending' THEN 'pending' ELSE 'unknown' END, fulfillment_status=CASE WHEN status='completed' THEN 'succeeded' WHEN status='refunded' THEN 'reversed' ELSE 'not_ready' END, reconciliation_status='ok'")
    for n,d in [('provider_status','not_created'),('fulfillment_status','not_ready'),('reconciliation_status','ok')]: op.alter_column('payments',n,nullable=False,server_default=d)
    op.create_check_constraint('ck_payments_provider_status','payments',"provider_status IN ('not_created','creating','pending','succeeded','canceled','refunded','unknown','manual_review')")
    op.create_check_constraint('ck_payments_fulfillment_status','payments',"fulfillment_status IN ('not_ready','pending','processing','succeeded','failed','reversal_pending','reversed','manual_review')")
    op.create_check_constraint('ck_payments_reconciliation_status','payments',"reconciliation_status IN ('ok','required','mismatch','manual_review')")
    op.create_index('uq_payments_public_order_id_not_null','payments',['public_order_id'],unique=True,postgresql_where=sa.text('public_order_id IS NOT NULL'))
    op.create_index('uq_payments_provider_idempotency_key_not_null','payments',['provider_idempotency_key'],unique=True,postgresql_where=sa.text('provider_idempotency_key IS NOT NULL'))
    op.drop_constraint('payments_user_id_fkey','payments',type_='foreignkey'); op.create_foreign_key('payments_user_id_fkey','payments','users',['user_id'],['id'],ondelete='RESTRICT')
    _queue('payment_provider_operations',"'create_payment','cancel_payment','reconcile_payment'")
    _queue('payment_fulfillment_operations',"'grant_subscription','grant_referral','reverse_payment'")
    op.create_table('webhook_inbox',sa.Column('id',sa.BigInteger(),primary_key=True),sa.Column('provider',sa.String(30),nullable=False),sa.Column('event_key',sa.String(64),nullable=False),sa.Column('event_type',sa.String(100),nullable=False),sa.Column('provider_object_id',sa.String(255),nullable=False),sa.Column('payment_external_id',sa.String(255)),sa.Column('public_order_id',sa.String(64)),sa.Column('payload',postgresql.JSONB(),nullable=False),sa.Column('status',sa.String(20),nullable=False,server_default='pending'),sa.Column('attempts',sa.Integer(),nullable=False,server_default='0'),sa.Column('max_attempts',sa.Integer(),nullable=False,server_default='30'),sa.Column('next_attempt_at',sa.DateTime(timezone=True),nullable=False,server_default=sa.func.now()),sa.Column('locked_at',sa.DateTime(timezone=True)),sa.Column('locked_by',sa.String(100)),sa.Column('last_error_code',sa.String(100)),sa.Column('last_error',sa.Text()),sa.Column('received_at',sa.DateTime(timezone=True),nullable=False,server_default=sa.func.now()),sa.Column('processed_at',sa.DateTime(timezone=True)),sa.UniqueConstraint('provider','event_key',name='uq_webhook_inbox_provider_event_key'),sa.CheckConstraint("status IN ('pending','processing','retry','succeeded','dead')",name='ck_webhook_inbox_status'))
    op.create_index('ix_webhook_inbox_claim','webhook_inbox',['next_attempt_at','id'],postgresql_where=sa.text("status IN ('pending','retry')")); op.create_index('ix_webhook_inbox_lease','webhook_inbox',['locked_at'],postgresql_where=sa.text("status='processing'")); op.create_index('ix_webhook_inbox_payment_external_id','webhook_inbox',['payment_external_id']); op.create_index('ix_webhook_inbox_public_order_id','webhook_inbox',['public_order_id'])
    op.create_table('entitlement_entries',sa.Column('id',sa.BigInteger(),primary_key=True),sa.Column('beneficiary_user_id',sa.Integer(),sa.ForeignKey('users.id',ondelete='RESTRICT'),nullable=False),sa.Column('source_type',sa.String(30),nullable=False),sa.Column('source_id',sa.String(100),nullable=False),sa.Column('entry_type',sa.String(40),nullable=False),sa.Column('days_delta',sa.Integer(),nullable=False),sa.Column('device_limit_snapshot',sa.Integer()),sa.Column('tariff_id_snapshot',sa.Integer()),sa.Column('metadata',postgresql.JSONB(),nullable=False,server_default='{}'),sa.Column('reversed_entry_id',sa.BigInteger(),sa.ForeignKey('entitlement_entries.id',ondelete='RESTRICT')),sa.Column('created_at',sa.DateTime(timezone=True),nullable=False,server_default=sa.func.now()),sa.UniqueConstraint('beneficiary_user_id','source_type','source_id','entry_type',name='uq_entitlement_entries_source'),sa.CheckConstraint("entry_type IN ('payment_grant','referral_user_bonus','referral_referrer_bonus','payment_reversal','referral_reversal','manual_grant')",name='ck_entitlement_entries_type'))
    op.create_index('ix_entitlement_entries_beneficiary_user_id','entitlement_entries',['beneficiary_user_id'])

def downgrade():
    op.drop_table('entitlement_entries'); op.drop_table('webhook_inbox'); op.drop_table('payment_fulfillment_operations'); op.drop_table('payment_provider_operations')
    op.drop_constraint('payments_user_id_fkey','payments',type_='foreignkey'); op.create_foreign_key('payments_user_id_fkey','payments','users',['user_id'],['id'],ondelete='CASCADE')
    for n in ['uq_payments_provider_idempotency_key_not_null','uq_payments_public_order_id_not_null']: op.drop_index(n,table_name='payments')
    for n in ['ck_payments_reconciliation_status','ck_payments_fulfillment_status','ck_payments_provider_status']: op.drop_constraint(n,'payments',type_='check')
    for n in ['fulfillment_last_error','fulfillment_last_error_code','provider_last_error','provider_last_error_code','last_reconciled_at','reversed_at','fulfilled_at','provider_confirmed_at','reconciliation_status','fulfillment_status','provider_status','provider_idempotency_key','public_order_id']: op.drop_column('payments',n)
