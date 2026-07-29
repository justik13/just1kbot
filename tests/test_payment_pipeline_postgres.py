import os, unittest, uuid
from datetime import timedelta
from decimal import Decimal
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from database.models import (EntitlementEntry, Payment, PaymentEvent, PaymentFulfillmentOperation, PaymentProviderOperation, PaymentRefund, ReferralEligibility, ReferralReward, Tariff, User, WebhookInbox)
from services.payment_queue_health import get_payment_queue_health_snapshot
from services.payment_provider_operations import (PaymentProviderOperationOwnershipError, ProviderOperationClaim, claim, ensure_reconcile_payment_operation, finalize, finalize_provider_failure, perform_http, recover_stale, retry_dead_provider_operation)
from services.workers.webhook_inbox import InboxClaim, finalize_webhook_failure, retry_dead_webhook_operation
from services.payment_fulfillment import FulfillmentClaim, finalize_fulfillment_failure, referral, reverse, retry_dead_fulfillment_operation
from services.yookassa_service import YooKassaResult
from services.payment_service import PaymentService
from utils.datetime_helpers import now_utc
DB=os.getenv("TEST_DATABASE_URL")
@unittest.skipUnless(DB,"TEST_DATABASE_URL is not set")
class PaymentPipelinePostgresTests(unittest.IsolatedAsyncioTestCase):
 async def asyncSetUp(self):
  self.engine=create_async_engine(DB); self.sessions=async_sessionmaker(self.engine,expire_on_commit=False)
  async with self.sessions.begin() as s:
   for model in (ReferralEligibility,ReferralReward,EntitlementEntry,PaymentRefund,WebhookInbox,PaymentFulfillmentOperation,PaymentProviderOperation,Payment,User,Tariff): await s.execute(delete(model))
   tariff=Tariff(name="T",duration_days=30,device_limit=2,price_rub=90,is_active=True); user=User(telegram_id=900000+uuid.uuid4().int%99999); s.add_all([tariff,user]); await s.flush(); self.tariff_id=tariff.id; self.user_id=user.id
 async def asyncTearDown(self): await self.engine.dispose()
 async def payment(self,s,**kw):
  values=dict(user_id=self.user_id,tariff_id=self.tariff_id,amount=Decimal("90"),currency="RUB",status="pending",public_order_id="pay_"+uuid.uuid4().hex,provider_idempotency_key=uuid.uuid4().hex,provider_status="pending",fulfillment_status="not_ready",reconciliation_status="ok",snapshot_amount=Decimal("90"),snapshot_currency="RUB",snapshot_duration_days=30,snapshot_device_limit=2,external_id="provider_"+uuid.uuid4().hex)
  values.update(kw); p=Payment(**values); s.add(p); await s.flush(); return p
 def snapshot(self,p,status="succeeded",**kw):
  data={"id":p.external_id,"status":status,"amount":{"value":"90.00","currency":"RUB"},"metadata":{"order_id":p.public_order_id,"local_payment_id":str(p.id)}}; data.update(kw); return data
 async def cancel_claim(self,s,p,*,attempts=1,max_attempts=3,key=None):
  op=PaymentProviderOperation(payment_id=p.id,operation_type="cancel_payment",status="processing",idempotency_key=key or uuid.uuid4().hex,payload={"provider_payment_id":p.external_id},attempts=attempts,max_attempts=max_attempts,next_attempt_at=now_utc(),locked_by="w",locked_at=now_utc()); s.add(op); await s.flush()
  return op,ProviderOperationClaim(op.id,p.id,op.operation_type,dict(op.payload),op.idempotency_key,"w",attempts,p.external_id,op.created_at)

 async def test_queue_health_classifies_real_durable_tables(self):
  from services.payment_queue_health import get_payment_queue_health_snapshot
  now=now_utc()
  async with self.sessions.begin() as s:
   p=await self.payment(s)
   for index,(status,next_at,locked_at) in enumerate((("pending",now+timedelta(hours=1),None),("retry",now-timedelta(minutes=2),None),("processing",now,now-timedelta(minutes=3)),("dead",now,None))):
    s.add(PaymentProviderOperation(payment_id=p.id,operation_type="reconcile_payment",status=status,idempotency_key=uuid.uuid4().hex,payload={"SECRET_CANARY":"hidden"},attempts=1,max_attempts=3,next_attempt_at=next_at,locked_at=locked_at,locked_by="worker" if locked_at else None,last_error_code="safe_code",last_error="SECRET_CANARY"))
    s.add(PaymentFulfillmentOperation(payment_id=p.id,operation_type="grant_subscription",status=status,idempotency_key=uuid.uuid4().hex,payload={"SECRET_CANARY":"hidden"},attempts=1,max_attempts=3,next_attempt_at=next_at,locked_at=locked_at,locked_by="worker" if locked_at else None,last_error_code="safe_code",last_error="SECRET_CANARY"))
    s.add(WebhookInbox(provider="yookassa",event_key=uuid.uuid4().hex,event_type="payment.succeeded",provider_object_id=uuid.uuid4().hex,payment_external_id=p.external_id,payload={"SECRET_CANARY":"hidden"},status=status,attempts=1,max_attempts=3,next_attempt_at=next_at,locked_at=locked_at,locked_by="worker" if locked_at else None,last_error_code="safe_code",last_error="SECRET_CANARY"))
   await s.flush()
   health=await get_payment_queue_health_snapshot(s,clock=lambda:now)
   self.assertEqual([q.name for q in health.queues],["provider_operations","fulfillment_operations","webhook_inbox"])
   for q in health.queues:
    self.assertEqual((q.pending,q.retry,q.due,q.overdue,q.processing,q.stale_processing,q.dead),(1,1,1,1,1,1,1))
    self.assertFalse(q.healthy); self.assertLessEqual(len(q.examples),5)
    self.assertTrue(all(example.last_error_code=="safe_code" for example in q.examples))
   rendered=repr(health)
   self.assertNotIn("SECRET_CANARY",rendered); self.assertNotIn("payload",rendered); self.assertNotIn("last_error=",rendered)

 async def _assert_processing_lease_health(self,queue_name,rows,now):
  async with self.sessions.begin() as s:
   s.add_all(rows); await s.flush()
   health=await get_payment_queue_health_snapshot(s,clock=lambda:now)
   queue=next(item for item in health.queues if item.name==queue_name)
   self.assertEqual((queue.processing,queue.stale_processing),(4,3))
   self.assertFalse(queue.healthy)
   self.assertEqual(3,len(queue.examples))
   self.assertTrue(all(item.status=="processing" for item in queue.examples))

 async def test_provider_processing_lease_shapes(self):
  from services.payment_queue_health import get_payment_queue_health_snapshot
  now=now_utc()
  async with self.sessions.begin() as s: p=await self.payment(s); payment_id=p.id
  def row(locked_at,locked_by):
   return PaymentProviderOperation(payment_id=payment_id,operation_type="reconcile_payment",status="processing",idempotency_key=uuid.uuid4().hex,payload={},next_attempt_at=now,locked_at=locked_at,locked_by=locked_by)
  await self._assert_processing_lease_health("provider_operations",[row(None,None),row(now,None),row(now,"worker"),row(now-timedelta(minutes=3),"worker")],now)

 async def test_fulfillment_processing_lease_shapes(self):
  from services.payment_queue_health import get_payment_queue_health_snapshot
  now=now_utc()
  async with self.sessions.begin() as s: p=await self.payment(s); payment_id=p.id
  def row(locked_at,locked_by):
   return PaymentFulfillmentOperation(payment_id=payment_id,operation_type="grant_subscription",status="processing",idempotency_key=uuid.uuid4().hex,payload={},next_attempt_at=now,locked_at=locked_at,locked_by=locked_by)
  await self._assert_processing_lease_health("fulfillment_operations",[row(None,None),row(now,None),row(now,"worker"),row(now-timedelta(minutes=3),"worker")],now)

 async def test_webhook_processing_lease_shapes(self):
  from services.payment_queue_health import get_payment_queue_health_snapshot
  now=now_utc()
  def row(locked_at,locked_by):
   return WebhookInbox(provider="yookassa",event_key=uuid.uuid4().hex,event_type="payment.succeeded",provider_object_id=uuid.uuid4().hex,payload={},status="processing",next_attempt_at=now,locked_at=locked_at,locked_by=locked_by)
  await self._assert_processing_lease_health("webhook_inbox",[row(None,None),row(now,None),row(now,"worker"),row(now-timedelta(minutes=3),"worker")],now)

 async def test_dead_age_uses_recent_terminal_timestamp(self):
  from services.payment_queue_health import get_payment_queue_health_snapshot
  now=now_utc(); old=now-timedelta(days=10); terminal=now-timedelta(seconds=20)
  async with self.sessions.begin() as s:
   p=await self.payment(s)
   s.add(PaymentProviderOperation(payment_id=p.id,operation_type="reconcile_payment",status="dead",idempotency_key=uuid.uuid4().hex,payload={},next_attempt_at=old,created_at=old,updated_at=old,completed_at=terminal))
   s.add(PaymentFulfillmentOperation(payment_id=p.id,operation_type="grant_subscription",status="dead",idempotency_key=uuid.uuid4().hex,payload={},next_attempt_at=old,created_at=old,updated_at=old,completed_at=terminal))
   s.add(WebhookInbox(provider="yookassa",event_key=uuid.uuid4().hex,event_type="payment.succeeded",provider_object_id=uuid.uuid4().hex,payload={},status="dead",next_attempt_at=old,received_at=old,processed_at=terminal))
   await s.flush(); health=await get_payment_queue_health_snapshot(s,clock=lambda:now)
   for queue in health.queues:
    self.assertEqual(20,queue.oldest_dead_age_seconds)
    self.assertEqual(20,queue.examples[0].age_seconds)

 async def test_cancel_waiting_for_capture_is_not_final_success(self):
  async with self.sessions.begin() as s:
   p=await self.payment(s,provider_status="waiting_for_capture"); op,c=await self.cancel_claim(s,p); await finalize(s,c,YooKassaResult(True,value=self.snapshot(p,status="waiting_for_capture")))
   self.assertEqual((op.status,op.last_error_code,p.provider_status),("retry","cancel_not_confirmed","waiting_for_capture")); self.assertIsNone(op.completed_at)

 async def test_cancel_retry_uses_same_key(self):
  from unittest.mock import AsyncMock
  async with self.sessions.begin() as s:
   p=await self.payment(s,provider_status="waiting_for_capture"); op,c=await self.cancel_claim(s,p,key="stable-cancel-key"); await finalize(s,c,YooKassaResult(True,value=self.snapshot(p,status="waiting_for_capture"))); op.next_attempt_at=now_utc(); c2=await claim(s,"w2")
   transport=type("Transport",(),{"cancel_payment_result":AsyncMock(return_value=YooKassaResult(True,value=self.snapshot(p,status="canceled")))})
   result=await perform_http(c2,transport); await finalize(s,c2,result)
   self.assertEqual(transport.cancel_payment_result.await_args.kwargs["idempotency_key"],"stable-cancel-key"); self.assertEqual((op.status,p.provider_status),("succeeded","canceled"))

 async def test_cancel_late_succeeded_records_paid_manual_review(self):
  async with self.sessions.begin() as s:
   p=await self.payment(s,provider_status="waiting_for_capture"); op,c=await self.cancel_claim(s,p); await finalize(s,c,YooKassaResult(True,value=self.snapshot(p)))
   self.assertEqual((op.status,p.provider_status,p.reconciliation_status,p.fulfillment_status),("succeeded","succeeded","mismatch","manual_review")); self.assertIsNotNone(p.paid_at); self.assertIsNotNone(p.provider_confirmed_at); self.assertIsNone(await s.scalar(select(PaymentFulfillmentOperation).where(PaymentFulfillmentOperation.payment_id==p.id)))

 async def test_provider_mismatch_preserves_paid_at(self):
  for field in ("amount","currency","order_id","local_payment_id","external_id"):
   async with self.sessions.begin() as s:
    p=await self.payment(s); op=PaymentProviderOperation(payment_id=p.id,operation_type="reconcile_payment",status="processing",idempotency_key=uuid.uuid4().hex,payload={},attempts=1,max_attempts=3,next_attempt_at=now_utc(),locked_by="w",locked_at=now_utc()); s.add(op); await s.flush(); data=self.snapshot(p)
    if field=="amount": data["amount"]["value"]="91.00"
    elif field=="currency": data["amount"]["currency"]="USD"
    elif field in {"order_id","local_payment_id"}: data["metadata"][field]="wrong"
    else: data["id"]="wrong"
    await finalize(s,ProviderOperationClaim(op.id,p.id,op.operation_type,{},op.idempotency_key,"w",1,p.external_id,op.created_at),YooKassaResult(True,value=data))
    self.assertEqual((p.provider_status,p.reconciliation_status,p.fulfillment_status),("succeeded","mismatch","manual_review")); self.assertIsNotNone(p.paid_at); self.assertIsNotNone(p.provider_confirmed_at); self.assertIsNone(await s.scalar(select(PaymentFulfillmentOperation).where(PaymentFulfillmentOperation.payment_id==p.id)))

 async def test_refunded_terminal_state_is_monotonic(self):
  async with self.sessions.begin() as s:
   p=await self.payment(s,provider_status="refunded",fulfillment_status="reversed"); op=PaymentProviderOperation(payment_id=p.id,operation_type="reconcile_payment",status="processing",idempotency_key=uuid.uuid4().hex,payload={},attempts=1,max_attempts=3,next_attempt_at=now_utc(),locked_by="w",locked_at=now_utc()); s.add(op); await s.flush(); data=self.snapshot(p); data["amount"]["value"]="1.00"; await finalize(s,ProviderOperationClaim(op.id,p.id,op.operation_type,{},op.idempotency_key,"w",1,p.external_id,op.created_at),YooKassaResult(True,value=data)); self.assertEqual((p.provider_status,p.fulfillment_status),("refunded","reversed")); self.assertIsNone(await s.scalar(select(PaymentFulfillmentOperation).where(PaymentFulfillmentOperation.payment_id==p.id)))

 async def test_dead_create_retry_keeps_immutable_payload(self):
  async with self.sessions.begin() as s:
   p=await self.payment(s); payload={"amount":{"value":"90.00","currency":"RUB"},"description":"x","confirmation":{"type":"redirect"},"metadata":{"order_id":p.public_order_id,"local_payment_id":str(p.id)},"capture":True}; op=PaymentProviderOperation(payment_id=p.id,operation_type="create_payment",status="dead",idempotency_key="stable-create-key",payload=payload,attempts=3,max_attempts=3,next_attempt_at=now_utc(),completed_at=now_utc()); s.add(op); await s.flush(); before=dict(op.payload); await retry_dead_provider_operation(s,op.id,reset_attempts=True,reason="operator approved"); self.assertEqual(op.payload,before); self.assertEqual(op.idempotency_key,"stable-create-key"); self.assertIsNotNone(await s.scalar(select(PaymentEvent).where(PaymentEvent.payment_id==p.id,PaymentEvent.reason=="operator approved")))

 async def test_create_invalid_2xx_retries_same_command(self):
  from unittest.mock import AsyncMock
  async with self.sessions.begin() as s:
   p=await self.payment(s,external_id=None,provider_status="not_created"); payload={"amount":{"value":"90.00","currency":"RUB"},"description":"x","confirmation":{"type":"redirect","return_url":"https://example.test"},"metadata":{"order_id":p.public_order_id,"local_payment_id":str(p.id)},"capture":True}; op=PaymentProviderOperation(payment_id=p.id,operation_type="create_payment",status="processing",idempotency_key="stable-create-key-2",payload=payload,attempts=1,max_attempts=3,next_attempt_at=now_utc(),locked_by="w",locked_at=now_utc()); s.add(op); await s.flush(); c=ProviderOperationClaim(op.id,p.id,op.operation_type,dict(payload),op.idempotency_key,"w",1,None,op.created_at); await finalize(s,c,YooKassaResult(False,error_kind=__import__('services.yookassa_service',fromlist=['YooKassaErrorKind']).YooKassaErrorKind.INVALID_RESPONSE,retryable=True,ambiguous=True)); op.next_attempt_at=now_utc(); c2=await claim(s,"w2"); transport=type("Transport",(),{"create_payment_result":AsyncMock(return_value=YooKassaResult(True,value={"id":"only-provider-payment","status":"pending","confirmation":{"confirmation_url":"https://pay.test"}}))}); result=await perform_http(c2,transport); await finalize(s,c2,result); args=transport.create_payment_result.await_args; self.assertEqual(args.args[0],payload); self.assertEqual(args.kwargs["idempotency_key"],"stable-create-key-2"); self.assertEqual(p.external_id,"only-provider-payment")

 async def test_expired_create_retry_rejection_is_committed(self):
  async with self.sessions.begin() as s:
   p=await self.payment(s,external_id=None); payload={"amount":{"value":"90.00","currency":"RUB"},"capture":True}; op=PaymentProviderOperation(payment_id=p.id,operation_type="create_payment",status="dead",idempotency_key="expired-key",payload=payload,attempts=12,max_attempts=12,next_attempt_at=now_utc(),completed_at=now_utc(),created_at=now_utc()-timedelta(hours=25)); s.add(op); await s.flush(); pid=p.id; oid=op.id; decision=await retry_dead_provider_operation(s,oid,reset_attempts=True,reason="admin"); self.assertFalse(decision.accepted)
  async with self.sessions() as s:
   p=await s.get(Payment,pid); op=await s.get(PaymentProviderOperation,oid); event=await s.scalar(select(PaymentEvent).where(PaymentEvent.payment_id==pid,PaymentEvent.event_type=="provider_operation_admin_retry_rejected")); self.assertEqual((p.reconciliation_status,p.fulfillment_status,p.manual_review_reason),("manual_review","manual_review","create_idempotency_window_expired")); self.assertIsNotNone(event); self.assertEqual((op.status,op.attempts,op.payload,op.idempotency_key),("dead",12,payload,"expired-key"))

 async def test_expired_create_with_external_id_can_retry_as_get(self):
  from unittest.mock import AsyncMock
  async with self.sessions.begin() as s:
   p=await self.payment(s); op=PaymentProviderOperation(payment_id=p.id,operation_type="create_payment",status="dead",idempotency_key="expired-known-key",payload={"capture":True},attempts=12,max_attempts=12,next_attempt_at=now_utc(),completed_at=now_utc(),created_at=now_utc()-timedelta(hours=25)); s.add(op); await s.flush(); decision=await retry_dead_provider_operation(s,op.id,reset_attempts=True,reason="admin"); self.assertTrue(decision.accepted); c=await claim(s,"w"); transport=type("Transport",(),{"create_payment_result":AsyncMock(),"get_payment_result":AsyncMock(return_value=YooKassaResult(True,value=self.snapshot(p))) }); await perform_http(c,transport); transport.get_payment_result.assert_awaited_once_with(p.external_id); transport.create_payment_result.assert_not_awaited()

 async def test_cancel_attempt_limit_preserves_waiting_for_capture(self):
  for observed in ("waiting_for_capture","pending"):
   async with self.sessions.begin() as s:
    p=await self.payment(s,provider_status="waiting_for_capture"); op,c=await self.cancel_claim(s,p,attempts=3,max_attempts=3); await finalize(s,c,YooKassaResult(True,value=self.snapshot(p,status=observed))); self.assertEqual((op.status,p.provider_status,p.reconciliation_status),("dead",observed,"manual_review")); self.assertEqual(p.fulfillment_status,"not_ready"); self.assertIsNone(p.paid_at); self.assertIsNotNone(await s.scalar(select(PaymentEvent).where(PaymentEvent.payment_id==p.id,PaymentEvent.event_type=="cancel_not_confirmed_at_attempt_limit")))

 async def _create_invalid_status_then_get(self,status_marker):
  from unittest.mock import AsyncMock
  async with self.sessions.begin() as s:
   p=await self.payment(s,external_id=None,provider_status="not_created"); payload={"amount":{"value":"90.00","currency":"RUB"},"capture":True}; op=PaymentProviderOperation(payment_id=p.id,operation_type="create_payment",status="processing",idempotency_key=uuid.uuid4().hex,payload=payload,attempts=1,max_attempts=3,next_attempt_at=now_utc(),locked_by="w",locked_at=now_utc()); s.add(op); await s.flush(); data={"id":"saved-provider-id","confirmation":{"confirmation_url":"https://pay.example"}};
   if status_marker is not None:data["status"]=status_marker
   await finalize(s,ProviderOperationClaim(op.id,p.id,"create_payment",payload,op.idempotency_key,"w",1,None,op.created_at),YooKassaResult(True,value=data)); self.assertEqual((op.status,p.external_id,p.reconciliation_status),("retry","saved-provider-id","required")); op.next_attempt_at=now_utc(); c=await claim(s,"w2"); transport=type("Transport",(),{"create_payment_result":AsyncMock(),"get_payment_result":AsyncMock(return_value=YooKassaResult(True,value={"id":"saved-provider-id","status":"pending"}))}); await perform_http(c,transport); transport.get_payment_result.assert_awaited_once_with("saved-provider-id"); transport.create_payment_result.assert_not_awaited()
 async def test_create_2xx_dict_missing_status_saves_id_and_retries_with_get(self): await self._create_invalid_status_then_get(None)
 async def test_create_2xx_dict_unknown_status_saves_id_and_retries_with_get(self): await self._create_invalid_status_then_get("mystery")

 async def test_create_2xx_dict_missing_status_without_id_retries_same_post(self):
  from unittest.mock import AsyncMock
  async with self.sessions.begin() as s:
   p=await self.payment(s,external_id=None); payload={"amount":{"value":"90.00","currency":"RUB"},"capture":True}; op=PaymentProviderOperation(payment_id=p.id,operation_type="create_payment",status="processing",idempotency_key="same-key",payload=payload,attempts=1,max_attempts=3,next_attempt_at=now_utc(),locked_by="w",locked_at=now_utc()); s.add(op); await s.flush(); await finalize(s,ProviderOperationClaim(op.id,p.id,"create_payment",payload,"same-key","w",1,None,op.created_at),YooKassaResult(True,value={"confirmation":{"confirmation_url":"https://pay.example"}})); self.assertEqual(op.status,"retry"); op.next_attempt_at=now_utc(); c=await claim(s,"w2"); transport=type("Transport",(),{"create_payment_result":AsyncMock(return_value=YooKassaResult(True,value={"id":"one","status":"pending","confirmation":{"confirmation_url":"https://pay.example"}}))}); await perform_http(c,transport); args=transport.create_payment_result.await_args; self.assertEqual(args.args[0],payload); self.assertEqual(args.kwargs["idempotency_key"],"same-key")

 async def test_reconcile_2xx_dict_missing_status_is_retryable(self):
  async with self.sessions.begin() as s:
   p=await self.payment(s,provider_status="pending"); op=PaymentProviderOperation(payment_id=p.id,operation_type="reconcile_payment",status="processing",idempotency_key=uuid.uuid4().hex,payload={},attempts=1,max_attempts=3,next_attempt_at=now_utc(),locked_by="w",locked_at=now_utc()); s.add(op); await s.flush(); await finalize(s,ProviderOperationClaim(op.id,p.id,"reconcile_payment",{},op.idempotency_key,"w",1,p.external_id,op.created_at),YooKassaResult(True,value={"id":p.external_id})); self.assertEqual((op.status,p.provider_status,p.reconciliation_status),("retry","pending","required"))

 async def test_cancel_get_dict_missing_status_is_not_success(self):
  async with self.sessions.begin() as s:
   p=await self.payment(s,provider_status="waiting_for_capture"); op,c=await self.cancel_claim(s,p); await finalize(s,c,YooKassaResult(True,value={"id":p.external_id})); self.assertEqual((op.status,p.provider_status,p.reconciliation_status),("retry","waiting_for_capture","required"))

 async def test_expired_create_retry_projects_legacy_manual_review(self):
  async with self.sessions.begin() as s:
   p=await self.payment(s,external_id=None); op=PaymentProviderOperation(payment_id=p.id,operation_type="create_payment",status="dead",idempotency_key="legacy-expired",payload={"capture":True},attempts=4,max_attempts=4,next_attempt_at=now_utc(),completed_at=now_utc(),created_at=now_utc()-timedelta(hours=25)); s.add(op); await s.flush(); pid=p.id; await retry_dead_provider_operation(s,op.id,reset_attempts=True,reason="admin")
  async with self.sessions() as s:
   p=await s.get(Payment,pid); self.assertEqual((p.status,p.reconciliation_status,p.fulfillment_status,p.manual_review_reason),("requires_manual_review","manual_review","manual_review","create_idempotency_window_expired"))
 async def test_reconcile_can_run_more_than_once(self):
  async with self.sessions.begin() as s: p=await self.payment(s); first=await ensure_reconcile_payment_operation(s,p,reason="one"); first.status="succeeded"; first.completed_at=now_utc(); second=await ensure_reconcile_payment_operation(s,p,reason="two"); self.assertNotEqual(first.id,second.id)
 async def test_concurrent_reconcile_has_one_active_operation(self):
  async with self.sessions.begin() as s: p=await self.payment(s); pid=p.id
  async def enqueue():
   async with self.sessions.begin() as s: p=await s.get(Payment,pid); return (await ensure_reconcile_payment_operation(s,p,reason="race")).id
  ids=await __import__('asyncio').gather(enqueue(),enqueue()); self.assertEqual(ids[0],ids[1])
 async def test_provider_success_atomically_enqueues_grant(self):
  async with self.sessions.begin() as s:
   p=await self.payment(s); op=PaymentProviderOperation(payment_id=p.id,operation_type="reconcile_payment",status="processing",idempotency_key=uuid.uuid4().hex,payload={"provider_payment_id":p.external_id},attempts=1,max_attempts=3,next_attempt_at=now_utc(),locked_by="w",locked_at=now_utc()); s.add(op); await s.flush(); claim=ProviderOperationClaim(op.id,p.id,op.operation_type,op.payload,op.idempotency_key,"w",1,p.external_id,op.created_at); await finalize(s,claim,YooKassaResult(True,value=self.snapshot(p)))
  async with self.sessions() as s: self.assertEqual((await s.get(Payment,p.id)).provider_status,"succeeded"); self.assertIsNotNone(await s.scalar(select(PaymentFulfillmentOperation).where(PaymentFulfillmentOperation.idempotency_key==f"payment-grant:{p.id}")))
 async def test_provider_snapshot_mismatches_never_grant(self):
  for field in ("amount","currency","order","external"):
   async with self.sessions.begin() as s:
    p=await self.payment(s); op=PaymentProviderOperation(payment_id=p.id,operation_type="reconcile_payment",status="processing",idempotency_key=uuid.uuid4().hex,payload={},attempts=1,max_attempts=3,next_attempt_at=now_utc(),locked_by="w",locked_at=now_utc()); s.add(op); await s.flush(); data=self.snapshot(p)
    if field=="amount":data["amount"]["value"]="91.00"
    if field=="currency":data["amount"]["currency"]="USD"
    if field=="order":data["metadata"]["order_id"]="wrong"
    if field=="external":data["id"]="wrong"
    await finalize(s,ProviderOperationClaim(op.id,p.id,op.operation_type,op.payload,op.idempotency_key,"w",1,p.external_id,op.created_at),YooKassaResult(True,value=data)); self.assertEqual(p.reconciliation_status,"mismatch"); self.assertIsNone(await s.scalar(select(PaymentFulfillmentOperation).where(PaymentFulfillmentOperation.payment_id==p.id)))
 async def test_provider_attempt_fencing_and_dead_restart(self):
  async with self.sessions.begin() as s:
   p=await self.payment(s); op=PaymentProviderOperation(payment_id=p.id,operation_type="reconcile_payment",status="pending",idempotency_key=uuid.uuid4().hex,payload={},attempts=0,max_attempts=1,next_attempt_at=now_utc()); s.add(op); await s.flush(); c=await claim(s,"same"); oid=op.id
  async with self.sessions.begin() as s: op=await s.get(PaymentProviderOperation,oid); op.locked_at=now_utc()-timedelta(hours=1); await recover_stale(s,0); self.assertEqual(op.status,"dead"); await retry_dead_provider_operation(s,oid,reset_attempts=True,reason="admin"); self.assertEqual(op.attempts,0)
  async with self.sessions.begin() as s:
   with self.assertRaises(PaymentProviderOperationOwnershipError): await finalize(s,c,YooKassaResult(True,value={}))
 async def test_all_queue_failure_finalizers_dead_at_limit_and_restart(self):
  async with self.sessions.begin() as s:
   p=await self.payment(s); f=PaymentFulfillmentOperation(payment_id=p.id,operation_type="grant_subscription",idempotency_key=uuid.uuid4().hex,status="processing",payload={},attempts=1,max_attempts=1,next_attempt_at=now_utc(),locked_by="w",locked_at=now_utc()); w=WebhookInbox(provider="yookassa",event_key=uuid.uuid4().hex,event_type="payment.succeeded",provider_object_id="x",payment_external_id=p.external_id,payload={},status="processing",attempts=1,max_attempts=1,next_attempt_at=now_utc(),locked_by="w",locked_at=now_utc()); s.add_all([f,w]); await s.flush(); await finalize_fulfillment_failure(s,FulfillmentClaim(f.id,"w",1,f.operation_type),error_code="x"); await finalize_webhook_failure(s,InboxClaim(w.id,"w",1,w.event_type,w.payment_external_id,None,w.payload,w.event_key),error_code="x"); self.assertEqual((f.status,w.status),("dead","dead")); await retry_dead_fulfillment_operation(s,f.id,reset_attempts=True,reason="admin"); await retry_dead_webhook_operation(s,w.id,reset_attempts=True,reason="admin"); self.assertEqual((f.attempts,w.attempts),(0,0))

 async def test_pending_auto_capture_cancel_orchestration_never_enqueues_provider_cancel(self):
  async with self.sessions.begin() as s:
   p=await self.payment(s,provider_status="pending"); queued=await PaymentService.cancel_payment_via_api(s,p.id); self.assertTrue(queued); self.assertEqual(p.checkout_status,"abandoned"); self.assertIsNone(await s.scalar(select(PaymentProviderOperation).where(PaymentProviderOperation.payment_id==p.id,PaymentProviderOperation.operation_type=="cancel_payment")))
 async def test_waiting_for_capture_enqueues_cancel(self):
  async with self.sessions.begin() as s:
   p=await self.payment(s,provider_status="waiting_for_capture"); await PaymentService.cancel_payment_via_api(s,p.id); self.assertIsNotNone(await s.scalar(select(PaymentProviderOperation).where(PaymentProviderOperation.payment_id==p.id,PaymentProviderOperation.operation_type=="cancel_payment")))
 async def test_terminal_provider_state_survives_late_failure_and_pending(self):
  for terminal in ("succeeded","refunded","canceled"):
   async with self.sessions.begin() as s:
    p=await self.payment(s,provider_status=terminal,paid_at=now_utc() if terminal=="succeeded" else None); op=PaymentProviderOperation(payment_id=p.id,operation_type="reconcile_payment",status="processing",idempotency_key=uuid.uuid4().hex,payload={},attempts=1,max_attempts=1,next_attempt_at=now_utc(),locked_by="w",locked_at=now_utc()); s.add(op); await s.flush(); c=ProviderOperationClaim(op.id,p.id,op.operation_type,op.payload,op.idempotency_key,"w",1,p.external_id,op.created_at); await finalize_provider_failure(s,c,error_code="timeout",retryable=True); self.assertEqual(p.provider_status,terminal)
 async def test_payment_not_visible_last_attempt_is_dead_with_timestamp(self):
  from services.workers.webhook_inbox import finalize as finalize_inbox
  async with self.sessions.begin() as s:
   row=WebhookInbox(provider="yookassa",event_key=uuid.uuid4().hex,event_type="payment.succeeded",provider_object_id="missing",payment_external_id="missing",payload={},status="processing",attempts=1,max_attempts=1,next_attempt_at=now_utc(),locked_by="w",locked_at=now_utc()); s.add(row); await s.flush(); await finalize_inbox(s,InboxClaim(row.id,"w",1,row.event_type,row.payment_external_id,None,row.payload,row.event_key),YooKassaResult(True,value={})); self.assertEqual(row.status,"dead"); self.assertIsNotNone(row.processed_at); self.assertIsNone(row.locked_by)
 async def test_force_manual_does_not_mutate_processing_payload(self):
  async with self.sessions.begin() as s:
   p=await self.payment(s,provider_status="succeeded"); op=PaymentFulfillmentOperation(payment_id=p.id,operation_type="grant_subscription",idempotency_key=f"payment-grant:{p.id}",status="processing",payload={},attempts=1,max_attempts=3,next_attempt_at=now_utc(),locked_by="worker",locked_at=now_utc()); s.add(op); await s.flush(); ok,code=await PaymentService.force_grant_payment(s,p.id,1,force_without_provider_confirmation=True); self.assertFalse(ok); self.assertEqual(code,"already_processing"); self.assertEqual(op.payload,{})

 async def test_referral_business_rules_5_3_1_are_exactly_once(self):
  async with self.sessions.begin() as s:
   invited=await s.get(User,self.user_id); ref=User(telegram_id=888001,subscription_end=now_utc(),device_limit=2); s.add(ref); await s.flush(); invited.referred_by=ref.telegram_id; invited.subscription_end=now_utc()
   first=await self.payment(s,provider_status="succeeded",fulfillment_status="succeeded"); op1=PaymentFulfillmentOperation(payment_id=first.id,operation_type="grant_referral",idempotency_key=f"payment-referral:{first.id}",status="processing",payload={},attempts=1,max_attempts=3,next_attempt_at=now_utc(),locked_by="w",locked_at=now_utc()); s.add(op1); await s.flush(); await referral(s,op1); self.assertEqual((first.referral_user_bonus_days,first.referral_referrer_bonus_days,ref.referral_days),(5,3,3))
   second=await self.payment(s,provider_status="succeeded",fulfillment_status="succeeded"); op2=PaymentFulfillmentOperation(payment_id=second.id,operation_type="grant_referral",idempotency_key=f"payment-referral:{second.id}",status="processing",payload={},attempts=1,max_attempts=3,next_attempt_at=now_utc(),locked_by="w",locked_at=now_utc()); s.add(op2); await s.flush(); await referral(s,op2); self.assertEqual((second.referral_user_bonus_days,second.referral_referrer_bonus_days,ref.referral_days),(0,1,4))
   await referral(s,op2); self.assertEqual(ref.referral_days,4)
 async def test_referral_rejects_self_and_banned_referrer(self):
  async with self.sessions.begin() as s:
   invited=await s.get(User,self.user_id); invited.referred_by=invited.telegram_id; p=await self.payment(s,provider_status="succeeded",fulfillment_status="succeeded"); op=PaymentFulfillmentOperation(payment_id=p.id,operation_type="grant_referral",idempotency_key=f"payment-referral:{p.id}",status="processing",payload={},attempts=1,max_attempts=3,next_attempt_at=now_utc(),locked_by="w",locked_at=now_utc()); s.add(op); await s.flush(); await referral(s,op); self.assertEqual(op.status,"cancelled"); self.assertIsNone(await s.scalar(select(ReferralReward).where(ReferralReward.source_payment_id==p.id)))

 async def test_stale_recovery_preserves_each_terminal_provider_state(self):
  for terminal in ("succeeded","refunded","canceled"):
   async with self.sessions.begin() as s:
    paid=now_utc() if terminal=="succeeded" else None; p=await self.payment(s,provider_status=terminal,paid_at=paid); op=PaymentProviderOperation(payment_id=p.id,operation_type="reconcile_payment",status="processing",idempotency_key=uuid.uuid4().hex,payload={},attempts=1,max_attempts=1,next_attempt_at=now_utc(),locked_by="w",locked_at=now_utc()-timedelta(hours=1)); s.add(op); await s.flush(); await recover_stale(s,0); self.assertEqual(p.provider_status,terminal); self.assertEqual(p.paid_at,paid); self.assertEqual(op.status,"dead")

 async def test_refunded_reconcile_succeeded_remains_refunded_without_grant(self):
  async with self.sessions.begin() as s:
   p=await self.payment(s,provider_status="refunded",fulfillment_status="reversed"); op=PaymentProviderOperation(payment_id=p.id,operation_type="reconcile_payment",status="processing",idempotency_key=uuid.uuid4().hex,payload={},attempts=1,max_attempts=3,next_attempt_at=now_utc(),locked_by="w",locked_at=now_utc()); s.add(op); await s.flush(); await finalize(s,ProviderOperationClaim(op.id,p.id,op.operation_type,op.payload,op.idempotency_key,"w",1,p.external_id,op.created_at),YooKassaResult(True,value=self.snapshot(p))); self.assertEqual(p.provider_status,"refunded"); self.assertEqual(p.fulfillment_status,"reversed"); self.assertIsNone(await s.scalar(select(PaymentFulfillmentOperation).where(PaymentFulfillmentOperation.payment_id==p.id)))
 async def test_succeeded_delayed_pending_is_mismatch_not_regression(self):
  async with self.sessions.begin() as s:
   p=await self.payment(s,provider_status="succeeded",fulfillment_status="succeeded",paid_at=now_utc()); op=PaymentProviderOperation(payment_id=p.id,operation_type="reconcile_payment",status="processing",idempotency_key=uuid.uuid4().hex,payload={},attempts=1,max_attempts=3,next_attempt_at=now_utc(),locked_by="w",locked_at=now_utc()); s.add(op); await s.flush(); await finalize(s,ProviderOperationClaim(op.id,p.id,op.operation_type,op.payload,op.idempotency_key,"w",1,p.external_id,op.created_at),YooKassaResult(True,value=self.snapshot(p,status="pending"))); self.assertEqual(p.provider_status,"succeeded"); self.assertEqual(p.reconciliation_status,"mismatch")
 async def test_webhook_succeeded_with_pending_provider_retries(self):
  from services.workers.webhook_inbox import finalize as finalize_inbox
  async with self.sessions.begin() as s:
   p=await self.payment(s); row=WebhookInbox(provider="yookassa",event_key=uuid.uuid4().hex,event_type="payment.succeeded",provider_object_id=p.external_id,payment_external_id=p.external_id,payload={},status="processing",attempts=1,max_attempts=3,next_attempt_at=now_utc(),locked_by="w",locked_at=now_utc()); s.add(row); await s.flush(); await finalize_inbox(s,InboxClaim(row.id,"w",1,row.event_type,row.payment_external_id,None,row.payload,row.event_key),YooKassaResult(True,value=self.snapshot(p,status="pending"))); self.assertEqual(row.status,"retry"); self.assertIsNone(await s.scalar(select(PaymentFulfillmentOperation).where(PaymentFulfillmentOperation.payment_id==p.id)))

 async def _finalize_duplicate_refund(self,s,p,refund_id="refund-1"):
  from services.workers.webhook_inbox import finalize as finalize_inbox
  payload={"object":{"id":refund_id,"payment_id":p.external_id,"amount":{"value":"90.00","currency":"RUB"}}}
  row=WebhookInbox(provider="yookassa",event_key=uuid.uuid4().hex,event_type="refund.succeeded",provider_object_id=refund_id,payment_external_id=p.external_id,payload=payload,status="processing",attempts=1,max_attempts=3,next_attempt_at=now_utc(),locked_by="w",locked_at=now_utc()); s.add(row); await s.flush()
  await finalize_inbox(s,InboxClaim(row.id,"w",1,row.event_type,row.payment_external_id,None,row.payload,row.event_key),None); return row
 async def test_duplicate_full_refund_after_successful_reversal_is_noop(self):
  async with self.sessions.begin() as s:
   user=await s.get(User,self.user_id); user.subscription_end=now_utc()+timedelta(days=30); p=await self.payment(s,provider_status="refunded",fulfillment_status="reversal_pending",fulfilled_at=now_utc()); grant=EntitlementEntry(beneficiary_user_id=user.id,source_type="payment",source_id=str(p.id),entry_type="payment_grant",days_delta=30,device_limit_snapshot=2,tariff_id_snapshot=p.tariff_id,metadata_={}); s.add(grant); op=PaymentFulfillmentOperation(payment_id=p.id,operation_type="reverse_payment",idempotency_key=f"payment-reverse:{p.id}",status="processing",payload={},attempts=1,max_attempts=3,next_attempt_at=now_utc(),locked_by="w",locked_at=now_utc()); s.add(op); s.add(PaymentRefund(payment_id=p.id,provider_refund_id="refund-1",amount=p.amount,currency=p.currency,provider_status="succeeded",event_key=uuid.uuid4().hex,processed_at=now_utc())); await s.flush(); await reverse(s,op); original_end=user.subscription_end; reversed_at=p.reversed_at; await self._finalize_duplicate_refund(s,p); self.assertEqual((p.provider_status,p.fulfillment_status,op.status),("refunded","reversed","succeeded")); self.assertEqual(p.reversed_at,reversed_at); self.assertEqual(user.subscription_end,original_end); self.assertEqual(await s.scalar(select(func.count(EntitlementEntry.id)).where(EntitlementEntry.source_id==str(p.id),EntitlementEntry.entry_type=="payment_reversal")),1)
 async def test_duplicate_full_refund_while_reversal_pending_preserves_operation(self):
  async with self.sessions.begin() as s:
   p=await self.payment(s,provider_status="refunded",fulfillment_status="reversal_pending"); op=PaymentFulfillmentOperation(payment_id=p.id,operation_type="reverse_payment",idempotency_key=f"payment-reverse:{p.id}",status="pending",payload={"original":True},attempts=2,max_attempts=5,next_attempt_at=now_utc()); s.add_all([op,PaymentRefund(payment_id=p.id,provider_refund_id="refund-1",amount=p.amount,currency=p.currency,provider_status="succeeded",event_key=uuid.uuid4().hex,processed_at=now_utc())]); await s.flush(); await self._finalize_duplicate_refund(s,p); self.assertEqual(p.fulfillment_status,"reversal_pending"); self.assertEqual((op.status,op.attempts,op.payload),("pending",2,{"original":True})); self.assertEqual(await s.scalar(select(func.count(PaymentFulfillmentOperation.id)).where(PaymentFulfillmentOperation.payment_id==p.id,PaymentFulfillmentOperation.operation_type=="reverse_payment")),1)
 async def test_duplicate_full_refund_with_dead_reversal_requires_review(self):
  async with self.sessions.begin() as s:
   p=await self.payment(s,provider_status="refunded",fulfillment_status="reversal_pending"); op=PaymentFulfillmentOperation(payment_id=p.id,operation_type="reverse_payment",idempotency_key=f"payment-reverse:{p.id}",status="dead",payload={},attempts=5,max_attempts=5,next_attempt_at=now_utc(),completed_at=now_utc()); s.add_all([op,PaymentRefund(payment_id=p.id,provider_refund_id="refund-1",amount=p.amount,currency=p.currency,provider_status="succeeded",event_key=uuid.uuid4().hex,processed_at=now_utc())]); await s.flush(); await self._finalize_duplicate_refund(s,p); self.assertEqual((op.status,op.attempts),("dead",5)); self.assertEqual((p.fulfillment_status,p.reconciliation_status,p.fulfillment_last_error_code),("manual_review","manual_review","reverse_operation_not_runnable"))
 async def test_duplicate_refund_inbox_delivery_deduplicates_ledger_and_reversal(self):
  async with self.sessions.begin() as s:
   p=await self.payment(s,provider_status="succeeded",fulfillment_status="succeeded"); first=await self._finalize_duplicate_refund(s,p); second=await self._finalize_duplicate_refund(s,p); self.assertEqual((first.status,second.status),("succeeded","succeeded")); self.assertEqual(await s.scalar(select(func.count(PaymentRefund.id)).where(PaymentRefund.payment_id==p.id)),1); self.assertEqual(await s.scalar(select(func.sum(PaymentRefund.amount)).where(PaymentRefund.payment_id==p.id)),p.amount); self.assertEqual(await s.scalar(select(func.count(PaymentFulfillmentOperation.id)).where(PaymentFulfillmentOperation.payment_id==p.id,PaymentFulfillmentOperation.operation_type=="reverse_payment")),1)

@unittest.skipUnless(DB,"TEST_DATABASE_URL is not set")
class LegacyPaymentMigrationPostgresTests(unittest.IsolatedAsyncioTestCase):
 async def test_legacy_completed_payment_is_backfilled_without_extending(self):
  import asyncio
  from alembic.command import downgrade, upgrade
  from alembic.config import Config
  cfg=Config("alembic.ini"); cfg.set_main_option("sqlalchemy.url",DB)
  await asyncio.to_thread(downgrade,cfg,"-2")
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
