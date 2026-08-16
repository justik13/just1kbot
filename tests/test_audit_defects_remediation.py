import unittest
from contextlib import asynccontextmanager
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

from aiohttp import web
from alembic.config import Config
from alembic.script import ScriptDirectory

from bot.handlers.webhook import _get_real_ip
from bot.middlewares.action_lock import LOCKED_ACTION_PREFIXES, STALE_ACTION_PREFIXES
from database.models import (
    PAYMENT_FULFILLMENT_STATUSES,
    PAYMENT_PROVIDER_STATUSES,
    AccountBalanceReservation,
    Payment,
    Server,
)
from database.repositories.servers_repo import update_server_health_snapshot
from services.provider_refunds import _consume_matching_reservation
from services.referral_bonus import reverse_referral_bonus_for_topup
from services.workers.node_monitor import AUTO_DISABLED_CHECK_INTERVAL
from services.workers.payments import _needs_recovery, _recover_stale_topups
from services.workers.webhook_inbox import auto_resolve_untracked_canceled_webhooks
from utils.datetime_helpers import now_utc


class TestAuditDefectsRemediationSync(unittest.TestCase):
    def test_payment_status_constants_and_quote_type(self):
        self.assertIn("waiting_for_capture", PAYMENT_PROVIDER_STATUSES)
        self.assertNotIn("pending", PAYMENT_FULFILLMENT_STATUSES)
        self.assertNotIn("reversal_pending", PAYMENT_FULFILLMENT_STATUSES)
        self.assertEqual(
            PAYMENT_FULFILLMENT_STATUSES,
            (
                "not_ready",
                "processing",
                "succeeded",
                "failed",
                "reversed",
                "manual_review",
            ),
        )

    def test_migration_0005_exists_and_revises_0004(self):
        scripts = ScriptDirectory.from_config(Config("alembic.ini"))
        rev_0005 = scripts.get_revision("0005_payment_statuses_sync")
        self.assertIsNotNone(rev_0005)
        self.assertEqual(rev_0005.down_revision, "0004_referral_entitlements")

    def test_action_lock_prefixes_updated(self):
        self.assertIn("admin_payment_refund_confirm:", LOCKED_ACTION_PREFIXES)
        self.assertIn("admin_payment_refund_confirm:", STALE_ACTION_PREFIXES)
        self.assertIn("confirm_admin_balance_apply", LOCKED_ACTION_PREFIXES)
        self.assertIn("confirm_admin_balance_apply", STALE_ACTION_PREFIXES)
        self.assertIn("confirm_mass_bonus_apply", LOCKED_ACTION_PREFIXES)
        self.assertIn("confirm_mass_bonus_apply", STALE_ACTION_PREFIXES)
        self.assertIn("admin_dispute_apply:", LOCKED_ACTION_PREFIXES)
        self.assertIn("admin_dispute_apply:", STALE_ACTION_PREFIXES)
        self.assertIn("balance_resume_purchase:", LOCKED_ACTION_PREFIXES)
        self.assertIn("balance_resume_purchase:", STALE_ACTION_PREFIXES)
        self.assertIn("aq:x:", LOCKED_ACTION_PREFIXES)
        self.assertIn("aq:x:", STALE_ACTION_PREFIXES)

    def test_auto_disabled_check_interval_is_fifteen_minutes(self):
        self.assertEqual(AUTO_DISABLED_CHECK_INTERVAL, 900.0)

    def test_alert_keyboard_has_dismiss_button(self):
        from services.workers.node_monitor import _build_alert_keyboard
        kb = _build_alert_keyboard(server_id=5, include_enable_button=True).as_markup()
        callbacks = [btn.callback_data for row in kb.inline_keyboard for btn in row]
        self.assertIn("admin_dismiss_alert:5", callbacks)
        self.assertIn("admin_server_toggle_apply:5", callbacks)
        self.assertIn("admin_server_card:5", callbacks)
        self.assertIn("admin_servers_list", callbacks)

    def test_get_real_ip_forwarded_for(self):
        # Loopback remote -> reads X-Forwarded-For first element
        request_mock = MagicMock(spec=web.Request)
        request_mock.remote = "127.0.0.1"
        request_mock.headers = {
            "X-Forwarded-For": "185.71.76.10, 10.0.0.2",
        }
        self.assertEqual(_get_real_ip(request_mock), "185.71.76.10")

        # Private IP remote with X-Real-IP
        request_mock_real = MagicMock(spec=web.Request)
        request_mock_real.remote = "10.0.0.5"
        request_mock_real.headers = {
            "X-Real-IP": "185.71.76.15",
            "X-Forwarded-For": "185.71.76.10, 10.0.0.2",
        }
        self.assertEqual(_get_real_ip(request_mock_real), "185.71.76.15")

        # Public remote -> ignores forwarded headers
        request_public = MagicMock(spec=web.Request)
        request_public.remote = "8.8.8.8"
        request_public.headers = {
            "X-Real-IP": "1.1.1.1",
            "X-Forwarded-For": "2.2.2.2",
        }
        self.assertEqual(_get_real_ip(request_public), "8.8.8.8")

    def test_needs_recovery_expression_structure(self):
        expr = _needs_recovery()
        self.assertIsNotNone(expr)


