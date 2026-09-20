"""Unit tests for White Internet Purchase lifecycle, failure states, and add-on semantics."""

from decimal import Decimal
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from config.enums import (
    PurchaseFulfillmentStatus,
    PurchaseStatus,
    TariffQuoteOperation,
    TariffQuoteStatus,
)
from database.models import Purchase, TariffQuote, TariffVersion, User
from database.repositories import purchases_repo
from database.repositories.account_ledger_repo import InsufficientAccountBalanceError
from services.white_internet_service import WhiteInternetService


class WhiteInternetPurchaseLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def test_create_white_internet_purchase_starts_in_pending_status(self):
        mock_session = AsyncMock()
        user_id = 42
        quote = TariffQuote(
            id=101,
            public_id="quote_101",
            user_id=user_id,
            service_type="white_internet",
            operation_type=TariffQuoteOperation.PURCHASE,
            amount_due_rub=Decimal("150.00"),
            status=TariffQuoteStatus.ACTIVE.value,
        )
        version = TariffVersion(
            id=1,
            tariff_id=1,
            version_number=1,
            duration_hours=720,
            price_rub=Decimal("150.00"),
        )


        with patch("database.repositories.purchases_repo.create_purchase", new_callable=AsyncMock) as mock_create:
            mock_create.side_effect = lambda session, **kwargs: Purchase(**kwargs)

            purchase = await WhiteInternetService._create_white_internet_purchase(
                mock_session,
                user_id=user_id,
                quote=quote,
                tariff_version=version,
                device_limit=1,
            )

            mock_create.assert_awaited_once()
            _, kwargs = mock_create.await_args
            self.assertEqual(kwargs["status"], PurchaseStatus.PENDING)
            self.assertEqual(kwargs["fulfillment_status"], PurchaseFulfillmentStatus.PENDING)
            self.assertIsNone(kwargs["completed_at"])
            self.assertIsNone(kwargs["fulfilled_at"])
            self.assertEqual(purchase.status, PurchaseStatus.PENDING)
            self.assertEqual(purchase.fulfillment_status, PurchaseFulfillmentStatus.PENDING)

    async def test_mark_purchase_failed_sets_status_and_fulfillment_failed(self):
        mock_session = AsyncMock()
        purchase = Purchase(
            id=999,
            user_id=42,
            quote_id=101,
            idempotency_key="quote:101",
            service_type="white_internet",
            operation_type="purchase",
            amount_rub=Decimal("150.00"),
            status=PurchaseStatus.PENDING,
            fulfillment_status=PurchaseFulfillmentStatus.PENDING,
        )

        with patch("database.repositories.purchases_repo.get_purchase_by_id", new_callable=AsyncMock, return_value=purchase):
            updated = await purchases_repo.mark_purchase_failed(
                mock_session, purchase, reason="insufficient_balance"
            )

            self.assertEqual(updated.status, PurchaseStatus.FAILED.value)
            self.assertEqual(updated.fulfillment_status, PurchaseFulfillmentStatus.FAILED.value)
            self.assertEqual(updated.details.get("failure_reason"), "insufficient_balance")
            self.assertNotEqual(updated.status, PurchaseStatus.COMPLETED.value)

    async def test_failed_white_internet_debit_does_not_leave_completed_purchase(self):
        mock_session = AsyncMock()
        mock_session.flush = AsyncMock()
        mock_session.scalar = AsyncMock()
        mock_session.add = MagicMock()


        user = User(id=42, telegram_id=99942, is_deleted=False)
        server_mock = MagicMock()
        server_mock.id = 1
        server_mock.is_active = True
        server_mock.health_state = "ONLINE"
        server_mock.lifecycle_status = "ACTIVE"
        server_mock.extra_data = {"relays": ["relay1"]}

        tariff_mock = MagicMock()
        tariff_mock.id = 1
        tariff_mock.duration_days = 30

        version_mock = MagicMock()
        version_mock.id = 1
        version_mock.price_rub = Decimal("150.00")
        version_mock.duration_days = 30
        version_mock.duration_hours = 720
        version_mock.base_quota_bytes = 100 * 1024**3

        purchase = Purchase(
            id=999,
            user_id=42,
            quote_id=101,
            idempotency_key="quote:101",
            service_type="white_internet",
            operation_type="purchase",
            amount_rub=Decimal("150.00"),
            status=PurchaseStatus.PENDING,
            fulfillment_status=PurchaseFulfillmentStatus.PENDING,
        )

        with patch("services.white_internet_service.lock_checkout_user", new_callable=AsyncMock, return_value=user), \
             patch("services.white_internet_service.white_internet_repo.get_subscription_by_user_id", new_callable=AsyncMock, return_value=None), \
             patch("services.white_internet_service.WhiteInternetService.select_origin_node", new_callable=AsyncMock, return_value=server_mock), \
             patch("services.white_internet_service.WhiteInternetService.get_or_create_white_internet_tariff", new_callable=AsyncMock, return_value=tariff_mock), \
             patch("services.white_internet_service.get_or_create_current_version", new_callable=AsyncMock, return_value=version_mock), \
             patch("services.white_internet_service.WhiteInternetService._create_white_internet_purchase", new_callable=AsyncMock, return_value=purchase), \
             patch("services.white_internet_service.create_purchase_debit", new_callable=AsyncMock, side_effect=InsufficientAccountBalanceError("Insufficient balance")), \
             patch("services.white_internet_service.purchases_repo.mark_purchase_failed", new_callable=AsyncMock) as mock_mark_failed, \
             patch("services.white_internet_service.get_account_balance", new_callable=AsyncMock) as mock_bal:

            mock_bal.return_value = MagicMock(available=Decimal("0.00"))

            success, msg, sub = await WhiteInternetService.purchase_subscription(mock_session, user_id=42)

            self.assertFalse(success)
            mock_mark_failed.assert_awaited_once_with(mock_session, purchase)
            # Purchase was not completed
            self.assertNotEqual(purchase.status, PurchaseStatus.COMPLETED)
