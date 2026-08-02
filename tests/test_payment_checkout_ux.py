import os
import uuid
import unittest
from datetime import timedelta
from decimal import Decimal

from sqlalchemy import delete, func, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from database.models import (
    Payment,
    PaymentProviderOperation,
    Tariff,
    TariffQuote,
    TariffVersion,
    User,
)
from services.payment_provider_operations import (
    ProviderOperationClaim,
    create_payload,
    finalize,
)
from services.payment_service import PaymentService
from services.yookassa_service import YooKassaResult
from utils.datetime_helpers import now_utc


DB = os.getenv("TEST_DATABASE_URL")


@unittest.skipUnless(DB, "TEST_DATABASE_URL is not set")
class PaymentCheckoutUxPostgresTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine(DB)
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)
        async with self.sessions.begin() as session:
            await session.execute(
                text(
                    "TRUNCATE paid_value_ledger, tariff_quotes, tariff_versions, "
                    "entitlement_entries RESTART IDENTITY CASCADE"
                )
            )
            for model in (PaymentProviderOperation, Payment, User, Tariff):
                await session.execute(delete(model))
            tariff = Tariff(
                name="Базовый",
                duration_days=30,
                device_limit=2,
                price_rub=90,
                is_active=True,
            )
            user = User(telegram_id=800000 + uuid.uuid4().int % 99999)
            session.add_all([tariff, user])
            await session.flush()
            self.tariff_id = tariff.id
            self.user_id = user.id
            self.telegram_id = user.telegram_id

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def _provider_checkout(
        self,
        session,
        *,
        checkout_status="active",
        quote_status="active",
        diagnostic_reason=None,
        payment_url="https://yookassa.example/pay",
        user_cancel_requested_at=None,
    ):
        tariff = await session.get(Tariff, self.tariff_id)
        version = TariffVersion(
            tariff_id=tariff.id,
            version_number=1,
            name_snapshot=tariff.name,
            duration_hours=720,
            device_limit=tariff.device_limit,
            price_rub=Decimal("90.00"),
            currency="RUB",
        )
        session.add(version)
        await session.flush()
        created = now_utc()
        quote = TariffQuote(
            public_id=uuid.uuid4(),
            user_id=self.user_id,
            operation_type="purchase",
            target_tariff_version_id=version.id,
            current_paid_hours=0,
            current_paid_value_rub=0,
            bonus_hours=0,
            confirmed_payment_required_rub=Decimal("90.00"),
            resulting_paid_hours=720,
            resulting_paid_value_rub=Decimal("90.00"),
            resulting_bonus_hours=0,
            rounding_loss_hours=0,
            rounding_loss_value_rub=0,
            currency="RUB",
            status=quote_status,
            diagnostic_reason=diagnostic_reason,
            created_at=created,
            expires_at=created + timedelta(minutes=15),
        )
        session.add(quote)
        await session.flush()
        payment = Payment(
            user_id=self.user_id,
            tariff_id=self.tariff_id,
            tariff_quote_id=quote.id,
            tariff_version_id=version.id,
            amount=Decimal("90.00"),
            currency="RUB",
            status="pending",
            public_order_id="pay_" + uuid.uuid4().hex,
            provider_idempotency_key=uuid.uuid4().hex,
            provider_required=True,
            provider_status="pending",
            fulfillment_status="not_ready",
            reconciliation_status="ok",
            checkout_status=checkout_status,
            user_cancel_requested_at=user_cancel_requested_at,
            snapshot_duration_days=30,
            snapshot_device_limit=2,
            snapshot_amount=Decimal("90.00"),
            snapshot_currency="RUB",
            external_id="provider_" + uuid.uuid4().hex,
            payment_url=payment_url,
            payment_method="yookassa",
        )
        session.add(payment)
        await session.flush()
        quote.payment_id = payment.id
        operation = PaymentProviderOperation(
            payment_id=payment.id,
            operation_type="create_payment",
            status="succeeded",
            idempotency_key=payment.provider_idempotency_key,
            payload=create_payload(
                payment,
                "Предоставление доступа",
                "https://t.me/test_bot",
            ),
            attempts=1,
            max_attempts=5,
            next_attempt_at=created,
            completed_at=created,
        )
        session.add(operation)
        await session.flush()
        return payment, quote, operation

    async def test_ready_provider_payment_is_reused_without_duplicate(self):
        async with self.sessions.begin() as session:
            payment, _, _ = await self._provider_checkout(session)
            payment_id = payment.id

        async with self.sessions.begin() as session:
            existing, error = await PaymentService.create_yookassa_payment(
                session=session,
                user_id=self.user_id,
                tariff_id=self.tariff_id,
                amount=Decimal("90.00"),
                telegram_id=self.telegram_id,
                bot_username="test_bot",
            )
            self.assertEqual(existing.id, payment_id)
            self.assertEqual(error, "https://yookassa.example/pay")
            self.assertEqual(
                await session.scalar(select(func.count(Payment.id))),
                1,
            )

    async def test_old_user_cancel_is_reopened_and_reconciled(self):
        async with self.sessions.begin() as session:
            payment, quote, _ = await self._provider_checkout(
                session,
                checkout_status="abandoned",
                quote_status="cancelled",
                diagnostic_reason="checkout_abandoned_by_user",
                payment_url=None,
                user_cancel_requested_at=now_utc(),
            )
            payment_id = payment.id
            quote_id = quote.id

        async with self.sessions.begin() as session:
            existing, error = await PaymentService.create_yookassa_payment(
                session=session,
                user_id=self.user_id,
                tariff_id=self.tariff_id,
                amount=Decimal("90.00"),
                telegram_id=self.telegram_id,
                bot_username="test_bot",
            )
            self.assertEqual(existing.id, payment_id)
            self.assertIsNone(error)
            self.assertEqual(existing.checkout_status, "active")
            self.assertIsNone(existing.user_cancel_requested_at)
            quote = await session.get(TariffQuote, quote_id)
            self.assertEqual(quote.status, "active")
            self.assertIsNone(quote.diagnostic_reason)
            reconcile = await session.scalar(
                select(PaymentProviderOperation).where(
                    PaymentProviderOperation.payment_id == payment_id,
                    PaymentProviderOperation.operation_type == "reconcile_payment",
                    PaymentProviderOperation.status == "pending",
                )
            )
            self.assertIsNotNone(reconcile)
            self.assertEqual(
                await session.scalar(select(func.count(Payment.id))),
                1,
            )

    async def test_reconcile_restores_redirect_url(self):
        async with self.sessions.begin() as session:
            payment, _, _ = await self._provider_checkout(
                session,
                payment_url=None,
            )
            reconcile = PaymentProviderOperation(
                payment_id=payment.id,
                operation_type="reconcile_payment",
                status="processing",
                idempotency_key="reconcile_" + uuid.uuid4().hex,
                payload={
                    "provider_payment_id": payment.external_id,
                    "reason": "resume_user_abandoned_checkout",
                },
                attempts=1,
                max_attempts=5,
                next_attempt_at=now_utc(),
                locked_by="worker",
                locked_at=now_utc(),
            )
            session.add(reconcile)
            await session.flush()
            claim = ProviderOperationClaim(
                reconcile.id,
                payment.id,
                "reconcile_payment",
                dict(reconcile.payload),
                reconcile.idempotency_key,
                "worker",
                1,
                payment.external_id,
                reconcile.created_at,
            )
            result = YooKassaResult(
                True,
                value={
                    "id": payment.external_id,
                    "status": "pending",
                    "amount": {"value": "90.00", "currency": "RUB"},
                    "metadata": {
                        "order_id": payment.public_order_id,
                        "local_payment_id": str(payment.id),
                    },
                    "confirmation": {
                        "type": "redirect",
                        "confirmation_url": "https://yookassa.example/recovered",
                    },
                },
            )
            await finalize(session, claim, result)
            self.assertEqual(
                payment.payment_url,
                "https://yookassa.example/recovered",
            )
            self.assertEqual(reconcile.status, "succeeded")


class PaymentCheckoutUxContractTests(unittest.TestCase):
    def test_user_facing_checkout_contract(self):
        root = os.path.dirname(os.path.dirname(__file__))
        keyboard = open(
            os.path.join(root, "bot/keyboards/payment.py"),
            encoding="utf-8",
        ).read()
        routes = open(
            os.path.join(root, "bot/handlers/payment/yookassa_routes.py"),
            encoding="utf-8",
        ).read()
        self.assertIn("← Вернуться позже", keyboard)
        self.assertNotIn('text="❌ Отменить"', keyboard)
        self.assertIn("_wait_and_show_payment_url", routes)
        self.assertIn("страница обновится автоматически", routes)
        self.assertIn('payment.provider_status == "pending"', routes)
        self.assertIn("Платёж сохранён", routes)


if __name__ == "__main__":
    unittest.main()
