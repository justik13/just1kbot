"""Discovery adapter for PostgreSQL payment-pipeline regression contracts."""

import uuid

import payment_pipeline_postgres_base as _base
from services.workers.webhook_inbox import InboxClaim, finalize as finalize_inbox

_base_test_case = [_base.PaymentPipelinePostgresTests]
_base.PaymentPipelinePostgresTests = None


class PaymentPipelinePostgresTests(_base_test_case[0]):
    """Run the base contracts with provider-verified refund fixtures."""

    async def _finalize_duplicate_refund(self, session, payment, refund_id="refund-1"):
        verified_refund = {
            "id": refund_id,
            "status": "succeeded",
            "payment_id": payment.external_id,
            "amount": {"value": "90.00", "currency": "RUB"},
        }
        payload = {"object": dict(verified_refund)}
        row = _base.WebhookInbox(
            provider="yookassa",
            event_key=uuid.uuid4().hex,
            event_type="refund.succeeded",
            provider_object_id=refund_id,
            payment_external_id=payment.external_id,
            payload=payload,
            status="processing",
            attempts=1,
            max_attempts=3,
            next_attempt_at=_base.now_utc(),
            locked_by="w",
            locked_at=_base.now_utc(),
        )
        session.add(row)
        await session.flush()
        await finalize_inbox(
            session,
            InboxClaim(
                row.id,
                "w",
                1,
                row.event_type,
                row.payment_external_id,
                None,
                row.payload,
                row.event_key,
            ),
            _base.YooKassaResult(True, value=verified_refund),
        )
        return row


if __name__ == "__main__":
    _base.unittest.main()
