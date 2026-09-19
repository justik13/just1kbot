from datetime import datetime, timedelta, timezone
from decimal import Decimal
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from database.models import (
    AccountLedgerAllocation,
    AccountLedgerEntry,
    Payment,
    Tariff,
    TariffQuote,
    TariffVersion,
    User,
)
from services.payment_provider_state import (
    PaymentFulfillmentStatus,
    PaymentProviderStatus,
    PaymentReconciliationStatus,
    apply_provider_transition,
)
from services.provider_refunds import apply_balance_topup_refund_success, place_financial_hold


class YooKassaCleanRefundsTests(unittest.IsolatedAsyncioTestCase):
    @patch("services.provider_refunds._update_topup_after_refund", new_callable=AsyncMock)
    @patch("services.referral_bonus.reverse_referral_bonus_for_topup", new_callable=AsyncMock)
    @patch("services.provider_refunds.create_payment_debit", new_callable=AsyncMock)
    @patch("services.provider_refunds._get_or_create_payment_refund", new_callable=AsyncMock)
    @patch("services.subscription.SubscriptionService._sync_access_state", new_callable=AsyncMock)
    @patch("database.repositories.account_ledger_repo.create_purchase_reversal", new_callable=AsyncMock)
    async def test_full_refund_awg_reduces_duration_and_syncs_nodes(
        self,
        mock_reversal,
        mock_sync,
        mock_get_refund,
        mock_debit,
        mock_rev_bonus,
        mock_update_topup,
    ):
        """Full refund of AWG purchase cuts only the tariff duration and syncs nodes when expired."""
        mock_debit.return_value = (MagicMock(), True)

        now = datetime.now(timezone.utc)
        user = User(
            id=1,
            telegram_id=1001,
            subscription_end=now + timedelta(days=30),
        )

        payment = Payment(
            id=10,
            user_id=1,
            amount=Decimal("250.00"),
            currency="RUB",
            topup_context={
                "auto_fulfill_action": "purchase",
                "quote_public_id": "00000000-0000-0000-0000-000000000001",
            },
            provider_status="succeeded",
            credited_at=now,
        )

        tariff = Tariff(id=5, service_type="awg", name="Standard AWG")
        t_version = TariffVersion(id=2, tariff_id=5, duration_hours=720, price_rub=Decimal("250.00"))
        quote = TariffQuote(
            id=100,
            public_id="00000000-0000-0000-0000-000000000001",
            user_id=1,
            target_tariff_version_id=2,
            status="consumed",
        )

        session = AsyncMock()
        session.add = MagicMock()
        def scalar_side_effect(stmt):
            stmt_str = str(stmt).lower()
            if "provider_refund_operations" in stmt_str:
                return None
            if "payment_refunds" in stmt_str:
                return Decimal(0)
            if "account_balance_reservations" in stmt_str:
                return None
            if "account_ledger_entries" in stmt_str:
                return None
            if "tariff_quotes" in stmt_str:
                return quote
            if "users" in stmt_str:
                return user
            return None

        session.scalar = AsyncMock(side_effect=scalar_side_effect)

        async def get_side_effect(model, ident):
            if model == TariffQuote:
                return quote
            if model == TariffVersion:
                return t_version
            if model == Tariff:
                return tariff
            return None

        session.get = AsyncMock(side_effect=get_side_effect)
        scalars_mock = MagicMock()
        scalars_mock.all.return_value = []
        session.scalars = AsyncMock(return_value=scalars_mock)

        nested_mock = AsyncMock()
        nested_mock.__aenter__ = AsyncMock()
        nested_mock.__aexit__ = AsyncMock()
        session.begin_nested = MagicMock(return_value=nested_mock)

        await apply_balance_topup_refund_success(
            session,
            payment=payment,
            provider_refund_id="rf_101",
            amount=Decimal("250.00"),
            currency="RUB",
            event_key="evt_101",
        )

        # 30 days was subtracted, so new_end <= now, leading to expired_threshold
        self.assertLessEqual(user.subscription_end, now)
        # Node access sync was called to disable peers
        mock_sync.assert_awaited_once_with(session, user)
        # Quote status is read-only and preserved as consumed (no DB trigger violations)
        self.assertEqual(quote.status, "consumed")

    @patch("services.provider_refunds._update_topup_after_refund", new_callable=AsyncMock)
    @patch("services.referral_bonus.reverse_referral_bonus_for_topup", new_callable=AsyncMock)
    @patch("services.provider_refunds.create_payment_debit", new_callable=AsyncMock)
    @patch("services.provider_refunds._get_or_create_payment_refund", new_callable=AsyncMock)
    @patch("services.subscription.SubscriptionService._sync_access_state", new_callable=AsyncMock)
    async def test_full_refund_multiple_payments_keeps_other_payment_time(
        self,
        mock_sync,
        mock_get_refund,
        mock_debit,
        mock_rev_bonus,
        mock_update_topup,
    ):
        """Refunding one 30-day payment when user has 60 days leaves remaining 30 days active."""
        mock_debit.return_value = (MagicMock(), True)

        now = datetime.now(timezone.utc)
        # User had 60 days (paid twice)
        user = User(
            id=1,
            telegram_id=1001,
            subscription_end=now + timedelta(days=60),
        )

        payment = Payment(
            id=10,
            user_id=1,
            amount=Decimal("250.00"),
            currency="RUB",
            topup_context={
                "auto_fulfill_action": "purchase",
                "quote_public_id": "00000000-0000-0000-0000-000000000001",
            },
            provider_status="succeeded",
            credited_at=now,
        )

        tariff = Tariff(id=5, service_type="awg", name="Standard AWG")
        t_version = TariffVersion(id=2, tariff_id=5, duration_hours=720, price_rub=Decimal("250.00"))
        quote = TariffQuote(
            id=100,
            public_id="00000000-0000-0000-0000-000000000001",
            user_id=1,
            target_tariff_version_id=2,
        )

        session = AsyncMock()
        session.add = MagicMock()
        def scalar_side_effect(stmt):
            stmt_str = str(stmt).lower()
            if "provider_refund_operations" in stmt_str:
                return None
            if "payment_refunds" in stmt_str:
                return Decimal(0)
            if "account_balance_reservations" in stmt_str:
                return None
            if "account_ledger_entries" in stmt_str:
                return None
            if "tariff_quotes" in stmt_str:
                return quote
            if "users" in stmt_str:
                return user
            return None

        session.scalar = AsyncMock(side_effect=scalar_side_effect)

        async def get_side_effect(model, ident):
            if model == TariffQuote:
                return quote
            if model == TariffVersion:
                return t_version
            if model == Tariff:
                return tariff
            return None

        session.get = AsyncMock(side_effect=get_side_effect)
        scalars_mock = MagicMock()
        scalars_mock.all.return_value = []
        session.scalars = AsyncMock(return_value=scalars_mock)

        nested_mock = AsyncMock()
        nested_mock.__aenter__ = AsyncMock()
        nested_mock.__aexit__ = AsyncMock()
        session.begin_nested = MagicMock(return_value=nested_mock)

        await apply_balance_topup_refund_success(
            session,
            payment=payment,
            provider_refund_id="rf_102",
            amount=Decimal("250.00"),
            currency="RUB",
            event_key="evt_102",
        )

        # 30 days subtracted from 60 days -> remaining is ~30 days, definitely still > now
        self.assertGreater(user.subscription_end, now + timedelta(days=28))
        # Subscription still active, so nodes are synced without expiring
        mock_sync.assert_awaited_once_with(session, user)

    @patch("services.provider_refunds._update_topup_after_refund", new_callable=AsyncMock)
    @patch("services.referral_bonus.reverse_referral_bonus_for_topup", new_callable=AsyncMock)
    @patch("services.provider_refunds.create_payment_debit", new_callable=AsyncMock)
    @patch("services.provider_refunds._get_or_create_payment_refund", new_callable=AsyncMock)
    async def test_unspent_topup_refund_does_not_touch_subscriptions(
        self,
        mock_get_refund,
        mock_debit,
        mock_rev_bonus,
        mock_update_topup,
    ):
        """Refunding an unspent balance top-up debits wallet but does not touch user subscription."""
        mock_debit.return_value = (MagicMock(), True)

        now = datetime.now(timezone.utc)
        original_end = now + timedelta(days=25)
        user = User(
            id=1,
            telegram_id=1001,
            subscription_end=original_end,
        )

        # No auto_fulfill_action, no quote: pure balance topup
        payment = Payment(
            id=11,
            user_id=1,
            amount=Decimal("250.00"),
            currency="RUB",
            topup_context={},
            provider_status="succeeded",
            credited_at=now,
        )

        session = AsyncMock()
        session.add = MagicMock()
        def scalar_side_effect(stmt):
            stmt_str = str(stmt).lower()
            if "provider_refund_operations" in stmt_str:
                return None
            if "payment_refunds" in stmt_str:
                return Decimal(0)
            if "account_balance_reservations" in stmt_str:
                return None
            if "account_ledger_entries" in stmt_str:
                return None
            return None

        session.scalar = AsyncMock(side_effect=scalar_side_effect)
        scalars_mock = MagicMock()
        scalars_mock.all.return_value = []
        session.scalars = AsyncMock(return_value=scalars_mock)

        nested_mock = AsyncMock()
        nested_mock.__aenter__ = AsyncMock()
        nested_mock.__aexit__ = AsyncMock()
        session.begin_nested = MagicMock(return_value=nested_mock)

        await apply_balance_topup_refund_success(
            session,
            payment=payment,
            provider_refund_id="rf_103",
            amount=Decimal("250.00"),
            currency="RUB",
            event_key="evt_103",
        )

        # Subscription end was NOT touched at all!
        self.assertEqual(user.subscription_end, original_end)

    @patch("services.provider_refunds._update_topup_after_refund", new_callable=AsyncMock)
    @patch("services.referral_bonus.reverse_referral_bonus_for_topup", new_callable=AsyncMock)
    @patch("services.provider_refunds.create_payment_debit", new_callable=AsyncMock)
    @patch("services.provider_refunds._get_or_create_payment_refund", new_callable=AsyncMock)
    async def test_full_refund_white_internet_deactivates_wi_and_preserves_awg(
        self,
        mock_get_refund,
        mock_debit,
        mock_rev_bonus,
        mock_update_topup,
    ):
        """Refunding White Internet deactivates Xray subscription and never touches AWG subscription."""
        from config.enums import WhiteInternetProvisioningStatus, WhiteInternetStatus
        from database.models import WhiteInternetSubscription

        mock_debit.return_value = (MagicMock(), True)

        now = datetime.now(timezone.utc)
        original_awg_end = now + timedelta(days=20)
        user = User(
            id=1,
            telegram_id=1001,
            subscription_end=original_awg_end,
        )

        payment = Payment(
            id=15,
            user_id=1,
            amount=Decimal("350.00"),
            currency="RUB",
            topup_context={"auto_fulfill_action": "white_internet_buy"},
            provider_status="succeeded",
            credited_at=now,
        )

        wi_sub = WhiteInternetSubscription(
            id=77,
            user_id=1,
            status=WhiteInternetStatus.ACTIVE,
            provisioning_status=WhiteInternetProvisioningStatus.ACTIVE,
            desired_version=1,
        )

        session = AsyncMock()
        session.add = MagicMock()
        def scalar_side_effect(stmt):
            stmt_str = str(stmt).lower()
            if "provider_refund_operations" in stmt_str:
                return None
            if "payment_refunds" in stmt_str:
                return Decimal(0)
            if "account_balance_reservations" in stmt_str:
                return None
            if "account_ledger_entries" in stmt_str:
                return None
            return None

        session.scalar = AsyncMock(side_effect=scalar_side_effect)
        def scalars_side_effect(stmt):
            stmt_str = str(stmt).lower()
            res = MagicMock()
            if "white_internet_subscriptions" in stmt_str:
                res.all.return_value = [wi_sub]
            else:
                res.all.return_value = []
            return res

        session.scalars = AsyncMock(side_effect=scalars_side_effect)

        nested_mock = AsyncMock()
        nested_mock.__aenter__ = AsyncMock()
        nested_mock.__aexit__ = AsyncMock()
        session.begin_nested = MagicMock(return_value=nested_mock)

        await apply_balance_topup_refund_success(
            session,
            payment=payment,
            provider_refund_id="rf_105",
            amount=Decimal("350.00"),
            currency="RUB",
            event_key="evt_105",
        )

        # White Internet subscription was disabled and marked for deletion
        self.assertEqual(wi_sub.status, WhiteInternetStatus.DISABLED)
        self.assertEqual(wi_sub.provisioning_status, WhiteInternetProvisioningStatus.PENDING_DELETE)
        self.assertEqual(wi_sub.status_reason, "payment_refunded")
        self.assertEqual(wi_sub.desired_version, 2)

        # AWG subscription was completely untouched
        self.assertEqual(user.subscription_end, original_awg_end)

    @patch("services.provider_refunds._update_topup_after_refund", new_callable=AsyncMock)
    @patch("services.referral_bonus.reverse_referral_bonus_for_topup", new_callable=AsyncMock)
    @patch("services.provider_refunds.create_payment_debit", new_callable=AsyncMock)
    @patch("services.provider_refunds._get_or_create_payment_refund", new_callable=AsyncMock)
    async def test_partial_refund_of_service_routes_to_manual_review(
        self,
        mock_get_refund,
        mock_debit,
        mock_rev_bonus,
        mock_update_topup,
    ):
        """Partial refund of an indivisible service sets manual_review instead of corrupting days."""
        mock_debit.return_value = (MagicMock(), True)

        now = datetime.now(timezone.utc)
        payment = Payment(
            id=12,
            user_id=1,
            amount=Decimal("250.00"),
            currency="RUB",
            topup_context={"auto_fulfill_action": "purchase"},
            provider_status="succeeded",
            credited_at=now,
        )

        session = AsyncMock()
        session.add = MagicMock()
        def scalar_side_effect(stmt):
            stmt_str = str(stmt).lower()
            if "provider_refund_operations" in stmt_str:
                return None
            if "payment_refunds" in stmt_str:
                return Decimal(0)
            if "account_balance_reservations" in stmt_str:
                return None
            if "account_ledger_entries" in stmt_str:
                return None
            return None

        session.scalar = AsyncMock(side_effect=scalar_side_effect)
        scalars_mock = MagicMock()
        scalars_mock.all.return_value = []
        session.scalars = AsyncMock(return_value=scalars_mock)

        nested_mock = AsyncMock()
        nested_mock.__aenter__ = AsyncMock()
        nested_mock.__aexit__ = AsyncMock()
        session.begin_nested = MagicMock(return_value=nested_mock)

        # Partial refund of 50 RUB out of 250 RUB
        await apply_balance_topup_refund_success(
            session,
            payment=payment,
            provider_refund_id="rf_104",
            amount=Decimal("50.00"),
            currency="RUB",
            event_key="evt_104",
        )

        self.assertEqual(payment.reconciliation_status, "manual_review")
        self.assertEqual(payment.manual_review_reason, "partial_refund_requires_admin_review")
        # Ensure debit was NOT created (no fake debt)
        mock_debit.assert_not_called()

    @patch("services.provider_refunds._update_topup_after_refund", new_callable=AsyncMock)
    @patch("services.referral_bonus.reverse_referral_bonus_for_topup", new_callable=AsyncMock)
    @patch("services.provider_refunds.create_payment_debit", new_callable=AsyncMock)
    @patch("services.provider_refunds._get_or_create_payment_refund", new_callable=AsyncMock)
    async def test_uncredited_payment_refund_does_not_debit_or_cut_subscription(
        self,
        mock_get_refund,
        mock_debit,
        mock_rev_bonus,
        mock_update_topup,
    ):
        """Uncredited payment refund records reversal without balance debit or cutting subscription."""
        now = datetime.now(timezone.utc)
        original_end = now + timedelta(days=30)
        user = User(
            id=1,
            telegram_id=1001,
            subscription_end=original_end,
            financial_hold=False,
        )

        payment = Payment(
            id=20,
            user_id=1,
            amount=Decimal("250.00"),
            currency="RUB",
            topup_context={"auto_fulfill_action": "purchase"},
            provider_status="succeeded",
            credited_at=None,
        )

        session = AsyncMock()
        session.add = MagicMock()
        session.scalar = AsyncMock(return_value=None)
        scalars_mock = MagicMock()
        scalars_mock.all.return_value = []
        session.scalars = AsyncMock(return_value=scalars_mock)

        nested_mock = AsyncMock()
        nested_mock.__aenter__ = AsyncMock()
        nested_mock.__aexit__ = AsyncMock()
        session.begin_nested = MagicMock(return_value=nested_mock)

        await apply_balance_topup_refund_success(
            session,
            payment=payment,
            provider_refund_id="rf_201",
            amount=Decimal("250.00"),
            currency="RUB",
            event_key="evt_201",
        )

        mock_update_topup.assert_awaited_once_with(session, payment)
        mock_debit.assert_not_called()
        self.assertEqual(user.subscription_end, original_end)
        self.assertFalse(user.financial_hold)

    @patch("services.provider_refunds._update_topup_after_refund", new_callable=AsyncMock)
    @patch("services.referral_bonus.reverse_referral_bonus_for_topup", new_callable=AsyncMock)
    @patch("services.provider_refunds.create_payment_debit", new_callable=AsyncMock)
    @patch("services.provider_refunds._get_or_create_payment_refund", new_callable=AsyncMock)
    async def test_tariff_change_refund_routes_to_manual_review(
        self,
        mock_get_refund,
        mock_debit,
        mock_rev_bonus,
        mock_update_topup,
    ):
        """Tariff change refund routes to manual review without tampering with subscription end."""
        now = datetime.now(timezone.utc)
        original_end = now + timedelta(days=15)
        user = User(
            id=1,
            telegram_id=1001,
            subscription_end=original_end,
        )

        payment = Payment(
            id=21,
            user_id=1,
            amount=Decimal("150.00"),
            currency="RUB",
            topup_context={"auto_fulfill_action": "tariff_change"},
            provider_status="succeeded",
            credited_at=now,
        )

        session = AsyncMock()
        session.add = MagicMock()
        def scalar_side_effect(stmt):
            stmt_str = str(stmt).lower()
            if "provider_refund_operations" in stmt_str:
                return None
            if "payment_refunds" in stmt_str:
                return Decimal(0)
            if "account_balance_reservations" in stmt_str:
                return None
            if "account_ledger_entries" in stmt_str:
                return None
            if "users" in stmt_str:
                return user
            return None

        session.scalar = AsyncMock(side_effect=scalar_side_effect)
        scalars_mock = MagicMock()
        scalars_mock.all.return_value = []
        session.scalars = AsyncMock(return_value=scalars_mock)

        nested_mock = AsyncMock()
        nested_mock.__aenter__ = AsyncMock()
        nested_mock.__aexit__ = AsyncMock()
        session.begin_nested = MagicMock(return_value=nested_mock)

        await apply_balance_topup_refund_success(
            session,
            payment=payment,
            provider_refund_id="rf_202",
            amount=Decimal("150.00"),
            currency="RUB",
            event_key="evt_202",
        )

        self.assertEqual(payment.reconciliation_status, "manual_review")
        self.assertEqual(payment.manual_review_reason, "tariff_change_refund_requires_admin_review")
        self.assertEqual(user.subscription_end, original_end)
        mock_debit.assert_not_called()

    @patch("services.provider_refunds._update_topup_after_refund", new_callable=AsyncMock)
    @patch("services.referral_bonus.reverse_referral_bonus_for_topup", new_callable=AsyncMock)
    @patch("services.provider_refunds.create_payment_debit", new_callable=AsyncMock)
    @patch("services.provider_refunds._get_or_create_payment_refund", new_callable=AsyncMock)
    async def test_failed_auto_fulfill_treated_as_unspent_balance(
        self,
        mock_get_refund,
        mock_debit,
        mock_rev_bonus,
        mock_update_topup,
    ):
        """Topup with failed auto-fulfillment is treated as unspent balance; subscription untouched."""
        mock_debit.return_value = (MagicMock(), True)

        now = datetime.now(timezone.utc)
        original_end = now + timedelta(days=10)
        user = User(
            id=1,
            telegram_id=1001,
            subscription_end=original_end,
        )

        payment = Payment(
            id=22,
            user_id=1,
            amount=Decimal("250.00"),
            currency="RUB",
            topup_context={
                "auto_fulfill_action": "purchase",
                "auto_fulfill_status": "failed",
            },
            provider_status="succeeded",
            credited_at=now,
        )

        session = AsyncMock()
        session.add = MagicMock()
        def scalar_side_effect(stmt):
            stmt_str = str(stmt).lower()
            if "provider_refund_operations" in stmt_str:
                return None
            if "payment_refunds" in stmt_str:
                return Decimal(0)
            if "account_balance_reservations" in stmt_str:
                return None
            if "account_ledger_entries" in stmt_str:
                return None
            return None

        session.scalar = AsyncMock(side_effect=scalar_side_effect)
        scalars_mock = MagicMock()
        scalars_mock.all.return_value = []
        session.scalars = AsyncMock(return_value=scalars_mock)

        nested_mock = AsyncMock()
        nested_mock.__aenter__ = AsyncMock()
        nested_mock.__aexit__ = AsyncMock()
        session.begin_nested = MagicMock(return_value=nested_mock)

        await apply_balance_topup_refund_success(
            session,
            payment=payment,
            provider_refund_id="rf_203",
            amount=Decimal("250.00"),
            currency="RUB",
            event_key="evt_203",
        )

        # Debited from balance
        mock_debit.assert_called_once()
        # Subscription was not cut
        self.assertEqual(user.subscription_end, original_end)

    async def test_reconciliation_status_full_refund_ok(self):
        """Full refund verification sets reconciliation status to OK."""
        session = MagicMock()
        payment = Payment(
            id=101,
            amount=Decimal("250.00"),
            currency="RUB",
            public_order_id="topup_101",
            provider_status=PaymentProviderStatus.REFUNDED.value,
            fulfillment_status=PaymentFulfillmentStatus.REVERSED.value,
            reconciliation_status=PaymentReconciliationStatus.OK.value,
        )
        data = {
            "id": "2d3e4f5a-000f-5000-8000-123456789abc",
            "status": "succeeded",
            "captured_at": "2026-09-18T10:00:00.000Z",
            "amount": {"value": "250.00", "currency": "RUB"},
            "refunded_amount": {"value": "250.00", "currency": "RUB"},
            "metadata": {"order_id": "topup_101", "local_payment_id": "101"},
        }

        transition = await apply_provider_transition(
            session,
            payment,
            data,
            source="reconciliation",
        )

        self.assertEqual(transition.outcome, "applied")
        self.assertEqual(payment.reconciliation_status, PaymentReconciliationStatus.OK.value)

    async def test_reconciliation_status_partial_refund_mismatch(self):
        """Partial refund reported by YooKassa does NOT mark locally full-refunded payment as OK."""
        session = MagicMock()
        payment = Payment(
            id=101,
            amount=Decimal("250.00"),
            currency="RUB",
            public_order_id="topup_101",
            provider_status=PaymentProviderStatus.REFUNDED.value,
            fulfillment_status=PaymentFulfillmentStatus.REVERSED.value,
            reconciliation_status=PaymentReconciliationStatus.MISMATCH.value,
        )
        # YooKassa says refunded_amount is only 50 RUB, while local payment expects full refund (250 RUB)
        data = {
            "id": "2d3e4f5a-000f-5000-8000-123456789abc",
            "status": "succeeded",
            "captured_at": "2026-09-18T10:00:00.000Z",
            "amount": {"value": "250.00", "currency": "RUB"},
            "refunded_amount": {"value": "50.00", "currency": "RUB"},
            "refundable": False,
            "metadata": {"order_id": "topup_101", "local_payment_id": "101"},
        }

        transition = await apply_provider_transition(
            session,
            payment,
            data,
            source="reconciliation",
        )

        self.assertEqual(transition.outcome, "conflict")
        self.assertNotEqual(payment.reconciliation_status, PaymentReconciliationStatus.OK.value)

    @patch("services.provider_refunds._update_topup_after_refund", new_callable=AsyncMock)
    @patch("services.referral_bonus.reverse_referral_bonus_for_topup", new_callable=AsyncMock)
    @patch("services.provider_refunds.create_payment_debit", new_callable=AsyncMock)
    @patch("services.provider_refunds._get_or_create_payment_refund", new_callable=AsyncMock)
    @patch("services.subscription.SubscriptionService._sync_access_state", new_callable=AsyncMock)
    async def test_duplicate_webhook_does_not_double_cut_subscription(
        self,
        mock_sync,
        mock_get_refund,
        mock_debit,
        mock_rev_bonus,
        mock_update_topup,
    ):
        """When created_debit is False (duplicate webhook), side effects like date cutting are skipped."""
        mock_debit.return_value = (MagicMock(), False)  # already created earlier

        now = datetime.now(timezone.utc)
        original_end = now + timedelta(days=20)
        user = User(
            id=1,
            telegram_id=1001,
            subscription_end=original_end,
        )

        payment = Payment(
            id=30,
            user_id=1,
            amount=Decimal("250.00"),
            currency="RUB",
            topup_context={
                "auto_fulfill_action": "purchase",
                "quote_public_id": "00000000-0000-0000-0000-000000000001",
            },
            provider_status="succeeded",
            credited_at=now,
        )

        tariff = Tariff(id=5, service_type="awg", name="Standard AWG")
        t_version = TariffVersion(id=2, tariff_id=5, duration_hours=720, price_rub=Decimal("250.00"))
        quote = TariffQuote(
            id=100,
            public_id="00000000-0000-0000-0000-000000000001",
            user_id=1,
            target_tariff_version_id=2,
            status="consumed",
        )

        session = AsyncMock()
        session.add = MagicMock()
        def scalar_side_effect(stmt):
            stmt_str = str(stmt).lower()
            if "provider_refund_operations" in stmt_str:
                return None
            if "payment_refunds" in stmt_str:
                return Decimal(0)
            if "account_balance_reservations" in stmt_str:
                return None
            if "account_ledger_entries" in stmt_str:
                return None
            if "tariff_quotes" in stmt_str:
                return quote
            if "users" in stmt_str:
                return user
            return None

        session.scalar = AsyncMock(side_effect=scalar_side_effect)

        async def get_side_effect(model, ident):
            if model == TariffQuote:
                return quote
            if model == TariffVersion:
                return t_version
            if model == Tariff:
                return tariff
            return None

        session.get = AsyncMock(side_effect=get_side_effect)
        scalars_mock = MagicMock()
        scalars_mock.all.return_value = []
        session.scalars = AsyncMock(return_value=scalars_mock)

        nested_mock = AsyncMock()
        nested_mock.__aenter__ = AsyncMock()
        nested_mock.__aexit__ = AsyncMock()
        session.begin_nested = MagicMock(return_value=nested_mock)

        await apply_balance_topup_refund_success(
            session,
            payment=payment,
            provider_refund_id="rf_duplicate",
            amount=Decimal("250.00"),
            currency="RUB",
            event_key="evt_duplicate",
        )

        # Subscription end was NOT cut again because created_debit was False
        self.assertEqual(user.subscription_end, original_end)
        mock_sync.assert_not_called()

    @patch("services.provider_refunds._update_topup_after_refund", new_callable=AsyncMock)
    @patch("services.provider_refunds.create_payment_debit", new_callable=AsyncMock)
    @patch("services.provider_refunds._get_or_create_payment_refund", new_callable=AsyncMock)
    async def test_split_funded_purchase_routes_to_manual_review(
        self,
        mock_get_refund,
        mock_debit,
        mock_update_topup,
    ):
        """Split-funded purchase (e.g. balance + topup) routes to manual_review to prevent over-restore."""
        now = datetime.now(timezone.utc)
        payment = Payment(
            id=40,
            user_id=1,
            amount=Decimal("200.00"),  # Top-up was 200 RUB
            currency="RUB",
            topup_context={"auto_fulfill_action": "purchase"},
            provider_status="succeeded",
            credited_at=now,
        )

        # Debit entry for the purchase was 500 RUB (300 RUB from balance + 200 RUB from this payment)
        p_debit = AccountLedgerEntry(
            id=501,
            entry_type="purchase_debit",
            amount=Decimal("-500.00"),
            user_id=1,
            payment_id=payment.id,
        )
        allocation = AccountLedgerAllocation(
            id=601,
            credit_entry_id=10,
            debit_entry_id=501,
            amount=Decimal("200.00"),
        )

        session = AsyncMock()
        session.add = MagicMock()
        def scalar_side_effect(stmt):
            stmt_str = str(stmt).lower()
            if "provider_refund_operations" in stmt_str:
                return None
            if "payment_refunds" in stmt_str:
                return Decimal(0)
            if "account_balance_reservations" in stmt_str:
                return None
            if "account_ledger_entries" in stmt_str:
                return None
            return None

        session.scalar = AsyncMock(side_effect=scalar_side_effect)

        async def get_side_effect(model, ident):
            if model == AccountLedgerEntry and ident == 501:
                return p_debit
            return None

        session.get = AsyncMock(side_effect=get_side_effect)

        def scalars_side_effect(stmt):
            stmt_str = str(stmt).lower()
            res = MagicMock()
            if "account_ledger_allocations" in stmt_str:
                res.all.return_value = [allocation]
            else:
                res.all.return_value = []
            return res

        session.scalars = AsyncMock(side_effect=scalars_side_effect)

        nested_mock = AsyncMock()
        nested_mock.__aenter__ = AsyncMock()
        nested_mock.__aexit__ = AsyncMock()
        session.begin_nested = MagicMock(return_value=nested_mock)

        await apply_balance_topup_refund_success(
            session,
            payment=payment,
            provider_refund_id="rf_split",
            amount=Decimal("200.00"),
            currency="RUB",
            event_key="evt_split",
        )

        self.assertEqual(payment.reconciliation_status, "manual_review")
        self.assertEqual(payment.fulfillment_status, "manual_review")
        self.assertEqual(payment.manual_review_reason, "split_funded_purchase_requires_admin_review")
        mock_debit.assert_not_called()

    @patch("services.provider_refunds._update_topup_after_refund", new_callable=AsyncMock)
    @patch("services.provider_refunds.create_payment_debit", new_callable=AsyncMock)
    @patch("services.provider_refunds._get_or_create_payment_refund", new_callable=AsyncMock)
    async def test_chained_refund_routes_to_manual_review(
        self,
        mock_get_refund,
        mock_debit,
        mock_update_topup,
    ):
        """Second refund on payment with existing refund / manual_review routes to chained_refund_requires_admin_review."""
        now = datetime.now(timezone.utc)
        payment = Payment(
            id=50,
            user_id=1,
            amount=Decimal("500.00"),
            currency="RUB",
            topup_context={"auto_fulfill_action": "purchase"},
            provider_status="succeeded",
            credited_at=now,
            reconciliation_status="manual_review",  # already under review or had prior refund
        )

        session = AsyncMock()
        session.add = MagicMock()
        def scalar_side_effect(stmt):
            stmt_str = str(stmt).lower()
            if "provider_refund_operations" in stmt_str:
                return None
            if "payment_refunds" in stmt_str:
                return Decimal("200.00")  # prior refund already happened
            if "account_balance_reservations" in stmt_str:
                return None
            if "account_ledger_entries" in stmt_str:
                return None
            return None

        session.scalar = AsyncMock(side_effect=scalar_side_effect)
        scalars_mock = MagicMock()
        scalars_mock.all.return_value = []
        session.scalars = AsyncMock(return_value=scalars_mock)

        nested_mock = AsyncMock()
        nested_mock.__aenter__ = AsyncMock()
        nested_mock.__aexit__ = AsyncMock()
        session.begin_nested = MagicMock(return_value=nested_mock)

        await apply_balance_topup_refund_success(
            session,
            payment=payment,
            provider_refund_id="rf_chain",
            amount=Decimal("300.00"),
            currency="RUB",
            event_key="evt_chain",
        )

        self.assertEqual(payment.reconciliation_status, "manual_review")
        self.assertEqual(payment.fulfillment_status, "manual_review")
        self.assertEqual(payment.manual_review_reason, "chained_refund_requires_admin_review")
        mock_debit.assert_not_called()

    @patch("services.provider_refunds._update_topup_after_refund", new_callable=AsyncMock)
    @patch("services.provider_refunds.create_payment_debit", new_callable=AsyncMock)
    @patch("services.provider_refunds._get_or_create_payment_refund", new_callable=AsyncMock)
    async def test_white_internet_renew_routes_to_manual_review(
        self,
        mock_get_refund,
        mock_debit,
        mock_update_topup,
    ):
        """Refunding white_internet_renew routes to manual review and preserves subscription."""
        now = datetime.now(timezone.utc)
        payment = Payment(
            id=60,
            user_id=1,
            amount=Decimal("350.00"),
            currency="RUB",
            topup_context={"auto_fulfill_action": "white_internet_renew"},
            provider_status="succeeded",
            credited_at=now,
        )

        session = AsyncMock()
        session.add = MagicMock()
        def scalar_side_effect(stmt):
            stmt_str = str(stmt).lower()
            if "provider_refund_operations" in stmt_str:
                return None
            if "payment_refunds" in stmt_str:
                return Decimal(0)
            if "account_balance_reservations" in stmt_str:
                return None
            if "account_ledger_entries" in stmt_str:
                return None
            return None

        session.scalar = AsyncMock(side_effect=scalar_side_effect)
        scalars_mock = MagicMock()
        scalars_mock.all.return_value = []
        session.scalars = AsyncMock(return_value=scalars_mock)

        nested_mock = AsyncMock()
        nested_mock.__aenter__ = AsyncMock()
        nested_mock.__aexit__ = AsyncMock()
        session.begin_nested = MagicMock(return_value=nested_mock)

        await apply_balance_topup_refund_success(
            session,
            payment=payment,
            provider_refund_id="rf_wi_renew",
            amount=Decimal("350.00"),
            currency="RUB",
            event_key="evt_wi_renew",
        )

        self.assertEqual(payment.reconciliation_status, "manual_review")
        self.assertEqual(payment.fulfillment_status, "manual_review")
        self.assertEqual(payment.manual_review_reason, "white_internet_renew_refund_requires_admin_review")
        mock_debit.assert_not_called()

    @patch("services.subscription.SubscriptionService._sync_access_state", new_callable=AsyncMock)
    @patch("services.user_cache.invalidate_user_cache")
    async def test_place_financial_hold_syncs_nodes_and_invalidates_cache(
        self,
        mock_invalidate_cache,
        mock_sync,
    ):
        """place_financial_hold sets hold, syncs node state and invalidates user cache."""
        user = User(id=1, telegram_id=12345, financial_hold=False, topup_blocked=False)
        payment = Payment(id=70, user_id=1)

        session = AsyncMock()
        session.scalar = AsyncMock(return_value=user)

        await place_financial_hold(session, payment=payment, reason="chargeback_debt")

        self.assertTrue(user.financial_hold)
        self.assertTrue(user.topup_blocked)
        self.assertEqual(user.financial_block_reason, "chargeback_debt")
        self.assertEqual(payment.reconciliation_status, "manual_review")
        self.assertEqual(payment.fulfillment_status, "manual_review")
        mock_sync.assert_awaited_once_with(session, user)
        mock_invalidate_cache.assert_called_once_with(12345)


if __name__ == "__main__":
    unittest.main()
