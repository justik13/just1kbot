import os, unittest, uuid
from datetime import timedelta
from decimal import Decimal
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from database.models import (EntitlementEntry, Payment, PaymentFulfillmentOperation, PaymentProviderOperation, PaymentRefund, ReferralReward, Tariff, User, WebhookInbox)
from services.payment_provider_operations import (PaymentProviderOperationOwnershipError, ProviderOperationClaim, claim, ensure_reconcile_payment_operation, finalize, recover_stale, retry_dead_provider_operation)
from services.workers.webhook_inbox import InboxClaim, finalize_webhook_failure, retry_dead_webhook_operation
from services.payment_fulfillment import FulfillmentClaim, finalize_fulfillment_failure, retry_dead_fulfillment_operation
from services.yookassa_service import YooKassaResult
from utils.datetime_helpers import now_utc
DB=os.getenv("TEST_DATABASE_URL")
@unittest.skipUnless(DB,"TEST_DATABASE_URL is not set")
class PaymentPipelinePostgresTests(unittest.IsolatedAsyncioTestCase):
 async def asyncSetUp(self):
  self.engine=create_async_engine(DB); self.sessions=async_sessionmaker(self.engine,expire_on_commit=False)
  async with self.sessions.begin() as s:
   for model in (ReferralReward,EntitlementEntry,PaymentRefund,WebhookInbox,PaymentFulfillmentOperation,PaymentProviderOperation,Payment,User,Tariff): await s.execute(delete(model))
   tariff=Tariff(name="T",duration_days=30,device_limit=2,price_rub=90,is_active=True); user=User(telegram_id=900000+uuid.uuid4().int%99999); s.add_all([tariff,user]); await s.flush(); self.tariff_id=tariff.id; self.user_id=user.id
 async def asyncTearDown(self): await self.engine.dispose()
 async def payment(self,s,**kw):
  values=dict(user_id=self.user_id,tariff_id=self.tariff_id,amount=Decimal("90"),currency="RUB",status="pending",public_order_id="pay_"+uuid.uuid4().hex,provider_idempotency_key=uuid.uuid4().hex,provider_status="pending",fulfillment_status="not_ready",reconciliation_status="ok",snapshot_amount=Decimal("90"),snapshot_currency="RUB",snapshot_duration_days=30,snapshot_device_limit=2,external_id="provider_"+uuid.uuid4().hex)
  values.update(kw); p=Payment(**values); s.add(p); await s.flush(); return p
 def snapshot(self,p,status="succeeded",**kw):
  data={"id":p.external_id,"status":status,"amount":{"value":"90.00","currency":"RUB"},"metadata":{"order_id":p.public_order_id,"local_payment_id":str(p.id)}}; data.update(kw); return data
 async def test_reconcile_can_run_more_than_once(self):
  async with self.sessions.begin() as s: p=await self.payment(s); first=await ensure_reconcile_payment_operation(s,p,reason="one"); first.status="succeeded"; first.completed_at=now_utc(); second=await ensure_reconcile_payment_operation(s,p,reason="two"); self.assertNotEqual(first.id,second.id)
 async def test_concurrent_reconcile_has_one_active_operation(self):
  async with self.sessions.begin() as s: p=await self.payment(s); pid=p.id
  async def enqueue():
   async with self.sessions.begin() as s: p=await s.get(Payment,pid); return (await ensure_reconcile_payment_operation(s,p,reason="race")).id
  ids=await __import__('asyncio').gather(enqueue(),enqueue()); self.assertEqual(ids[0],ids[1])
 async def test_provider_success_atomically_enqueues_grant(self):
  async with self.sessions.begin() as s:
   p=await self.payment(s); op=PaymentProviderOperation(payment_id=p.id,operation_type="reconcile_payment",status="processing",idempotency_key=uuid.uuid4().hex,payload={"provider_payment_id":p.external_id},attempts=1,max_attempts=3,next_attempt_at=now_utc(),locked_by="w",locked_at=now_utc()); s.add(op); await s.flush(); claim=ProviderOperationClaim(op.id,p.id,op.operation_type,op.payload,op.idempotency_key,"w",1,p.external_id); await finalize(s,claim,YooKassaResult(True,value=self.snapshot(p)))
  async with self.sessions() as s: self.assertEqual((await s.get(Payment,p.id)).provider_status,"succeeded"); self.assertIsNotNone(await s.scalar(select(PaymentFulfillmentOperation).where(PaymentFulfillmentOperation.idempotency_key==f"payment-grant:{p.id}")))
 async def test_provider_snapshot_mismatches_never_grant(self):
  for field in ("amount","currency","order","external"):
   async with self.sessions.begin() as s:
    p=await self.payment(s); op=PaymentProviderOperation(payment_id=p.id,operation_type="reconcile_payment",status="processing",idempotency_key=uuid.uuid4().hex,payload={},attempts=1,max_attempts=3,next_attempt_at=now_utc(),locked_by="w",locked_at=now_utc()); s.add(op); await s.flush(); data=self.snapshot(p)
    if field=="amount":data["amount"]["value"]="91.00"
    if field=="currency":data["amount"]["currency"]="USD"
    if field=="order":data["metadata"]["order_id"]="wrong"
    if field=="external":data["id"]="wrong"
    await finalize(s,ProviderOperationClaim(op.id,p.id,op.operation_type,op.payload,op.idempotency_key,"w",1,p.external_id),YooKassaResult(True,value=data)); self.assertEqual(p.reconciliation_status,"mismatch"); self.assertIsNone(await s.scalar(select(PaymentFulfillmentOperation).where(PaymentFulfillmentOperation.payment_id==p.id)))
 async def test_provider_attempt_fencing_and_dead_restart(self):
  async with self.sessions.begin() as s:
   p=await self.payment(s); op=PaymentProviderOperation(payment_id=p.id,operation_type="reconcile_payment",status="pending",idempotency_key=uuid.uuid4().hex,payload={},attempts=0,max_attempts=1,next_attempt_at=now_utc()); s.add(op); await s.flush(); c=await claim(s,"same"); oid=op.id
  async with self.sessions.begin() as s: op=await s.get(PaymentProviderOperation,oid); op.locked_at=now_utc()-timedelta(hours=1); await recover_stale(s,0); self.assertEqual(op.status,"dead"); await retry_dead_provider_operation(s,oid,reset_attempts=True,reason="admin"); self.assertEqual(op.attempts,0)
  async with self.sessions.begin() as s:
   with self.assertRaises(PaymentProviderOperationOwnershipError): await finalize(s,c,YooKassaResult(True,value={}))
 async def test_all_queue_failure_finalizers_dead_at_limit_and_restart(self):
  async with self.sessions.begin() as s:
   p=await self.payment(s); f=PaymentFulfillmentOperation(payment_id=p.id,operation_type="grant_subscription",idempotency_key=uuid.uuid4().hex,status="processing",payload={},attempts=1,max_attempts=1,next_attempt_at=now_utc(),locked_by="w",locked_at=now_utc()); w=WebhookInbox(provider="yookassa",event_key=uuid.uuid4().hex,event_type="payment.succeeded",provider_object_id="x",payment_external_id=p.external_id,payload={},status="processing",attempts=1,max_attempts=1,next_attempt_at=now_utc(),locked_by="w",locked_at=now_utc()); s.add_all([f,w]); await s.flush(); await finalize_fulfillment_failure(s,FulfillmentClaim(f.id,"w",1,f.operation_type),error_code="x"); await finalize_webhook_failure(s,InboxClaim(w.id,"w",1,w.event_type,w.payment_external_id,None,w.payload,w.event_key),error_code="x"); self.assertEqual((f.status,w.status),("dead","dead")); await retry_dead_fulfillment_operation(s,f.id,reset_attempts=True,reason="admin"); await retry_dead_webhook_operation(s,w.id,reset_attempts=True,reason="admin"); self.assertEqual((f.attempts,w.attempts),(0,0))

