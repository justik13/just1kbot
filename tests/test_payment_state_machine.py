import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock
from services.payment_provider_operations import ProviderOperationClaim, perform_http
from utils.datetime_helpers import now_utc
from services.yookassa_service import YooKassaErrorKind, YooKassaResult


class FakeJSONResponse:
    def __init__(self, value):
        self.status = 200
        self.value = value

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass

    async def json(self, **kwargs):
        return self.value


class FakeJSONClient:
    responses = []

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass

    def request(self, *args, **kwargs):
        return FakeJSONResponse(self.responses.pop(0))


class PaymentStateMachineTests(unittest.IsolatedAsyncioTestCase):
    async def _transport_call(self, method, *values):
        from unittest.mock import patch
        from services.yookassa_service import YooKassaService

        FakeJSONClient.responses = list(values)
        settings = SimpleNamespace(
            YOOKASSA_SHOP_ID="shop", YOOKASSA_SECRET_KEY="secret"
        )
        with (
            patch("services.yookassa_service.get_settings", return_value=settings),
            patch("services.yookassa_service.aiohttp.ClientSession", FakeJSONClient),
        ):
            return await method(YooKassaService)

    async def test_create_2xx_json_list_is_ambiguous_retryable(self):
        result = await self._transport_call(
            lambda service: service.create_payment_result({}, idempotency_key="key"), []
        )
        self.assertEqual(result.error_kind, YooKassaErrorKind.INVALID_RESPONSE)
        self.assertTrue(result.retryable)
        self.assertTrue(result.ambiguous)

    async def test_create_2xx_json_null_is_ambiguous_retryable(self):
        result = await self._transport_call(
            lambda service: service.create_payment_result({}, idempotency_key="key"),
            None,
        )
        self.assertEqual(result.error_kind, YooKassaErrorKind.INVALID_RESPONSE)
        self.assertTrue(result.retryable)
        self.assertTrue(result.ambiguous)

    async def test_get_2xx_json_list_is_retryable_not_ambiguous(self):
        result = await self._transport_call(
            lambda service: service.get_payment_result("p"), []
        )
        self.assertEqual(result.error_kind, YooKassaErrorKind.INVALID_RESPONSE)
        self.assertTrue(result.retryable)
        self.assertFalse(result.ambiguous)

    async def test_malformed_2xx_has_command_specific_ambiguity(self):
        from unittest.mock import patch
        from services.yookassa_service import YooKassaService

        class Response:
            status = 200

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                pass

            async def json(self, **kwargs):
                raise ValueError("broken json")

        class Client:
            def __init__(self, *args, **kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                pass

            def request(self, *args, **kwargs):
                return Response()

        settings = SimpleNamespace(
            YOOKASSA_SHOP_ID="shop", YOOKASSA_SECRET_KEY="secret"
        )
        with (
            patch("services.yookassa_service.get_settings", return_value=settings),
            patch("services.yookassa_service.aiohttp.ClientSession", Client),
        ):
            create = await YooKassaService.create_payment_result(
                {}, idempotency_key="key"
            )
            get = await YooKassaService.get_payment_result("p")
        self.assertEqual(create.error_kind, YooKassaErrorKind.INVALID_RESPONSE)
        self.assertTrue(create.retryable)
        self.assertTrue(create.ambiguous)
        self.assertTrue(get.retryable)
        self.assertFalse(get.ambiguous)

    async def test_get_server_error_is_not_ambiguous_by_contract(self):
        result = YooKassaResult(
            False,
            error_kind=YooKassaErrorKind.SERVER_ERROR,
            retryable=True,
            ambiguous=False,
        )
        self.assertFalse(result.ambiguous)

    async def test_expired_create_never_posts(self):
        from datetime import timedelta

        transport = SimpleNamespace(
            create_payment_result=AsyncMock(), get_payment_result=AsyncMock()
        )
        claim = ProviderOperationClaim(
            1,
            1,
            "create_payment",
            {},
            "stable",
            "w",
            1,
            None,
            now_utc() - timedelta(hours=24),
        )
        result = await perform_http(claim, transport)
        self.assertEqual(
            result.error_kind, YooKassaErrorKind.IDEMPOTENCY_WINDOW_EXPIRED
        )
        transport.create_payment_result.assert_not_awaited()

    async def test_create_with_external_id_uses_get(self):
        transport = SimpleNamespace(
            create_payment_result=AsyncMock(),
            get_payment_result=AsyncMock(
                return_value=YooKassaResult(
                    True, value={"id": "p", "status": "succeeded"}
                )
            ),
        )
        claim = ProviderOperationClaim(
            1,
            1,
            "create_payment",
            {},
            "stable",
            "w",
            1,
            "p",
            now_utc() - __import__("datetime").timedelta(hours=24),
        )
        await perform_http(claim, transport)
        transport.get_payment_result.assert_awaited_once_with("p")
        transport.create_payment_result.assert_not_awaited()


class ProviderValidationTests(unittest.TestCase):
    def test_local_payment_id_is_required(self):
        from services.payment_provider_validation import validate_provider_payment

        payment = SimpleNamespace(
            id=1,
            external_id="p",
            amount=__import__("decimal").Decimal("90"),
            snapshot_amount=None,
            currency="RUB",
            snapshot_currency=None,
            public_order_id="pay_x",
        )
        data = {
            "id": "p",
            "amount": {"value": "90", "currency": "RUB"},
            "metadata": {"order_id": "pay_x"},
        }
        self.assertEqual(
            validate_provider_payment(payment, data), "local_payment_id_missing"
        )


class ProviderCapturedAtTests(unittest.TestCase):
    def test_create_post_and_followup_get_have_distinct_sources(self):
        from services.payment_provider_operations import (
            ProviderOperationClaim,
            provider_transition_source,
        )

        common = (1, 1, "create_payment", {}, "key", "worker", 1)
        post = ProviderOperationClaim(*common, None, now_utc())
        get = ProviderOperationClaim(*common, "provider-id", now_utc())
        self.assertEqual(
            provider_transition_source(post), "provider_create_payment_post"
        )
        self.assertEqual(provider_transition_source(get), "provider_get_payment")

    def test_strict_timezone_aware_utc_parser(self):
        from services.payment_provider_state import parse_provider_captured_at

        parsed = parse_provider_captured_at("2026-07-29T12:34:56.123+03:00")
        self.assertEqual(parsed.isoformat(), "2026-07-29T09:34:56.123000+00:00")

    def test_missing_malformed_and_naive_are_rejected(self):
        from services.payment_provider_state import parse_provider_captured_at

        for value in (None, "", "not-a-date", "2026-07-29T12:34:56"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                parse_provider_captured_at(value)

class LateWebhookTests(unittest.IsolatedAsyncioTestCase):
    async def test_late_succeeded_webhook_applies_with_mismatch(self):
        from services.payment_provider_state import apply_provider_transition
        from database.models import Payment
        from decimal import Decimal
        
        payment = Payment(
            id=1,
            user_id=1,
            external_id="p",
            public_order_id="pay_x",
            amount=Decimal("100"),
            currency="RUB",
            provider_status="canceled",
            checkout_status="abandoned",
        )
        
        # We need a mock session that can capture session.add() calls
        class MockSession:
            def __init__(self):
                self.added = []
            def add(self, obj):
                self.added.append(obj)
                
        session = MockSession()
        transition = await apply_provider_transition(
            session,
            payment=payment,
            data={
                "status": "succeeded", 
                "captured_at": "2026-07-29T12:34:56.123+03:00",
                "id": "p",
                "amount": {"value": "100.00", "currency": "RUB"},
                "metadata": {"order_id": "pay_x", "local_payment_id": "1"},
            },
            source="webhook"
        )
        
        self.assertEqual(transition.outcome, "applied")
        self.assertEqual(payment.provider_status, "succeeded")
        self.assertEqual(payment.reconciliation_status, "mismatch")
        self.assertEqual(len(session.added), 1)
        self.assertEqual(session.added[0].reason, "late_success_after_hidden_checkout")

    async def test_late_canceled_webhook_conflicts(self):
        from services.payment_provider_state import apply_provider_transition
        from database.models import Payment
        from decimal import Decimal
        
        payment = Payment(
            id=1,
            user_id=1,
            external_id="p",
            public_order_id="pay_x",
            amount=Decimal("100"),
            currency="RUB",
            provider_status="succeeded",
            fulfillment_status="succeeded"
        )
        
        class MockSession:
            def __init__(self):
                self.added = []
            def add(self, obj):
                self.added.append(obj)
                
        session = MockSession()
        transition = await apply_provider_transition(
            session,
            payment=payment,
            data={
                "status": "canceled",
                "id": "p",
                "amount": {"value": "100.00", "currency": "RUB"},
                "metadata": {"order_id": "pay_x", "local_payment_id": "1"},
            },
            source="webhook"
        )
        
        self.assertEqual(transition.outcome, "conflict")
        self.assertEqual(transition.reason, "terminal_regression")
        self.assertEqual(payment.fulfillment_status, "manual_review")
        self.assertEqual(len(session.added), 1)
        self.assertEqual(session.added[0].reason, "succeeded_to_canceled")