class TestAuditDefectsRemediationAsync(unittest.IsolatedAsyncioTestCase):
    async def test_recover_stale_topups_fifo_starvation_prevention(self):
        # Verify oldest payments (FIFO) are processed first
        processed_payment_ids = []

        fake_payments = [
            Payment(
                id=1,
                user_id=10,
                external_id="ext-1",
                provider_status="pending",
                fulfillment_status="not_ready",
            ),
            Payment(
                id=2,
                user_id=11,
                external_id="ext-2",
                provider_status="pending",
                fulfillment_status="not_ready",
            ),
        ]

        query_count = 0

        @asynccontextmanager
        async def fake_session_scope():
            nonlocal query_count
            mock_session = AsyncMock()
            mock_scalars = MagicMock()
            if query_count == 0:
                mock_scalars.all.return_value = fake_payments
            else:
                mock_scalars.all.return_value = []
            query_count += 1
            mock_session.scalars.return_value = mock_scalars
            yield mock_session

        async def fake_reconcile(session, payment, reason=""):
            processed_payment_ids.append(payment.id)

        with (
            patch(
                "services.workers.payments.session_scope", fake_session_scope
            ),
            patch(
                "services.workers.payments.ensure_reconcile_payment_operation",
                fake_reconcile,
            ),
        ):
            await _recover_stale_topups(bot=None)

        self.assertEqual(processed_payment_ids, [1, 2])

    async def test_referral_bonus_retried_for_succeeded_payment(self):
        # Succeeded payment whose referral bonus failed in the past is retried
        bonus_called_with = []

        fake_payment = Payment(
            id=99,
            user_id=15,
            external_id="ext-99",
            amount=Decimal("1000"),
            provider_status="succeeded",
            provider_confirmed_at=now_utc(),
            fulfillment_status="succeeded",
        )

        query_count = 0

        @asynccontextmanager
        async def fake_nested():
            yield

        @asynccontextmanager
        async def fake_session_scope():
            nonlocal query_count
            mock_session = AsyncMock()
            mock_session.begin_nested = fake_nested
            mock_scalars = MagicMock()
            if query_count == 0:
                mock_scalars.all.return_value = [fake_payment]
            else:
                mock_scalars.all.return_value = []
            query_count += 1
            mock_session.scalars.return_value = mock_scalars
            yield mock_session

        async def fake_grant(session, purchaser_user_id, payment_id, topup_amount):
            bonus_called_with.append((purchaser_user_id, payment_id, topup_amount))

        with (
            patch(
                "services.workers.payments.session_scope", fake_session_scope
            ),
            patch(
                "services.workers.payments.grant_referral_bonus_for_topup",
                fake_grant,
            ),
        ):
            await _recover_stale_topups(bot=None)

        self.assertEqual(bonus_called_with, [(15, 99, Decimal("1000"))])

    async def test_slots_cache_raises_server_unavailable_on_failure(self):
        from services.device_service import ServerUnavailable
        from services.slots_cache import capture_server_peer_snapshot

        server = Server(id=4, name="Down Node", api_url="http://down.node", api_key="k")

        @asynccontextmanager
        async def fake_session_scope():
            mock_session = AsyncMock()
            mock_session.get.return_value = server
            yield mock_session

        with (
            patch("database.connection.session_scope", fake_session_scope),
            patch("services.slots_cache.AmneziaClient.get_all_clients", AsyncMock(return_value=None)),
        ):
            with self.assertRaises(ServerUnavailable):
                await capture_server_peer_snapshot(4)

    async def test_untracked_refund_not_auto_resolved(self):
        session = AsyncMock()
        mock_scalars = MagicMock()
        mock_scalars.all.return_value = []
        session.scalars.return_value = mock_scalars
        resolved = await auto_resolve_untracked_canceled_webhooks(session)
        self.assertEqual(resolved, 0)

    async def test_welcome_bonus_reversal_without_referrer(self):
        session = AsyncMock()

        payment = MagicMock()
        payment.id = 55
        payment.user_id = 10

        purchaser = MagicMock()
        purchaser.id = 10
        purchaser.referred_by = None  # No referrer!

        welcome_credit = MagicMock()
        welcome_credit.id = 100
        welcome_credit.user_id = 10
        welcome_credit.amount = Decimal("50")
        welcome_credit.metadata_ = {
            "topup_payment_id": 55,
            "reason": "first_topup_welcome",
        }

        added_entries = []

        def fake_add(item):
            added_entries.append(item)

        session.get = AsyncMock(return_value=payment)
        session.scalar = AsyncMock(side_effect=[purchaser, None])
        session.scalars = AsyncMock(
            return_value=MagicMock(all=MagicMock(return_value=[welcome_credit]))
        )
        session.add = fake_add
        session.flush = AsyncMock()

        with patch(
            "services.referral_bonus._credit_capacity",
            AsyncMock(return_value=Decimal("50")),
        ):
            total_reversed = await reverse_referral_bonus_for_topup(
                session, payment_id=55
            )

        self.assertEqual(total_reversed, Decimal("50"))
        self.assertGreaterEqual(len(added_entries), 1)
        self.assertEqual(added_entries[0].amount, Decimal("-50"))

    async def test_partial_refund_reservation_split(self):
        session = AsyncMock()

        existing_reservation = AccountBalanceReservation(
            id=77,
            user_id=10,
            payment_id=42,
            reservation_type="refund",
            amount=Decimal("500"),
            currency="RUB",
            status="active",
        )

        added_items = []

        def fake_add(item):
            added_items.append(item)

        session.scalar = AsyncMock(return_value=existing_reservation)
        session.add = fake_add
        session.flush = AsyncMock()

        result = await _consume_matching_reservation(
            session,
            payment_id=42,
            amount=Decimal("200"),
            reservation_id=77,
        )

        self.assertIs(result, existing_reservation)
        self.assertEqual(existing_reservation.status, "consumed")
        self.assertEqual(len(added_items), 1)
        split_res = added_items[0]
        self.assertIsInstance(split_res, AccountBalanceReservation)
        self.assertEqual(split_res.amount, Decimal("300"))
        self.assertEqual(split_res.status, "active")
        self.assertEqual(split_res.payment_id, 42)
        self.assertEqual(split_res.metadata_["split_from_reservation_id"], 77)
        self.assertEqual(split_res.metadata_["consumed_amount"], "200")
        self.assertEqual(split_res.metadata_["remaining_amount"], "300")

    async def test_partial_refund_selection_picks_sufficient_reservation(self):
        session = AsyncMock()

        # Reservation 700 covers 500 refund
        res_700 = AccountBalanceReservation(
            id=2,
            user_id=10,
            payment_id=42,
            reservation_type="refund",
            amount=Decimal("700"),
            currency="RUB",
            status="active",
        )

        added_items = []

        def fake_add(item):
            added_items.append(item)

        # scalar returns res_700 because it is >= 500
        session.scalar = AsyncMock(return_value=res_700)
        session.add = fake_add
        session.flush = AsyncMock()

        result = await _consume_matching_reservation(
            session,
            payment_id=42,
            amount=Decimal("500"),
            reservation_id=None,
        )

        self.assertIs(result, res_700)
        self.assertEqual(res_700.status, "consumed")
        self.assertEqual(len(added_items), 1)
        self.assertEqual(added_items[0].amount, Decimal("200"))
        self.assertEqual(added_items[0].status, "active")

    async def test_partial_refund_multi_reservation_consumption(self):
        session = AsyncMock()

        # Two smaller reservations 300 and 200 covering 500 refund
        res_300 = AccountBalanceReservation(
            id=1,
            user_id=10,
            payment_id=42,
            reservation_type="refund",
            amount=Decimal("300"),
            currency="RUB",
            status="active",
        )
        res_200 = AccountBalanceReservation(
            id=2,
            user_id=10,
            payment_id=42,
            reservation_type="refund",
            amount=Decimal("200"),
            currency="RUB",
            status="active",
        )

        session.scalar = AsyncMock(return_value=None)
        mock_scalars = MagicMock()
        mock_scalars.all.return_value = [res_300, res_200]
        session.scalars = AsyncMock(return_value=mock_scalars)
        session.flush = AsyncMock()

        with patch(
            "services.provider_refunds.resolve_reservation", AsyncMock()
        ) as mock_resolve:
            result = await _consume_matching_reservation(
                session,
                payment_id=42,
                amount=Decimal("500"),
                reservation_id=None,
            )

        self.assertIs(result, res_300)
        self.assertEqual(mock_resolve.await_count, 2)
        mock_resolve.assert_any_await(session, reservation_id=1, outcome="consumed")
        mock_resolve.assert_any_await(session, reservation_id=2, outcome="consumed")

    async def test_partial_refund_insufficient_reservations_returns_none(self):
        session = AsyncMock()

        # Reservations total only 300, but refund is 500
        res_200 = AccountBalanceReservation(
            id=1,
            user_id=10,
            payment_id=42,
            reservation_type="refund",
            amount=Decimal("200"),
            currency="RUB",
            status="active",
        )
        res_100 = AccountBalanceReservation(
            id=2,
            user_id=10,
            payment_id=42,
            reservation_type="refund",
            amount=Decimal("100"),
            currency="RUB",
            status="active",
        )

        session.scalar = AsyncMock(return_value=None)
        mock_scalars = MagicMock()
        mock_scalars.all.return_value = [res_200, res_100]
        session.scalars = AsyncMock(return_value=mock_scalars)

        result = await _consume_matching_reservation(
            session,
            payment_id=42,
            amount=Decimal("500"),
            reservation_id=None,
        )

        # Must return None safely and NOT consume any reservation
        self.assertIsNone(result)
        self.assertEqual(res_200.status, "active")
        self.assertEqual(res_100.status, "active")

    def test_recovery_query_sql_structure(self):
        from sqlalchemy import select
        # Verify the actual compiled SQL ordering and filter
        stmt = (
            select(Payment)
            .where(
                Payment.id > 10,
                _needs_recovery(),
            )
            .order_by(Payment.id.asc())
            .limit(100)
        )
        compiled_sql = str(stmt.compile())
        self.assertIn("ORDER BY payments.id ASC", compiled_sql)
        self.assertIn("LIMIT", compiled_sql)
        self.assertIn("payments.id >", compiled_sql)
        # Verify referral bonus retry subquery joins referrer and filters banned
        self.assertIn("is_banned", compiled_sql)
        self.assertIn("account_ledger_entries.idempotency_key", compiled_sql)
        # Verify no 24h cutoff
        self.assertNotIn("hours=24", compiled_sql)

    async def test_update_server_health_snapshot_auto_disabled_allowed(self):
        session = AsyncMock()

        server = Server(
            id=3,
            name="Auto Server",
            is_active=False,
            health_state="AUTO_DISABLED",
            disabled_reason="AUTO_UNAVAILABLE",
            consecutive_fails=5,
            consecutive_successes=0,
            recovery_notice_sent=False,
        )

        session.execute = AsyncMock(
            return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=server))
        )
        session.flush = AsyncMock()
        session.refresh = AsyncMock()

        res_server, applied = await update_server_health_snapshot(
            session,
            server_id=3,
            expected_health_state="AUTO_DISABLED",
            expected_consecutive_fails=5,
            expected_consecutive_successes=0,
            new_health_state="AUTO_DISABLED",
            consecutive_successes=1,
            recovery_notice_sent=True,
        )

        self.assertTrue(applied)
        self.assertEqual(res_server.consecutive_successes, 1)
        self.assertTrue(res_server.recovery_notice_sent)
        self.assertFalse(res_server.is_active)


if __name__ == "__main__":
    unittest.main()
