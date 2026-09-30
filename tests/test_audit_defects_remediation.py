import unittest
from contextlib import asynccontextmanager
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

from aiohttp import web
from alembic.config import Config
from alembic.script import ScriptDirectory

from bot.handlers.webhook import _get_real_ip
from bot.middlewares.action_lock import LOCKED_ACTION_PREFIXES, STALE_ACTION_PREFIXES
from config.enums import PaymentFulfillmentStatus, PaymentProviderStatus
from database.models import Server
from database.repositories.servers_repo import update_server_health_snapshot
from services.referral_bonus import reverse_referral_bonus_for_topup
from services.workers.node_monitor import AUTO_DISABLED_CHECK_INTERVAL
from utils.datetime_helpers import now_utc


class TestAuditDefectsRemediationSync(unittest.TestCase):
    def test_payment_status_constants_and_quote_type(self):
        provider_statuses = tuple(m.value for m in PaymentProviderStatus)
        fulfillment_statuses = tuple(m.value for m in PaymentFulfillmentStatus)
        self.assertIn("waiting_for_capture", provider_statuses)
        self.assertNotIn("pending", fulfillment_statuses)
        self.assertNotIn("reversal_pending", fulfillment_statuses)
        self.assertEqual(
            fulfillment_statuses,
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
        self.assertIn("confirm_admin_balance_apply", LOCKED_ACTION_PREFIXES)
        self.assertIn("confirm_admin_balance_apply", STALE_ACTION_PREFIXES)
        self.assertIn("confirm_mass_bonus_apply", LOCKED_ACTION_PREFIXES)
        self.assertIn("confirm_mass_bonus_apply", STALE_ACTION_PREFIXES)

        for dead_prefix in (
            "admin_payment_refund_confirm:",
            "admin_dispute_apply:",
            "balance_resume_purchase:",
            "balance_purchase_confirm:",
            "balance_change_confirm:",
            "bal_short_exact:",
            "bal_chg_short_exact:",
            "aq:x:",
        ):
            self.assertNotIn(dead_prefix, LOCKED_ACTION_PREFIXES)
            self.assertNotIn(dead_prefix, STALE_ACTION_PREFIXES)

        for prefix in (
            "admin_wi_traffic_add:",
            "admin_wi_traffic_reset_apply:",
            "admin_wi_quota_set:",
            "admin_wi_devlimit_set:",
            "admin_wi_hwid_reset_apply:",
            "admin_wl_reset_apply:",
            "admin_wl_grant_trial:",
            "wl_add_device_confirm",
            "wl_buy_confirm",
            "wl_renew_confirm",
            "wl_topup_pack_",
            "confirm_server_purge:",
            "admin_server_migrate_confirm:",
            "admin_bal_preset:",
        ):
            self.assertIn(prefix, LOCKED_ACTION_PREFIXES)
            self.assertIn(prefix, STALE_ACTION_PREFIXES)

        for wrong_prefix in (
            "admin_wi_traffic_add_apply:",
            "admin_wi_quota_set_apply:",
            "admin_wi_devlimit_set_apply:",
            "admin_wi_reset_trial_apply:",
            "admin_wi_grant_trial_apply:",
        ):
            self.assertNotIn(wrong_prefix, LOCKED_ACTION_PREFIXES)
            self.assertNotIn(wrong_prefix, STALE_ACTION_PREFIXES)

    def test_auto_disabled_check_interval_is_fifteen_minutes(self):
        self.assertEqual(AUTO_DISABLED_CHECK_INTERVAL, 900.0)

    def test_alert_keyboard_has_dismiss_button(self):
        from services.workers.node_monitor import _build_alert_keyboard
        kb = _build_alert_keyboard(server_id=5, include_enable_button=True).as_markup()
        callbacks = [btn.callback_data for row in kb.inline_keyboard for btn in row]
        self.assertIn("admin_dismiss_alert:5", callbacks)
        self.assertIn("admin_server_toggle_apply:5", callbacks)
        self.assertIn("admin_server_card:5", callbacks)
        self.assertIn("admin_servers", callbacks)

    def test_get_real_ip_forwarded_for(self):
        # Loopback remote with trusted proxy chain -> resolves rightmost
        # untrusted client IP; the client-controllable first entry can never
        # win, so a spoofed YooKassa allowlist address is ignored.
        request_mock = MagicMock(spec=web.Request)
        request_mock.remote = "127.0.0.1"
        request_mock.app = {"trusted_proxies": "127.0.0.1,::1,10.0.0.0/8"}
        request_mock.headers = {
            "X-Forwarded-For": "185.71.76.10, 198.51.100.9",
        }
        self.assertEqual(_get_real_ip(request_mock), "198.51.100.9")

        # Spoofed leftmost entry must be ignored even when it mimics a
        # YooKassa allowlisted address.
        request_spoof = MagicMock(spec=web.Request)
        request_spoof.remote = "127.0.0.1"
        request_spoof.headers = {
            "X-Forwarded-For": "185.71.76.10, 198.51.100.9, 172.16.0.9",
        }
        self.assertEqual(_get_real_ip(request_spoof), "198.51.100.9")

        # Trusted proxy peer (declared via app config) with X-Real-IP
        request_mock_real = MagicMock(spec=web.Request)
        request_mock_real.remote = "10.0.0.5"
        request_mock_real.app = {"trusted_proxies": "10.0.0.0/8"}
        request_mock_real.headers = {
            "X-Real-IP": "185.71.76.15",
            "X-Forwarded-For": "185.71.76.10, 10.0.0.2",
        }
        self.assertEqual(_get_real_ip(request_mock_real), "185.71.76.15")

        # Private peer NOT declared as trusted proxy -> forwarded headers are
        # ignored so private networks cannot spoof the YooKassa allowlist
        request_untrusted = MagicMock(spec=web.Request)
        request_untrusted.remote = "10.0.0.5"
        request_untrusted.app = {"trusted_proxies": "127.0.0.1,::1,172.16.0.0/12"}
        request_untrusted.headers = {
            "X-Real-IP": "185.71.76.15",
            "X-Forwarded-For": "185.71.76.10, 10.0.0.2",
        }
        self.assertEqual(_get_real_ip(request_untrusted), "10.0.0.5")

        # Public remote -> ignores forwarded headers
        request_public = MagicMock(spec=web.Request)
        request_public.remote = "8.8.8.8"
        request_public.headers = {
            "X-Real-IP": "1.1.1.1",
            "X-Forwarded-For": "2.2.2.2",
        }
        self.assertEqual(_get_real_ip(request_public), "8.8.8.8")

        # Rightmost untrusted resolution defeats spoofed first IP in X-Forwarded-For
        request_spoofed = MagicMock(spec=web.Request)
        request_spoofed.remote = "127.0.0.1"
        request_spoofed.app = {"trusted_proxies": "127.0.0.1,::1,10.0.0.0/8"}
        request_spoofed.headers = {
            "X-Forwarded-For": "185.71.76.10, 203.0.113.50, 10.0.0.2",
        }
        self.assertEqual(_get_real_ip(request_spoofed), "203.0.113.50")

class TestAuditDefectsRemediationAsync(unittest.IsolatedAsyncioTestCase):

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
        welcome_credit.amount = Decimal(50)
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
            AsyncMock(return_value=Decimal(50)),
        ):
            total_reversed = await reverse_referral_bonus_for_topup(
                session, payment_id=55
            )

        self.assertEqual(total_reversed, Decimal(50))
        self.assertGreaterEqual(len(added_entries), 1)
        self.assertEqual(added_entries[0].amount, Decimal(-50))


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

    def test_apply_user_filters_differentiates_no_sub_and_never(self):
        from database.repositories.users_repo import _apply_user_filters
        from sqlalchemy import select
        from database.models import User

        stmt_never = _apply_user_filters(select(User), "never")
        stmt_no_sub = _apply_user_filters(select(User), "no_sub")

        compiled_never = str(stmt_never.compile())
        compiled_no_sub = str(stmt_no_sub.compile())

        self.assertNotEqual(compiled_never, compiled_no_sub)
        self.assertIn("users.subscription_end IS NULL", compiled_never)

