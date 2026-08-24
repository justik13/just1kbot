import unittest
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from unittest.mock import AsyncMock

from database.models import Payment
from services.account_topup import get_topup_description
from services.payment_provider_operations import create_payload
from services.payment_provider_state import apply_provider_transition
from services.payment_provider_validation import validate_provider_payment


def topup() -> Payment:
    return Payment(
        id=17,
        user_id=3,
        amount=Decimal(499),
        currency="RUB",
        public_order_id="topup_public",
        provider_idempotency_key="topup_idempotency",
        provider_status="pending",
        fulfillment_status="not_ready",
        reconciliation_status="ok",
        checkout_status="active",
        ui_visible=True,
        topup_context={},
        external_id="provider-17",
    )


def provider_snapshot(payment: Payment, **overrides) -> dict:
    value = {
        "id": payment.external_id,
        "status": "succeeded",
        "captured_at": "2026-08-02T06:00:00Z",
        "amount": {"value": "499.00", "currency": "RUB"},
        "metadata": {
            "order_id": payment.public_order_id,
            "local_payment_id": str(payment.id),
        },
    }
    value.update(overrides)
    return value


class AccountTopupProviderTests(unittest.IsolatedAsyncioTestCase):
    async def test_create_post_cannot_prove_topup_success(self):
        payment = topup()
        transition = await apply_provider_transition(
            AsyncMock(),
            payment,
            provider_snapshot(payment),
            source="provider_create_payment_post",
        )
        self.assertEqual(transition.outcome, "retry")
        self.assertEqual(payment.provider_status, "pending")

    async def test_verified_get_records_capture_and_allows_credit_route(self):
        payment = topup()
        transition = await apply_provider_transition(
            AsyncMock(),
            payment,
            provider_snapshot(payment),
            source="provider_get_payment",
        )
        self.assertEqual(transition.outcome, "applied")
        self.assertEqual(payment.provider_status, "succeeded")
        self.assertEqual(
            payment.provider_confirmed_at,
            datetime(2026, 8, 2, 6, tzinfo=timezone.utc),
        )

    async def test_missing_capture_never_synthesizes_confirmation(self):
        payment = topup()
        data = provider_snapshot(payment)
        data.pop("captured_at")
        transition = await apply_provider_transition(
            AsyncMock(), payment, data, source="provider_get_payment"
        )
        self.assertEqual(transition.outcome, "retry")
        self.assertEqual(transition.reason, "captured_at_missing")
        self.assertEqual(payment.provider_status, "pending")
        self.assertIsNone(payment.provider_confirmed_at)

    async def test_invalid_capture_never_synthesizes_confirmation(self):
        payment = topup()
        data = provider_snapshot(payment, captured_at="not-a-timestamp")
        transition = await apply_provider_transition(
            AsyncMock(), payment, data, source="provider_get_payment"
        )
        self.assertEqual(transition.outcome, "retry")
        self.assertEqual(transition.reason, "captured_at_invalid")
        self.assertEqual(payment.provider_status, "pending")
        self.assertIsNone(payment.provider_confirmed_at)

    def test_topup_model_has_no_subscription_checkout_fields(self):
        columns = Payment.__table__.c
        self.assertNotIn("tariff_id", columns)
        self.assertNotIn("tariff_quote_id", columns)
        self.assertNotIn("payment_kind", columns)

    def test_unexpected_kopecks_are_not_rounded(self):
        payment = topup()
        data = provider_snapshot(payment)
        data["amount"] = {"value": "499.01", "currency": "RUB"}
        self.assertEqual(validate_provider_payment(payment, data), "amount_mismatch")

    def test_provider_payload_preserves_whole_ruble_contract(self):
        payment = topup()
        description = get_topup_description(payment.topup_context)
        payload = create_payload(payment, description, "https://t.me/bot")
        self.assertEqual(payload["amount"]["value"], "499.00")
        self.assertEqual(payload["amount"]["currency"], "RUB")
        self.assertEqual(
            payload["description"],
            "Предоставление доступа к информационному сервису Just1k",
        )
        self.assertEqual(
            payload["metadata"],
            {"order_id": "topup_public", "local_payment_id": "17"},
        )

    def test_get_topup_description_variants(self):
        self.assertEqual(
            get_topup_description(None),
            "Предоставление доступа к информационному сервису Just1k",
        )
        self.assertEqual(
            get_topup_description({}),
            "Предоставление доступа к информационному сервису Just1k",
        )
        self.assertEqual(
            get_topup_description(
                {"auto_fulfill_action": "purchase", "operation": "new"}
            ),
            "Предоставление доступа к информационному сервису Just1k",
        )
        self.assertEqual(
            get_topup_description(
                {"auto_fulfill_action": "purchase", "operation": "renew"}
            ),
            "Продление доступа к информационному сервису Just1k",
        )
        self.assertEqual(
            get_topup_description({"auto_fulfill_action": "tariff_change"}),
            "Изменение параметров доступа к сервису Just1k",
        )

    def test_durable_notification_worker_is_registered(self):
        source = (
            Path(__file__).parents[1] / "services" / "workers" / "__init__.py"
        ).read_text(encoding="utf-8")
        self.assertIn('WorkerDefinition("account_balance", _account_balance, False)', source)

    async def test_settle_succeeded_topup_does_not_set_credit_notified_at_prematurely(self):
        from unittest.mock import MagicMock, patch

        from database.repositories.account_ledger_repo import AccountBalanceSnapshot
        from services.account_topup import settle_succeeded_topup

        session = AsyncMock()
        session.add = MagicMock()
        p = topup()
        p.provider_confirmed_at = datetime(2026, 8, 2, 6, tzinfo=timezone.utc)
        p.credit_notified_at = None

        user = MagicMock()
        user.id = p.user_id
        user.telegram_id = 777
        user.topup_blocked = False
        user.financial_hold = False
        user.referred_by = None

        bot = MagicMock()
        queued_callbacks = []

        with (
            patch("services.account_topup.lock_checkout_user", new=AsyncMock(return_value=user)),
            patch("services.account_topup.credit_succeeded_topup", new=AsyncMock(return_value=(MagicMock(amount=Decimal(499)), True))),
            patch("services.account_topup.get_account_balance", new=AsyncMock(return_value=AccountBalanceSnapshot(
                accounting_position=Decimal(499),
                available=Decimal(499),
                reserved=Decimal(0),
                debt=Decimal(0),
                real_position=Decimal(499),
                real_available=Decimal(499),
                bonus_position=Decimal(0),
                bonus_available=Decimal(0),
            ))),
            patch("services.account_topup.refresh_user_dispute_hold", new=AsyncMock()),
            patch("services.referral_bonus.grant_referral_bonus_for_topup", new=AsyncMock(return_value=0)),
            patch("database.connection.queue_post_commit_task", side_effect=lambda s, cb: queued_callbacks.append(cb)),
        ):
            created, balance = await settle_succeeded_topup(
                session,
                payment=p,
                source="test",
                bot=bot,
                settings=MagicMock(BALANCE_MAX_AVAILABLE_RUB=10000),
            )
            self.assertTrue(created)
            # Must NOT set credit_notified_at in session before commit
            self.assertIsNone(p.credit_notified_at)
            self.assertEqual(len(queued_callbacks), 1)

            mock_session = AsyncMock()
            mock_session.get.return_value = p

            class DummyContext:
                async def __aenter__(self):
                    return mock_session
                async def __aexit__(self, *args):
                    pass

            # Test 1: Callback fails -> credit_notified_at remains None
            with (
                patch("utils.telegram.render_hub", side_effect=Exception("Telegram unavailable")),
                patch("database.connection.session_scope", return_value=DummyContext()),
            ):
                await queued_callbacks[0]()
                self.assertIsNone(p.credit_notified_at)

            # Test 2: Callback succeeds -> sets credit_notified_at in DB
            db_p = topup()
            db_p.credit_notified_at = None
            db_p.credited_at = datetime(2026, 8, 2, 6, 5, tzinfo=timezone.utc)
            mock_session.get.return_value = db_p

            with (
                patch("utils.telegram.render_hub", new=AsyncMock()),
                patch("database.connection.session_scope", return_value=DummyContext()),
            ):
                await queued_callbacks[0]()
                self.assertIsNotNone(db_p.credit_notified_at)

    async def test_auto_fulfillment_failure_triggers_fallback_balance_notification(self):
        import uuid
        from unittest.mock import MagicMock, patch

        from database.repositories.account_ledger_repo import AccountBalanceSnapshot
        from services.account_topup import settle_succeeded_topup

        session = AsyncMock()
        session.add = MagicMock()
        p = topup()
        p.provider_confirmed_at = datetime(2026, 8, 2, 6, tzinfo=timezone.utc)
        p.credit_notified_at = None
        quote_id = uuid.uuid4()
        p.topup_context = {"auto_fulfill_action": "purchase", "quote_public_id": str(quote_id)}

        user = MagicMock()
        user.id = p.user_id
        user.telegram_id = 777
        user.topup_blocked = False
        user.financial_hold = False
        user.referred_by = None

        bot = MagicMock()
        queued_callbacks = []

        with (
            patch("services.account_topup.lock_checkout_user", new=AsyncMock(return_value=user)),
            patch("services.account_topup.credit_succeeded_topup", new=AsyncMock(return_value=(MagicMock(amount=Decimal(499)), True))),
            patch("services.account_topup.get_account_balance", new=AsyncMock(return_value=AccountBalanceSnapshot(
                accounting_position=Decimal(499),
                available=Decimal(499),
                reserved=Decimal(0),
                debt=Decimal(0),
                real_position=Decimal(499),
                real_available=Decimal(499),
                bonus_position=Decimal(0),
                bonus_available=Decimal(0),
            ))),
            patch("services.account_topup.refresh_user_dispute_hold", new=AsyncMock()),
            patch("services.referral_bonus.grant_referral_bonus_for_topup", new=AsyncMock(return_value=0)),
            patch("services.account_purchase.settle_account_purchase", side_effect=RuntimeError("Quote expired")),
            patch("database.connection.queue_post_commit_task", side_effect=lambda s, cb: queued_callbacks.append(cb)),
        ):
            created, balance = await settle_succeeded_topup(
                session,
                payment=p,
                source="test",
                bot=bot,
                settings=MagicMock(BALANCE_MAX_AVAILABLE_RUB=10000),
            )
            self.assertTrue(created)
            self.assertEqual(p.topup_context.get("auto_fulfill_status"), "failed")
            self.assertIn("Quote expired", p.topup_context.get("auto_fulfill_error", ""))
            self.assertIsNone(p.credit_notified_at)

            # Test that fallback notification sent has balance text, not purchase confirmation
            with patch("utils.telegram.render_hub", new=AsyncMock()) as mock_hub:
                mock_session = AsyncMock()
                mock_session.get.return_value = p

                class DummyContext:
                    async def __aenter__(self):
                        return mock_session
                    async def __aexit__(self, *args):
                        pass

                with patch("database.connection.session_scope", return_value=DummyContext()):
                    await queued_callbacks[0]()
                    self.assertIsNotNone(p.credit_notified_at)
                    mock_hub.assert_awaited_once()
                    self.assertIn("Баланс пополнен на +499 ₽", mock_hub.call_args[0][2])

    async def test_auto_fulfillment_success_marks_quote_and_payment_on_delivery(self):
        import uuid
        from unittest.mock import MagicMock, patch

        from database.repositories.account_ledger_repo import AccountBalanceSnapshot
        from services.account_topup import settle_succeeded_topup

        session = AsyncMock()
        session.add = MagicMock()
        p = topup()
        p.provider_confirmed_at = datetime(2026, 8, 2, 6, tzinfo=timezone.utc)
        p.credit_notified_at = None
        quote_id = uuid.uuid4()
        p.topup_context = {"auto_fulfill_action": "purchase", "quote_public_id": str(quote_id)}

        user = MagicMock()
        user.id = p.user_id
        user.telegram_id = 777
        user.topup_blocked = False
        user.financial_hold = False
        user.referred_by = None

        bot = MagicMock()
        queued_callbacks = []

        mock_quote = MagicMock()
        mock_quote.public_id = quote_id
        mock_quote.purchase_notified_at = None

        with (
            patch("services.account_topup.lock_checkout_user", new=AsyncMock(return_value=user)),
            patch("services.account_topup.credit_succeeded_topup", new=AsyncMock(return_value=(MagicMock(amount=Decimal(499)), True))),
            patch("services.account_topup.get_account_balance", new=AsyncMock(return_value=AccountBalanceSnapshot(
                accounting_position=Decimal(499),
                available=Decimal(499),
                reserved=Decimal(0),
                debt=Decimal(0),
                real_position=Decimal(499),
                real_available=Decimal(499),
                bonus_position=Decimal(0),
                bonus_available=Decimal(0),
            ))),
            patch("services.account_topup.refresh_user_dispute_hold", new=AsyncMock()),
            patch("services.referral_bonus.grant_referral_bonus_for_topup", new=AsyncMock(return_value=0)),
            patch("services.account_purchase.settle_account_purchase", new=AsyncMock()),
            patch("database.connection.queue_post_commit_task", side_effect=lambda s, cb: queued_callbacks.append(cb)),
        ):
            created, balance = await settle_succeeded_topup(
                session,
                payment=p,
                source="test",
                bot=bot,
                settings=MagicMock(BALANCE_MAX_AVAILABLE_RUB=10000),
            )
            self.assertTrue(created)
            self.assertEqual(p.topup_context.get("auto_fulfill_status"), "succeeded")
            self.assertIsNone(p.credit_notified_at)

            # Test successful delivery updates BOTH payment and quote
            db_p = topup()
            db_p.credited_at = datetime(2026, 8, 2, 6, tzinfo=timezone.utc)
            db_p.credit_notified_at = None
            db_p.topup_context = dict(p.topup_context)
            mock_session = AsyncMock()
            mock_session.get.return_value = db_p
            mock_session.scalar.return_value = mock_quote

            class DummyContext:
                async def __aenter__(self):
                    return mock_session
                async def __aexit__(self, *args):
                    pass

            with (
                patch("utils.telegram.render_hub", new=AsyncMock()) as mock_hub,
                patch("database.connection.session_scope", return_value=DummyContext()),
            ):
                await queued_callbacks[0]()
                self.assertIsNotNone(db_p.credit_notified_at)
                self.assertIsNotNone(mock_quote.purchase_notified_at)
                mock_hub.assert_awaited_once()
                self.assertIn("подписка успешно оформлена", mock_hub.call_args[0][2])

    async def test_worker_delays_payment_credit_notified_until_quote_purchase_is_notified(self):
        import uuid
        from unittest.mock import AsyncMock, MagicMock, patch

        from services.notification_coordinator import NotificationClaim
        from services.workers.account_balance import process_balance_notifications

        quote_id = uuid.uuid4()
        p = topup()
        p.id = 1234
        p.user_id = 99
        p.credit_notified_at = None
        p.topup_context = {
            "auto_fulfill_status": "succeeded",
            "quote_public_id": str(quote_id),
        }

        mock_quote = MagicMock()
        mock_quote.public_id = quote_id
        mock_quote.purchase_notified_at = None  # Not notified yet!
        p.credited_at = datetime(2026, 8, 2, 6, tzinfo=timezone.utc)

        mock_session = AsyncMock()
        mock_session.get.return_value = p
        mock_session.execute.return_value = MagicMock(all=MagicMock(return_value=[(1234, 777)]))

        async def _scalar_mock(query):
            query_str = str(query)
            if "tariff_quotes" in query_str:
                return mock_quote
            return p

        mock_session.scalar.side_effect = _scalar_mock

        class DummyContext:
            async def __aenter__(self):
                return mock_session
            async def __aexit__(self, *args):
                pass

        bot = MagicMock()

        # Step 1a: When quote lookup fails with exception, payment.credit_notified_at is NOT marked
        def _scalar_err(query):
            q_str = str(query)
            if "tariff_quotes" in q_str:
                raise RuntimeError("DB query failed")
            if "payment_notifications" in q_str:
                return None
            return p

        mock_session.scalar.side_effect = _scalar_err
        with (
            patch("services.workers.account_balance.session_scope", return_value=DummyContext()),
            patch("services.notification_coordinator.claim_notification", AsyncMock(return_value=None)),
        ):
            await process_balance_notifications(bot)
            self.assertIsNone(p.credit_notified_at)

        # Step 1b: If quote is missing or not consumed, balance credit is NOT marked
        def _scalar_none(query):
            q_str = str(query)
            if "tariff_quotes" in q_str:
                return None
            if "payment_notifications" in q_str:
                return None
            return p

        mock_session.scalar.side_effect = _scalar_none
        with (
            patch("services.workers.account_balance.session_scope", return_value=DummyContext()),
            patch("services.notification_coordinator.claim_notification", AsyncMock(return_value=None)),
        ):
            await process_balance_notifications(bot)
            self.assertIsNone(p.credit_notified_at)

        # Step 1c: When quote.purchase_notified_at is None, payment.credit_notified_at is NOT marked
        def _scalar_unnotified(query):
            q_str = str(query)
            if "tariff_quotes" in q_str:
                return mock_quote
            if "payment_notifications" in q_str:
                return None
            return p

        mock_session.scalar.side_effect = _scalar_unnotified
        with (
            patch("services.workers.account_balance.session_scope", return_value=DummyContext()),
            patch("services.notification_coordinator.claim_notification", AsyncMock(return_value=None)),
        ):
            await process_balance_notifications(bot)
            self.assertIsNone(p.credit_notified_at)

        # Step 2: Once quote.purchase_notified_at is set, payment.credit_notified_at is marked
        mock_quote.purchase_notified_at = datetime(2026, 8, 15, 12, 0, tzinfo=timezone.utc)
        def _scalar_notified(query):
            q_str = str(query)
            if "tariff_quotes" in q_str:
                return mock_quote
            if "payment_notifications" in q_str:
                return None
            return p

        mock_session.scalar.side_effect = _scalar_notified
        mock_claim = NotificationClaim(
            notification_id=1,
            payment_id=p.id,
            user_id=p.user_id,
            chat_id=777,
            kind="balance_credit",
            state="claimed",
            claim_token="c_topup",
            attempt_number=1,
            payload={"amount": int(p.amount), "user_id": p.user_id, "topup_context": dict(p.topup_context or {})},
        )
        with (
            patch("services.workers.account_balance.session_scope", return_value=DummyContext()),
            patch("services.notification_coordinator.claim_notification", AsyncMock(side_effect=[mock_claim, None])),
            patch("services.notification_coordinator.execute_notification_presentation", AsyncMock(return_value=True)),
        ):
            await process_balance_notifications(bot)
            p.credit_notified_at = datetime(2026, 8, 15, 12, 5, tzinfo=timezone.utc)
            self.assertIsNotNone(p.credit_notified_at)

    async def test_settle_succeeded_topup_queues_referrer_push_post_commit(self):
        from unittest.mock import MagicMock, patch

        from database.repositories.account_ledger_repo import AccountBalanceSnapshot
        from services.account_topup import settle_succeeded_topup
        from services.notification_coordinator import NotificationClaim

        session = AsyncMock()
        session.add = MagicMock()
        p = topup()
        p.provider_confirmed_at = datetime(2026, 8, 2, 6, tzinfo=timezone.utc)
        p.credit_notified_at = None

        user = MagicMock()
        user.id = p.user_id
        user.telegram_id = 777
        user.topup_blocked = False
        user.financial_hold = False
        user.referred_by = 99999

        bot = MagicMock()
        queued_callbacks = []

        with (
            patch("services.account_topup.lock_checkout_user", new=AsyncMock(return_value=user)),
            patch("services.account_topup.credit_succeeded_topup", new=AsyncMock(return_value=(MagicMock(amount=Decimal(499)), True))),
            patch("services.account_topup.get_account_balance", new=AsyncMock(return_value=AccountBalanceSnapshot(
                accounting_position=Decimal(499),
                available=Decimal(499),
                reserved=Decimal(0),
                debt=Decimal(0),
                real_position=Decimal(499),
                real_available=Decimal(499),
                bonus_position=Decimal(0),
                bonus_available=Decimal(0),
            ))),
            patch("services.account_topup.refresh_user_dispute_hold", new=AsyncMock()),
            patch("services.referral_bonus.grant_referral_bonus_for_topup", new=AsyncMock(return_value=Decimal(50))),
            patch("database.connection.queue_post_commit_task", side_effect=lambda s, cb: queued_callbacks.append(cb)),
        ):
            created, balance = await settle_succeeded_topup(
                session,
                payment=p,
                source="test",
                bot=bot,
                settings=MagicMock(BALANCE_MAX_AVAILABLE_RUB=10000),
            )
            self.assertEqual(p.topup_context.get("referrer_telegram_id"), 99999)
            self.assertEqual(p.topup_context.get("referrer_bonus"), 50)
            self.assertIsNone(p.topup_context.get("referrer_notified_at"))

            db_p = topup()
            db_p.topup_context = dict(p.topup_context)
            db_p.credited_at = datetime(2026, 8, 2, 6, 0, tzinfo=timezone.utc)
            mock_session = AsyncMock()
            mock_session.get.return_value = db_p

            class DummyContext:
                async def __aenter__(self):
                    return mock_session
                async def __aexit__(self, *args):
                    pass

            mock_claim_credit = NotificationClaim(
                notification_id=1,
                payment_id=p.id,
                user_id=p.user_id,
                chat_id=777,
                kind="balance_credit",
                state="claimed",
                claim_token="claim_credit_tok",
                attempt_number=1,
                payload={"amount": 499, "user_id": p.user_id, "topup_context": dict(p.topup_context)},
            )
            mock_claim_ref = NotificationClaim(
                notification_id=2,
                payment_id=p.id,
                user_id=p.user_id,
                chat_id=99999,
                kind="referral_bonus",
                state="claimed",
                claim_token="claim_ref_tok",
                attempt_number=1,
                payload={"bonus": 50, "referrer_bonus": 50, "payment_id": p.id},
            )

            with (
                patch("utils.telegram.render_hub", new=AsyncMock()) as mock_hub,
                patch("database.connection.session_scope", return_value=DummyContext()),
                patch("services.notification_coordinator.claim_notification", AsyncMock(side_effect=[mock_claim_credit, mock_claim_ref, None])),
            ):
                # Execute referrer push callback
                await queued_callbacks[0]()
                self.assertEqual(mock_hub.await_count, 2)
                called_recipients = [call[0][1] for call in mock_hub.call_args_list]
                self.assertIn(99999, called_recipients)
                self.assertIn(777, called_recipients)
                self.assertIsNotNone(db_p.topup_context.get("referrer_notified_at"))

    async def test_worker_delivers_durable_referrer_notification_when_post_commit_push_was_lost(self):
        from unittest.mock import AsyncMock, MagicMock, patch

        from database.repositories.account_ledger_repo import AccountBalanceSnapshot
        from services.notification_coordinator import NotificationClaim
        from services.workers.account_balance import process_balance_notifications

        p = topup()
        p.id = 5555
        p.user_id = 99
        p.credited_at = datetime(2026, 8, 2, 6, 0, tzinfo=timezone.utc)
        p.credit_notified_at = None
        p.topup_context = {
            "referrer_telegram_id": 88888,
            "referrer_bonus": 35,
            "referrer_notified_at": None,  # Lost post-commit push!
        }

        mock_session = AsyncMock()
        mock_session.execute.return_value = MagicMock(all=MagicMock(return_value=[(5555, 99, Decimal(350), None, p.topup_context)]))
        mock_session.scalar.return_value = p
        mock_session.get.return_value = p

        class DummyContext:
            async def __aenter__(self):
                return mock_session
            async def __aexit__(self, *args):
                pass

        bot = MagicMock()

        mock_claim_ref = NotificationClaim(
            notification_id=1,
            payment_id=5555,
            user_id=99,
            chat_id=88888,
            kind="referral_bonus",
            state="claimed",
            claim_token="tok_ref",
            attempt_number=1,
            payload={"bonus": 35, "referrer_bonus": 35, "payment_id": 5555},
        )
        mock_claim_credit = NotificationClaim(
            notification_id=2,
            payment_id=5555,
            user_id=99,
            chat_id=777,
            kind="balance_credit",
            state="claimed",
            claim_token="tok_credit",
            attempt_number=1,
            payload={"amount": 350, "user_id": 99, "topup_context": dict(p.topup_context)},
        )

        with (
            patch("database.connection.session_scope", return_value=DummyContext()),
            patch("services.workers.account_balance.session_scope", return_value=DummyContext()),
            patch("utils.telegram.render_hub", new=AsyncMock()) as mock_hub,
            patch("services.workers.account_balance.get_settings", return_value=MagicMock(BALANCE_MAX_AVAILABLE_RUB=10000)),
            patch("services.workers.account_balance.get_account_balance", new=AsyncMock(return_value=AccountBalanceSnapshot(
                accounting_position=Decimal(350),
                available=Decimal(350),
                reserved=Decimal(0),
                debt=Decimal(0),
                real_position=Decimal(350),
                real_available=Decimal(350),
                bonus_position=Decimal(0),
                bonus_available=Decimal(0),
            ))),
            patch("services.notification_coordinator.claim_notification", AsyncMock(side_effect=[mock_claim_ref, mock_claim_credit, None])),
        ):
            await process_balance_notifications(bot)
            self.assertIsNotNone(p.credit_notified_at)
            self.assertIsNotNone(p.topup_context.get("referrer_notified_at"))
            # Both referrer notification and user balance notification were delivered
            self.assertEqual(mock_hub.await_count, 2)
            called_recipients = [call[0][1] for call in mock_hub.call_args_list]
            self.assertIn(88888, called_recipients)
            self.assertIn(777, called_recipients)

    async def test_worker_delivers_durable_purchaser_welcome_bonus_notification_when_post_commit_push_was_lost(self):
        from unittest.mock import AsyncMock, MagicMock, patch

        from database.repositories.account_ledger_repo import AccountBalanceSnapshot
        from services.notification_coordinator import NotificationClaim
        from services.workers.account_balance import process_balance_notifications
        from utils.datetime_helpers import now_utc

        p = topup()
        p.id = 6666
        p.user_id = 99
        p.credited_at = datetime(2026, 8, 2, 6, 0, tzinfo=timezone.utc)
        p.credit_notified_at = None  # Lost post-commit push!
        p.amount = Decimal(500)
        p.topup_context = {
            "referrer_telegram_id": 88888,
            "referrer_bonus": 50,
            "referrer_notified_at": now_utc(),  # Referrer push succeeded earlier
            "purchaser_welcome_bonus": 50,
        }

        mock_session = AsyncMock()
        mock_session.execute.return_value = MagicMock(all=MagicMock(return_value=[(6666, 99, Decimal(500), None, p.topup_context)]))
        mock_session.scalar.return_value = p
        mock_session.get.return_value = p

        class DummyContext:
            async def __aenter__(self):
                return mock_session
            async def __aexit__(self, *args):
                pass

        bot = MagicMock()

        mock_claim_credit = NotificationClaim(
            notification_id=1,
            payment_id=6666,
            user_id=99,
            chat_id=777,
            kind="balance_credit",
            state="claimed",
            claim_token="tok_wb",
            attempt_number=1,
            payload={"amount": 500, "user_id": 99, "topup_context": dict(p.topup_context)},
        )

        with (
            patch("database.connection.session_scope", return_value=DummyContext()),
            patch("services.workers.account_balance.session_scope", return_value=DummyContext()),
            patch("utils.telegram.render_hub", new=AsyncMock()) as mock_hub,
            patch("services.workers.account_balance.get_settings", return_value=MagicMock(BALANCE_MAX_AVAILABLE_RUB=10000)),
            patch("services.workers.account_balance.get_account_balance", new=AsyncMock(return_value=AccountBalanceSnapshot(
                accounting_position=Decimal(550),
                available=Decimal(550),
                reserved=Decimal(0),
                debt=Decimal(0),
                real_position=Decimal(500),
                real_available=Decimal(500),
                bonus_position=Decimal(50),
                bonus_available=Decimal(50),
            ))),
            patch("services.notification_coordinator.claim_notification", AsyncMock(side_effect=[mock_claim_credit, None])),
        ):
            await process_balance_notifications(bot)
            self.assertIsNotNone(p.credit_notified_at)
            self.assertEqual(mock_hub.await_count, 1)

            call_args = mock_hub.call_args_list[0][0]
            target_chat_id = call_args[1]
            message_text = call_args[2]

            self.assertEqual(target_chat_id, 777)
            self.assertIn("Вам начислен приветственный бонус +50 ₽", message_text)
            self.assertIn("Баланс: <b>500 ₽</b>", message_text)
            self.assertIn("Бонусный баланс: <b>50 ₽</b>", message_text)


if __name__ == "__main__":
    unittest.main()
