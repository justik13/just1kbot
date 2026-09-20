"""Unit tests for billing v2 backfill runner script."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from config.enums import (
    EntitlementGrantStatus,
    PurchaseFulfillmentStatus,
    PurchaseStatus,
    TariffQuoteOperation,
    TariffQuoteStatus,
)
from database.models import (
    AccountLedgerEntry,
    Purchase,
    Tariff,
    TariffQuote,
    TariffVersion,
    User,
)
from scripts.backfill_billing_v2 import backfill_billing_v2


class TestBackfillBillingV2(unittest.IsolatedAsyncioTestCase):
    async def test_backfill_billing_v2_flow(self):
        base_time = datetime(2026, 9, 20, 12, 0, 0, tzinfo=timezone.utc)

        tariff = Tariff(
            id=1,
            name="AWG 1 Month",
            service_type="awg",
            duration_days=30,
            device_limit=2,
        )
        t_ver = TariffVersion(
            id=10,
            tariff_id=1,
            version_number=1,
            name_snapshot="AWG 1 Month",
            duration_hours=720,
            device_limit=2,
            price_rub=Decimal("300.00"),
        )
        t_ver.tariff = tariff

        # 1. Normal consumed quote
        q_normal = TariffQuote(
            id=101,
            user_id=42,
            service_type="awg",
            target_tariff_version_id=10,
            operation_type=TariffQuoteOperation.PURCHASE.value,
            status=TariffQuoteStatus.CONSUMED.value,
            amount_due_rub=Decimal("300.00"),
            current_paid_hours=0,
            current_paid_value_rub=Decimal("0.00"),
            bonus_hours=0,
            resulting_paid_hours=720,
            resulting_paid_value_rub=Decimal("300.00"),
            resulting_bonus_hours=0,
            rounding_loss_hours=Decimal("0.0"),
            rounding_loss_value_rub=Decimal("0.0"),
            created_at=base_time - timedelta(days=5),
            consumed_at=base_time - timedelta(days=5),
            target_tariff_version=t_ver,
        )

        # 2. Trial quote (must be skipped)
        q_trial = TariffQuote(
            id=102,
            user_id=42,
            service_type="awg",
            target_tariff_version_id=10,
            operation_type=TariffQuoteOperation.TRIAL.value,
            status=TariffQuoteStatus.CONSUMED.value,
            amount_due_rub=Decimal("0.00"),
            current_paid_hours=0,
            current_paid_value_rub=Decimal("0.00"),
            bonus_hours=0,
            resulting_paid_hours=72,
            resulting_paid_value_rub=Decimal("0.00"),
            resulting_bonus_hours=0,
            rounding_loss_hours=Decimal("0.0"),
            rounding_loss_value_rub=Decimal("0.0"),
            target_tariff_version=t_ver,
        )

        # Ledger entry to link
        ledger_entry = AccountLedgerEntry(
            id=501,
            user_id=42,
            entry_type="purchase_debit",
            amount=Decimal("-300.00"),
            quote_id=101,
            purchase_id=None,
        )

        # User with active subscription
        user = User(
            id=42,
            telegram_id=12345678,
            subscription_end=base_time + timedelta(days=25),
            device_limit=2,
        )

        # Mock legacy snapshot
        mock_paid_lot = MagicMock()
        mock_paid_lot.entitlement_entry_id = 1
        mock_paid_lot.paid_value_ledger_entry_id = 1
        mock_paid_lot.quote_id = 101
        mock_paid_lot.segment_start = base_time - timedelta(days=5)
        mock_paid_lot.segment_end = base_time + timedelta(days=25)
        mock_paid_lot.original_paid_hours = 720
        mock_paid_lot.original_paid_value_rub = Decimal("300.000000")
        mock_paid_lot.tariff_version_id = 10

        mock_snapshot = MagicMock()
        mock_snapshot.tracked = True
        mock_snapshot.paid_lots = (mock_paid_lot,)
        mock_snapshot.bonus_lots = ()

        mock_session = AsyncMock()
        def on_add(obj):
            if isinstance(obj, Purchase) and obj.id is None:
                obj.id = 888

        mock_session.add = MagicMock(side_effect=on_add)
        mock_session.flush = AsyncMock()
        mock_session.commit = AsyncMock()
        mock_session.rollback = AsyncMock()

        quotes_res = MagicMock()
        quotes_res.all.return_value = [q_normal, q_trial]

        ledger_res = MagicMock()
        ledger_res.all.return_value = [ledger_entry]

        users_res = MagicMock()
        users_res.all.return_value = [user]

        mock_session.scalars.side_effect = [quotes_res, ledger_res, users_res]
        mock_session.scalar.return_value = None

        mock_grant = MagicMock()
        mock_grant.coverage_end = base_time + timedelta(days=25)

        with patch("scripts.backfill_billing_v2.session_scope") as mock_scope, patch(
            "scripts.backfill_billing_v2.get_subscription_balance_snapshot",
            new_callable=AsyncMock,
            return_value=mock_snapshot,
        ), patch(
            "database.repositories.entitlement_grants_repo.get_active_grants_for_user",
            new_callable=AsyncMock,
            side_effect=[[], [mock_grant]],
        ), patch(
            "database.repositories.entitlement_grants_repo.create_grant",
            new_callable=AsyncMock,
        ) as mock_create_grant:
            mock_scope.return_value.__aenter__.return_value = mock_session

            # Run with commit=True
            await backfill_billing_v2(commit=True)

            # 1. Assert normal quote added as Purchase
            added_purchases = [call.args[0] for call in mock_session.add.call_args_list if isinstance(call.args[0], Purchase)]
            self.assertEqual(len(added_purchases), 1)
            purchase = added_purchases[0]
            self.assertEqual(purchase.quote_id, 101)
            self.assertEqual(purchase.service_type, "awg")
            self.assertEqual(purchase.amount_rub, Decimal("300.00"))
            self.assertEqual(purchase.status, PurchaseStatus.COMPLETED)
            self.assertEqual(purchase.fulfillment_status, PurchaseFulfillmentStatus.FULFILLED)

            # 2. Assert ledger entry linked
            self.assertEqual(ledger_entry.purchase_id, purchase.id)

            # 3. Assert EntitlementGrant created from paid lot
            mock_create_grant.assert_awaited_once()
            _, grant_kwargs = mock_create_grant.await_args
            self.assertEqual(grant_kwargs["user_id"], 42)
            self.assertEqual(grant_kwargs["purchase_id"], purchase.id)
            self.assertEqual(grant_kwargs["original_duration_hours"], 720)
            self.assertEqual(grant_kwargs["paid_value_rub"], Decimal("300.000000"))
            self.assertEqual(grant_kwargs["status"], EntitlementGrantStatus.ACTIVE)

            # 4. Assert session.commit was called
            mock_session.commit.assert_awaited_once()

    async def test_backfill_billing_v2_white_internet_addon_and_grant_idempotency(self):
        base_time = datetime(2026, 9, 20, 12, 0, 0, tzinfo=timezone.utc)

        tariff = Tariff(
            id=2,
            name="White Internet Base",
            service_type="white_internet",
            duration_days=30,
            device_limit=1,
            price_rub=Decimal("150.00"),
        )
        t_ver = TariffVersion(
            id=20,
            tariff_id=2,
            version_number=1,
            name_snapshot="White Internet Base",
            duration_hours=720,
            device_limit=1,
            price_rub=Decimal("150.00"),
        )
        t_ver.tariff = tariff

        # WI add-on quote: price 50.00 < 150.00 base price
        q_addon = TariffQuote(
            id=202,
            user_id=99,
            service_type="white_internet",
            target_tariff_version_id=20,
            operation_type=TariffQuoteOperation.PURCHASE.value,
            status=TariffQuoteStatus.CONSUMED.value,
            amount_due_rub=Decimal("50.00"),
            current_paid_hours=0,
            current_paid_value_rub=Decimal("0.00"),
            bonus_hours=0,
            resulting_paid_hours=0,
            resulting_paid_value_rub=Decimal("50.00"),
            resulting_bonus_hours=0,
            rounding_loss_hours=Decimal("0.0"),
            rounding_loss_value_rub=Decimal("0.0"),
            created_at=base_time,
            consumed_at=base_time,
            target_tariff_version=t_ver,
        )

        user = User(
            id=99,
            telegram_id=999999,
            subscription_end=base_time + timedelta(days=10),
            device_limit=1,
        )

        mock_paid_lot = MagicMock()
        mock_paid_lot.entitlement_entry_id = 2
        mock_paid_lot.paid_value_ledger_entry_id = 2
        mock_paid_lot.quote_id = 202
        mock_paid_lot.segment_start = base_time
        mock_paid_lot.segment_end = base_time + timedelta(days=10)
        mock_paid_lot.original_paid_hours = 240
        mock_paid_lot.original_paid_value_rub = Decimal("50.000000")
        mock_paid_lot.tariff_version_id = 20

        mock_snapshot = MagicMock()
        mock_snapshot.tracked = True
        mock_snapshot.paid_lots = (mock_paid_lot,)
        mock_snapshot.bonus_lots = ()

        mock_session = AsyncMock()
        def on_add(obj):
            if isinstance(obj, Purchase) and obj.id is None:
                obj.id = 999

        mock_session.add = MagicMock(side_effect=on_add)
        mock_session.flush = AsyncMock()
        mock_session.commit = AsyncMock()
        mock_session.rollback = AsyncMock()

        quotes_res = MagicMock()
        quotes_res.all.return_value = [q_addon]

        ledger_res = MagicMock()
        ledger_res.all.return_value = []

        users_res = MagicMock()
        users_res.all.return_value = [user]

        mock_session.scalars.side_effect = [quotes_res, ledger_res, users_res]

        # Simulate existing grant in DB (e.g. revoked or previously migrated)
        existing_grant = MagicMock()
        existing_grant.id = 777
        def scalar_mock(stmt):
            if "purchases" in str(stmt):
                return None
            return existing_grant
        mock_session.scalar.side_effect = scalar_mock

        with patch("scripts.backfill_billing_v2.session_scope") as mock_scope, patch(
            "scripts.backfill_billing_v2.get_subscription_balance_snapshot",
            new_callable=AsyncMock,
            return_value=mock_snapshot,
        ), patch(
            "database.repositories.entitlement_grants_repo.get_active_grants_for_user",
            new_callable=AsyncMock,
            return_value=[],
        ), patch(
            "database.repositories.entitlement_grants_repo.create_grant",
            new_callable=AsyncMock,
        ) as mock_create_grant:
            mock_scope.return_value.__aenter__.return_value = mock_session

            await backfill_billing_v2(commit=True)

            # Assert WI add-on Purchase has duration_days=0 and op_type="addon"
            added_purchases = [call.args[0] for call in mock_session.add.call_args_list if isinstance(call.args[0], Purchase)]
            self.assertEqual(len(added_purchases), 1)
            p = added_purchases[0]
            self.assertEqual(p.service_type, "white_internet")
            self.assertEqual(p.operation_type, "addon")
            self.assertEqual(p.duration_days, 0)
            self.assertEqual(p.amount_rub, Decimal("50.00"))

            # Because existing_grant was found, create_grant MUST NOT be called (idempotency)
            mock_create_grant.assert_not_called()

