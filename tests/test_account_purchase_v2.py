"""Unit tests for Billing v2 Account Purchase & Refund integration."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from sqlalchemy.ext.asyncio import AsyncSession

from config.enums import (

    EntitlementGrantStatus,
    PurchaseFulfillmentStatus,
    PurchaseStatus,
    TariffQuoteStatus,
)
from database.models import (
    AccountLedgerEntry,
    EntitlementGrant,
    Purchase,
    Tariff,
    TariffQuote,
    TariffVersion,
    User,
)
from database.repositories.account_ledger_repo import AccountBalanceSnapshot
from services.account_purchase import _settle_account_purchase, refund_purchase


class AccountPurchaseV2Tests(unittest.IsolatedAsyncioTestCase):
    async def test_settle_account_purchase_v2_flow(self):
        mock_session = AsyncMock()
        mock_session.flush = AsyncMock()
        mock_session.scalar = AsyncMock()
        mock_session.get = AsyncMock()

        base_time = datetime(2026, 9, 20, 12, 0, 0, tzinfo=timezone.utc)

        user = User(
            id=42,
            telegram_id=12345678,
            subscription_end=None,
            device_limit=1,
            financial_hold=False,
        )

        tariff = Tariff(
            id=1,
            name="AWG Standard",
            service_type="awg",
            duration_days=30,
            device_limit=2,
            is_active=True,
            price_rub=300,
        )

        version = TariffVersion(
            id=10,
            tariff_id=1,
            version_number=1,
            name_snapshot="AWG Standard",
            duration_hours=720,
            device_limit=2,
            price_rub=Decimal("300.00"),
            service_type="awg",
            currency="RUB",
        )

        quote = TariffQuote(
            id=101,
            public_id="quote_101",
            user_id=42,
            service_type="awg",
            target_tariff_version_id=10,
            operation_type="purchase",
            amount_due_rub=Decimal("300.00"),
            status=TariffQuoteStatus.ACTIVE.value,
            expires_at=base_time + timedelta(minutes=15),
            current_paid_hours=0,
            current_paid_value_rub=Decimal("0.00"),
            bonus_hours=0,
            resulting_paid_hours=720,
            resulting_paid_value_rub=Decimal("300.00"),
            resulting_bonus_hours=0,
            rounding_loss_hours=Decimal("0.0"),
            rounding_loss_value_rub=Decimal("0.0"),
            currency="RUB",
        )

        purchase_mock = Purchase(
            id=999,
            user_id=42,
            quote_id=101,
            idempotency_key="quote:101",
            service_type="awg",
            operation_type="purchase",
            amount_rub=Decimal("300.00"),
            status=PurchaseStatus.COMPLETED,
            fulfillment_status=PurchaseFulfillmentStatus.PENDING,
        )

        debit_mock = AccountLedgerEntry(
            id=501,
            user_id=42,
            entry_type="purchase_debit",
            amount=Decimal("-300.00"),
            quote_id=101,
            purchase_id=999,
        )

        grant_mock = EntitlementGrant(
            id=701,
            user_id=42,
            purchase_id=999,
            service_type="awg",
            source_type="purchase",
            source_id="999",
            coverage_start=base_time,
            coverage_end=base_time + timedelta(hours=720),
            original_duration_hours=720,
            paid_value_rub=Decimal("300.000000"),
            status=EntitlementGrantStatus.ACTIVE,
        )

        balance_snap = AccountBalanceSnapshot(
            accounting_position=Decimal("300.00"),
            available=Decimal("300.00"),
            reserved=Decimal("0.00"),
            debt=Decimal("0.00"),
            real_position=Decimal("300.00"),
            real_available=Decimal("300.00"),
            bonus_available=Decimal("0.00"),
        )

        mock_session.scalar.side_effect = [
            quote,           # query quote
            None,            # _settled_state debit
            None,            # _settled_state paid
            None,            # _settled_state entitlement
            tariff,          # tariff select
        ]
        mock_session.get.side_effect = lambda model, pk: version if model == TariffVersion else None

        with patch("services.account_purchase.lock_checkout_user", new_callable=AsyncMock, return_value=user), \
             patch("services.account_purchase.get_account_balance", new_callable=AsyncMock, return_value=balance_snap), \
             patch("services.account_purchase.get_or_create_current_version", new_callable=AsyncMock, return_value=version), \
             patch("services.account_purchase.get_user_profiles_count", new_callable=AsyncMock, return_value=0), \
             patch("services.account_purchase.purchases_repo.get_purchase_by_quote_id", new_callable=AsyncMock, return_value=None), \
             patch("services.account_purchase.purchases_repo.create_purchase", new_callable=AsyncMock, return_value=purchase_mock) as mock_create_purchase, \
             patch("services.account_purchase.create_purchase_debit", new_callable=AsyncMock, return_value=(debit_mock, True)) as mock_create_debit, \
             patch("services.account_purchase.subscription_coverage_service.append_awg_grant", new_callable=AsyncMock, return_value=grant_mock) as mock_append_grant, \
             patch("services.account_purchase.get_or_create_account_purchase_entry", new_callable=AsyncMock), \
             patch("services.account_purchase._get_or_create_entitlement", new_callable=AsyncMock, return_value=(MagicMock(), True)), \
             patch("services.account_purchase.invalidate_user_cache"), \
             patch("services.account_purchase.SubscriptionService._sync_access_state", new_callable=AsyncMock) as mock_sync_access, \
             patch("services.account_purchase.purchases_repo.mark_purchase_fulfilled", new_callable=AsyncMock) as mock_fulfill, \
             patch("services.account_purchase.AuditService.log_action", new_callable=AsyncMock):

            settlement = await _settle_account_purchase(
                mock_session,
                user_id=42,
                quote_public_id=quote.public_id,
            )

            self.assertTrue(settlement.created)
            self.assertEqual(quote.status, "consumed")

            # Verify Purchase created
            mock_create_purchase.assert_awaited_once()
            _, p_kwargs = mock_create_purchase.await_args
            self.assertEqual(p_kwargs["user_id"], 42)
            self.assertEqual(p_kwargs["quote_id"], 101)
            self.assertEqual(p_kwargs["amount_rub"], Decimal("300.00"))

            # Verify debit passed purchase_id
            mock_create_debit.assert_awaited_once_with(
                mock_session,
                user_id=42,
                quote_id=101,
                amount=Decimal("300.00"),
                purchase_id=999,
            )

            # Verify canonical grant created
            mock_append_grant.assert_awaited_once()
            _, g_kwargs = mock_append_grant.await_args
            self.assertEqual(g_kwargs["user_id"], 42)
            self.assertEqual(g_kwargs["duration_hours"], 720)
            self.assertEqual(g_kwargs["purchase_id"], 999)

            # Verify user projection and access sync called without double extend
            self.assertEqual(user.current_tariff_id, 1)
            self.assertEqual(user.device_limit, 2)

            self.assertFalse(user.notified_3d)
            mock_sync_access.assert_awaited_once_with(mock_session, user)

            mock_fulfill.assert_awaited_once_with(mock_session, purchase_mock)

    async def test_refund_purchase_reverses_ledger_and_revokes_grants(self):
        mock_session = AsyncMock(spec=AsyncSession)
        user = User(id=42, telegram_id=99942, is_deleted=False)
        purchase = Purchase(
            id=999,
            user_id=42,
            quote_id=101,
            idempotency_key="quote:101",
            service_type="awg",
            operation_type="purchase",
            amount_rub=Decimal("300.00"),
            status=PurchaseStatus.COMPLETED,
            fulfillment_status=PurchaseFulfillmentStatus.FULFILLED,
        )

        debit = AccountLedgerEntry(
            id=501,
            user_id=42,
            entry_type="purchase_debit",
            amount=Decimal("-300.00"),
            quote_id=101,
            purchase_id=999,
        )

        reversal = AccountLedgerEntry(
            id=502,
            user_id=42,
            entry_type="purchase_reversal",
            amount=Decimal("300.00"),
            quote_id=101,
            purchase_id=999,
            reversal_of_id=501,
        )

        grant = EntitlementGrant(
            id=701,
            user_id=42,
            purchase_id=999,
            status=EntitlementGrantStatus.ACTIVE,
        )

        mock_session.scalar.return_value = debit

        with patch("services.account_purchase.purchases_repo.get_purchase_by_id", new_callable=AsyncMock, return_value=purchase), \
             patch("services.account_purchase.lock_checkout_user", new_callable=AsyncMock, return_value=user), \
             patch("services.account_purchase.create_purchase_reversal", new_callable=AsyncMock, return_value=(reversal, True)) as mock_reversal, \
             patch("services.account_purchase.entitlement_grants_repo.get_grants_by_purchase_id", new_callable=AsyncMock, return_value=[grant]), \
             patch("services.account_purchase.entitlement_grants_repo.revoke_grants_by_ids", new_callable=AsyncMock) as mock_revoke_grants, \
             patch("services.account_purchase.subscription_coverage_service.sync_user_subscription_projection", new_callable=AsyncMock) as mock_sync_sub, \
             patch("services.account_purchase.invalidate_user_cache"), \
             patch("services.account_purchase.SubscriptionService._sync_access_state", new_callable=AsyncMock) as mock_sync_access, \
             patch("services.account_purchase.purchases_repo.mark_purchase_refunded", new_callable=AsyncMock) as mock_mark_refunded, \
             patch("services.account_purchase.AuditService.log_action", new_callable=AsyncMock):

            res = await refund_purchase(
                mock_session,
                purchase_id=999,
                reason="admin_cancel",
            )

            self.assertEqual(res.id, 999)
            mock_reversal.assert_awaited_once_with(
                mock_session,
                debit_id=501,
                metadata={"reason": "admin_cancel", "purchase_id": 999},
            )
            mock_revoke_grants.assert_awaited_once_with(mock_session, [701])
            mock_sync_sub.assert_awaited_once_with(mock_session, 42, locked_user=user)
            mock_sync_access.assert_awaited_once_with(mock_session, user)
            mock_mark_refunded.assert_awaited_once_with(mock_session, purchase)

    async def test_refund_purchase_rejects_non_awg_service_type(self):
        mock_session = AsyncMock(spec=AsyncSession)
        purchase = Purchase(
            id=999,
            user_id=42,
            quote_id=101,
            idempotency_key="quote:101",
            service_type="white_internet",
            operation_type="purchase",
            amount_rub=Decimal("150.00"),
            status=PurchaseStatus.COMPLETED,
            fulfillment_status=PurchaseFulfillmentStatus.FULFILLED,
        )

        with patch("services.account_purchase.purchases_repo.get_purchase_by_id", new_callable=AsyncMock, return_value=purchase):
            with self.assertRaises(NotImplementedError):
                await refund_purchase(mock_session, purchase_id=999)

    async def test_settle_account_purchase_single_extension_only(self):
        """Verify AWG purchase extends subscription exactly once (30 days -> 30 days, not 60 days)."""
        mock_session = AsyncMock(spec=AsyncSession)
        mock_session.flush = AsyncMock()
        mock_session.scalar = AsyncMock()
        mock_session.get = AsyncMock()

        base_time = datetime(2026, 9, 20, 12, 0, 0, tzinfo=timezone.utc)
        user = User(
            id=42,
            telegram_id=12345678,
            subscription_end=None,
            device_limit=1,
            financial_hold=False,
        )

        tariff = Tariff(
            id=1,
            name="AWG Standard",
            service_type="awg",
            duration_days=30,
            device_limit=2,
            is_active=True,
            price_rub=300,
        )

        version = TariffVersion(
            id=10,
            tariff_id=1,
            version_number=1,
            name_snapshot="AWG Standard",
            duration_hours=720,
            device_limit=2,
            price_rub=Decimal("300.00"),
            service_type="awg",
            currency="RUB",
        )

        quote = TariffQuote(
            id=101,
            public_id="quote_101",
            user_id=42,
            service_type="awg",
            target_tariff_version_id=10,
            operation_type="purchase",
            amount_due_rub=Decimal("300.00"),
            status=TariffQuoteStatus.ACTIVE.value,
            expires_at=base_time + timedelta(minutes=15),
            current_paid_hours=0,
            current_paid_value_rub=Decimal("0.00"),
            bonus_hours=0,
            resulting_paid_hours=720,
            resulting_paid_value_rub=Decimal("300.00"),
            resulting_bonus_hours=0,
            rounding_loss_hours=Decimal("0.0"),
            rounding_loss_value_rub=Decimal("0.0"),
            currency="RUB",
        )

        purchase_mock = Purchase(
            id=999,
            user_id=42,
            quote_id=101,
            idempotency_key="quote:101",
            service_type="awg",
            operation_type="purchase",
            amount_rub=Decimal("300.00"),
            status=PurchaseStatus.COMPLETED,
            fulfillment_status=PurchaseFulfillmentStatus.PENDING,
        )

        debit_mock = AccountLedgerEntry(
            id=501,
            user_id=42,
            entry_type="purchase_debit",
            amount=Decimal("-300.00"),
            quote_id=101,
            purchase_id=999,
        )

        balance_snap = AccountBalanceSnapshot(
            accounting_position=Decimal("300.00"),
            available=Decimal("300.00"),
            reserved=Decimal("0.00"),
            debt=Decimal("0.00"),
            real_position=Decimal("300.00"),
            real_available=Decimal("300.00"),
            bonus_available=Decimal("0.00"),
        )

        mock_session.scalar.side_effect = [
            quote,
            None,
            None,
            None,
            tariff,
        ]
        mock_session.get.side_effect = lambda model, pk: version if model == TariffVersion else None

        grant_created = EntitlementGrant(
            id=701,
            user_id=42,
            purchase_id=999,
            service_type="awg",
            source_type="purchase",
            source_id="999",
            coverage_start=base_time,
            coverage_end=base_time + timedelta(hours=720),
            original_duration_hours=720,
            paid_value_rub=Decimal("300.000000"),
            status=EntitlementGrantStatus.ACTIVE,
        )

        with patch("services.account_purchase.now_utc", return_value=base_time), \
             patch("services.account_purchase.lock_checkout_user", new_callable=AsyncMock, return_value=user), \
             patch("services.account_purchase.get_account_balance", new_callable=AsyncMock, return_value=balance_snap), \
             patch("services.account_purchase.get_or_create_current_version", new_callable=AsyncMock, return_value=version), \
             patch("services.account_purchase.get_user_profiles_count", new_callable=AsyncMock, return_value=0), \
             patch("services.account_purchase.purchases_repo.get_purchase_by_quote_id", new_callable=AsyncMock, return_value=None), \
             patch("services.account_purchase.purchases_repo.create_purchase", new_callable=AsyncMock, return_value=purchase_mock), \
             patch("services.account_purchase.create_purchase_debit", new_callable=AsyncMock, return_value=(debit_mock, True)), \
             patch("services.subscription_coverage_service.now_utc", return_value=base_time), \
             patch("services.subscription_coverage_service.entitlement_grants_repo.get_active_grants_for_user", new_callable=AsyncMock, return_value=[]), \
             patch("services.subscription_coverage_service.entitlement_grants_repo.create_grant", new_callable=AsyncMock, return_value=grant_created), \
             patch("services.account_purchase.get_or_create_account_purchase_entry", new_callable=AsyncMock), \
             patch("services.account_purchase._get_or_create_entitlement", new_callable=AsyncMock, return_value=(MagicMock(), True)), \
             patch("services.account_purchase.invalidate_user_cache"), \
             patch("services.account_purchase.SubscriptionService._sync_access_state", new_callable=AsyncMock), \
             patch("services.account_purchase.purchases_repo.mark_purchase_fulfilled", new_callable=AsyncMock), \
             patch("services.account_purchase.AuditService.log_action", new_callable=AsyncMock):

            settlement = await _settle_account_purchase(
                mock_session,
                user_id=42,
                quote_public_id=quote.public_id,
            )

            self.assertTrue(settlement.created)
            # CRITICAL INVARIANT: Exactly 30 days (720 hours) duration added, NOT 60 days (1440 hours)!
            self.assertEqual(user.subscription_end, base_time + timedelta(hours=720))
            self.assertNotEqual(user.subscription_end, base_time + timedelta(hours=1440))