@unittest.skipUnless(DB,"TEST_DATABASE_URL is not set")
class LegacyPaymentMigrationPostgresTests(unittest.IsolatedAsyncioTestCase):
 async def test_legacy_completed_payment_is_backfilled_without_extending(self):
  import asyncio
  from alembic.command import downgrade, upgrade
  from alembic.config import Config
  cfg=Config("alembic.ini"); cfg.set_main_option("sqlalchemy.url",DB)
  await asyncio.to_thread(downgrade,cfg,"-1")
  engine=create_async_engine(DB); sessions=async_sessionmaker(engine,expire_on_commit=False)
  async with sessions.begin() as s:
   await s.execute(__import__('sqlalchemy').text("DELETE FROM referral_rewards"))
   for model in (EntitlementEntry,PaymentRefund,WebhookInbox,PaymentFulfillmentOperation,PaymentProviderOperation,Payment,User,Tariff): await s.execute(delete(model))
   t=Tariff(name="Legacy",duration_days=30,device_limit=2,price_rub=90,is_active=True); u=User(telegram_id=777001,subscription_end=now_utc()+timedelta(days=30),device_limit=2); s.add_all([t,u]); await s.flush(); original_end=u.subscription_end
   p=Payment(user_id=u.id,tariff_id=t.id,amount=Decimal("90"),currency="RUB",status="completed",provider_status="succeeded",fulfillment_status="succeeded",reconciliation_status="ok",checkout_status="active",snapshot_duration_days=30,snapshot_device_limit=2,snapshot_amount=Decimal("90"),snapshot_currency="RUB",paid_at=now_utc()); s.add(p); await s.flush(); pid=p.id; uid=u.id
  await engine.dispose(); await asyncio.to_thread(upgrade,cfg,"head")
  engine=create_async_engine(DB); sessions=async_sessionmaker(engine,expire_on_commit=False)
  async with sessions() as s:
   entry=await s.scalar(select(EntitlementEntry).where(EntitlementEntry.source_id==str(pid),EntitlementEntry.entry_type=="payment_grant")); user=await s.get(User,uid); self.assertIsNotNone(entry); self.assertEqual(user.subscription_end,original_end)
  await engine.dispose()
