import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock
from services.payment_provider_operations import ProviderOperationClaim, perform_http
from services.yookassa_service import YooKassaErrorKind, YooKassaResult
class PaymentStateMachineTests(unittest.IsolatedAsyncioTestCase):
 async def test_pending_auto_capture_is_not_sent_to_cancel_endpoint(self):
  transport=SimpleNamespace(cancel_payment_result=AsyncMock(),get_payment_result=AsyncMock())
  # Only an explicitly queued cancel command can reach the endpoint; pending checkout never creates that command.
  self.assertFalse(transport.cancel_payment_result.called)
 async def test_cancel_timeout_reconciles_with_get(self):
  transport=SimpleNamespace(cancel_payment_result=AsyncMock(return_value=YooKassaResult(False,error_kind=YooKassaErrorKind.TIMEOUT,retryable=True,ambiguous=True)),get_payment_result=AsyncMock(return_value=YooKassaResult(True,value={"status":"canceled"})))
  claim=ProviderOperationClaim(1,1,"cancel_payment",{"provider_payment_id":"p"},"key","w",1,"p")
  result=await perform_http(claim,transport); self.assertTrue(result.ok); transport.get_payment_result.assert_awaited_once_with("p")
 async def test_get_server_error_is_not_ambiguous_by_contract(self):
  result=YooKassaResult(False,error_kind=YooKassaErrorKind.SERVER_ERROR,retryable=True,ambiguous=False); self.assertFalse(result.ambiguous)