class TestAuditDefectsRemediationXrayAndLedgerAsync(unittest.IsolatedAsyncioTestCase):
    async def test_admin_user_card_hwid_ttl_filtering(self):
        from bot.handlers.admin.users.common import _get_white_internet_card_info
        from datetime import timedelta
        from unittest.mock import MagicMock, AsyncMock
        from config.constants import WHITE_INTERNET_HWID_TTL_HOURS

        now = now_utc()
        fresh_ts = (now - timedelta(hours=1)).isoformat()
        stale_ts = (now - timedelta(hours=WHITE_INTERNET_HWID_TTL_HOURS + 1)).isoformat()

        session = AsyncMock()
        sub = MagicMock()
        sub.origin_node_id = None
        sub.status = "ACTIVE"
        sub.device_limit = 2
        sub.traffic_used_bytes = 0
        sub.traffic_limit_bytes = 10 * 1024 * 1024 * 1024
        sub.expires_at = None
        sub.provisioning_status = "ACTIVE"
        sub.last_sync_error = None
        sub.last_error = None
        sub.active_hwids = {
            "hwid-fresh-1": fresh_ts,
            "hwid-stale-2": stale_ts,
        }

        block = await _get_white_internet_card_info(session, user_id=1, sub=sub)
        self.assertIsNotNone(block)
        # Stale HWID must be filtered out, leaving only 1 active device out of 2
        self.assertIn("1 / 2", block)

    async def test_migrate_origin_subscriptions_rejects_missing_xray_origin_or_relays(self):
        from config.constants import XRAY_PROTOCOL
        from config.enums import ServerHealthState
        from database.repositories.servers_repo import migrate_origin_subscriptions

        session = AsyncMock()
        source = Server(id=1, protocol=XRAY_PROTOCOL, is_active=True, capabilities=["xray_origin"], health_state=ServerHealthState.ONLINE)
        target_no_cap = Server(id=2, protocol=XRAY_PROTOCOL, is_active=True, capabilities=[], health_state=ServerHealthState.ONLINE, extra_data={"relays": [{"code": "de"}]})

        session.execute = AsyncMock(
            side_effect=[
                MagicMock(scalar_one_or_none=MagicMock(return_value=source)),
                MagicMock(scalar_one_or_none=MagicMock(return_value=target_no_cap)),
            ]
        )

        with self.assertRaises(ValueError) as ctx:
            await migrate_origin_subscriptions(session, source_id=1, target_id=2)
        self.assertIn("xray_origin", str(ctx.exception))

        # Target with capability but no relays
        target_no_relays = Server(id=2, protocol=XRAY_PROTOCOL, is_active=True, capabilities=["xray_origin"], health_state=ServerHealthState.ONLINE, extra_data={})
        session.execute = AsyncMock(
            side_effect=[
                MagicMock(scalar_one_or_none=MagicMock(return_value=source)),
                MagicMock(scalar_one_or_none=MagicMock(return_value=target_no_relays)),
            ]
        )
        with self.assertRaises(ValueError) as ctx:
            await migrate_origin_subscriptions(session, source_id=1, target_id=2)
        self.assertIn("relays", str(ctx.exception))


    def test_admin_audit_actions_white_internet_enums(self):
        from config.enums import AdminAuditAction
        self.assertEqual(AdminAuditAction.WHITE_INTERNET_QUOTA_SET, "WHITE_INTERNET_QUOTA_SET")
        self.assertEqual(AdminAuditAction.WHITE_INTERNET_DEVLIMIT_SET, "WHITE_INTERNET_DEVLIMIT_SET")

    def test_count_active_hwids(self):
        from datetime import datetime, timezone
        from database.repositories.white_internet_repo import count_active_hwids

        now = datetime(2026, 9, 17, 12, 0, 0, tzinfo=timezone.utc)

        # None or invalid input
        self.assertEqual(count_active_hwids(None, now=now), 0)
        self.assertEqual(count_active_hwids({}, now=now), 0)
        self.assertEqual(count_active_hwids("invalid", now=now), 0)
        self.assertEqual(count_active_hwids([1, 2, 3], now=now), 0)

        # Active vs expired HWIDs with 48h TTL
        hwids = {
            "hwid_1": "2026-09-17T11:00:00+00:00",  # 1h ago -> active
            "hwid_2": "2026-09-16T12:00:00+00:00",  # 24h ago -> active
            "hwid_3": "2026-09-15T12:00:00+00:00",  # exactly 48h ago -> active (>= cutoff)
            "hwid_4": "2026-09-15T11:59:59+00:00",  # > 48h ago -> expired
            "hwid_5": "2026-09-10T00:00:00+00:00",  # long expired
            "hwid_6": None,                          # invalid value
            "hwid_7": 12345,                         # non-str
        }
        self.assertEqual(count_active_hwids(hwids, now=now), 3)

    async def test_white_internet_repo_get_subscription_by_user_id_validation(self):
        from database.repositories.white_internet_repo import get_subscription_by_user_id
        session = AsyncMock()

        # Invalid user_id types / ranges
        self.assertIsNone(await get_subscription_by_user_id(session, 0))
        self.assertIsNone(await get_subscription_by_user_id(session, -5))
        self.assertIsNone(await get_subscription_by_user_id(session, 2_147_483_648))
        self.assertIsNone(await get_subscription_by_user_id(session, "123"))

    def test_admin_fallback_parsing(self):
        from utils.admin import is_admin
        with patch("utils.admin.get_settings", side_effect=Exception("no settings")):
            with patch.dict("os.environ", {"ADMIN_IDS": "[12345, 67890]"}):
                self.assertTrue(is_admin(12345))
                self.assertTrue(is_admin(67890))
                self.assertFalse(is_admin(99999))
            with patch.dict("os.environ", {"ADMIN_IDS": "12345, 67890"}):
                self.assertTrue(is_admin(12345))
                self.assertFalse(is_admin(99999))


if __name__ == "__main__":
    unittest.main()
