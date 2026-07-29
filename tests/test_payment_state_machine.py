import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock
from services.payment_provider_operations import ProviderOperationClaim, perform_http
from utils.datetime_helpers import now_utc
from services.yookassa_service import YooKassaErrorKind, YooKassaResult
class PaymentStateMachineTests(unittest.IsolatedAsyncioTestCase):
 async def test_cancel_timeout_reconciles_with_get(self):
  transport=SimpleNamespace(cancel_payment_result=AsyncMock(return_value=YooKassaResult(False,error_kind=YooKassaErrorKind.TIMEOUT,retryable=True,ambiguous=True)),get_payment_result=AsyncMock(return_value=YooKassaResult(True,value={"status":"canceled"})))
  claim=ProviderOperationClaim(1,1,"cancel_payment",{"provider_payment_id":"p"},"key","w",1,"p",now_utc())
  result=await perform_http(claim,transport); self.assertTrue(result.ok); transport.get_payment_result.assert_awaited_once_with("p")
 async def test_get_server_error_is_not_ambiguous_by_contract(self):
  result=YooKassaResult(False,error_kind=YooKassaErrorKind.SERVER_ERROR,retryable=True,ambiguous=False); self.assertFalse(result.ambiguous)

 async def test_expired_create_never_posts(self):
  from datetime import timedelta
  transport=SimpleNamespace(create_payment_result=AsyncMock(),get_payment_result=AsyncMock())
  claim=ProviderOperationClaim(1,1,"create_payment",{},"stable","w",1,None,now_utc()-timedelta(hours=24))
  result=await perform_http(claim,transport); self.assertEqual(result.error_kind,YooKassaErrorKind.IDEMPOTENCY_WINDOW_EXPIRED); transport.create_payment_result.assert_not_awaited()
 async def test_create_with_external_id_uses_get(self):
  transport=SimpleNamespace(create_payment_result=AsyncMock(),get_payment_result=AsyncMock(return_value=YooKassaResult(True,value={"id":"p","status":"succeeded"})))
  claim=ProviderOperationClaim(1,1,"create_payment",{},"stable","w",1,"p",now_utc()-__import__('datetime').timedelta(hours=24))
  await perform_http(claim,transport); transport.get_payment_result.assert_awaited_once_with("p"); transport.create_payment_result.assert_not_awaited()

class ProviderValidationTests(unittest.TestCase):
 def test_local_payment_id_is_required(self):
  from services.payment_provider_validation import validate_provider_payment
  payment=SimpleNamespace(id=1,external_id="p",amount=__import__('decimal').Decimal("90"),snapshot_amount=None,currency="RUB",snapshot_currency=None,public_order_id="pay_x")
  data={"id":"p","amount":{"value":"90","currency":"RUB"},"metadata":{"order_id":"pay_x"}}
  self.assertEqual(validate_provider_payment(payment,data),"local_payment_id_missing")
